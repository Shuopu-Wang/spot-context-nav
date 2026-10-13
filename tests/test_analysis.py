"""Unit tests for the analysis engine. Run: python tests/test_analysis.py  (or pytest)"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import analysis as A  # noqa: E402

W, H = 1600, 900
DIAG = math.hypot(W, H)


def test_curve_passes_through_points():
    P = [[100, 800], [300, 500], [700, 450], [900, 100]]
    segs = A.build_segments(P, None, "curve")
    dense = A.sample_path(P, segs)
    for p in P:
        assert np.min(np.linalg.norm(dense - np.array(p), axis=1)) < 1e-6


def test_straight_segments_are_polyline():
    P = [[0, 0], [100, 0], [100, 100]]
    dense = A.sample_path(P, A.build_segments(P, None, "line"))
    assert np.allclose(dense, P)


def test_two_point_curve_is_straight():
    P = [[0, 0], [300, 300]]
    dense = A.sample_path(P, A.build_segments(P, None, "curve"))
    assert np.allclose(dense[:, 0], dense[:, 1])


def test_explicit_bezier_controls_are_used():
    # normalized control points are honoured exactly
    pts = [[0.5, 1.0], [0.5, 0.0]]
    segs = [{"type": "curve", "c1": [0.9, 0.7], "c2": [0.9, 0.3]}]
    c = A.path_curve(pts, W, H, segments=segs)
    mid = c[len(c) // 2] * DIAG / np.array([W, H])
    assert mid[0] > 0.75  # bulges right


def test_mixed_line_then_curve_is_smooth_at_join():
    P = [[0, 0], [100, 0], [200, 100]]
    segs = A.build_segments(P, [{"type": "line"}, {"type": "curve"}])
    c1 = np.array(segs[1]["c1"])
    assert abs(c1[1]) < 1e-9 and c1[0] > 100  # leaves the join along the straight segment


def test_resample_uniform_spacing():
    r = A.resample(np.array([[0, 0], [10, 0], [10, 0], [10, 30]], float), 41)
    assert np.allclose(np.linalg.norm(np.diff(r, axis=0), axis=1), 1.0)


def test_parallel_offset_lines_and_similarity():
    a = A.path_curve([[0.4, 1.0], [0.4, 0.0]], W, H, kind="polyline")
    b = A.path_curve([[0.5, 1.0], [0.5, 0.0]], W, H, kind="polyline")
    expect = 160 / DIAG
    for m in ("frechet", "rmse", "dtw"):
        assert abs(A.pair(a, b, m) - expect) < 1e-9
    assert abs(float(A.similarity(0.1, 0.1)) - 0.5) < 1e-12
    assert float(A.similarity(0.0, 0.1)) == 1.0


def test_frechet_respects_direction_and_excursions():
    a = A.path_curve([[0.2, 0.9], [0.8, 0.9]], W, H, kind="polyline")
    assert A.pair(a, a[::-1]) > 0.3
    base = A.path_curve([[0.5, 1.0], [0.5, 0.0]], W, H, kind="polyline")
    det = A.path_curve([[0.5, 1.0], [0.5, 0.55], [0.75, 0.5], [0.5, 0.45], [0.5, 0.0]], W, H, kind="polyline")
    assert A.pair(base, det, "frechet") > 2 * A.pair(base, det, "rmse")


def _h(i, pts, decision="path", norms=None, style="curve"):
    return {"id": f"h{i}", "participant_id": f"p{i}", "decision": decision, "points": pts,
            "norms": norms or [], "style": style}


def _left(j=0.0):
    return [[0.5, 1.0], [0.25 + j, 0.55 + j], [0.5, 0.1]]


def _right(j=0.0):
    return [[0.5, 1.0], [0.75 + j, 0.55 - j], [0.5, 0.1]]


def test_routes_compiled_mean_and_outlier():
    humans = [_h(i, _left(0.01 * (i % 3))) for i in range(6)]
    humans += [_h(10 + i, _right(0.01 * (i % 2))) for i in range(3)]
    humans += [_h(20, None, "wait")]
    humans += [_h(30, [[0.5, 1.0], [0.98, 0.05], [0.02, 0.05], [0.5, 0.1]])]  # scribble
    cands = [
        {"id": "ai_left", "model": "vlm", "decision": "path", "kind": "waypoints",
         "points": [[0.5, 1.0], [0.27, 0.56], [0.5, 0.1]], "confidence": 0.8},
        {"id": "ai_mid", "model": "straight", "decision": "path", "kind": "polyline",
         "points": [[0.5, 1.0], [0.5, 0.1]]},
        {"id": "ai_wait", "model": "waiter", "decision": "wait"},
    ]
    r = A.analyze_scene({"width": W, "height": H}, humans, cands)
    s = r["summary"]
    rows = {h["id"]: h for h in r["humans"]}
    assert rows["h30"]["outlier"] and s["n_outliers"] == 1
    assert sum(h["outlier"] for h in r["humans"]) == 1          # minority route is kept
    assert s["n_route_modes"] == 2 and s["n_counted"] == 10
    assert abs(s["top_choice_share"] - 0.6) < 1e-9
    comp = r["compiled"]
    assert comp["source"] == "mean" and len(comp["member_ids"]) >= 5
    mid = comp["curve"][len(comp["curve"]) // 2]
    assert mid[0] < 0.35                                        # the average stays on the left route
    assert "corridor_left" in comp
    c = {row["id"]: row for row in r["candidates"]}
    assert c["ai_left"]["mode"] == 0 and c["ai_left"]["matches_top_choice"]
    assert c["ai_mid"]["mode"] is None and c["ai_mid"]["choice_agreement"] == 0.0
    assert abs(c["ai_wait"]["choice_agreement"] - 0.1) < 1e-9
    assert c["ai_left"]["similarity_to_compiled"] > 0.8


def test_two_person_minority_route_is_not_an_outlier():
    humans = [_h(i, _left(0.01 * (i % 3))) for i in range(12)]
    humans += [_h(20, _right()), _h(21, _right(0.01))]
    r = A.analyze_scene({"width": W, "height": H}, humans, [])
    assert r["summary"]["n_outliers"] == 0 and r["summary"]["n_route_modes"] == 2


def test_trimming_inside_route():
    humans = [_h(i, _left(0.004 * i)) for i in range(7)]
    humans.append(_h(9, [[0.5, 1.0], [0.20, 0.62], [0.33, 0.30], [0.5, 0.1]]))  # same route, sloppy
    r = A.analyze_scene({"width": W, "height": H}, humans, [], {"cluster_threshold": 0.2})
    assert "h9" in r["compiled"]["trimmed_ids"]


def test_medoid_method():
    humans = [_h(i, _left(0.01 * i)) for i in range(4)]
    r = A.analyze_scene({"width": W, "height": H}, humans, [], {"compiled_method": "medoid"})
    assert r["compiled"]["source"] == "medoid"


def test_ambiguity_flag():
    humans = [_h(i, _left()) for i in range(3)] + [_h(9 + i, _right()) for i in range(3)]
    assert A.analyze_scene({"width": W, "height": H}, humans, [])["summary"]["ambiguous"]


def test_krippendorff_alpha():
    perfect = [[1, 1, 1], [5, 5], [3, 3, 3]]
    assert abs(A.krippendorff_alpha(perfect) - 1.0) < 1e-12
    # textbook example (Krippendorff 2011, interval data, 4 coders) -> about 0.849
    data = [[1, 1, 1], [2, 2, 3, 2], [3, 3, 3, 3], [3, 3, 3, 3], [2, 2, 2, 2], [1, 2, 3, 4],
            [4, 4, 4, 4], [1, 1, 2, 1], [2, 2, 2, 2], [5, 5, 5], [1, 1], [3]]
    assert abs(A.krippendorff_alpha(data, "interval") - 0.849) < 0.01
    assert A.krippendorff_alpha([[1, 0], [1, 1], [0, 0]], "nominal") is not None


def test_rating_summary():
    qs = [{"key": "acceptable", "type": "yesno"}, {"key": "safe", "type": "scale", "points": 7}]
    rows = [{"item": "a", "group": "m1", "answers": {"acceptable": "yes", "safe": 6}},
            {"item": "a", "group": "m1", "answers": {"acceptable": "no", "safe": 4}},
            {"item": "b", "group": "m1", "answers": {"acceptable": "yes", "safe": 7}}]
    s = A.rating_summary(rows, qs)
    assert s["items"]["a"]["acceptable"] == 0.5 and s["items"]["a"]["safe"] == 5
    assert s["groups"]["m1"]["acceptable"] == 0.75 and s["groups"]["m1"]["n_items"] == 2


def test_ranking_summary_and_kendall_w():
    ids = ["a", "b", "c"]
    assert A.kendall_w([["a", "b", "c"]] * 4, ids) == 1.0
    rows = [{"ranking": ["a", "b", "c"], "unacceptable": ["c"]},
            {"ranking": ["b", "a", "c"], "unacceptable": ["c"]},
            {"ranking": ["a", "c", "b"], "unacceptable": []}]
    s = A.ranking_summary(ids, rows)
    assert s["order"][0] == "a" and s["best"] == "a" and s["n_complete"] == 3
    assert abs(s["paths"]["c"]["unacceptable_share"] - 2 / 3) < 1e-12
    # best skips a top-scored path that most people called unacceptable
    s = A.ranking_summary(ids, [{"ranking": ["a", "b", "c"], "unacceptable": ["a"]}] * 3)
    assert s["top_overall"] == "a" and s["best"] == "b"
    # a ranking made before path c existed still counts for a and b
    s = A.ranking_summary(ids, [{"ranking": ["b", "a"], "unacceptable": []}])
    assert s["paths"]["b"]["score"] == 1.0 and s["paths"]["c"]["n"] == 0 and s["n_complete"] == 0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
