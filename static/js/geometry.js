/* Path geometry: points joined by straight segments or cubic Bezier curves.
   Mirrors analysis.build_segments / sample_path in Python exactly, so the path a
   participant sees is the path that gets analysed. All coordinates here are pixels. */
(function (global) {
  "use strict";
  const EPS = 1e-9;
  const unit = (x, y) => { const n = Math.hypot(x, y); return n > EPS ? [x / n, y / n] : [0, 0]; };
  const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);

  /* Unit tangent at every point (used for automatic Bezier handles). */
  function tangents(P, types) {
    const n = P.length, T = [];
    for (let i = 0; i < n; i++) {
      if (n < 2) { T.push([0, 0]); continue; }
      if (i === 0) { T.push(unit(P[1][0] - P[0][0], P[1][1] - P[0][1])); continue; }
      if (i === n - 1) { T.push(unit(P[i][0] - P[i - 1][0], P[i][1] - P[i - 1][1])); continue; }
      const a = unit(P[i][0] - P[i - 1][0], P[i][1] - P[i - 1][1]);
      const b = unit(P[i + 1][0] - P[i][0], P[i + 1][1] - P[i][1]);
      if (types[i - 1] === "line" && types[i] === "curve") T.push(a);
      else if (types[i - 1] === "curve" && types[i] === "line") T.push(b);
      else {
        const s = [a[0] + b[0], a[1] + b[1]];
        T.push(Math.hypot(s[0], s[1]) > 1e-6 ? unit(s[0], s[1]) : b);
      }
    }
    return T;
  }

  /* specs[j] = {type: "line"|"curve", c1?, c2?} -> [{type, c1, c2}] with every control point filled in. */
  function buildSegments(P, specs, style = "curve") {
    const n = P.length;
    if (n < 2) return [];
    specs = specs || [];
    const types = [];
    for (let j = 0; j < n - 1; j++) types.push(((specs[j] && specs[j].type) || style) === "line" ? "line" : "curve");
    const T = tangents(P, types), out = [];
    for (let j = 0; j < n - 1; j++) {
      const a = P[j], b = P[j + 1];
      if (types[j] === "line") { out.push({ type: "line" }); continue; }
      const s = specs[j] || {};
      const L = dist(a, b) / 3;
      out.push({ type: "curve",
        c1: s.c1 ? [s.c1[0], s.c1[1]] : [a[0] + T[j][0] * L, a[1] + T[j][1] * L],
        c2: s.c2 ? [s.c2[0], s.c2[1]] : [b[0] - T[j + 1][0] * L, b[1] - T[j + 1][1] * L] });
    }
    return out;
  }

  function bezier(a, c1, c2, b, t) {
    const u = 1 - t;
    return [u * u * u * a[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t * t * t * b[0],
            u * u * u * a[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t * t * t * b[1]];
  }

  function segmentPoint(P, segs, j, t) {
    const s = segs[j], a = P[j], b = P[j + 1];
    return s.type === "line" ? [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t] : bezier(a, s.c1, s.c2, b, t);
  }

  function sample(P, segs, perCurve = 24) {
    if (!P.length) return [];
    const out = [P[0]];
    segs.forEach((s, j) => {
      if (s.type === "line") out.push(P[j + 1]);
      else for (let k = 1; k <= perCurve; k++) out.push(bezier(P[j], s.c1, s.c2, P[j + 1], k / perCurve));
    });
    return out;
  }

  const f = (v) => v.toFixed(1);
  function toPathD(P, segs) {
    if (P.length < 2) return "";
    let d = `M${f(P[0][0])},${f(P[0][1])}`;
    segs.forEach((s, j) => {
      const b = P[j + 1];
      d += s.type === "line" ? `L${f(b[0])},${f(b[1])}` : `C${f(s.c1[0])},${f(s.c1[1])} ${f(s.c2[0])},${f(s.c2[1])} ${f(b[0])},${f(b[1])}`;
    });
    return d;
  }

  function polylineD(pts) {
    if (!pts.length) return "";
    let d = `M${f(pts[0][0])},${f(pts[0][1])}`;
    for (let i = 1; i < pts.length; i++) d += `L${f(pts[i][0])},${f(pts[i][1])}`;
    return d;
  }

  /* Points every `spacing` along a polyline, with heading (for direction chevrons). */
  function along(pts, spacing, start = spacing / 2) {
    const out = [];
    let cum = 0, next = start;
    for (let i = 1; i < pts.length; i++) {
      const a = pts[i - 1], b = pts[i], seg = dist(a, b);
      if (seg < EPS) continue;
      while (next <= cum + seg) {
        const t = (next - cum) / seg;
        out.push({ x: a[0] + (b[0] - a[0]) * t, y: a[1] + (b[1] - a[1]) * t, angle: Math.atan2(b[1] - a[1], b[0] - a[0]) });
        next += spacing;
      }
      cum += seg;
    }
    return out;
  }

  function pathLength(pts) { let L = 0; for (let i = 1; i < pts.length; i++) L += dist(pts[i - 1], pts[i]); return L; }

  global.Geometry = { buildSegments, sample, toPathD, polylineD, along, pathLength, dist, bezier, segmentPoint };
})(window);
