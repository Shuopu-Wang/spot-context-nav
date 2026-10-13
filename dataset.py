"""
The dataset folder, image rendering, the export zip and merge-import.

Folder layout (DATA_DIR/dataset/), one set per training image:

    <image-name>__<id>/
        original.jpg                 the training image (decision frame for clips)
        original_clip.mp4            the clip, for video scenes
        annotations/<code>.jpg       one image per participant: the original with their path drawn
        annotations/excluded/...     responses you excluded in the admin
        vlm/<model>__<name>.png      VLM images of this training image
        all_paths.jpg                every human path, coloured by route, compiled path in black
        compiled_path.jpg            the compiled trajectory only (ready-made in-context example)
        paths.json                   all paths as coordinates + similarity, outliers, compiled path
        similarity.csv               pairwise similarity (0-1) between participants' paths
        frechet_distance.csv         the underlying Frechet distances (fraction of image diagonal)
    _vlm_unlinked/                   VLM images not matched to a training image
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import shutil
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import analysis as A

PATH_COLOR = (255, 45, 135)
ROBOT_COLOR = (36, 89, 255)
GOAL_COLOR = (31, 157, 85)
COMPILED_COLOR = (29, 36, 51)
MODE_COLORS = [(42, 120, 214), (235, 104, 52), (27, 175, 122)]   # validated categorical slots 1-3
OTHER_COLOR = (138, 147, 166)
MAX_W = 1600
UNLINKED = "_vlm_unlinked"

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(key):
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def jload(s, default=None):
    try:
        return json.loads(s) if s else default
    except (TypeError, ValueError):
        return default


def slug(s, n=40):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", str(s or "")).strip("-").lower()
    return (s[:n].strip("-") or "x")


def set_dir_name(row) -> str:
    return f"{slug(row['title'] or 'image')}__{row['id'][:6]}"


def vlm_file_name(c) -> str:
    stem = Path(c["original_filename"] or "").stem or ("wait" if c["decision"] == "wait" else "path")
    ext = Path(c["image_file"]).suffix.lower() if c["image_file"] else ".jpg"
    return f"{slug(c['model'], 30)}__{slug(stem, 40)}__{c['id'][:6]}{ext}"


def _json_default(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def _atomic_write(path: Path, data: bytes):
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _frame(media_dir: Path, scene) -> Image.Image:
    im = Image.open(media_dir / scene["id"] / scene["frame_file"]).convert("RGB")
    if im.width > MAX_W:
        im = im.resize((MAX_W, round(im.height * MAX_W / im.width)), Image.LANCZOS)
    return im


def _dashes(pts, on, off):
    out, cur, acc, drawing = [], [pts[0]], 0.0, True
    for a, b in zip(pts, pts[1:]):
        seg = math.dist(a, b)
        pos = 0.0
        while seg - pos > 1e-9:
            left = (on if drawing else off) - acc
            step = min(left, seg - pos)
            pos += step
            acc += step
            t = pos / seg
            p = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            if drawing:
                cur.append(p)
            if acc >= (on if drawing else off) - 1e-9:
                if drawing and len(cur) > 1:
                    out.append(cur)
                drawing, acc, cur = not drawing, 0.0, [p]
    if drawing and len(cur) > 1:
        out.append(cur)
    return out


def _chevrons(dr, pts, spacing, size, color):
    cum = 0.0
    nxt = spacing / 2
    for a, b in zip(pts, pts[1:]):
        seg = math.dist(a, b)
        while seg > 1e-9 and nxt <= cum + seg:
            t = (nxt - cum) / seg
            x, y = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
            ang = math.atan2(b[1] - a[1], b[0] - a[0])
            c, s = math.cos(ang), math.sin(ang)
            pts_ch = [(-size, -size * 1.2), (size * 0.8, 0), (-size, size * 1.2)]
            rot = [(x + px * c - py * s, y + px * s + py * c) for px, py in pts_ch]
            dr.line(rot, fill=color, width=max(2, round(size * 0.55)), joint="curve")
            nxt += spacing
        cum += seg


def draw_scene(im: Image.Image, paths=(), start=None, goal=None, band=None, wait=False):
    """paths: [(normalized pts, rgb, width_scale, dashed, arrows)]; band: (left, right, rgba)."""
    W, H = im.size
    dr = ImageDraw.Draw(im, "RGBA")
    base = max(2, round(math.hypot(W, H) / 330))
    if band:
        left, right, rgba = band
        poly = [(x * W, y * H) for x, y in left] + [(x * W, y * H) for x, y in reversed(right)]
        if len(poly) > 2:
            dr.polygon(poly, fill=rgba)
    for pts, color, ws, dashed, arrows in paths:
        px = [(x * W, y * H) for x, y in pts]
        if len(px) < 2:
            continue
        lw = max(2, round(base * ws))
        pieces = _dashes(px, lw * 3.5, lw * 2.5) if dashed else [px]
        for piece in pieces:
            dr.line(piece, fill=(255, 255, 255, 235), width=lw + 2 * max(2, lw // 3), joint="curve")
        for piece in pieces:
            dr.line(piece, fill=color, width=lw, joint="curve")
        if arrows:
            _chevrons(dr, px, base * 24, max(3, lw * 0.95), (255, 255, 255, 255))
    r = base * 3.4
    if goal:
        x, y = goal[0] * W, goal[1] * H
        dr.ellipse([x - r * 0.7, y - r * 0.3, x + r * 0.7, y + r * 0.3], fill=(0, 0, 0, 90))
        dr.line([x, y, x, y - r * 2.6], fill=(255, 255, 255), width=max(3, round(base * 1.2)))
        dr.line([x, y, x, y - r * 2.6], fill=(29, 36, 51), width=max(1, round(base * 0.5)))
        dr.polygon([(x + 1, y - r * 2.6), (x + r * 1.6, y - r * 2.1), (x + 1, y - r * 1.5)],
                   fill=GOAL_COLOR, outline=(255, 255, 255))
    if start:
        x, y = start[0] * W, start[1] * H
        dr.ellipse([x - r, y - r, x + r, y + r], fill=ROBOT_COLOR, outline=(255, 255, 255), width=max(2, round(base)))
        a = r * 0.55
        dr.polygon([(x, y - a), (x + a * 0.85, y + a * 0.7), (x, y + a * 0.25), (x - a * 0.85, y + a * 0.7)],
                   fill=(255, 255, 255))
    if wait and start:
        x, y = start[0] * W, start[1] * H
        f = _font(max(14, base * 7))
        text = "Robot waits here"
        tw = dr.textlength(text, font=f)
        pad = base * 3
        bx, by = x - tw / 2 - pad, y - r * 2 - base * 12
        dr.rounded_rectangle([bx, by, bx + tw + 2 * pad, by + base * 10], radius=base * 5, fill=(29, 36, 51, 230))
        dr.text((x - tw / 2, by + base * 1.6), text, font=f, fill=(255, 255, 255))
    return im


def _dense_norm(points, segments, style, W, H, kind="waypoints"):
    d = A.dense_path(points, W, H, kind, segments, style or "curve")
    return (d / np.array([W, H])).tolist()


def _anchors(scene):
    start = [scene["start_x"], scene["start_y"]]
    goal = [scene["goal_x"], scene["goal_y"]] if scene["goal_x"] is not None else None
    return start, goal


def _jpeg(im) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def render_drawing(scene, d, media_dir: Path) -> bytes:
    im = _frame(media_dir, scene)
    start, goal = _anchors(scene)
    paths = []
    if d["decision"] == "path" and d["points"]:
        pts = _dense_norm(jload(d["points"]), jload(d["segments"]), d["style"], scene["width"], scene["height"])
        paths.append((pts, PATH_COLOR, 1.35, False, True))
    return _jpeg(draw_scene(im, paths, start, goal, wait=d["decision"] == "wait"))


def render_candidate(scene, c, media_dir: Path) -> Path:
    """House-style image of a candidate that has coordinates (cached by content)."""
    key = hashlib.sha1(json.dumps([c["points"], c["segments"], c["style"], c["kind"], c["decision"],
                                   scene["frame_file"], scene["start_x"], scene["start_y"], scene["goal_x"],
                                   scene["goal_y"]]).encode()).hexdigest()[:10]
    out = media_dir / "vlm" / f"{c['id']}__{key}.jpg"
    if not out.exists():
        im = _frame(media_dir, scene)
        start, goal = _anchors(scene)
        paths = []
        if c["decision"] == "path" and c["points"]:
            pts = _dense_norm(jload(c["points"]), jload(c["segments"]), c["style"], scene["width"], scene["height"],
                              c["kind"] or "waypoints")
            paths.append((pts, PATH_COLOR, 1.15, False, True))
        _atomic_write(out, _jpeg(draw_scene(im, paths, start, goal, wait=c["decision"] == "wait")))
    return out


def render_control(scene, compiled: dict, media_dir: Path) -> Path | None:
    """Human-consensus image in the same style as rendered candidates (for hidden controls)."""
    if not compiled or not (compiled.get("curve") or compiled.get("wait")):
        return None
    key = hashlib.sha1(json.dumps([compiled.get("curve"), compiled.get("wait"), scene["frame_file"],
                                   scene["start_x"], scene["goal_x"]]).encode()).hexdigest()[:10]
    out = media_dir / "controls" / f"{scene['id']}__{key}.jpg"
    if not out.exists():
        start, goal = _anchors(scene)
        paths = [(compiled["curve"], PATH_COLOR, 1.15, False, True)] if compiled.get("curve") else []
        _atomic_write(out, _jpeg(draw_scene(_frame(media_dir, scene), paths, start, goal, wait=bool(compiled.get("wait")))))
    return out


def _mode_color(m):
    return MODE_COLORS[m] if m is not None and m < len(MODE_COLORS) else OTHER_COLOR


def render_all_paths(scene, analysis, media_dir: Path) -> bytes:
    start, goal = _anchors(scene)
    comp = analysis.get("compiled") or {}
    band = None
    if comp.get("corridor_left"):
        band = (comp["corridor_left"], comp["corridor_right"], (29, 36, 51, 60))
    paths = []
    for h in analysis["humans"]:
        if h.get("curve"):
            paths.append((h["curve"], OTHER_COLOR if h.get("outlier") else _mode_color(h.get("mode")),
                          0.55, bool(h.get("outlier") or h.get("trimmed")), False))
    if comp.get("curve"):
        paths.append((comp["curve"], COMPILED_COLOR, 1.5, False, True))
    return _jpeg(draw_scene(_frame(media_dir, scene), paths, start, goal, band, wait=bool(comp.get("wait"))))


def render_compiled(scene, analysis, media_dir: Path) -> bytes | None:
    comp = analysis.get("compiled") or {}
    if not (comp.get("curve") or comp.get("wait")):
        return None
    start, goal = _anchors(scene)
    paths = [(comp["curve"], PATH_COLOR, 1.15, False, True)] if comp.get("curve") else []
    return _jpeg(draw_scene(_frame(media_dir, scene), paths, start, goal, wait=bool(comp.get("wait"))))


# ---------------------------------------------------------------------------
# Folder sync
# ---------------------------------------------------------------------------

def _ensure_dir(row, dataset_dir: Path) -> Path:
    name = set_dir_name(row)
    d = dataset_dir / name
    if not d.exists():
        for old in dataset_dir.glob(f"*__{row['id'][:6]}"):
            if old.is_dir() and old.name != name:
                old.rename(d)
                break
    d.mkdir(parents=True, exist_ok=True)
    return d


def remove_set(row, dataset_dir: Path):
    for old in dataset_dir.glob(f"*__{row['id'][:6]}"):
        shutil.rmtree(old, ignore_errors=True)


def _copy_if_changed(src: Path, dst: Path):
    if src.exists() and (not dst.exists() or dst.stat().st_size != src.stat().st_size):
        shutil.copyfile(src, dst)


def _drawing_rows(con, scene_id):
    return con.execute("""SELECT d.*, p.code, p.role, p.excluded AS p_excluded FROM drawings d
                          JOIN participants p ON p.id = d.participant_id
                          WHERE d.scene_id = ? ORDER BY p.code""", (scene_id,)).fetchall()


def _analysis_key(row, s):
    return json.dumps([row["version"], row["frame_file"]] + [s.get(k) for k in sorted(A.DEFAULTS)], default=str)


def sync_set(con, row, s, dataset_dir: Path, media_dir: Path, analysis_fn, render_ids=(), force=False):
    """Bring one training image's folder up to date. Cheap when nothing changed."""
    with _lock(row["id"]):
        d = _ensure_dir(row, dataset_dir)
        state = jload((d / ".state.json").read_text() if (d / ".state.json").exists() else None, {}) or {}
        frame = media_dir / row["id"] / row["frame_file"]
        original = d / f"original{frame.suffix.lower()}"
        for f in d.glob("original.*"):
            if f != original:
                f.unlink()
        _copy_if_changed(frame, original)
        if row["media_type"] == "video":
            clip = media_dir / row["id"] / row["media_file"]
            _copy_if_changed(clip, d / f"original_clip{clip.suffix.lower()}")

        # annotations: one image per participant
        ann_state = state.get("annotations", {})
        wanted, new_state = set(), {}
        for dr in _drawing_rows(con, row["id"]):
            sub = "annotations/excluded" if (dr["excluded"] or dr["p_excluded"]) else "annotations"
            rel = f"{sub}/{dr['code']}.jpg"
            wanted.add(rel)
            stamp = f"{dr['created_at']}|{row['frame_file']}|{row['start_x']}|{row['goal_x']}|{row['goal_y']}"
            target = d / rel
            if force or dr["id"] in render_ids or not target.exists() or ann_state.get(rel) != stamp:
                target.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write(target, render_drawing(row, dr, media_dir))
            new_state[rel] = stamp
        for sub in ("annotations", "annotations/excluded"):
            folder = d / sub
            if folder.exists():
                for f in folder.glob("*.jpg"):
                    if f"{sub}/{f.name}" not in wanted:
                        f.unlink()
        ex = d / "annotations" / "excluded"
        if ex.exists() and not any(ex.iterdir()):
            ex.rmdir()

        # VLM images of this training image
        vdir = d / "vlm"
        cands = con.execute("SELECT * FROM candidates WHERE scene_id=? AND consensus_override=0 "
                            "AND COALESCE(source, '') != 'admin'", (row["id"],)).fetchall()
        keep = set()
        for c in cands:
            name = vlm_file_name(c)
            keep.add(name)
            vdir.mkdir(exist_ok=True)
            if c["image_file"]:
                _copy_if_changed(media_dir / "vlm" / c["image_file"], vdir / name)
            elif c["points"] or c["decision"] == "wait":
                _copy_if_changed(render_candidate(row, c, media_dir), vdir / name)
        if vdir.exists():
            for f in vdir.iterdir():
                if f.name not in keep:
                    f.unlink()
            if not any(vdir.iterdir()):
                vdir.rmdir()

        # set-level summary files
        key = _analysis_key(row, s)
        if force or state.get("summary_key") != key or not (d / "paths.json").exists():
            _write_summary(con, row, s, d, media_dir, analysis_fn(row), cands)
        _atomic_write(d / ".state.json", json.dumps({"annotations": new_state, "summary_key": key}).encode())
        return d


