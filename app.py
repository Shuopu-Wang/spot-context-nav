"""
TrajLab: human trajectory collection, path compilation and VLM path rating.

Run locally:   python app.py            (http://localhost:5000, also reachable on your Wi-Fi)
Production:    gunicorn -w 2 --threads 4 -b 0.0.0.0:$PORT app:app

Environment variables
  DATA_DIR        database, uploaded media and the dataset folder   (default ./data)
  ADMIN_PASSWORD  password for /admin                               (default "changeme")
  API_TOKEN       bearer token for the /api/v1 pipeline API         (default = ADMIN_PASSWORD)
  SECRET_KEY      session signing key (generated and saved in DATA_DIR if unset)
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import random
import re
import secrets
import shutil
import socket
import sqlite3
import string
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import (Flask, abort, g, jsonify, redirect, render_template, request, send_file,
                   send_from_directory, session, url_for)
from PIL import Image
from werkzeug.utils import secure_filename

import analysis as A
import dataset as DS

BASE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE / "data")).resolve()
MEDIA_DIR = DATA_DIR / "media"
DATASET_DIR = DATA_DIR / "dataset"
DB_PATH = DATA_DIR / "trajlab.db"
for d in (DATA_DIR, MEDIA_DIR, MEDIA_DIR / "vlm", MEDIA_DIR / "controls", DATASET_DIR):
    d.mkdir(parents=True, exist_ok=True)

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")
API_TOKEN = os.environ.get("API_TOKEN", ADMIN_PASSWORD)


def _secret_key() -> str:
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    f = DATA_DIR / "secret_key"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
    return f.read_text().strip()


app = Flask(__name__)
app.config.update(
    SECRET_KEY=_secret_key(),
    MAX_CONTENT_LENGTH=int(os.environ.get("MAX_UPLOAD_MB", "1024")) * 1024 * 1024,
    SESSION_COOKIE_SAMESITE="Lax",
)
app.json.sort_keys = False

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
VIDEO_EXT = {".mp4", ".webm", ".m4v", ".mov", ".ogg"}
CONTROL_MODEL = "Human consensus (control)"

DEFAULT_NORMS = [
    {"key": "personal_space", "label": "Passes close to people (personal space)"},
    {"key": "group", "label": "Cuts through or interrupts a group / activity"},
    {"key": "queue", "label": "Cuts across a line or queue"},
    {"key": "terrain", "label": "Uses grass or an area people usually avoid"},
    {"key": "detour", "label": "Takes a long detour"},
    {"key": "delay", "label": "Waits or holds others up"},
    {"key": "traffic", "label": "Bends a traffic or crossing rule"},
    {"key": "blocking", "label": "Blocks or crowds someone else's path"},
    {"key": "other", "label": "Something else (explain in the comment)"},
]

# Rating questionnaire. Constructs follow the human-aware navigation literature:
# comfort / naturalness / sociability (Kruse et al., 2013), perceived safety (Godspeed V,
# Bartneck et al., 2009), legibility / predictability (Dragan et al., 2013).
DEFAULT_QUESTIONS = [
    {"key": "acceptable", "type": "yesno", "text": "Is this path acceptable for a robot in this situation?"},
    {"key": "comfortable", "type": "scale", "points": 7,
     "text": "If you were one of the people in this scene, how comfortable would you feel with the robot taking this path?",
     "low": "Very uncomfortable", "high": "Very comfortable"},
    {"key": "safe", "type": "scale", "points": 7,
     "text": "How safe would you feel around the robot as it takes this path?",
     "low": "Very unsafe", "high": "Very safe"},
    {"key": "reasonable", "type": "scale", "points": 7,
     "text": "How reasonable is this path, given the situation?",
     "low": "Not at all reasonable", "high": "Completely reasonable"},
    {"key": "appropriate", "type": "scale", "points": 7,
     "text": "How socially appropriate (polite) is this path toward the people around it?",
     "low": "Very inappropriate", "high": "Very appropriate"},
    {"key": "natural", "type": "scale", "points": 7,
     "text": "How natural is this path? Is it how a considerate person would walk?",
     "low": "Very unnatural", "high": "Very natural"},
    {"key": "predictable", "type": "scale", "points": 7,
     "text": "Could people nearby easily tell where the robot is going?",
     "low": "Not at all", "high": "Very easily"},
]

DEFAULT_SETTINGS = {
    "study_title": "Robot Path Study",
    "study_subtitle": "Help a walking robot learn how to move politely through crowds.",
    "consent_text": (
        "This study is run by student researchers studying how robots should move around people.\n\n"
        "You will see photos taken from a robot's point of view and draw the path you think the robot should "
        "take. Then you will rate some paths drawn by AI systems.\n\n"
        "It takes about 10–15 minutes. Taking part is voluntary and you can stop at any time. We ask for your "
        "name and email so we can tell responses apart and reach you if needed. They are stored securely and "
        "never published; any data we share uses only an anonymous participant code. "
        "Questions? Contact the research team."
    ),
    "default_prompt": (
        "You are the robot (blue dot). Tap to place points along the path you think is most socially "
        "acceptable for reaching the green flag, and choose whether they connect with straight lines or "
        "curves. Every option may break some unwritten rule; pick the one you think is least bad. If nothing "
        "is acceptable, choose “I'd wait”."
    ),
    "rating_intro": (
        "Next you'll see paths drawn by AI systems. For each one, imagine a robot following the drawn path "
        "and answer a few questions about it."
    ),
    "rating_prompt": "Imagine a robot following the drawn path in this scene.",
    "completion_message": "Thank you! Your responses have been saved.",
    "survey_draw_count": 5,
    "team_draw_count": 0,          # 0 = every active training image
    "rate_per_participant": 10,
    "consensus_controls": 0,       # hidden human-consensus images mixed into each rating set
    "rating_display": "original",  # "original" VLM image, or "uniform" re-drawing when coordinates exist
    "default_line_style": "curve",
    "ask_demographics": True,
    "require_email": True,
    "ask_norms_in_rating": True,
    "questions": DEFAULT_QUESTIONS,
    "cluster_threshold": A.DEFAULTS["cluster_threshold"],
    "ambiguity_threshold": A.DEFAULTS["ambiguity_threshold"],
    "min_mode_share": A.DEFAULTS["min_mode_share"],
    "exclude_outliers": True,
    "compiled_method": "mean",
    "fast_response_ms": 4000,
    "norms": DEFAULT_NORMS,
    "team_code": "",
}
ANALYSIS_KEYS = ("cluster_threshold", "ambiguity_threshold", "min_mode_share", "exclude_outliers", "compiled_method")

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scenes (
    id TEXT PRIMARY KEY, title TEXT, source_name TEXT DEFAULT '', scenario TEXT, prompt TEXT, split TEXT DEFAULT '',
    media_type TEXT NOT NULL, media_file TEXT NOT NULL, frame_file TEXT NOT NULL,
    width INTEGER NOT NULL, height INTEGER NOT NULL,
    start_x REAL NOT NULL DEFAULT 0.5, start_y REAL NOT NULL DEFAULT 0.96, goal_x REAL, goal_y REAL,
    decision_time REAL, active INTEGER NOT NULL DEFAULT 1, sort_order INTEGER NOT NULL DEFAULT 0,
    notes TEXT DEFAULT '', version INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS participants (
    id TEXT PRIMARY KEY, code TEXT UNIQUE, name TEXT, email TEXT, external_id TEXT,
    role TEXT NOT NULL DEFAULT 'survey', grp TEXT, demographics TEXT, assigned TEXT, user_agent TEXT,
    completion_code TEXT, excluded INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, completed_at TEXT
);
CREATE TABLE IF NOT EXISTS drawings (
    id TEXT PRIMARY KEY, participant_id TEXT NOT NULL, scene_id TEXT NOT NULL,
    decision TEXT NOT NULL, points TEXT, segments TEXT, style TEXT, norms TEXT, confidence INTEGER,
    comment TEXT, duration_ms INTEGER, n_edits INTEGER, viewport TEXT,
    excluded INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, UNIQUE(participant_id, scene_id)
);
CREATE TABLE IF NOT EXISTS candidates (
    id TEXT PRIMARY KEY, scene_id TEXT, source TEXT, model TEXT, label TEXT,
    decision TEXT NOT NULL DEFAULT 'path', kind TEXT NOT NULL DEFAULT 'waypoints',
    points TEXT, segments TEXT, style TEXT, norms TEXT, confidence REAL, rationale TEXT, meta TEXT,
    image_file TEXT, image_w INTEGER, image_h INTEGER, original_filename TEXT,
    include_in_rating INTEGER NOT NULL DEFAULT 1, consensus_override INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vlm_ratings (
    id TEXT PRIMARY KEY, participant_id TEXT NOT NULL, item_ref TEXT NOT NULL, item_kind TEXT NOT NULL,
    scene_id TEXT, model TEXT, answers TEXT, norms TEXT, comment TEXT, position INTEGER, duration_ms INTEGER,
    rater_drew_scene INTEGER, shown_image TEXT, created_at TEXT NOT NULL, UNIQUE(participant_id, item_ref)
);
CREATE INDEX IF NOT EXISTS ix_drawings_scene ON drawings(scene_id);
CREATE INDEX IF NOT EXISTS ix_cand_scene ON candidates(scene_id);
CREATE INDEX IF NOT EXISTS ix_vr_item ON vlm_ratings(item_ref);
CREATE INDEX IF NOT EXISTS ix_part_email ON participants(email);
CREATE UNIQUE INDEX IF NOT EXISTS ux_part_code ON participants(code);
"""

