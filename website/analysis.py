"""
Trajectory geometry, agreement statistics and rating statistics for TrajLab.

Pure numpy; no web framework imports, so it can be reused offline on an exported
dataset (see scripts/analyze_export.py).

Conventions
-----------
* Points arrive in *normalized image coordinates*: x = px / width, y = py / height,
  both in [0, 1], origin at the top-left of the image.
* A drawn path is a list of points plus one segment per consecutive pair. Each segment
  is either a straight line or a cubic Bezier curve with two control points. Geometry
  is evaluated in pixel space (so it matches what the browser draws), then every
  distance is reported in units of the image diagonal: 0.05 means "5% of the diagonal".
* Every path is resampled to N points equally spaced by arc length before comparison,
  so the number of points a person placed and how unevenly they spaced them does not
  matter.

Metrics between two paths
-------------------------
frechet  Discrete Frechet distance (primary): the largest gap between the two paths
         under the best forward-moving alignment. Respects direction of travel and
         catches a brief excursion (e.g. cutting through a group) that averages hide.
rmse     Root-mean-square error between arc-length-matched points (MSE = rmse**2).
dtw      Dynamic time warping, mean distance along the optimal warping path.
similarity = 1 / (1 + frechet / tau): 100% for identical paths, 50% exactly at the
         same-route threshold tau, falling towards 0 as paths diverge.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

N_SAMPLES = 64          # points per path after arc-length resampling
CURVE_SAMPLES = 24      # samples per Bezier segment before resampling
EPS = 1e-9

DEFAULTS = {
    "cluster_threshold": 0.10,    # same-route threshold (Frechet, fraction of image diagonal)
    "ambiguity_threshold": 0.60,  # image is ambiguous if the top choice share is below this
    "min_mode_share": 0.15,       # a route counts as a real option at >= this share
    "exclude_outliers": True,     # leave outlier paths out of the vote and the compiled path
    "compiled_method": "mean",    # "mean" (robust average of the top route) or "medoid"
}


# ---------------------------------------------------------------------------
# Path geometry: straight segments and cubic Bezier segments
# (mirrored exactly by static/js/geometry.js)
# ---------------------------------------------------------------------------

def _unit(v):
    n = math.hypot(v[0], v[1])
    return (v[0] / n, v[1] / n) if n > EPS else (0.0, 0.0)


def _tangents(P, types):
    """Unit tangent at every point, used to place automatic Bezier handles.

    End points aim at their neighbour. Where a curve meets a straight segment the
    tangent follows the straight segment, so the join is smooth. Elsewhere it is the
    bisector of the incoming and outgoing directions.
    """
    n = len(P)
    T = []
    for i in range(n):
        if n < 2:
            T.append((0.0, 0.0))
            continue
        if i == 0:
            T.append(_unit((P[1][0] - P[0][0], P[1][1] - P[0][1])))
            continue
        if i == n - 1:
            T.append(_unit((P[i][0] - P[i - 1][0], P[i][1] - P[i - 1][1])))
            continue
        a = _unit((P[i][0] - P[i - 1][0], P[i][1] - P[i - 1][1]))
        b = _unit((P[i + 1][0] - P[i][0], P[i + 1][1] - P[i][1]))
        if types[i - 1] == "line" and types[i] == "curve":
            T.append(a)
        elif types[i - 1] == "curve" and types[i] == "line":
            T.append(b)
        else:
            s = (a[0] + b[0], a[1] + b[1])
            T.append(_unit(s) if math.hypot(*s) > 1e-6 else b)
    return T


def build_segments(P, segments=None, style: str = "curve") -> list[dict]:
    """Pixel points + optional segment specs -> [{"type", "c1", "c2"}] in pixel space.

    segments: list of {"type": "line"|"curve", "c1": [x, y], "c2": [x, y]} with control
    points already in pixel space; a curve without c1/c2 gets automatic handles.
    """
    n = len(P)
    if n < 2:
        return []
    specs = list(segments or [])
    types = [(specs[j].get("type") if j < len(specs) and isinstance(specs[j], dict) else None) or style
             for j in range(n - 1)]
    types = ["line" if t == "line" else "curve" for t in types]
    T = _tangents(P, types)
    out = []
    for j in range(n - 1):
        a, b = P[j], P[j + 1]
        if types[j] == "line":
            out.append({"type": "line"})
            continue
        spec = specs[j] if j < len(specs) and isinstance(specs[j], dict) else {}
        L = math.hypot(b[0] - a[0], b[1] - a[1]) / 3.0
        c1 = spec.get("c1") or (a[0] + T[j][0] * L, a[1] + T[j][1] * L)
        c2 = spec.get("c2") or (b[0] - T[j + 1][0] * L, b[1] - T[j + 1][1] * L)
        out.append({"type": "curve", "c1": (float(c1[0]), float(c1[1])), "c2": (float(c2[0]), float(c2[1]))})
    return out


def sample_path(P, segs, per_curve: int = CURVE_SAMPLES) -> np.ndarray:
    """Dense polyline for points P (pixel) joined by built segments."""
    P = [tuple(map(float, p)) for p in P]
    if not P:
        return np.zeros((0, 2))
    out = [P[0]]
    t = np.linspace(0.0, 1.0, per_curve + 1)[1:, None]
    for j, s in enumerate(segs):
        a, b = np.array(P[j]), np.array(P[j + 1])
        if s["type"] == "line":
            out.append(tuple(b))
        else:
            c1, c2 = np.array(s["c1"]), np.array(s["c2"])
            pts = ((1 - t) ** 3) * a + 3 * ((1 - t) ** 2) * t * c1 + 3 * (1 - t) * t ** 2 * c2 + t ** 3 * b
            out.extend(map(tuple, pts))
    return np.asarray(out, dtype=float)


def resample(poly, n: int = N_SAMPLES) -> np.ndarray:
    """Resample a polyline to n points equally spaced by arc length."""
    P = np.asarray(poly, dtype=float).reshape(-1, 2)
    if len(P) == 0:
        raise ValueError("empty path")
    if len(P) == 1:
        return np.repeat(P, n, axis=0)
    seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
    P = P[np.concatenate([[True], seg > EPS])]
    if len(P) == 1:
        return np.repeat(P, n, axis=0)
    seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    s = np.linspace(0.0, cum[-1], n)
    return np.column_stack([np.interp(s, cum, P[:, 0]), np.interp(s, cum, P[:, 1])])


def segments_to_pixels(segments, width, height):
    """Normalized segment specs (as stored) -> pixel-space specs for build_segments."""
    out = []
    for s in segments or []:
        s = dict(s or {})
        for k in ("c1", "c2"):
            if s.get(k):
                s[k] = (float(s[k][0]) * width, float(s[k][1]) * height)
        out.append(s)
    return out


def dense_path(points, width, height, kind="waypoints", segments=None, style="curve") -> np.ndarray:
    """Normalized points -> dense polyline in pixel space."""
    P = np.asarray(points, dtype=float).reshape(-1, 2) * np.array([width, height])
    if kind == "polyline":
        return P
    segs = build_segments(P.tolist(), segments_to_pixels(segments, width, height), style)
    return sample_path(P.tolist(), segs)


def path_curve(points, width: float, height: float, kind: str = "waypoints", segments=None,
               style: str = "curve", n: int = N_SAMPLES) -> np.ndarray:
    """Normalized points -> arc-length-resampled path in diagonal units, shape (n, 2).

    kind="waypoints": points are the places a person (or model) clicked, joined by the
                      given segments (straight or Bezier); missing segment info defaults
                      to `style` with automatic smooth handles.
    kind="polyline":  points are already a dense trajectory.
    """
    dense = dense_path(points, width, height, kind, segments, style)
    return resample(dense, n) / math.hypot(width, height)


def curve_to_normalized(curve: np.ndarray, width: float, height: float) -> list:
    """Diagonal-unit curve -> normalized image coords (for drawing)."""
    diag = math.hypot(width, height)
    xy = np.asarray(curve) * diag / np.array([width, height])
    return np.round(xy, 5).tolist()


def path_length(curve: np.ndarray) -> float:
    return float(np.linalg.norm(np.diff(curve, axis=0), axis=1).sum())


# ---------------------------------------------------------------------------
# Distances (batched over many pairs at once)
# ---------------------------------------------------------------------------

def _row_dist(A, B, i):
    return np.linalg.norm(A[:, i, None, :] - B, axis=2)  # (P, M)


def frechet_batch(A: np.ndarray, B: np.ndarray) -> np.ndarray:
    """Discrete Frechet distance for P pairs. A: (P, N, 2), B: (P, M, 2)."""
    N, M = A.shape[1], B.shape[1]
    prev = np.maximum.accumulate(_row_dist(A, B, 0), axis=1)
    for i in range(1, N):
        d = _row_dist(A, B, i)
        row = np.empty_like(d)
        row[:, 0] = np.maximum(prev[:, 0], d[:, 0])
        m = np.minimum(prev[:, 1:], prev[:, :-1])
        for j in range(1, M):
            row[:, j] = np.maximum(np.minimum(m[:, j - 1], row[:, j - 1]), d[:, j])
        prev = row
    return prev[:, -1]


def dtw_batch(A: np.ndarray, B: np.ndarray) -> np.ndarray:
    """DTW for P pairs, normalized to the mean distance along the warping path."""
    P, N, _ = A.shape
    M = B.shape[1]
    cost = np.cumsum(_row_dist(A, B, 0), axis=1)
    steps = np.tile(np.arange(1, M + 1, dtype=float), (P, 1))
    idx = np.arange(P)
    for i in range(1, N):
        d = _row_dist(A, B, i)
        c_row, s_row = np.empty_like(d), np.empty_like(d)
        c_row[:, 0] = cost[:, 0] + d[:, 0]
        s_row[:, 0] = steps[:, 0] + 1
        for j in range(1, M):
            cand_c = np.stack([cost[:, j], cost[:, j - 1], c_row[:, j - 1]])
            cand_s = np.stack([steps[:, j], steps[:, j - 1], s_row[:, j - 1]])
            k = np.argmin(cand_c, axis=0)
            c_row[:, j] = cand_c[k, idx] + d[:, j]
            s_row[:, j] = cand_s[k, idx] + 1
        cost, steps = c_row, s_row
    return cost[:, -1] / steps[:, -1]


def rmse_batch(A: np.ndarray, B: np.ndarray) -> np.ndarray:
    return np.sqrt(np.mean(np.sum((A - B) ** 2, axis=2), axis=1))


METRICS = {"frechet": frechet_batch, "rmse": rmse_batch, "dtw": dtw_batch}


def _batched(fn, A, B, chunk=2048):
    out = np.empty(len(A))
    for s in range(0, len(A), chunk):
        out[s:s + chunk] = fn(A[s:s + chunk], B[s:s + chunk])
    return out


def pairwise_matrix(curves: np.ndarray, metric: str = "frechet") -> np.ndarray:
    K = len(curves)
    D = np.zeros((K, K))
    if K < 2:
        return D
    iu, ju = np.triu_indices(K, 1)
    vals = _batched(METRICS[metric], curves[iu], curves[ju])
    D[iu, ju] = vals
    D[ju, iu] = vals
    return D


def one_to_many(curve: np.ndarray, curves: np.ndarray, metric: str = "frechet") -> np.ndarray:
    if len(curves) == 0:
        return np.zeros(0)
    return _batched(METRICS[metric], np.repeat(curve[None], len(curves), axis=0), curves)


def pair(a: np.ndarray, b: np.ndarray, metric: str = "frechet") -> float:
    return float(METRICS[metric](a[None], b[None])[0])


def similarity(d, tau):
    """Distance -> similarity in [0, 1]; 0.5 exactly at the same-route threshold."""
    return 1.0 / (1.0 + np.asarray(d, dtype=float) / tau)


# ---------------------------------------------------------------------------
# Routes, outliers, compiled path
# ---------------------------------------------------------------------------

def cluster_paths(D: np.ndarray, threshold: float) -> np.ndarray:
    """Average-linkage (UPGMA) clustering cut at `threshold`; label 0 = largest cluster."""
    K = len(D)
    if K <= 1:
        return np.zeros(K, dtype=int)
    C = D.astype(float).copy()
    np.fill_diagonal(C, np.inf)
    size = np.ones(K)
    alive = np.ones(K, dtype=bool)
    members = {i: [i] for i in range(K)}
    while alive.sum() > 1:
        sub = np.where(alive[:, None] & alive[None, :], C, np.inf)
        i, j = divmod(int(np.argmin(sub)), K)
        if sub[i, j] >= threshold:
            break
        new = (size[i] * C[i] + size[j] * C[j]) / (size[i] + size[j])
        C[i, :] = new
        C[:, i] = new
        C[i, i] = np.inf
        C[j, :] = np.inf
        C[:, j] = np.inf
        alive[j] = False
        size[i] += size[j]
        members[i].extend(members.pop(j))
    groups = sorted(members.values(), key=lambda m: (-len(m), min(m)))
    labels = np.empty(K, dtype=int)
    for lab, m in enumerate(groups):
        labels[m] = lab
    return labels


def medoid(D: np.ndarray, idx) -> int:
    idx = np.asarray(idx)
    return int(idx[np.argmin(D[np.ix_(idx, idx)].sum(axis=1))])


def _mad_cut(x, z=3.0):
    """median + z * robust standard deviation (1.4826 * MAD)."""
    x = np.asarray(x, float)
    med = float(np.median(x))
    return med + z * 1.4826 * float(np.median(np.abs(x - med)))


def knn_outliers(D: np.ndarray, tau: float, min_paths: int = 5):
    """Flag paths that nobody else drew anything like.

    Score = Frechet distance to the nearest other path. A path is an outlier when that
    distance exceeds both the same-route threshold and the robust cut
    median + 3 * 1.4826 * MAD of all scores. Because the score only looks at the
    nearest neighbour, any route that at least two people drew independently is kept
    (a legitimate minority opinion); a lone path far from everyone is flagged.
    """
    K = len(D)
    if K < min_paths:
        return np.zeros(K, dtype=bool), np.full(K, np.nan), None
    S = np.sort(D + np.diag(np.full(K, np.inf)), axis=1)[:, 0]
    cut = max(tau, _mad_cut(S))
    return S > cut, S, cut


def _corridor(mean_curve, member_curves):
    """Per-point lateral spread (std, diagonal units) of members around the mean path."""
    if len(member_curves) < 2:
        return np.zeros(len(mean_curve))
    t = np.gradient(mean_curve, axis=0)
    norm = np.linalg.norm(t, axis=1, keepdims=True)
    t = np.where(norm > EPS, t / np.maximum(norm, EPS), 0.0)
    nrm = np.column_stack([-t[:, 1], t[:, 0]])
    lateral = np.einsum("knd,nd->kn", member_curves - mean_curve[None], nrm)
    return lateral.std(axis=0)


def compile_route(curves, D, idx, method="mean"):
    """Combine the paths of one route into a single trajectory.

    1. Find the route's medoid (the member closest to all others).
    2. Trim members whose Frechet distance to the medoid is a robust outlier
       (> median + 3 * 1.4826 * MAD), when the route has 4+ members.
    3. method="mean":   point-wise average of the kept members, after arc-length
                        resampling (all paths share the robot's start point).
       method="medoid": the medoid path itself (a real drawn path).
    Returns curve, corridor (lateral std per point), kept indices, trimmed indices.
    """
    idx = np.asarray(idx)
    m = medoid(D, idx)
    kept, trimmed = idx, np.array([], dtype=int)
    if len(idx) >= 4:
        d = D[m, idx]
        cut = _mad_cut(d)
        if cut > 0:
            keep = d <= cut + 1e-12
            if keep.sum() >= 2:
                kept, trimmed = idx[keep], idx[~keep]
    curve = curves[kept].mean(axis=0) if method == "mean" else curves[m]
    return curve, _corridor(curve, curves[kept]), kept, trimmed, m


def _entropy(shares) -> float:
    p = np.asarray([s for s in shares if s > 0])
    return float(-(p * np.log2(p)).sum()) if len(p) else 0.0


def _jaccard_mean(sets) -> float | None:
    sets = [set(s or []) for s in sets]
    if len(sets) < 2:
        return None
    vals = []
    for a in range(len(sets)):
        for b in range(a + 1, len(sets)):
            u = sets[a] | sets[b]
            vals.append(1.0 if not u else len(sets[a] & sets[b]) / len(u))
    return float(np.mean(vals))


def _human_curve(h, W, H):
    return path_curve(h["points"], W, H, "waypoints", h.get("segments"), h.get("style") or "curve")


def candidate_curve(c, W, H):
    return path_curve(c["points"], W, H, c.get("kind") or "waypoints", c.get("segments"),
                      c.get("style") or "curve")


# ---------------------------------------------------------------------------
# Image-level analysis
# ---------------------------------------------------------------------------

def analyze_scene(scene: dict, humans: list[dict], candidates: list[dict],
                  params: dict | None = None) -> dict[str, Any]:
    """Agreement analysis for one training image.

    scene:      {"width", "height"}
    humans:     [{"id", "participant_id", "decision": "path"|"wait", "points", "segments", "norms"}]
    candidates: AI / baseline paths with coordinates
                [{"id", "model", "decision", "kind", "points", "segments", "confidence",
                  "consensus_override"}]
    """
    p = {**DEFAULTS, **(params or {})}
    tau = float(p["cluster_threshold"])
    W, H = float(scene["width"]), float(scene["height"])

    path_humans = [h for h in humans if h.get("decision") == "path" and h.get("points")]
    wait_humans = [h for h in humans if h.get("decision") == "wait"]
    curves_all = (np.stack([_human_curve(h, W, H) for h in path_humans])
                  if path_humans else np.zeros((0, N_SAMPLES, 2)))
    K_all = len(curves_all)
    D_all = pairwise_matrix(curves_all, "frechet")

    # ---- outliers
    is_out, knn_score, out_cut = knn_outliers(D_all, tau)
    use = ~is_out if p["exclude_outliers"] else np.ones(K_all, dtype=bool)
    inl = np.where(use)[0]
    curves = curves_all[inl]
    D = D_all[np.ix_(inl, inl)]
    K = len(inl)
    n_total = K + len(wait_humans)

    labels = cluster_paths(D, tau) if K else np.zeros(0, dtype=int)
    D_rmse = pairwise_matrix(curves, "rmse")

    # ---- choices: each route + "wait"
    modes = []
    for lab in (sorted(set(labels.tolist())) if K else []):
        idx = np.where(labels == lab)[0]
        mi = medoid(D, idx)
        modes.append({
            "mode": int(lab), "count": int(len(idx)),
            "share": len(idx) / n_total if n_total else 0.0,
            "medoid_id": path_humans[inl[mi]]["id"], "_idx": idx,
            "within_frechet": float(D[np.ix_(idx, idx)][np.triu_indices(len(idx), 1)].mean())
            if len(idx) > 1 else 0.0,
        })
    wait_share = len(wait_humans) / n_total if n_total else 0.0
    shares = [m["share"] for m in modes] + ([wait_share] if wait_humans else [])
    top_share = max(shares) if shares else 0.0
    top_choice = None
    if shares:
        top_choice = "wait" if wait_humans and wait_share >= (modes[0]["share"] if modes else 0) else "mode:0"

    # ---- compiled trajectory
    override = next((c for c in candidates if c.get("consensus_override")), None)
    compiled = None
    if override and override.get("decision") == "path" and override.get("points"):
        cc = candidate_curve(override, W, H)
        compiled = {"source": "override", "candidate_id": override["id"], "curve_d": cc}
    elif override and override.get("decision") == "wait":
        compiled = {"source": "override", "candidate_id": override["id"], "wait": True}
    elif top_choice == "wait":
        compiled = {"source": "majority_wait", "wait": True}
    elif modes:
        method = "medoid" if p["compiled_method"] == "medoid" else "mean"
        curve, spread, kept, trimmed, mi = compile_route(curves, D, modes[0]["_idx"], method)
        compiled = {"source": method, "curve_d": curve, "spread": spread,
                    "medoid_drawing_id": path_humans[inl[mi]]["id"],
                    "member_ids": [path_humans[inl[i]]["id"] for i in kept],
                    "trimmed_ids": [path_humans[inl[i]]["id"] for i in trimmed]}

    # ---- per-human rows
    sim = similarity(D, tau)
    loo = D.sum(axis=1) / (K - 1) if K > 1 else np.zeros(K)
    pos = {int(g): i for i, g in enumerate(inl)}
    trimmed_ids = set(compiled.get("trimmed_ids", [])) if compiled else set()
    human_rows = []
    for g, h in enumerate(path_humans):
        row = {"id": h["id"], "participant_id": h.get("participant_id"), "decision": "path",
               "outlier": bool(is_out[g]), "nn_distance": None if np.isnan(knn_score[g]) else float(knn_score[g]),
               "length": path_length(curves_all[g]), "curve": curve_to_normalized(curves_all[g], W, H),
               "trimmed": h["id"] in trimmed_ids}
        if g in pos:
            i = pos[g]
            row["mode"] = int(labels[i])
            row["loo_mean_frechet"] = float(loo[i]) if K > 1 else None
            row["mean_similarity"] = float((sim[i].sum() - 1.0) / (K - 1)) if K > 1 else None
        else:
            row["mode"] = None
            row["loo_mean_frechet"] = float(one_to_many(curves_all[g], curves).mean()) if K else None
        if compiled and "curve_d" in compiled:
            f = pair(curves_all[g], compiled["curve_d"], "frechet")
            row["frechet_to_compiled"] = f
            row["rmse_to_compiled"] = pair(curves_all[g], compiled["curve_d"], "rmse")
            row["similarity_to_compiled"] = float(similarity(f, tau))
        human_rows.append(row)
    for h in wait_humans:
        human_rows.append({"id": h["id"], "participant_id": h.get("participant_id"), "decision": "wait",
                           "mode": None, "outlier": False})

    iu = np.triu_indices(K, 1)
    summary = {
        "n_responses": n_total + int((~use).sum()),
        "n_counted": n_total,
        "n_paths": K_all, "n_wait": len(wait_humans),
        "n_outliers": int(is_out.sum()),
        "outlier_cut": out_cut,
        "wait_share": wait_share,
        "n_route_modes": len(modes),
        "n_substantial_choices": sum(1 for s in shares if s >= p["min_mode_share"]),
        "top_choice": top_choice, "top_choice_share": top_share,
        "choice_entropy_bits": _entropy(shares),
        "ambiguous": bool(n_total >= 3 and top_share < p["ambiguity_threshold"]),
        "enough_data": n_total >= 3,
        "mean_similarity": float(sim[iu].mean()) if K > 1 else None,
        "top_route_similarity": None,
        "mean_pairwise_frechet": float(D[iu].mean()) if K > 1 else None,
        "median_pairwise_frechet": float(np.median(D[iu])) if K > 1 else None,
        "mean_pairwise_rmse": float(D_rmse[iu].mean()) if K > 1 else None,
        "median_pairwise_rmse": float(np.median(D_rmse[iu])) if K > 1 else None,
        "mean_pairwise_mse": float((D_rmse[iu] ** 2).mean()) if K > 1 else None,
        "norm_jaccard": _jaccard_mean([h.get("norms") for h in humans]),
        "compiled_source": compiled["source"] if compiled else None,
        "compiled_n_members": len(compiled.get("member_ids", [])) if compiled else 0,
        "compiled_n_trimmed": len(compiled.get("trimmed_ids", [])) if compiled else 0,
    }
    if modes and modes[0]["count"] > 1:
        idx = modes[0]["_idx"]
        summary["top_route_similarity"] = float(sim[np.ix_(idx, idx)][np.triu_indices(len(idx), 1)].mean())

    norm_counts: dict[str, int] = {}
    for h in humans:
        for nm in h.get("norms") or []:
            norm_counts[nm] = norm_counts.get(nm, 0) + 1

    # ---- AI / baseline paths with coordinates
    cand_rows = []
    for c in candidates:
        row = {"id": c["id"], "model": c.get("model"), "source": c.get("source"), "decision": c.get("decision"),
               "confidence": c.get("confidence"), "consensus_override": bool(c.get("consensus_override"))}
        if c.get("decision") == "wait":
            row.update(mode="wait", choice_agreement=wait_share if n_total else None,
                       matches_top_choice=(top_choice == "wait") if n_total else None)
        elif c.get("points"):
            cc = candidate_curve(c, W, H)
            row["curve"] = curve_to_normalized(cc, W, H)
            if K:
                f_all = one_to_many(cc, curves, "frechet")
                row["mean_frechet_to_humans"] = float(f_all.mean())
                row["mean_similarity_to_humans"] = float(similarity(f_all, tau).mean())
                if K > 1:
                    row["human_percentile"] = float((loo >= f_all.mean()).mean())
                best_mode, best_d = None, np.inf
                for m in modes:
                    d = float(f_all[m["_idx"]].mean())
                    if d < best_d:
                        best_mode, best_d = m, d
                if best_mode is not None and best_d < tau:
                    row.update(mode=best_mode["mode"], choice_agreement=best_mode["share"],
                               matches_top_choice=top_choice == f"mode:{best_mode['mode']}")
                else:
                    row.update(mode=None, choice_agreement=0.0, matches_top_choice=False)
            elif n_total:
                row.update(mode=None, choice_agreement=0.0, matches_top_choice=False)
            if compiled and "curve_d" in compiled:
                f = pair(cc, compiled["curve_d"], "frechet")
                r = pair(cc, compiled["curve_d"], "rmse")
                row.update(frechet_to_compiled=f, similarity_to_compiled=float(similarity(f, tau)),
                           rmse_to_compiled=r, mse_to_compiled=r * r,
                           dtw_to_compiled=pair(cc, compiled["curve_d"], "dtw"))
        cand_rows.append(row)

    out_compiled = None
    if compiled:
        out_compiled = {k: v for k, v in compiled.items() if k not in ("curve_d", "spread")}
        if "curve_d" in compiled:
            cd = compiled["curve_d"]
            out_compiled["curve"] = curve_to_normalized(cd, W, H)
            spread = compiled.get("spread")
            if spread is not None and np.any(spread > 0):
                t = np.gradient(cd, axis=0)
                t = t / np.maximum(np.linalg.norm(t, axis=1, keepdims=True), EPS)
                nrm = np.column_stack([-t[:, 1], t[:, 0]])
                out_compiled["corridor_left"] = curve_to_normalized(cd + nrm * spread[:, None], W, H)
                out_compiled["corridor_right"] = curve_to_normalized(cd - nrm * spread[:, None], W, H)
                out_compiled["mean_spread"] = float(spread.mean())

    return {
        "params": p,
        "summary": summary,
        "modes": [{k: v for k, v in m.items() if k != "_idx"} for m in modes],
        "compiled": out_compiled,
        "humans": human_rows,
        "candidates": cand_rows,
        "norm_counts": norm_counts,
        "pairwise_ids": [path_humans[i]["id"] for i in inl],
        "pairwise_frechet": np.round(D, 5).tolist(),
        "pairwise_similarity": np.round(sim, 4).tolist(),
        "route_labels": labels.tolist(),
    }


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------

def _spearman(x, y) -> float | None:
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) < 3 or np.ptp(x) == 0 or np.ptp(y) == 0:
        return None

    def rank(v):
        r = np.empty(len(v))
        r[v.argsort()] = np.arange(len(v))
        for val in np.unique(v):
            m = v == val
            r[m] = r[m].mean()
        return r
    return float(np.corrcoef(rank(x), rank(y))[0, 1])


def boot_ci(vals, n_boot=2000, seed=0):
    vals = np.asarray([v for v in vals if v is not None], float)
    if len(vals) < 2:
        return None
    rng = np.random.default_rng(seed)
    means = rng.choice(vals, size=(n_boot, len(vals)), replace=True).mean(axis=1)
    return [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))]


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return float(np.mean(vals)) if vals else None


def krippendorff_alpha(units: list[list[float]], metric: str = "interval") -> float | None:
    """Krippendorff's alpha for inter-rater reliability.

    units: one list of ratings per rated item (any number of raters per item; items with
    fewer than 2 ratings are not pairable and are ignored). metric: "interval" or "nominal".
    """
    units = [[v for v in u if v is not None] for u in units]
    units = [u for u in units if len(u) >= 2]
    if not units:
        return None
    vals = np.concatenate([np.asarray(u, float) for u in units])
    n = len(vals)

    def pair_sum(x):  # sum over ordered pairs (c != k) of delta(x_c, x_k)
        x = np.asarray(x, float)
        m = len(x)
        if metric == "nominal":
            _, counts = np.unique(x, return_counts=True)
            return float(m * m - (counts ** 2).sum())
        return float(2 * (m * (x ** 2).sum() - x.sum() ** 2))

    d_o = sum(pair_sum(u) / (len(u) - 1) for u in units) / n
    d_e = pair_sum(vals) / (n * (n - 1))
    if d_e <= 0:
        return None
    return float(1.0 - d_o / d_e)


def answer_value(q: dict, v):
    """Stored answer -> number (yes/no -> 1/0, scale -> int) or None."""
    if v is None:
        return None
    if q.get("type") == "yesno":
        return 1.0 if v in ("yes", True, 1, "1") else 0.0 if v in ("no", False, 0, "0") else None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def rating_summary(rows: list[dict], questions: list[dict]) -> dict:
    """Summaries of questionnaire answers.

    rows: [{"item", "group", "answers": {key: value}}], one per (rater, rated image);
          group is the model name (or "Human consensus (control)").
    Returns {"items": {item: {...}}, "groups": {group: {...}}, "reliability": {key: alpha}}.
    Per group, every rated image counts once (the mean of its ratings), and the 95% CI is a
    bootstrap over images.
    """
    by_item: dict[str, dict] = {}
    for r in rows:
        it = by_item.setdefault(r["item"], {"group": r.get("group"), "values": {q["key"]: [] for q in questions},
                                            "n": 0})
        it["n"] += 1
        for q in questions:
            v = answer_value(q, (r.get("answers") or {}).get(q["key"]))
            if v is not None:
                it["values"][q["key"]].append(v)
    items = {}
    for item, it in by_item.items():
        items[item] = {"group": it["group"], "n": it["n"],
                       **{q["key"]: _mean(it["values"][q["key"]]) for q in questions}}
    groups: dict[str, dict] = {}
    for item, s in items.items():
        g = groups.setdefault(s["group"] or "unnamed", {"n_items": 0, "n_ratings": 0, "_vals": {q["key"]: [] for q in questions}})
        g["n_items"] += 1
        g["n_ratings"] += s["n"]
        for q in questions:
            if s[q["key"]] is not None:
                g["_vals"][q["key"]].append(s[q["key"]])
    for g in groups.values():
        vals = g.pop("_vals")
        for q in questions:
            g[q["key"]] = _mean(vals[q["key"]])
            g[q["key"] + "_ci"] = boot_ci(vals[q["key"]])
    reliability = {}
    for q in questions:
        reliability[q["key"]] = krippendorff_alpha(
            [it["values"][q["key"]] for it in by_item.values()],
            "nominal" if q.get("type") == "yesno" else "interval")
    return {"items": items, "groups": groups, "reliability": reliability}


def model_path_summary(scene_results: list[dict]) -> dict[str, dict]:
    """Per-model path agreement across images (only outputs that have coordinates)."""
    by_model: dict[str, list] = {}
    for sr in scene_results:
        a = sr["analysis"]
        s = a["summary"]
        if not s["enough_data"]:
            continue
        for c in a["candidates"]:
            if c.get("consensus_override") or (c.get("decision") == "path" and "curve" not in c):
                continue
            by_model.setdefault(c.get("model") or "unnamed", []).append((s, c))
    out = {}
    for model, items in by_model.items():
        agree = [c.get("choice_agreement") for _, c in items]
        fr = [c.get("frechet_to_compiled") for _, c in items]
        conf = [(c["confidence"], s["top_choice_share"]) for s, c in items if c.get("confidence") is not None]
        out[model] = {
            "n_paths": len(items),
            "choice_agreement": _mean(agree), "choice_agreement_ci": boot_ci(agree),
            "top_choice_match_rate": _mean([1.0 if c.get("matches_top_choice") else 0.0 for _, c in items
                                            if c.get("matches_top_choice") is not None]),
            "frechet_to_compiled": _mean(fr), "frechet_to_compiled_ci": boot_ci(fr),
            "similarity_to_compiled": _mean([c.get("similarity_to_compiled") for _, c in items]),
            "human_percentile": _mean([c.get("human_percentile") for _, c in items]),
            "agreement_on_ambiguous": _mean([c.get("choice_agreement") for s, c in items if s["ambiguous"]]),
            "agreement_on_clear": _mean([c.get("choice_agreement") for s, c in items if not s["ambiguous"]]),
            "confidence_vs_human_agreement_spearman":
                _spearman([a for a, _ in conf], [b for _, b in conf]) if conf else None,
        }
    return out