def _write_summary(con, row, s, d: Path, media_dir: Path, a: dict, cands):
    rows = {dr["id"]: dr for dr in _drawing_rows(con, row["id"])}
    hum = {h["id"]: h for h in a["humans"]}
    code = {i: rows[i]["code"] for i in rows}
    comp = a.get("compiled") or {}
    route_name = lambda m: None if m is None else chr(65 + m)  # noqa: E731
    annotations = []
    for did, dr in rows.items():
        h = hum.get(did, {})
        excluded = bool(dr["excluded"] or dr["p_excluded"])
        annotations.append({
            "code": dr["code"], "role": dr["role"], "decision": dr["decision"], "style": dr["style"],
            "points": jload(dr["points"]), "segments": jload(dr["segments"]), "norms": jload(dr["norms"], []),
            "confidence": dr["confidence"], "comment": dr["comment"], "duration_ms": dr["duration_ms"],
            "route": route_name(h.get("mode")), "outlier": h.get("outlier", False), "trimmed": h.get("trimmed", False),
            "excluded": excluded, "mean_similarity": h.get("mean_similarity"),
            "similarity_to_compiled": h.get("similarity_to_compiled"),
            "frechet_to_compiled": h.get("frechet_to_compiled"),
            "file": f"annotations/{'excluded/' if excluded else ''}{dr['code']}.jpg"})
    metrics = {c["id"]: c for c in a["candidates"]}
    vlm = []
    for c in cands:
        m = metrics.get(c["id"], {})
        vlm.append({"id": c["id"], "model": c["model"], "file": f"vlm/{vlm_file_name(c)}", "decision": c["decision"],
                    "points": jload(c["points"]), "segments": jload(c["segments"]),
                    **{k: m.get(k) for k in ("mode", "choice_agreement", "frechet_to_compiled",
                                             "similarity_to_compiled", "mse_to_compiled", "dtw_to_compiled")}})
    start, goal = _anchors(row)
    payload = {
        "image": {"id": row["id"], "title": row["title"], "source_name": row["source_name"],
                  "scenario": row["scenario"], "split": row["split"], "width": row["width"], "height": row["height"],
                  "start": start, "goal": goal, "original_file": f"original{Path(row['frame_file']).suffix.lower()}"},
        "coordinates": "normalized image coordinates (x / width, y / height), origin top-left; "
                       "distances are fractions of the image diagonal",
        "settings": a["params"],
        "summary": a["summary"],
        "compiled": {**{k: v for k, v in comp.items() if k not in ("member_ids", "trimmed_ids", "medoid_drawing_id")},
                     "members": [code.get(i) for i in comp.get("member_ids", [])],
                     "trimmed": [code.get(i) for i in comp.get("trimmed_ids", [])],
                     "medoid": code.get(comp.get("medoid_drawing_id"))} if comp else None,
        "routes": [{"route": route_name(m["mode"]), "count": m["count"], "share": m["share"],
                    "medoid": code.get(m["medoid_id"])} for m in a["modes"]],
        "annotations": annotations,
        "vlm_outputs": vlm,
    }
    _atomic_write(d / "paths.json", json.dumps(payload, indent=1, default=_json_default).encode())
    codes = [code.get(i) for i in a["pairwise_ids"]]
    for fname, matrix in (("similarity.csv", a["pairwise_similarity"]), ("frechet_distance.csv", a["pairwise_frechet"])):
        if codes:
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow([""] + codes)
            for c, r in zip(codes, matrix):
                w.writerow([c] + r)
            _atomic_write(d / fname, buf.getvalue().encode())
        else:
            (d / fname).unlink(missing_ok=True)
    if a["summary"]["n_responses"]:
        _atomic_write(d / "all_paths.jpg", render_all_paths(row, a, media_dir))
    else:
        (d / "all_paths.jpg").unlink(missing_ok=True)
    comp_img = render_compiled(row, a, media_dir)
    if comp_img:
        _atomic_write(d / "compiled_path.jpg", comp_img)
    else:
        (d / "compiled_path.jpg").unlink(missing_ok=True)
    _write_ranking(con, row, d, media_dir)