MIGRATE_COLUMNS = {
    "scenes": {"source_name": "TEXT DEFAULT ''"},
    "participants": {"code": "TEXT", "name": "TEXT", "email": "TEXT", "role": "TEXT NOT NULL DEFAULT 'survey'"},
    "drawings": {"points": "TEXT", "segments": "TEXT", "style": "TEXT"},
    "candidates": {"segments": "TEXT", "style": "TEXT", "image_file": "TEXT", "image_w": "INTEGER",
                   "image_h": "INTEGER", "original_filename": "TEXT"},
}


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def _close_db(_exc):
    con = g.pop("db", None)
    if con is not None:
        con.close()


def _columns(con, table):
    return {r[1]: r for r in con.execute(f"PRAGMA table_info({table})")}


def init_db():
    con = connect()
    # Upgrade a v1 database in place: add new columns, relax candidates.scene_id.
    for table, cols in MIGRATE_COLUMNS.items():
        have = _columns(con, table)
        if not have:
            continue
        for col, decl in cols.items():
            if col not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    have = _columns(con, "drawings")
    if "waypoints" in have:
        con.execute("UPDATE drawings SET points = waypoints WHERE points IS NULL AND waypoints IS NOT NULL")
    have = _columns(con, "candidates")
    if have and have["scene_id"][3]:  # NOT NULL from v1
        con.executescript("""
            ALTER TABLE candidates RENAME TO candidates_v1;
        """)
        con.executescript(SCHEMA)
        cols = [c for c in _columns(con, "candidates") if c in _columns(con, "candidates_v1")]
        con.execute(f"INSERT INTO candidates ({','.join(cols)}) SELECT {','.join(cols)} FROM candidates_v1")
        con.execute("DROP TABLE candidates_v1")
    con.executescript(SCHEMA)
    for r in con.execute("SELECT id, role FROM participants WHERE code IS NULL").fetchall():
        _assign_code(con, r["id"], r["role"] or "survey")
    con.commit()
    con.close()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex


def jload(s, default=None):
    if s is None or s == "":
        return default
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return default


def _assign_code(con, pid, role):
    prefix, width = ("T", 3) if role == "team" else ("S", 4)
    n = con.execute("SELECT COUNT(*) FROM participants WHERE role=? AND code IS NOT NULL", (role,)).fetchone()[0] + 1
    for _ in range(10000):
        code = f"{prefix}{n:0{width}d}"
        try:
            con.execute("UPDATE participants SET code=? WHERE id=?", (code, pid))
            return code
        except sqlite3.IntegrityError:
            n += 1
    raise RuntimeError("could not assign a participant code")


init_db()


def get_settings(con=None) -> dict:
    con = con or db()
    s = dict(DEFAULT_SETTINGS)
    for r in con.execute("SELECT key, value FROM settings"):
        if r["key"] in DEFAULT_SETTINGS:
            s[r["key"]] = jload(r["value"], DEFAULT_SETTINGS[r["key"]])
    if not s["team_code"]:
        words = secrets.SystemRandom().sample(["robot", "path", "quad", "walk", "lawn", "crowd", "spot", "trail",
                                               "plaza", "step", "curb", "gate"], 2)
        s["team_code"] = f"{words[0]}-{words[1]}-{secrets.randbelow(90) + 10}"
        con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('team_code', ?)", (json.dumps(s["team_code"]),))
        con.commit()
    return s


def analysis_params(s: dict, overrides: dict | None = None) -> dict:
    p = {k: s[k] for k in ANALYSIS_KEYS}
    for k, v in (overrides or {}).items():
        if v is not None and k in p:
            p[k] = v
    return p


def bump_scene(con, scene_id: str | None = None):
    if scene_id:
        con.execute("UPDATE scenes SET version = version + 1 WHERE id = ?", (scene_id,))
    else:
        con.execute("UPDATE scenes SET version = version + 1")


def scene_dir(scene_id: str) -> Path:
    d = MEDIA_DIR / scene_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def scene_public(row) -> dict:
    base = f"/media/{row['id']}/"
    return {
        "id": row["id"], "prompt": row["prompt"] or "", "media_type": row["media_type"],
        "image_url": base + row["frame_file"],
        "video_url": base + row["media_file"] if row["media_type"] == "video" else None,
        "decision_time": row["decision_time"], "width": row["width"], "height": row["height"],
        "start": [row["start_x"], row["start_y"]],
        "goal": [row["goal_x"], row["goal_y"]] if row["goal_x"] is not None else None,
    }


def scene_admin(row) -> dict:
    d = scene_public(row)
    d.update({k: row[k] for k in ("title", "source_name", "scenario", "split", "active", "sort_order", "notes",
                                  "media_file", "frame_file", "created_at", "version")})
    d["folder"] = DS.set_dir_name(row)
    return d


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class BadRequest(Exception):
    pass


@app.errorhandler(BadRequest)
def _bad(e):
    return jsonify({"error": str(e)}), 400


def clean_points(pts, max_n=60, clamp=True) -> list:
    if not isinstance(pts, list) or len(pts) < 2:
        raise BadRequest("a path needs at least 2 points")
    if len(pts) > max_n:
        raise BadRequest(f"too many points (max {max_n})")
    out = []
    for p in pts:
        if isinstance(p, dict):
            p = [p.get("x"), p.get("y")]
        try:
            x, y = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError):
            raise BadRequest("points must be [x, y] pairs in normalized image coordinates")
        if x != x or y != y:
            raise BadRequest("NaN in points")
        if clamp:
            x, y = min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)
        out.append([round(x, 5), round(y, 5)])
    return out


def clean_segments(segs, n_points):
    """[{type: line|curve, c1, c2}] with one entry per consecutive pair of points."""
    if segs is None:
        return None
    if not isinstance(segs, list) or len(segs) != n_points - 1:
        raise BadRequest("segments need one entry per pair of consecutive points")
    out = []
    for s in segs:
        t = s.get("type") if isinstance(s, dict) else None
        if t == "line":
            out.append({"type": "line"})
        elif t == "curve":
            seg = {"type": "curve"}
            for k in ("c1", "c2"):
                if s.get(k) is not None:
                    try:
                        x, y = float(s[k][0]), float(s[k][1])
                    except (TypeError, ValueError, IndexError):
                        raise BadRequest("Bezier control points must be [x, y]")
                    seg[k] = [round(min(max(x, -1.0), 2.0), 5), round(min(max(y, -1.0), 2.0), 5)]
            if s.get("manual"):
                seg["manual"] = True
            out.append(seg)
        else:
            raise BadRequest("segment type must be 'line' or 'curve'")
    return out


def style_of(segs, default="curve"):
    if not segs:
        return default
    types = {s["type"] for s in segs}
    return "mixed" if len(types) > 1 else ("line" if types == {"line"} else "curve")


def clean_norms(norms) -> list:
    if not norms:
        return []
    if not isinstance(norms, list):
        raise BadRequest("norms must be a list")
    return [str(n)[:64] for n in norms][:20]


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _token_ok() -> bool:
    h = request.headers.get("Authorization", "")
    tok = h[7:] if h.lower().startswith("bearer ") else request.args.get("token", "")
    return bool(tok) and hmac.compare_digest(tok, API_TOKEN)


def admin_required(fn):
    @wraps(fn)
    def wrap(*a, **kw):
        if session.get("admin") or _token_ok():
            return fn(*a, **kw)
        if request.path.startswith("/api/"):
            return jsonify({"error": "admin login or API token required"}), 401
        return redirect(url_for("admin_login", next=request.path))
    return wrap


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    err = None
    if request.method == "POST":
        if hmac.compare_digest(request.form.get("password", ""), ADMIN_PASSWORD):
            session["admin"] = True
            session.permanent = True
            nxt = request.args.get("next") or "/admin"
            return redirect(nxt if nxt.startswith("/") else "/admin")
        err = "Wrong password."
    return render_template("admin_login.html", error=err, default_pw=ADMIN_PASSWORD == "changeme")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect("/")


# ---------------------------------------------------------------------------
# Background dataset-folder maintenance
# ---------------------------------------------------------------------------

_pending: dict[str, set] = {}
_pending_cv = threading.Condition()
_worker_started = False


def _worker():
    while True:
        with _pending_cv:
            while not _pending:
                _pending_cv.wait()
            sid, render_ids = _pending.popitem()
        try:
            con = connect()
            row = con.execute("SELECT * FROM scenes WHERE id=?", (sid,)).fetchone()
            if row:
                s = get_settings(con)
                DS.sync_set(con, row, s, DATASET_DIR, MEDIA_DIR,
                            lambda r, s=s, con=con: scene_analysis(con, r, s), render_ids=render_ids)
            con.close()
        except Exception as e:  # never let the worker die
            app.logger.exception("dataset folder sync failed for %s: %s", sid, e)