# ---------------------------------------------------------------------------
# Rankings of admin-drawn paths
# ---------------------------------------------------------------------------

RANK_COLORS = ["#ff2d87", "#2459ff", "#f2a900", "#00a6a6", "#8b5cf6", "#e4572e", "#5c7c00", "#0f172a",
               "#d946ef", "#0891b2", "#a16207", "#be123c"]


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def scene_ranking(con, sid):
    """(admin paths, ranking rows, summary) for one training image."""
    paths = con.execute("""SELECT * FROM candidates WHERE scene_id=? AND source='admin'
                           AND (points IS NOT NULL OR decision='wait') ORDER BY created_at""", (sid,)).fetchall()
    rows = con.execute("""SELECT r.*, p.code, p.role FROM rankings r JOIN participants p ON p.id = r.participant_id
                          WHERE r.scene_id=? AND r.excluded=0 AND p.excluded=0 ORDER BY p.code""", (sid,)).fetchall()
    rk = [{"ranking": jload(r["ranking"], []), "unacceptable": jload(r["unacceptable"], []), "code": r["code"],
           "role": r["role"], "comment": r["comment"], "duration_ms": r["duration_ms"], "created_at": r["created_at"]}
          for r in rows]
    return paths, rk, A.ranking_summary([c["id"] for c in paths], rk)


def path_labels(paths):
    return {c["id"]: (c["label"] or f"Path {i + 1}") for i, c in enumerate(paths)}


def _chip(dr, x, y, text, color, size):
    r = size
    dr.ellipse([x - r, y - r, x + r, y + r], fill=color, outline=(255, 255, 255), width=max(2, r // 5))
    f = _font(max(10, int(r * 1.2)))
    tw = dr.textlength(text, font=f)
    dr.text((x - tw / 2, y - r * 0.68), text, font=f, fill=(255, 255, 255))


def render_ranked_paths(scene, paths, summ, media_dir: Path) -> bytes:
    """Every admin path in its own colour, numbered by overall rank (1 = best) once people have ranked."""
    im = _frame(media_dir, scene)
    W, H = im.size
    start, goal = _anchors(scene)
    order = summ["order"]
    items, chips = [], []
    for i, c in enumerate(paths):
        color = _rgb(RANK_COLORS[i % len(RANK_COLORS)])
        num = str(order.index(c["id"]) + 1) if c["id"] in order else str(i + 1)
        if c["decision"] == "wait" or not c["points"]:
            chips.append((start[0] * W + 40, start[1] * H - 40 - 30 * len(chips), num + "W", color))
            continue
        pts = _dense_norm(jload(c["points"]), jload(c["segments"]), c["style"], scene["width"], scene["height"],
                          c["kind"] or "waypoints")
        items.append((pts, color, 0.9, False, True))
        mid = pts[len(pts) // 2]
        chips.append((mid[0] * W, mid[1] * H, num, color))
    draw_scene(im, items, start, goal)
    dr = ImageDraw.Draw(im)
    size = max(12, round(math.hypot(W, H) / 110))
    for x, y, t, col in chips:
        _chip(dr, x, y, t, col, size)
    return _jpeg(im)


def _write_ranking(con, row, d: Path, media_dir: Path):
    paths, rk, summ = scene_ranking(con, row["id"])
    files = ("ranking.json", "rankings.csv", "ranked_paths.jpg", "best_path.jpg")
    if not paths:
        for f in files:
            (d / f).unlink(missing_ok=True)
        return
    label = path_labels(paths)
    by_id = {c["id"]: c for c in paths}
    ordered = summ["order"] + [c["id"] for c in paths if c["id"] not in summ["order"]]
    start, goal = _anchors(row)
    payload = {
        "image": {"id": row["id"], "title": row["title"], "source_name": row["source_name"], "width": row["width"],
                  "height": row["height"], "start": start, "goal": goal,
                  "original_file": f"original{Path(row['frame_file']).suffix.lower()}"},
        "coordinates": "normalized image coordinates (x / width, y / height), origin top-left",
        "scoring": "score = normalized Borda count averaged over rankings (1 = always ranked best, 0 = always worst); "
                   "best_path = highest-scoring path that fewer than half the raters marked not socially acceptable",
        "n_rankings": summ["n_rankings"], "n_complete_rankings": summ["n_complete"],
        "kendall_w": summ["kendall_w"], "top_agreement": summ["top_agreement"],
        "best_path": label.get(summ["best"]), "best_path_id": summ["best"],
        "all_unacceptable": summ["all_unacceptable"],
        "paths": [{"id": i, "label": label[i], **summ["paths"][i], "is_best": i == summ["best"],
                   "decision": by_id[i]["decision"], "points": jload(by_id[i]["points"]),
                   "segments": jload(by_id[i]["segments"]), "style": by_id[i]["style"]} for i in ordered],
        "rankings": [{"code": r["code"], "role": r["role"], "order": [label[x] for x in r["ranking"] if x in label],
                      "not_acceptable": [label[x] for x in r["unacceptable"] if x in label], "comment": r["comment"]}
                     for r in rk],
    }
    _atomic_write(d / "ranking.json", json.dumps(payload, indent=1, default=_json_default).encode())
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["code", "role", "ranking_best_to_worst", "not_socially_acceptable", "comment"])
    for r in payload["rankings"]:
        w.writerow([r["code"], r["role"], " > ".join(r["order"]), "; ".join(r["not_acceptable"]), r["comment"] or ""])
    _atomic_write(d / "rankings.csv", buf.getvalue().encode())
    _atomic_write(d / "ranked_paths.jpg", render_ranked_paths(row, paths, summ, media_dir))
    best = by_id.get(summ["best"])
    if best is not None:
        _copy_bytes = render_candidate(row, best, media_dir).read_bytes()
        _atomic_write(d / "best_path.jpg", _copy_bytes)
    else:
        (d / "best_path.jpg").unlink(missing_ok=True)


def sync_unlinked(con, dataset_dir: Path, media_dir: Path):
    d = dataset_dir / UNLINKED
    with _lock(UNLINKED):
        cands = con.execute("SELECT * FROM candidates WHERE scene_id IS NULL AND image_file IS NOT NULL").fetchall()
        keep = set()
        for c in cands:
            d.mkdir(parents=True, exist_ok=True)
            name = vlm_file_name(c)
            keep.add(name)
            _copy_if_changed(media_dir / "vlm" / c["image_file"], d / name)
        if d.exists():
            for f in d.iterdir():
                if f.name not in keep:
                    f.unlink()
            if not any(d.iterdir()):
                d.rmdir()


def rebuild_all(con, s, dataset_dir: Path, media_dir: Path, analysis_fn):
    rows = con.execute("SELECT * FROM scenes").fetchall()
    names = {set_dir_name(r) for r in rows}
    for r in rows:
        sync_set(con, r, s, dataset_dir, media_dir, analysis_fn, force=True)
    for f in dataset_dir.iterdir():
        if f.is_dir() and f.name not in names and f.name != UNLINKED:
            shutil.rmtree(f, ignore_errors=True)
    sync_unlinked(con, dataset_dir, media_dir)


# ---------------------------------------------------------------------------
# Export zip
# ---------------------------------------------------------------------------

README = """TrajLab dataset
===============

images/<image>__<id>/          one folder per training image (a "set"):
    original.<ext>               the training image (for clips: the frame people drew on)
    annotations/<code>.jpg       each participant's path drawn on the original
    annotations/excluded/        responses excluded in the admin (kept for transparency)
    vlm/<model>__<name>.<ext>    VLM images of this training image
    all_paths.jpg                every human path coloured by route; dashed grey = outlier or
                                 trimmed; black = compiled trajectory with its spread band
    compiled_path.jpg            the compiled trajectory alone
    paths.json                   all paths as coordinates, routes, outliers, compiled path, VLM metrics
    similarity.csv               pairwise similarity between participants' paths (1 = identical,
                                 0.5 = at the same-route threshold)
    frechet_distance.csv         pairwise discrete Frechet distance (fraction of image diagonal)
    ranked_paths.jpg             the paths the admins drew, numbered by participants' overall rank (1 = best)
    best_path.jpg                the best-ranked path that most raters found socially acceptable
    ranking.json                 every admin path (coordinates, score, rank, % not acceptable) + each ranking
    rankings.csv                 one row per participant: their order, best to worst, and paths they flagged
images/_vlm_unlinked/          VLM images not matched to any training image

csv/path_scores.csv            one row per admin-drawn path: overall rank, score, % ranked first,
                               % marked not socially acceptable, is_best, coordinates
csv/rankings.csv               one row per (participant, image, path): rank given and whether flagged

csv/participants.csv           participant codes, role (team/survey), identifiers, demographics
csv/images.csv                 per training image: agreement summary + compiled trajectory
csv/annotations.csv            one row per human response, with path coordinates and statistics
csv/vlm_images.csv             one row per VLM image, with mean ratings and path metrics
csv/vlm_ratings.csv            one row per (rater, VLM image) with every questionnaire answer
csv/model_summary.csv          per model: mean answers with 95% bootstrap CI, path agreement
csv/reliability.csv            Krippendorff's alpha per question (inter-rater reliability)
dataset.json                   everything above in one file (+ raw rows for merging copies)

Coordinates are normalized image coordinates: x = pixel_x / width, y = pixel_y / height,
origin top-left. A path = points + one segment per consecutive pair; a segment is a straight
line or a cubic Bezier curve with control points c1, c2 (same coordinates).
"""


def _csv(rows, cols) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in r.items()})
    return buf.getvalue()