def enqueue_sync(scene_id: str, render_ids=()):
    global _worker_started
    with _pending_cv:
        _pending.setdefault(scene_id, set()).update(render_ids)
        if not _worker_started:
            threading.Thread(target=_worker, daemon=True).start()
            _worker_started = True
        _pending_cv.notify()


def enqueue_all(con):
    for r in con.execute("SELECT id FROM scenes"):
        enqueue_sync(r["id"])


def sync_all(con, s):
    for row in con.execute("SELECT * FROM scenes").fetchall():
        DS.sync_set(con, row, s, DATASET_DIR, MEDIA_DIR, lambda r: scene_analysis(con, r, s))
    DS.sync_unlinked(con, DATASET_DIR, MEDIA_DIR)


# ---------------------------------------------------------------------------
# Analysis (cached on scene version)
# ---------------------------------------------------------------------------

_cache: dict = {}
_cache_lock = threading.Lock()


def scene_inputs(con, scene_row, role: str | None = None):
    q = """SELECT d.* FROM drawings d JOIN participants p ON p.id = d.participant_id
           WHERE d.scene_id = ? AND d.excluded = 0 AND p.excluded = 0"""
    args = [scene_row["id"]]
    if role:
        q += " AND p.role = ?"
        args.append(role)
    humans = [{"id": d["id"], "participant_id": d["participant_id"], "decision": d["decision"],
               "points": jload(d["points"]), "segments": jload(d["segments"]), "style": d["style"],
               "norms": jload(d["norms"], []), "duration_ms": d["duration_ms"], "confidence": d["confidence"]}
              for d in con.execute(q + " ORDER BY d.created_at", args)]
    cands = [{"id": c["id"], "model": c["model"], "source": c["source"], "decision": c["decision"],
              "kind": c["kind"], "points": jload(c["points"]), "segments": jload(c["segments"]),
              "style": c["style"], "confidence": c["confidence"], "consensus_override": bool(c["consensus_override"])}
             for c in con.execute("SELECT * FROM candidates WHERE scene_id=? AND (points IS NOT NULL OR decision='wait') "
                                  "ORDER BY created_at", (scene_row["id"],))]
    return humans, cands


def scene_analysis(con, scene_row, s: dict, role: str | None = None, overrides: dict | None = None) -> dict:
    params = analysis_params(s, overrides)
    key = (scene_row["id"], scene_row["version"], role or "", json.dumps(params, sort_keys=True))
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    humans, cands = scene_inputs(con, scene_row, role)
    res = A.analyze_scene({"width": scene_row["width"], "height": scene_row["height"]}, humans, cands, params)
    with _cache_lock:
        if len(_cache) > 512:
            _cache.clear()
        _cache[key] = res
    return res


def rating_rows(con, role: str | None = None, item: str | None = None) -> list[dict]:
    q = """SELECT r.*, p.role, p.code, c.model AS cmodel FROM vlm_ratings r
           JOIN participants p ON p.id = r.participant_id
           LEFT JOIN candidates c ON c.id = r.item_ref
           WHERE p.excluded = 0"""
    args = []
    if role:
        q += " AND p.role = ?"
        args.append(role)
    if item:
        q += " AND r.item_ref = ?"
        args.append(item)
    out = []
    for r in con.execute(q + " ORDER BY r.created_at", args):
        group = CONTROL_MODEL if r["item_kind"] == "consensus" else (r["cmodel"] or r["model"] or "unnamed")
        out.append({"id": r["id"], "item": r["item_ref"], "group": group, "answers": jload(r["answers"], {}),
                    "norms": jload(r["norms"], []), "comment": r["comment"], "role": r["role"], "code": r["code"],
                    "scene_id": r["scene_id"], "duration_ms": r["duration_ms"],
                    "rater_drew_scene": r["rater_drew_scene"], "created_at": r["created_at"]})
    return out


def rating_overview(con, s, role=None):
    rows = rating_rows(con, role)
    return A.rating_summary(rows, s["questions"]), rows


# ---------------------------------------------------------------------------
# Participant flow
# ---------------------------------------------------------------------------

def public_settings(s: dict) -> dict:
    keys = ["study_title", "study_subtitle", "consent_text", "default_prompt", "rating_intro", "rating_prompt",
            "completion_message", "ask_demographics", "require_email", "norms", "questions",
            "ask_norms_in_rating", "default_line_style", "survey_draw_count"]
    return {k: s[k] for k in keys}


@app.route("/")
def participant_page():
    return render_template("participant.html", settings=public_settings(get_settings()))


@app.route("/media/<path:fname>")
def media(fname):
    return send_from_directory(MEDIA_DIR, fname, max_age=3600)


def _ratable_items(con):
    """Candidates that can be shown in a rating task, with their current rating counts."""
    return con.execute("""
        SELECT c.id, c.scene_id, (SELECT COUNT(*) FROM vlm_ratings r WHERE r.item_ref = c.id) n
        FROM candidates c LEFT JOIN scenes s ON s.id = c.scene_id
        WHERE c.include_in_rating = 1 AND c.consensus_override = 0
          AND (c.image_file IS NOT NULL OR s.id IS NOT NULL)""").fetchall()


def _draw_list(con, p, s) -> list:
    """Training images for this participant. Team: every active image (grows as images are added)."""
    assigned = jload(p["assigned"], {}) or {}
    lst = list(assigned.get("draw") or [])
    active = [r["id"] for r in con.execute("SELECT id FROM scenes WHERE active = 1")]
    if p["role"] == "team" and not int(s["team_draw_count"]):
        new = [sid for sid in active if sid not in lst]
        if new:
            random.shuffle(new)
            lst += new
            assigned["draw"] = lst
            con.execute("UPDATE participants SET assigned=? WHERE id=?", (json.dumps(assigned), p["id"]))
            con.commit()
    done = {r[0] for r in con.execute("SELECT scene_id FROM drawings WHERE participant_id=?", (p["id"],))}
    active_set = set(active)
    return [sid for sid in lst if sid in active_set or sid in done]


def _balanced_scenes(con, k):
    rows = con.execute("""SELECT s.id, (SELECT COUNT(*) FROM drawings d WHERE d.scene_id = s.id AND d.excluded = 0) n
                          FROM scenes s WHERE s.active = 1""").fetchall()
    rows = sorted(rows, key=lambda r: (r["n"], random.random()))
    picked = [r["id"] for r in rows[:k] if k] if k else [r["id"] for r in rows]
    random.shuffle(picked)
    return picked


def _item_token(pid, ref):
    return hashlib.sha1(f"{pid}:{ref}:{app.config['SECRET_KEY']}".encode()).hexdigest()[:16]


def _assign_rate(con, p, s) -> list:
    """Balanced: least-rated first, at most one item per training image where possible."""
    n = int(s["rate_per_participant"])
    n_ctrl = min(int(s["consensus_controls"]), n)
    items = sorted(_ratable_items(con), key=lambda r: (r["n"], random.random()))
    picked, seen = [], set()
    for r in items:
        if len(picked) >= n - n_ctrl:
            break
        if r["scene_id"] and r["scene_id"] in seen:
            continue
        picked.append(r["id"])
        seen.add(r["scene_id"])
    for r in items:  # fill up if there were too few distinct images
        if len(picked) >= n - n_ctrl:
            break
        if r["id"] not in picked:
            picked.append(r["id"])
    if n_ctrl:
        ctrl_counts = {}
        for r in con.execute("SELECT item_ref, COUNT(*) n FROM vlm_ratings WHERE item_kind='consensus' GROUP BY item_ref"):
            ctrl_counts[r["item_ref"]] = r["n"]
        options = []
        for row in con.execute("SELECT * FROM scenes WHERE active = 1").fetchall():
            a = scene_analysis(con, row, s)
            if a["summary"]["enough_data"] and a["compiled"] and a["compiled"].get("curve"):
                ref = f"consensus:{row['id']}"
                options.append((ctrl_counts.get(ref, 0), random.random(), ref))
        picked += [ref for *_, ref in sorted(options)[:n_ctrl]]
    random.shuffle(picked)
    return [{"k": _item_token(p["id"], ref), "ref": ref} for ref in picked]


def _session_payload(con, pid: str, resumed=False) -> dict:
    p = con.execute("SELECT * FROM participants WHERE id = ?", (pid,)).fetchone()
    if not p:
        abort(404)
    s = get_settings(con)
    draw_ids = _draw_list(con, p, s)
    done_d = {r[0] for r in con.execute("SELECT scene_id FROM drawings WHERE participant_id=?", (pid,))}
    scenes = {}
    if draw_ids:
        q = ",".join("?" * len(draw_ids))
        for r in con.execute(f"SELECT * FROM scenes WHERE id IN ({q})", draw_ids):
            scenes[r["id"]] = scene_public(r)
    draw = [{"scene": scenes[i], "done": i in done_d} for i in draw_ids if i in scenes]

    assigned = jload(p["assigned"], {}) or {}
    rate = assigned.get("rate")
    # Assign the rating set once drawing is finished. Someone who finished before any VLM images
    # existed gets a set when they come back.
    if (rate is None or (rate == [] and _ratable_items(con))) and all(t["done"] for t in draw):
        rate = _assign_rate(con, p, s)
        assigned["rate"] = rate
        con.execute("UPDATE participants SET assigned=? WHERE id=?", (json.dumps(assigned), pid))
        con.commit()
    done_r = {r[0] for r in con.execute("SELECT item_ref FROM vlm_ratings WHERE participant_id=?", (pid,))}
    rate_out = None
    if rate is not None:
        rate_out = []
        for it in rate:
            info = _item_info(con, it["ref"])
            if info is None:
                continue
            rate_out.append({"k": it["k"], "done": it["ref"] in done_r,
                             "image_url": f"/api/item/{pid}/{it['k']}/image",
                             "original_url": f"/api/item/{pid}/{it['k']}/original" if info.get("scene") else None})
    planned = min(int(s["rate_per_participant"]), len(_ratable_items(con)) + int(s["consensus_controls"]))
    return {"participant_id": pid, "code": p["code"], "role": p["role"], "resumed": resumed,
            "completion_code": p["completion_code"] if p["completed_at"] else None,
            "draw": draw, "rate": rate_out, "rate_planned": planned if rate_out is None else len(rate_out)}