def build_export(con, fp, s, dataset_dir: Path, media_dir: Path, analysis_fn, ratings, results_fn,
                 include_media=True, anonymize=False):
    summ, rating_rows = ratings
    questions = s["questions"]
    scenes = con.execute("SELECT * FROM scenes ORDER BY sort_order, created_at").fetchall()
    scene_by_id = {r["id"]: r for r in scenes}
    parts = {p["id"]: dict(p) for p in con.execute("SELECT * FROM participants ORDER BY code")}
    cands = con.execute("SELECT * FROM candidates ORDER BY created_at").fetchall()

    with zipfile.ZipFile(fp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", README)
        if include_media and dataset_dir.exists():
            for f in sorted(dataset_dir.rglob("*")):
                if f.is_file() and not f.name.startswith("."):
                    z.write(f, f"images/{f.relative_to(dataset_dir).as_posix()}",
                            compress_type=zipfile.ZIP_STORED if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".mp4", ".webm") else zipfile.ZIP_DEFLATED)

        image_rows, ann_rows, scene_json = [], [], []
        analyses = {}
        for r in scenes:
            a = analysis_fn(r)
            analyses[r["id"]] = a
            comp = a.get("compiled") or {}
            folder = set_dir_name(r)
            start, goal = _anchors(r)
            image_rows.append({
                "id": r["id"], "folder": f"images/{folder}", "title": r["title"], "source_name": r["source_name"],
                "scenario": r["scenario"], "split": r["split"], "media_type": r["media_type"], "width": r["width"],
                "height": r["height"], "start": start, "goal": goal, "active": r["active"], **a["summary"],
                "compiled_curve": comp.get("curve"), "compiled_wait": bool(comp.get("wait"))})
            hum = {h["id"]: h for h in a["humans"]}
            for dr in _drawing_rows(con, r["id"]):
                h = hum.get(dr["id"], {})
                excluded = bool(dr["excluded"] or dr["p_excluded"])
                pts = jload(dr["points"])
                curve = None
                if pts:
                    curve = A.curve_to_normalized(A.path_curve(pts, r["width"], r["height"], "waypoints",
                                                               jload(dr["segments"]), dr["style"] or "curve"),
                                                  r["width"], r["height"])
                ann_rows.append({
                    "id": dr["id"], "image_id": r["id"], "image_title": r["title"],
                    "file": f"images/{folder}/annotations/{'excluded/' if excluded else ''}{dr['code']}.jpg",
                    "code": dr["code"], "role": dr["role"], "decision": dr["decision"], "style": dr["style"],
                    "points": pts, "segments": jload(dr["segments"]), "curve_64": curve,
                    "norms": jload(dr["norms"], []), "confidence": dr["confidence"], "comment": dr["comment"],
                    "duration_ms": dr["duration_ms"], "n_edits": dr["n_edits"],
                    "route": None if h.get("mode") is None else chr(65 + h["mode"]),
                    "outlier": h.get("outlier"), "trimmed": h.get("trimmed"), "excluded": excluded,
                    "mean_similarity": h.get("mean_similarity"), "similarity_to_compiled": h.get("similarity_to_compiled"),
                    "frechet_to_compiled": h.get("frechet_to_compiled"), "rmse_to_compiled": h.get("rmse_to_compiled"),
                    "created_at": dr["created_at"]})
            scene_json.append({**image_rows[-1], "compiled": comp, "routes": a["modes"], "norm_counts": a["norm_counts"],
                               "prompt": r["prompt"], "notes": r["notes"], "decision_time": r["decision_time"]})

        vlm_rows = []
        for c in cands:
            scene = scene_by_id.get(c["scene_id"])
            m = next((x for x in analyses.get(c["scene_id"], {}).get("candidates", []) if x["id"] == c["id"]), {}) \
                if scene else {}
            st = summ["items"].get(c["id"], {})
            folder = f"images/{set_dir_name(scene)}/vlm" if scene else f"images/{UNLINKED}"
            vlm_rows.append({
                "id": c["id"], "model": c["model"], "source": c["source"], "image_id": c["scene_id"],
                "image_title": scene["title"] if scene else None,
                "file": f"{folder}/{vlm_file_name(c)}" if (c["image_file"] or scene) else None,
                "original_filename": c["original_filename"], "decision": c["decision"],
                "has_path": bool(c["points"]), "points": jload(c["points"]), "segments": jload(c["segments"]),
                "include_in_rating": c["include_in_rating"], "consensus_override": c["consensus_override"],
                "n_ratings": st.get("n", 0), **{f"mean_{q['key']}": st.get(q["key"]) for q in questions},
                "route_agreement": m.get("choice_agreement"), "matches_top_route": m.get("matches_top_choice"),
                "frechet_to_compiled": m.get("frechet_to_compiled"), "similarity_to_compiled": m.get("similarity_to_compiled"),
                "mse_to_compiled": m.get("mse_to_compiled"), "dtw_to_compiled": m.get("dtw_to_compiled"),
                "human_percentile": m.get("human_percentile"), "confidence": c["confidence"],
                "rationale": c["rationale"], "created_at": c["created_at"]})

        rate_rows = []
        raw_ratings = {r["id"]: r for r in con.execute("SELECT * FROM vlm_ratings")}
        for r in rating_rows:
            raw = raw_ratings[r["id"]]
            p = parts.get(raw["participant_id"], {})
            rate_rows.append({
                "id": r["id"], "rater_code": r["code"], "rater_role": r["role"], "item_id": r["item"],
                "item_kind": raw["item_kind"], "model": r["group"], "image_id": r["scene_id"], "position": raw["position"],
                **{f"q_{q['key']}": r["answers"].get(q["key"]) for q in questions},
                "norms": r["norms"], "comment": r["comment"], "duration_ms": r["duration_ms"],
                "rater_drew_image": r["rater_drew_scene"], "shown_image": raw["shown_image"],
                "rater_group": p.get("grp"), "created_at": r["created_at"]})

        part_rows = []
        counts_d = {r[0]: r[1] for r in con.execute("SELECT participant_id, COUNT(*) FROM drawings GROUP BY participant_id")}
        counts_r = {r[0]: r[1] for r in con.execute("SELECT participant_id, COUNT(*) FROM vlm_ratings GROUP BY participant_id")}
        for p in parts.values():
            demo = jload(p["demographics"], {}) or {}
            row = {"code": p["code"], "role": p["role"], "name": p["name"], "email": p["email"],
                   "external_id": p["external_id"], "group": p["grp"], "n_annotations": counts_d.get(p["id"], 0),
                   "n_ratings": counts_r.get(p["id"], 0), "excluded": p["excluded"], "completed_at": p["completed_at"],
                   "completion_code": p["completion_code"], "created_at": p["created_at"],
                   **{f"demo_{k}": v for k, v in demo.items()}}
            if anonymize:
                for k in ("name", "email", "external_id"):
                    row.pop(k)
            part_rows.append(row)
        demo_cols = sorted({k for r in part_rows for k in r if k.startswith("demo_")})
        pcols = ["code", "role"] + ([] if anonymize else ["name", "email", "external_id"]) + \
            ["group", "n_annotations", "n_ratings", "excluded", "completed_at", "completion_code", "created_at"] + demo_cols

        results = results_fn()
        model_rows = []
        for m in results["models"]:
            row = {"model": m["model"], "is_control": m["control"], "n_images": m["n_images"]}
            rs = m.get("ratings") or {}
            row.update(n_rated_images=rs.get("n_items"), n_ratings=rs.get("n_ratings"))
            for q in questions:
                row[f"mean_{q['key']}"] = rs.get(q["key"])
                ci = rs.get(q["key"] + "_ci") or [None, None]
                row[f"{q['key']}_ci_low"], row[f"{q['key']}_ci_high"] = ci
            ps = m.get("paths") or {}
            for k in ("n_paths", "choice_agreement", "top_choice_match_rate", "frechet_to_compiled",
                      "similarity_to_compiled", "human_percentile", "agreement_on_ambiguous", "agreement_on_clear",
                      "confidence_vs_human_agreement_spearman"):
                row[f"path_{k}"] = ps.get(k)
            model_rows.append(row)

        def cols(rows):
            out = []
            for r in rows:
                for k in r:
                    if k not in out:
                        out.append(k)
            return out or ["id"]

        z.writestr("csv/participants.csv", _csv(part_rows, pcols))
        z.writestr("csv/images.csv", _csv(image_rows, cols(image_rows)))
        z.writestr("csv/annotations.csv", _csv(ann_rows, cols(ann_rows)))
        z.writestr("csv/vlm_images.csv", _csv(vlm_rows, cols(vlm_rows)))
        z.writestr("csv/vlm_ratings.csv", _csv(rate_rows, cols(rate_rows)))
        z.writestr("csv/model_summary.csv", _csv(model_rows, cols(model_rows)))
        score_rows, rank_rows = [], []
        for r in scenes:
            paths, rk, rs = scene_ranking(con, r["id"])
            if not paths:
                continue
            label = path_labels(paths)
            for c in paths:
                ps = rs["paths"][c["id"]]
                score_rows.append({"image_id": r["id"], "image_title": r["title"], "path_id": c["id"], "label": label[c["id"]],
                                   "overall_rank": ps.get("overall_rank"), "score": ps["score"], "mean_rank": ps["mean_rank"],
                                   "ranked_first_share": ps["first_share"], "not_acceptable_share": ps["unacceptable_share"],
                                   "n_rankings": ps["n"], "is_best": c["id"] == rs["best"], "decision": c["decision"],
                                   "points": jload(c["points"]), "segments": jload(c["segments"]), "style": c["style"],
                                   "image_kendall_w": rs["kendall_w"]})
            for k in rk:
                order = [x for x in k["ranking"] if x in label]
                for pos, x in enumerate(order, start=1):
                    rank_rows.append({"rater_code": k["code"], "rater_role": k["role"], "image_id": r["id"],
                                      "image_title": r["title"], "path_id": x, "label": label[x], "rank": pos,
                                      "n_paths": len(order), "not_acceptable": x in k["unacceptable"],
                                      "comment": k["comment"], "duration_ms": k["duration_ms"], "created_at": k["created_at"]})
        z.writestr("csv/path_scores.csv", _csv(score_rows, cols(score_rows)))
        z.writestr("csv/rankings.csv", _csv(rank_rows, cols(rank_rows)))
        z.writestr("csv/reliability.csv", _csv([{"question": q["key"], "type": q["type"], "text": q["text"],
                                                 "krippendorff_alpha": summ["reliability"].get(q["key"])}
                                                for q in questions], ["question", "type", "text", "krippendorff_alpha"]))

        def raw(table, scrub=()):
            out = []
            for r in con.execute(f"SELECT * FROM {table}"):
                d = dict(r)
                for k in scrub:
                    d[k] = None
                out.append(d)
            return out

        raw_scenes = raw("scenes")
        for r in raw_scenes:
            folder = f"images/{set_dir_name(r)}"
            r["_zip_frame"] = f"{folder}/original{Path(r['frame_file']).suffix.lower()}"
            if r["media_type"] == "video":
                r["_zip_clip"] = f"{folder}/original_clip{Path(r['media_file']).suffix.lower()}"
        raw_cands = raw("candidates")
        for r in raw_cands:
            if r["image_file"]:
                sc = scene_by_id.get(r["scene_id"])
                folder = f"images/{set_dir_name(sc)}/vlm" if sc else f"images/{UNLINKED}"
                r["_zip_image"] = f"{folder}/{vlm_file_name(r)}"
        dataset = {
            "format": "trajlab-export-v2",
            "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "anonymized": anonymize,
            "coordinate_system": "normalized image coords, origin top-left; distances in image-diagonal units",
            "settings": {k: v for k, v in s.items() if k != "team_code"},
            "images": scene_json, "annotations": ann_rows, "vlm_images": vlm_rows, "vlm_ratings": rate_rows,
            "participants": part_rows, "model_summary": model_rows, "reliability": summ["reliability"],
            "path_scores": score_rows, "rankings": rank_rows,
            "_raw": {"scenes": raw_scenes,
                     "participants": raw("participants", ("name", "email", "external_id", "user_agent") if anonymize else ()),
                     "drawings": raw("drawings"), "candidates": raw_cands, "vlm_ratings": raw("vlm_ratings"),
                     "rankings": raw("rankings")},
        }
        z.writestr("dataset.json", json.dumps(dataset, indent=1, default=_json_default))


def merge_import(con, fileobj, media_dir: Path, assign_code) -> dict:
    """Merge another copy's export into this one. Records already present (same id) are kept."""
    report = {}
    with zipfile.ZipFile(fileobj) as z:
        names = set(z.namelist())
        ds_name = next((n for n in names if n.endswith("dataset.json")), None)
        if not ds_name:
            raise ValueError("dataset.json not found in the zip")
        prefix = ds_name[: -len("dataset.json")]
        data = json.loads(z.read(ds_name))
        raw = data.get("_raw")
        if not raw:
            raise ValueError("this export has no raw section (export it again from a current copy)")

        def put(src, dst: Path):
            if src and prefix + src in names and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(z.read(prefix + src))

        for table in ("scenes", "participants", "drawings", "candidates", "vlm_ratings", "rankings"):
            rows = raw.get(table, [])
            cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
            added = 0
            for r in rows:
                keep = {k: v for k, v in r.items() if k in cols}
                if table == "participants" and keep.get("code") and con.execute(
                        "SELECT 1 FROM participants WHERE code=? AND id!=?", (keep["code"], keep["id"])).fetchone():
                    keep["code"] = None
                cur = con.execute(f"INSERT OR IGNORE INTO {table} ({', '.join(keep)}) VALUES ({', '.join('?' * len(keep))})",
                                  list(keep.values()))
                added += cur.rowcount
                if not cur.rowcount:
                    continue
                if table == "participants" and keep.get("code") is None:
                    assign_code(con, keep["id"], keep.get("role") or "survey")
                if table == "scenes":
                    put(r.get("_zip_frame"), media_dir / r["id"] / r["frame_file"])
                    if r.get("_zip_clip"):
                        put(r["_zip_clip"], media_dir / r["id"] / r["media_file"])
                if table == "candidates" and r.get("image_file"):
                    put(r.get("_zip_image"), media_dir / "vlm" / r["image_file"])
            report[table] = {"in_file": len(rows), "added": added}
    return report