@app.post("/api/register")
def register():
    b = request.get_json(force=True, silent=True) or {}
    s = get_settings()
    con = db()
    name = " ".join(str(b.get("name") or "").split())[:120]
    email = str(b.get("email") or "").strip().lower()[:200]
    if not name:
        raise BadRequest("Please enter your name.")
    if s["require_email"] and not email:
        raise BadRequest("Please enter your email.")
    if email and not EMAIL_RE.match(email):
        raise BadRequest("That email address doesn't look right.")
    role = "team" if b.get("role") == "team" else "survey"
    if role == "team":
        code = str(b.get("team_code") or "").strip().lower()
        if not code or not hmac.compare_digest(code, str(s["team_code"]).strip().lower()):
            raise BadRequest("That team access code isn't right. Ask the study lead, or sign up as a survey participant.")
    demo = b.get("demographics") if isinstance(b.get("demographics"), dict) else {}
    demo = {str(k)[:40]: str(v)[:120] for k, v in demo.items() if v}

    if email:
        existing = con.execute("SELECT * FROM participants WHERE email=? ORDER BY created_at LIMIT 1", (email,)).fetchone()
        if existing:
            if role == "team" and existing["role"] != "team":
                con.execute("UPDATE participants SET role='team' WHERE id=?", (existing["id"],))
                con.commit()
            return jsonify(_session_payload(con, existing["id"], resumed=True))

    pid = new_id()
    k = int(s["team_draw_count"]) if role == "team" else int(s["survey_draw_count"])
    draw = [] if role == "team" and not k else _balanced_scenes(con, k)
    con.execute("""INSERT INTO participants (id, name, email, external_id, role, grp, demographics, assigned,
                   user_agent, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (pid, name, email, str(b.get("external_id") or "")[:128], role,
                 str(b.get("group") or "")[:64], json.dumps(demo), json.dumps({"draw": draw, "rate": None}),
                 request.headers.get("User-Agent", "")[:300], now()))
    _assign_code(con, pid, role)
    con.commit()
    return jsonify(_session_payload(con, pid))


@app.get("/api/session/<pid>")
def session_resume(pid):
    return jsonify(_session_payload(db(), pid, resumed=True))


@app.post("/api/session/<pid>/complete")
def session_complete(pid):
    con = db()
    p = con.execute("SELECT * FROM participants WHERE id=?", (pid,)).fetchone()
    if not p:
        abort(404)
    code = p["completion_code"] or "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    con.execute("UPDATE participants SET completed_at=COALESCE(completed_at, ?), completion_code=? WHERE id=?",
                (now(), code, pid))
    con.commit()
    return jsonify({"completion_code": code})


@app.post("/api/drawings")
def submit_drawing():
    b = request.get_json(force=True, silent=True) or {}
    con = db()
    pid, sid = b.get("participant_id"), b.get("scene_id")
    p = con.execute("SELECT * FROM participants WHERE id=?", (pid,)).fetchone()
    if not p:
        raise BadRequest("unknown participant")
    if sid not in _draw_list(con, p, get_settings(con)):
        raise BadRequest("this image isn't assigned to you")
    decision = b.get("decision")
    if decision not in ("path", "wait"):
        raise BadRequest("decision must be 'path' or 'wait'")
    pts = segs = None
    style = None
    if decision == "path":
        pts = clean_points(b.get("points"))
        segs = clean_segments(b.get("segments"), len(pts))
        style = style_of(segs, b.get("style") if b.get("style") in ("line", "curve") else "curve")
    conf = b.get("confidence")
    conf = int(conf) if isinstance(conf, (int, float)) and 1 <= conf <= 5 else None
    did = new_id()
    con.execute("""
        INSERT INTO drawings (id, participant_id, scene_id, decision, points, segments, style, norms, confidence,
                              comment, duration_ms, n_edits, viewport, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(participant_id, scene_id) DO UPDATE SET
            decision=excluded.decision, points=excluded.points, segments=excluded.segments, style=excluded.style,
            norms=excluded.norms, confidence=excluded.confidence, comment=excluded.comment,
            duration_ms=excluded.duration_ms, n_edits=excluded.n_edits, viewport=excluded.viewport,
            created_at=excluded.created_at""",
        (did, pid, sid, decision, json.dumps(pts) if pts else None, json.dumps(segs) if segs else None, style,
         json.dumps(clean_norms(b.get("norms"))), conf, str(b.get("comment") or "")[:2000],
         int(b.get("duration_ms") or 0), int(b.get("n_edits") or 0), json.dumps(b.get("viewport") or {})[:500], now()))
    bump_scene(con, sid)
    con.commit()
    did = con.execute("SELECT id FROM drawings WHERE participant_id=? AND scene_id=?", (pid, sid)).fetchone()[0]
    enqueue_sync(sid, {did})
    return jsonify({"ok": True})


def _item_info(con, ref):
    """Resolve a rating item -> {kind, candidate, scene} or None if it no longer exists."""
    if ref.startswith("consensus:"):
        row = con.execute("SELECT * FROM scenes WHERE id=?", (ref.split(":", 1)[1],)).fetchone()
        return {"kind": "consensus", "scene": row} if row else None
    c = con.execute("SELECT * FROM candidates WHERE id=?", (ref,)).fetchone()
    if not c:
        return None
    scene = con.execute("SELECT * FROM scenes WHERE id=?", (c["scene_id"],)).fetchone() if c["scene_id"] else None
    return {"kind": "candidate", "candidate": c, "scene": scene}


def _item_image_path(con, info, s) -> Path | None:
    """File to show for a rating item (uploaded VLM image, uniform re-drawing, or consensus snapshot)."""
    scene = info.get("scene")
    if info["kind"] == "consensus":
        a = scene_analysis(con, scene, s)
        comp = a.get("compiled") or {}
        return DS.render_control(scene, comp, MEDIA_DIR)
    c = info["candidate"]
    has_coords = scene is not None and (c["points"] or c["decision"] == "wait")
    if c["image_file"] and not (s["rating_display"] == "uniform" and has_coords):
        return MEDIA_DIR / "vlm" / c["image_file"]
    if has_coords:
        return DS.render_candidate(scene, c, MEDIA_DIR)
    return None


def _resolve_item(con, pid, k):
    p = con.execute("SELECT * FROM participants WHERE id=?", (pid,)).fetchone()
    if not p:
        abort(404)
    for it in (jload(p["assigned"], {}) or {}).get("rate") or []:
        if hmac.compare_digest(it["k"], k):
            return p, it["ref"]
    abort(404)


@app.get("/api/item/<pid>/<k>/<which>")
def item_image(pid, k, which):
    con = db()
    _, ref = _resolve_item(con, pid, k)
    info = _item_info(con, ref)
    if not info:
        abort(404)
    if which == "original":
        if not info.get("scene"):
            abort(404)
        return send_file(MEDIA_DIR / info["scene"]["id"] / info["scene"]["frame_file"], max_age=600)
    path = _item_image_path(con, info, get_settings(con))
    if not path or not path.exists():
        abort(404)
    return send_file(path, max_age=600)


@app.post("/api/vlm_ratings")
def submit_vlm_rating():
    b = request.get_json(force=True, silent=True) or {}
    con = db()
    s = get_settings(con)
    p, ref = _resolve_item(con, b.get("participant_id"), str(b.get("k") or ""))
    info = _item_info(con, ref)
    if not info:
        raise BadRequest("this item was removed")
    raw = b.get("answers") or {}
    answers = {}
    for q in s["questions"]:
        v = raw.get(q["key"])
        if q["type"] == "yesno":
            if v not in ("yes", "no"):
                raise BadRequest(f"Please answer: {q['text']}")
        else:
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise BadRequest(f"Please answer: {q['text']}")
            if not 1 <= v <= int(q.get("points", 7)):
                raise BadRequest("answer out of range")
        answers[q["key"]] = v
    scene = info.get("scene")
    order = [it["ref"] for it in (jload(p["assigned"], {}) or {}).get("rate") or []]
    shown = _item_image_path(con, info, s)
    drew = bool(scene and con.execute("SELECT 1 FROM drawings WHERE participant_id=? AND scene_id=?",
                                      (p["id"], scene["id"])).fetchone())
    con.execute("""INSERT INTO vlm_ratings (id, participant_id, item_ref, item_kind, scene_id, model, answers, norms,
                   comment, position, duration_ms, rater_drew_scene, shown_image, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(participant_id, item_ref) DO UPDATE SET answers=excluded.answers,
                   norms=excluded.norms, comment=excluded.comment, duration_ms=excluded.duration_ms,
                   shown_image=excluded.shown_image, created_at=excluded.created_at""",
                (new_id(), p["id"], ref, info["kind"], scene["id"] if scene else None,
                 CONTROL_MODEL if info["kind"] == "consensus" else info["candidate"]["model"],
                 json.dumps(answers), json.dumps(clean_norms(b.get("norms"))), str(b.get("comment") or "")[:2000],
                 order.index(ref) if ref in order else None, int(b.get("duration_ms") or 0), int(drew),
                 str(shown.relative_to(DATA_DIR)) if shown else None, now()))
    con.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Admin pages
# ---------------------------------------------------------------------------

@app.route("/admin")
@admin_required
def admin_page():
    return render_template("admin.html")


@app.route("/admin/image/<sid>")
@admin_required
def admin_scene_page(sid):
    if not db().execute("SELECT 1 FROM scenes WHERE id=?", (sid,)).fetchone():
        abort(404)
    return render_template("admin_scene.html", scene_id=sid)


@app.route("/admin/vlm/<cid>")
@admin_required
def admin_vlm_page(cid):
    if not db().execute("SELECT 1 FROM candidates WHERE id=?", (cid,)).fetchone():
        abort(404)
    return render_template("admin_vlm.html", candidate_id=cid)


def _scene_counts(con) -> dict:
    out = {}
    for r in con.execute("""
        SELECT s.id,
          (SELECT COUNT(*) FROM drawings d JOIN participants p ON p.id=d.participant_id
             WHERE d.scene_id=s.id AND d.excluded=0 AND p.excluded=0) nd,
          (SELECT COUNT(*) FROM drawings d JOIN participants p ON p.id=d.participant_id
             WHERE d.scene_id=s.id AND d.excluded=0 AND p.excluded=0 AND p.role='team') nt,
          (SELECT COUNT(*) FROM candidates c WHERE c.scene_id=s.id) nc
        FROM scenes s"""):
        out[r["id"]] = {"n_drawings": r["nd"], "n_team": r["nt"], "n_vlm": r["nc"]}
    return out


@app.get("/api/admin/overview")
@admin_required
def admin_overview():
    con = db()
    s = get_settings(con)
    counts = _scene_counts(con)
    scenes = []
    for r in con.execute("SELECT * FROM scenes ORDER BY sort_order, created_at"):
        d = scene_admin(r)
        d.update(counts.get(r["id"], {}))
        scenes.append(d)
    tot = con.execute("""SELECT
        (SELECT COUNT(*) FROM participants) np,
        (SELECT COUNT(*) FROM participants WHERE role='team') nteam,
        (SELECT COUNT(*) FROM participants WHERE completed_at IS NOT NULL) nc,
        (SELECT COUNT(*) FROM drawings) nd,
        (SELECT COUNT(*) FROM candidates) ncand,
        (SELECT COUNT(*) FROM vlm_ratings) nr""").fetchone()
    return jsonify({"scenes": scenes, "settings": s, "totals": dict(tot), "dataset_dir": str(DATASET_DIR),
                    "default_password": ADMIN_PASSWORD == "changeme",
                    "models": [r[0] for r in con.execute("SELECT DISTINCT model FROM candidates ORDER BY model")]})


@app.get("/api/admin/scene_summaries")
@admin_required
def admin_scene_summaries():
    con = db()
    s = get_settings(con)
    role = request.args.get("role") or None
    return jsonify({r["id"]: scene_analysis(con, r, s, role)["summary"]
                    for r in con.execute("SELECT * FROM scenes").fetchall()})


def _save_frame(sid: str, fs, base="frame") -> tuple[str, int, int]:
    ext = Path(fs.filename or "").suffix.lower()
    name = f"{base}_{secrets.token_hex(3)}{ext if ext in IMAGE_EXT else '.png'}"
    p = scene_dir(sid) / name
    fs.save(p)
    try:
        with Image.open(p) as im:
            w, h = im.size
    except Exception:
        p.unlink(missing_ok=True)
        raise BadRequest(f"could not read image {fs.filename}")
    return name, w, h


class _MemFile:
    """Minimal FileStorage stand-in for files read out of a zip."""

    def __init__(self, name, data):
        self.filename, self._data = name, data

    def save(self, path):
        Path(path).write_bytes(self._data)

    def read(self):
        return self._data


def _expand_uploads(files):
    """Yield (FileStorage-like, original index); zip archives are expanded to their images."""
    for i, fs in enumerate(files):
        if Path(fs.filename or "").suffix.lower() == ".zip":
            with zipfile.ZipFile(io.BytesIO(fs.read())) as z:
                for name in sorted(z.namelist()):
                    base = Path(name).name
                    if name.startswith("__MACOSX") or base.startswith(".") or Path(base).suffix.lower() not in IMAGE_EXT:
                        continue
                    yield _MemFile(base, z.read(name)), None
        else:
            yield fs, i


@app.post("/api/admin/scenes")
@admin_required
def admin_create_scenes():
    """Upload training images (or a zip of them), or a video plus its decision frame (frame_<i>)."""
    con = db()
    files = request.files.getlist("files")
    if not files:
        raise BadRequest("no files")
    created = []
    order = con.execute("SELECT COALESCE(MAX(sort_order), 0) FROM scenes").fetchone()[0]
    for fs, i in _expand_uploads(files):
        ext = Path(fs.filename or "").suffix.lower()
        sid = new_id()
        title = re.sub(r"[_\-]+", " ", Path(fs.filename or "image").stem).strip()[:120] or "image"
        if ext in IMAGE_EXT:
            name, w, h = _save_frame(sid, fs, "image")
            media_type, media_file, frame_file, dtime = "image", name, name, None
        elif ext in VIDEO_EXT:
            frame = request.files.get(f"frame_{i}")
            if not frame:
                raise BadRequest(f"{fs.filename}: video uploads need a decision frame (use the admin page)")
            media_file = f"video{ext}"
            fs.save(scene_dir(sid) / media_file)
            frame_file, w, h = _save_frame(sid, frame)
            media_type = "video"
            try:
                dtime = float(request.form.get(f"time_{i}", "0"))
            except ValueError:
                dtime = 0.0
        else:
            raise BadRequest(f"{fs.filename}: unsupported file type")
        order += 1
        con.execute("""INSERT INTO scenes (id, title, source_name, scenario, prompt, media_type, media_file, frame_file,
                       width, height, decision_time, sort_order, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (sid, title, Path(fs.filename or "").name[:200], request.form.get("scenario", ""), "",
                     media_type, media_file, frame_file, w, h, dtime, order, now()))
        created.append(sid)
    con.commit()
    for sid in created:
        enqueue_sync(sid)
    return jsonify({"created": created})


@app.get("/api/admin/scenes/<sid>")
@admin_required
def admin_get_scene(sid):
    row = db().execute("SELECT * FROM scenes WHERE id=?", (sid,)).fetchone()
    if not row:
        abort(404)
    return jsonify(scene_admin(row))


@app.patch("/api/admin/scenes/<sid>")
@admin_required
def admin_update_scene(sid):
    con = db()
    if not con.execute("SELECT 1 FROM scenes WHERE id=?", (sid,)).fetchone():
        abort(404)
    b = request.get_json(force=True, silent=True) or {}
    sets, args = [], []
    for k in ("title", "scenario", "prompt", "split", "notes"):
        if k in b:
            sets.append(f"{k}=?")
            args.append(str(b[k] or "")[:4000])
    for k in ("active", "sort_order"):
        if k in b:
            sets.append(f"{k}=?")
            args.append(int(b[k]))
    if "decision_time" in b:
        sets.append("decision_time=?")
        args.append(float(b["decision_time"]) if b["decision_time"] is not None else None)
    if "start" in b:
        x, y = clean_points([b["start"], b["start"]])[0]
        sets += ["start_x=?", "start_y=?"]
        args += [x, y]
    if "goal" in b:
        if b["goal"] is None:
            sets += ["goal_x=NULL", "goal_y=NULL"]
        else:
            x, y = clean_points([b["goal"], b["goal"]])[0]
            sets += ["goal_x=?", "goal_y=?"]
            args += [x, y]
    if sets:
        con.execute(f"UPDATE scenes SET {', '.join(sets)}, version=version+1 WHERE id=?", args + [sid])
        con.commit()
        enqueue_sync(sid)
    return admin_get_scene(sid)


@app.post("/api/admin/scenes/<sid>/frame")
@admin_required
def admin_replace_frame(sid):
    con = db()
    row = con.execute("SELECT * FROM scenes WHERE id=?", (sid,)).fetchone()
    if not row or "frame" not in request.files:
        raise BadRequest("scene or frame missing")
    name, w, h = _save_frame(sid, request.files["frame"])
    dtime = request.form.get("time")
    con.execute("UPDATE scenes SET frame_file=?, width=?, height=?, decision_time=COALESCE(?, decision_time),"
                " version=version+1 WHERE id=?", (name, w, h, float(dtime) if dtime else None, sid))
    con.commit()
    enqueue_sync(sid)
    return admin_get_scene(sid)


@app.delete("/api/admin/scenes/<sid>")
@admin_required
def admin_delete_scene(sid):
    con = db()
    row = con.execute("SELECT * FROM scenes WHERE id=?", (sid,)).fetchone()
    if not row:
        abort(404)
    con.execute("DELETE FROM drawings WHERE scene_id=?", (sid,))
    con.execute("UPDATE candidates SET scene_id=NULL WHERE scene_id=?", (sid,))
    con.execute("DELETE FROM vlm_ratings WHERE item_ref=?", (f"consensus:{sid}",))
    con.execute("DELETE FROM scenes WHERE id=?", (sid,))
    con.commit()
    shutil.rmtree(MEDIA_DIR / secure_filename(sid), ignore_errors=True)
    DS.remove_set(row, DATASET_DIR)
    return jsonify({"deleted": sid})


def _candidate_public(c, scene=None, stats=None):
    d = {k: c[k] for k in ("id", "scene_id", "source", "model", "label", "decision", "kind", "confidence",
                           "rationale", "image_w", "image_h", "original_filename", "created_at")}
    d.update(points=jload(c["points"]), segments=jload(c["segments"]), style=c["style"], norms=jload(c["norms"], []),
             include_in_rating=bool(c["include_in_rating"]), consensus_override=bool(c["consensus_override"]),
             has_path=bool(c["points"]) or c["decision"] == "wait",
             image_url=f"/media/vlm/{c['image_file']}" if c["image_file"] else None,
             scene_title=scene["title"] if scene else None)
    if not d["image_url"] and scene is not None and d["has_path"]:
        d["image_url"] = "/media/" + str(DS.render_candidate(scene, c, MEDIA_DIR).relative_to(MEDIA_DIR))
    if stats is not None:
        d["ratings"] = stats
    return d


@app.get("/api/admin/scenes/<sid>/analysis")
@admin_required
def admin_scene_analysis(sid):
    con = db()
    row = con.execute("SELECT * FROM scenes WHERE id=?", (sid,)).fetchone()
    if not row:
        abort(404)
    s = get_settings(con)
    role = request.args.get("role") or None
    ov = {}
    if request.args.get("cluster_threshold"):
        ov["cluster_threshold"] = request.args.get("cluster_threshold", type=float)
    res = scene_analysis(con, row, s, role, ov)
    hum = {h["id"]: h for h in res["humans"]}
    drawings = []
    for d in con.execute("""SELECT d.*, p.code, p.name, p.role, p.excluded p_excluded FROM drawings d
                            JOIN participants p ON p.id=d.participant_id WHERE d.scene_id=? ORDER BY d.created_at""", (sid,)):
        r = {"id": d["id"], "participant_id": d["participant_id"], "code": d["code"], "name": d["name"],
             "role": d["role"], "decision": d["decision"], "style": d["style"], "norms": jload(d["norms"], []),
             "confidence": d["confidence"], "comment": d["comment"], "duration_ms": d["duration_ms"],
             "excluded": bool(d["excluded"] or d["p_excluded"]), "created_at": d["created_at"],
             "points": jload(d["points"]), "segments": jload(d["segments"]),
             "fast": bool(d["duration_ms"] and d["duration_ms"] < int(s["fast_response_ms"]))}
        for k, v in hum.get(d["id"], {}).items():
            r.setdefault(k, v)
        drawings.append(r)
    summ, _ = rating_overview(con, s)
    cands = []
    metrics = {c["id"]: c for c in res["candidates"]}
    for c in con.execute("SELECT * FROM candidates WHERE scene_id=? ORDER BY created_at", (sid,)):
        d = _candidate_public(c, row, summ["items"].get(c["id"]))
        d["metrics"] = metrics.get(c["id"])
        cands.append(d)
    codes = {d["id"]: d["code"] for d in drawings}
    return jsonify({"scene": scene_admin(row), "analysis": res, "drawings": drawings, "candidates": cands,
                    "pairwise_codes": [codes.get(i) for i in res["pairwise_ids"]],
                    "control_ratings": summ["items"].get(f"consensus:{sid}"),
                    "settings": {k: s[k] for k in ("cluster_threshold", "norms", "fast_response_ms", "questions",
                                                   "exclude_outliers", "compiled_method")}})


def _insert_candidate(con, b: dict, image=None) -> str:
    sid = b.get("scene_id") or None
    if sid and not con.execute("SELECT 1 FROM scenes WHERE id=?", (sid,)).fetchone():
        raise BadRequest(f"unknown training image {sid}")
    decision = b.get("decision") or "path"
    if decision not in ("path", "wait"):
        raise BadRequest("decision must be 'path' or 'wait'")
    kind = b.get("kind") or "waypoints"
    if kind not in ("waypoints", "polyline"):
        raise BadRequest("kind must be 'waypoints' or 'polyline'")
    pts = segs = None
    if decision == "path" and b.get("points") is not None:
        pts = clean_points(b.get("points"), max_n=60 if kind == "waypoints" else 4000)
        segs = clean_segments(b.get("segments"), len(pts)) if kind == "waypoints" else None
    elif decision == "path" and image is None:
        raise BadRequest("a path needs points (or upload an image)")
    style = b.get("style") if b.get("style") in ("line", "curve") else "curve"
    style = style_of(segs, style) if segs else style
    conf = b.get("confidence")
    if isinstance(conf, (int, float)) and conf > 1:
        conf = conf / 100.0
    cid = str(b.get("id") or new_id())[:64]
    override = 1 if b.get("consensus_override") else 0
    if override and sid:
        con.execute("UPDATE candidates SET consensus_override=0 WHERE scene_id=?", (sid,))
    img = image or {}
    con.execute("""INSERT INTO candidates (id, scene_id, source, model, label, decision, kind, points, segments, style,
                   norms, confidence, rationale, meta, image_file, image_w, image_h, original_filename,
                   include_in_rating, consensus_override, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (cid, sid, str(b.get("source") or "vlm")[:64], str(b.get("model") or "unnamed")[:128],
                 str(b.get("label") or "")[:200], decision, kind, json.dumps(pts) if pts else None,
                 json.dumps(segs) if segs else None, style, json.dumps(clean_norms(b.get("norms"))),
                 float(conf) if isinstance(conf, (int, float)) else None, str(b.get("rationale") or "")[:8000],
                 json.dumps(b.get("meta") or {})[:8000], img.get("file"), img.get("w"), img.get("h"),
                 img.get("original"), 0 if override else int(b.get("include_in_rating", True)), override, now()))
    if sid:
        bump_scene(con, sid)
    return cid


@app.post("/api/admin/scenes/<sid>/candidates")
@admin_required
def admin_add_candidate(sid):
    con = db()
    b = request.get_json(force=True, silent=True) or {}
    b["scene_id"] = sid
    cid = _insert_candidate(con, b)
    con.commit()
    enqueue_sync(sid)
    return jsonify({"id": cid})


def _store_vlm_image(fs) -> dict:
    ext = Path(fs.filename or "").suffix.lower()
    if ext not in IMAGE_EXT:
        raise BadRequest(f"{fs.filename}: not an image")
    name = f"{new_id()}{ext}"
    path = MEDIA_DIR / "vlm" / name
    fs.save(path)
    try:
        with Image.open(path) as im:
            w, h = im.size
    except Exception:
        path.unlink(missing_ok=True)
        raise BadRequest(f"could not read image {fs.filename}")
    return {"file": name, "w": w, "h": h, "original": Path(fs.filename or name).name[:200]}


@app.post("/api/admin/vlm_images")
@admin_required
def admin_upload_vlm_images():
    """Images drawn by a VLM outside the site. Form: files[], model, source, scene_<i> (training image id)."""
    con = db()
    files = request.files.getlist("files")
    model = (request.form.get("model") or "").strip()
    if not files:
        raise BadRequest("no files")
    if not model:
        raise BadRequest("Name the model that drew these images.")
    ids, touched = [], set()
    for i, fs in enumerate(files):
        sid = request.form.get(f"scene_{i}") or None
        img = _store_vlm_image(fs)
        meta = jload(request.form.get(f"meta_{i}"), {}) or {}
        cid = _insert_candidate(con, {"scene_id": sid, "model": model, "source": request.form.get("source") or "vlm",
                                      "decision": "path", **meta}, image=img)
        ids.append(cid)
        if sid:
            touched.add(sid)
    con.commit()
    for sid in touched:
        enqueue_sync(sid)
    if len(touched) < len(files) or any(not request.form.get(f"scene_{i}") for i in range(len(files))):
        DS.sync_unlinked(con, DATASET_DIR, MEDIA_DIR)
    return jsonify({"created": ids})


@app.get("/api/admin/vlm_images")
@admin_required
def admin_list_vlm():
    con = db()
    s = get_settings(con)
    summ, _ = rating_overview(con, s)
    scenes = {r["id"]: r for r in con.execute("SELECT * FROM scenes")}
    out = [_candidate_public(c, scenes.get(c["scene_id"]), summ["items"].get(c["id"]))
           for c in con.execute("SELECT * FROM candidates ORDER BY created_at DESC")]
    return jsonify({"items": out, "questions": s["questions"]})


@app.get("/api/admin/candidates/<cid>")
@admin_required
def admin_get_candidate(cid):
    con = db()
    s = get_settings(con)
    c = con.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
    if not c:
        abort(404)
    scene = con.execute("SELECT * FROM scenes WHERE id=?", (c["scene_id"],)).fetchone() if c["scene_id"] else None
    rows = rating_rows(con, item=cid)
    summ = A.rating_summary(rows, s["questions"])
    dist = {}
    for q in s["questions"]:
        counts = {}
        for r in rows:
            v = r["answers"].get(q["key"])
            if v is not None:
                counts[str(v)] = counts.get(str(v), 0) + 1
        dist[q["key"]] = counts
    norms = {}
    for r in rows:
        for nm in r["norms"]:
            norms[nm] = norms.get(nm, 0) + 1
    metrics = None
    if scene is not None:
        a = scene_analysis(con, scene, s)
        metrics = next((x for x in a["candidates"] if x["id"] == cid), None)
    return jsonify({"candidate": _candidate_public(c, scene, summ["items"].get(cid)),
                    "scene": scene_admin(scene) if scene else None, "distribution": dist, "norm_counts": norms,
                    "comments": [{"comment": r["comment"], "code": r["code"]} for r in rows if r["comment"]],
                    "n_ratings": len(rows), "metrics": metrics, "questions": s["questions"], "norms": s["norms"],
                    "scenes": [{"id": r["id"], "title": r["title"]} for r in
                               con.execute("SELECT id, title FROM scenes ORDER BY sort_order, created_at")]})


@app.patch("/api/admin/candidates/<cid>")
@admin_required
def admin_update_candidate(cid):
    con = db()
    c = con.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
    if not c:
        abort(404)
    b = request.get_json(force=True, silent=True) or {}
    old_sid = c["scene_id"]
    sid = old_sid
    if "scene_id" in b:
        sid = b["scene_id"] or None
        if sid and not con.execute("SELECT 1 FROM scenes WHERE id=?", (sid,)).fetchone():
            raise BadRequest("unknown training image")
        con.execute("UPDATE candidates SET scene_id=?, consensus_override=0 WHERE id=?", (sid, cid))
    if b.get("consensus_override"):
        con.execute("UPDATE candidates SET consensus_override=0 WHERE scene_id=?", (sid,))
        con.execute("UPDATE candidates SET consensus_override=1, include_in_rating=0 WHERE id=?", (cid,))
    elif "consensus_override" in b:
        con.execute("UPDATE candidates SET consensus_override=0 WHERE id=?", (cid,))
    if "include_in_rating" in b:
        con.execute("UPDATE candidates SET include_in_rating=? WHERE id=?", (int(bool(b["include_in_rating"])), cid))
    for k in ("model", "label", "source", "rationale"):
        if k in b:
            con.execute(f"UPDATE candidates SET {k}=? WHERE id=?", (str(b[k] or "")[:2000], cid))
    if "decision" in b and b["decision"] in ("path", "wait"):
        con.execute("UPDATE candidates SET decision=? WHERE id=?", (b["decision"], cid))
    if "points" in b:  # traced / corrected path
        if b["points"] is None:
            con.execute("UPDATE candidates SET points=NULL, segments=NULL WHERE id=?", (cid,))
        else:
            pts = clean_points(b["points"])
            segs = clean_segments(b.get("segments"), len(pts))
            con.execute("UPDATE candidates SET points=?, segments=?, style=?, kind='waypoints' WHERE id=?",
                        (json.dumps(pts), json.dumps(segs) if segs else None, style_of(segs, b.get("style") or "curve"), cid))
    for x in {old_sid, sid} - {None}:
        bump_scene(con, x)
    con.commit()
    for x in {old_sid, sid} - {None}:
        enqueue_sync(x)
    if not sid or not old_sid:
        DS.sync_unlinked(con, DATASET_DIR, MEDIA_DIR)
    return jsonify({"ok": True})


@app.delete("/api/admin/candidates/<cid>")
@admin_required
def admin_delete_candidate(cid):
    con = db()
    c = con.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
    if not c:
        abort(404)
    con.execute("DELETE FROM candidates WHERE id=?", (cid,))
    con.execute("DELETE FROM vlm_ratings WHERE item_ref=?", (cid,))
    if c["scene_id"]:
        bump_scene(con, c["scene_id"])
    con.commit()
    if c["image_file"]:
        (MEDIA_DIR / "vlm" / c["image_file"]).unlink(missing_ok=True)
    if c["scene_id"]:
        enqueue_sync(c["scene_id"])
    DS.sync_unlinked(con, DATASET_DIR, MEDIA_DIR)
    return jsonify({"ok": True})


@app.post("/api/admin/candidates/import")
@admin_required
def admin_import_candidates():
    """Bulk JSON: [{scene_id | scene_title, model, decision, kind, points, segments, confidence, ...}]."""
    con = db()
    data = jload(request.files["file"].read().decode("utf-8", "replace")) if "file" in request.files \
        else request.get_json(force=True, silent=True)
    if isinstance(data, dict):
        data = data.get("candidates", [data])
    if not isinstance(data, list):
        raise BadRequest("expected a JSON list")
    titles = {}
    for r in con.execute("SELECT id, title, source_name FROM scenes"):
        titles[r["title"]] = r["id"]
        titles[Path(r["source_name"] or "").stem] = r["id"]
    ids, errors, touched = [], [], set()
    for i, c in enumerate(data):
        try:
            if not isinstance(c, dict):
                raise BadRequest("not an object")
            c = dict(c)
            c["scene_id"] = c.get("scene_id") or titles.get(c.get("scene_title"))
            ids.append(_insert_candidate(con, c))
            if c["scene_id"]:
                touched.add(c["scene_id"])
        except (BadRequest, sqlite3.IntegrityError) as e:
            errors.append(f"item {i}: {e}")
    con.commit()
    for sid in touched:
        enqueue_sync(sid)
    return jsonify({"imported": len(ids), "errors": errors})


@app.patch("/api/admin/drawings/<did>")
@admin_required
def admin_update_drawing(did):
    con = db()
    d = con.execute("SELECT scene_id FROM drawings WHERE id=?", (did,)).fetchone()
    if not d:
        abort(404)
    b = request.get_json(force=True, silent=True) or {}
    con.execute("UPDATE drawings SET excluded=? WHERE id=?", (int(bool(b.get("excluded"))), did))
    bump_scene(con, d["scene_id"])
    con.commit()
    enqueue_sync(d["scene_id"])
    return jsonify({"ok": True})


@app.get("/api/admin/participants")
@admin_required
def admin_participants():
    con = db()
    s = get_settings(con)
    n_active = con.execute("SELECT COUNT(*) FROM scenes WHERE active=1").fetchone()[0]
    rows = []
    for p in con.execute("""SELECT p.*,
            (SELECT COUNT(*) FROM drawings d WHERE d.participant_id=p.id) nd,
            (SELECT COUNT(*) FROM vlm_ratings r WHERE r.participant_id=p.id) nr,
            (SELECT AVG(duration_ms) FROM drawings d WHERE d.participant_id=p.id) avg_ms
            FROM participants p ORDER BY created_at DESC"""):
        assigned = jload(p["assigned"], {}) or {}
        n_draw = n_active if p["role"] == "team" and not int(s["team_draw_count"]) else len(assigned.get("draw") or [])
        rate = assigned.get("rate")
        rows.append({"id": p["id"], "code": p["code"], "name": p["name"], "email": p["email"],
                     "external_id": p["external_id"], "role": p["role"], "group": p["grp"],
                     "demographics": jload(p["demographics"], {}), "n_drawings": p["nd"], "n_draw_assigned": n_draw,
                     "n_ratings": p["nr"], "n_rate_assigned": len(rate) if rate is not None else None,
                     "avg_draw_ms": p["avg_ms"], "excluded": bool(p["excluded"]), "completed": bool(p["completed_at"]),
                     "completion_code": p["completion_code"], "created_at": p["created_at"]})
    return jsonify(rows)


@app.patch("/api/admin/participants/<pid>")
@admin_required
def admin_update_participant(pid):
    con = db()
    b = request.get_json(force=True, silent=True) or {}
    if "excluded" in b:
        con.execute("UPDATE participants SET excluded=? WHERE id=?", (int(bool(b["excluded"])), pid))
    if b.get("role") in ("team", "survey"):
        con.execute("UPDATE participants SET role=? WHERE id=?", (b["role"], pid))
    scenes = [r[0] for r in con.execute("SELECT DISTINCT scene_id FROM drawings WHERE participant_id=?", (pid,))]
    for sid in scenes:
        bump_scene(con, sid)
    con.commit()
    for sid in scenes:
        enqueue_sync(sid)
    return jsonify({"ok": True})


@app.delete("/api/admin/participants/<pid>")
@admin_required
def admin_delete_participant(pid):
    """Remove a participant and everything they submitted (e.g. consent withdrawn)."""
    con = db()
    scenes = [r[0] for r in con.execute("SELECT DISTINCT scene_id FROM drawings WHERE participant_id=?", (pid,))]
    con.execute("DELETE FROM drawings WHERE participant_id=?", (pid,))
    con.execute("DELETE FROM vlm_ratings WHERE participant_id=?", (pid,))
    con.execute("DELETE FROM participants WHERE id=?", (pid,))
    for sid in scenes:
        bump_scene(con, sid)
    con.commit()
    for sid in scenes:
        enqueue_sync(sid)
    return jsonify({"ok": True})


@app.route("/api/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    con = db()
    if request.method == "POST":
        b = request.get_json(force=True, silent=True) or {}
        for k, v in b.items():
            if k not in DEFAULT_SETTINGS:
                continue
            if k == "norms" and not (isinstance(v, list) and all(isinstance(n, dict) and n.get("key") for n in v)):
                raise BadRequest("norms must be a list of {key, label}")
            if k == "questions":
                if not isinstance(v, list) or not v:
                    raise BadRequest("add at least one rating question")
                keys = set()
                for q in v:
                    if not isinstance(q, dict) or not q.get("key") or q.get("type") not in ("yesno", "scale"):
                        raise BadRequest("each question needs a key and a type (yesno or scale)")
                    if q["key"] in keys:
                        raise BadRequest(f"duplicate question key {q['key']}")
                    keys.add(q["key"])
            if k == "team_code" and len(str(v).strip()) < 4:
                raise BadRequest("the team access code needs at least 4 characters")
            con.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET "
                        "value=excluded.value", (k, json.dumps(v)))
        if any(k in ANALYSIS_KEYS for k in b):
            bump_scene(con)
        con.commit()
        if any(k in ANALYSIS_KEYS for k in b):
            enqueue_all(con)
    return jsonify(get_settings(con))


@app.get("/api/admin/results")
@admin_required
def admin_results():
    """Per model: questionnaire results and (when coordinates exist) path agreement."""
    con = db()
    s = get_settings(con)
    role = request.args.get("role") or None
    split = request.args.get("split") or None
    summ, rows = rating_overview(con, s, role)
    q, args = "SELECT * FROM scenes", []
    if split:
        q += " WHERE split=?"
        args.append(split)
    paths = A.model_path_summary([{"scene_id": r["id"], "analysis": scene_analysis(con, r, s)}
                                  for r in con.execute(q, args).fetchall()])
    n_uploaded = {r["model"]: r["n"] for r in
                  con.execute("SELECT model, COUNT(*) n FROM candidates WHERE consensus_override=0 GROUP BY model")}
    models = sorted(set(summ["groups"]) | set(paths) | set(n_uploaded), key=lambda m: (m == CONTROL_MODEL, m))
    out = []
    for m in models:
        out.append({"model": m, "control": m == CONTROL_MODEL, "n_images": n_uploaded.get(m),
                    "ratings": summ["groups"].get(m), "paths": paths.get(m)})
    return jsonify({"questions": s["questions"], "models": out, "reliability": summ["reliability"],
                    "n_ratings": len(rows), "n_raters": len({r["code"] for r in rows})})


@app.post("/api/admin/rebuild_folders")
@admin_required
def admin_rebuild():
    con = db()
    s = get_settings(con)
    DS.rebuild_all(con, s, DATASET_DIR, MEDIA_DIR, lambda r: scene_analysis(con, r, s))
    return jsonify({"ok": True, "dataset_dir": str(DATASET_DIR)})


@app.get("/api/admin/export.zip")
@admin_required
def admin_export():
    con = db()
    s = get_settings(con)
    sync_all(con, s)
    buf = io.BytesIO()
    DS.build_export(con, buf, s, DATASET_DIR, MEDIA_DIR,
                    analysis_fn=lambda row: scene_analysis(con, row, s),
                    ratings=rating_overview(con, s),
                    results_fn=lambda: admin_results().get_json(),
                    include_media=request.args.get("media", "1") != "0",
                    anonymize=request.args.get("anonymize") == "1")
    buf.seek(0)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    tag = "_anonymized" if request.args.get("anonymize") == "1" else ""
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name=f"trajlab_dataset_{stamp}{tag}.zip")


@app.post("/api/admin/import")
@admin_required
def admin_import():
    if "file" not in request.files:
        raise BadRequest("no file")
    con = db()
    try:
        report = DS.merge_import(con, request.files["file"], MEDIA_DIR, _assign_code)
    except (ValueError, KeyError, zipfile.BadZipFile) as e:
        raise BadRequest(f"could not import: {e}")
    bump_scene(con)
    con.commit()
    enqueue_all(con)
    return jsonify(report)


# ---------------------------------------------------------------------------
# Pipeline API (bearer token) for VLM scripts
# ---------------------------------------------------------------------------

@app.get("/api/v1/scenes")
@admin_required
def v1_scenes():
    con = db()
    q, args = "SELECT * FROM scenes WHERE active=1", []
    if request.args.get("split"):
        q += " AND split=?"
        args.append(request.args["split"])
    out = []
    host = request.host_url.rstrip("/")
    for r in con.execute(q + " ORDER BY sort_order, created_at", args):
        d = scene_admin(r)
        d["image_url"] = host + d["image_url"]
        if d["video_url"]:
            d["video_url"] = host + d["video_url"]
        out.append(d)
    return jsonify(out)


@app.post("/api/v1/candidates")
@admin_required
def v1_candidates():
    return admin_import_candidates()


@app.post("/api/v1/vlm_images")
@admin_required
def v1_vlm_images():
    """multipart: files[], model, source, scene_<i> = training image id."""
    return admin_upload_vlm_images()


@app.get("/api/v1/norms")
@admin_required
def v1_norms():
    return jsonify(get_settings()["norms"])


@app.get("/api/v1/consensus")
@admin_required
def v1_consensus():
    """Compiled human trajectory per image, e.g. for in-context examples (?split=train)."""
    con = db()
    s = get_settings(con)
    q, args = "SELECT * FROM scenes", []
    if request.args.get("split"):
        q += " WHERE split=?"
        args.append(request.args["split"])
    host = request.host_url.rstrip("/")
    out = []
    for r in con.execute(q, args).fetchall():
        a = scene_analysis(con, r, s)
        comp = a.get("compiled") or {}
        out.append({"scene_id": r["id"], "title": r["title"], "scenario": r["scenario"], "split": r["split"],
                    "image_url": f"{host}/media/{r['id']}/{r['frame_file']}",
                    "start": [r["start_x"], r["start_y"]],
                    "goal": [r["goal_x"], r["goal_y"]] if r["goal_x"] is not None else None,
                    "consensus_decision": "wait" if comp.get("wait") else ("path" if comp else None),
                    "consensus_curve": comp.get("curve"), "consensus_source": comp.get("source"),
                    "top_choice_share": a["summary"]["top_choice_share"], "ambiguous": a["summary"]["ambiguous"],
                    "n_responses": a["summary"]["n_responses"]})
    return jsonify(out)


@app.get("/healthz")
def healthz():
    return "ok"


def _lan_ip() -> str | None:
    try:
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sk.connect(("10.255.255.255", 1))
        ip = sk.getsockname()[0]
        sk.close()
        return ip
    except OSError:
        return None


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    host = os.environ.get("HOST", "0.0.0.0")
    with app.app_context():
        team_code = get_settings()["team_code"]
    print("\n  TrajLab is running")
    print(f"  Participants:      http://localhost:{port}/")
    ip = _lan_ip()
    if ip and host == "0.0.0.0":
        print(f"  Same Wi-Fi:        http://{ip}:{port}/")
    print(f"  Admin:             http://localhost:{port}/admin")
    print(f"  Team access code:  {team_code}")
    if ADMIN_PASSWORD == "changeme":
        print("  WARNING: default admin password 'changeme'. Set ADMIN_PASSWORD before sharing publicly.")
    print(f"  Dataset folder:    {DATASET_DIR}\n")
    app.run(host=host, port=port, debug=bool(os.environ.get("DEBUG")), threaded=True)
