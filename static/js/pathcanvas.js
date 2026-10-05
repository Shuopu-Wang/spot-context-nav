/* PathCanvas: an image with an SVG overlay for drawing a path and showing other paths.

   Drawing model: points (robot start, the points the person places, optional goal flag)
   joined by segments. Each segment is a straight line or a cubic Bezier curve. Curves get
   smooth automatic handles; the person can bend a segment by dragging its middle knob, or
   select it and drag its two Bezier handles.

   Coordinates handed in and out are normalized image coords ([0,1] x [0,1], origin top-left). */
(function (global) {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const PATH_COLOR = "#ff2d87";
  const G = global.Geometry;

  function el(tag, attrs = {}, parent) {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (parent) parent.appendChild(e);
    return e;
  }
  const clone = (o) => JSON.parse(JSON.stringify(o));

  class PathCanvas {
    constructor(container, opts) {
      this.o = Object.assign({ editable: false, anchorEditable: false, maxHeight: "70vh", lineStyle: "curve",
                               pathColor: PATH_COLOR, onChange: () => {}, onAnchors: () => {} }, opts);
      this.W = opts.width; this.H = opts.height;
      this.start = opts.start ? this._px(opts.start) : null;
      this.goal = opts.goal ? this._px(opts.goal) : null;
      this.wps = [];
      this.segs = [];            // [{type, h1, h2}] h = manual handle offset from its end point (px) or null
      this.style = this.o.lineStyle === "line" ? "line" : "curve";
      this.history = [];
      this.sel = null;           // {kind: "pt", i} | {kind: "seg", j}
      this.edits = 0;
      this.straight = false;     // accepted the direct start -> goal path
      this.waiting = false;
      this.enabled = true;
      this.showUser = !!this.o.editable;
      this.overlays = [];
      this.highlightId = null;
      this.anchorTarget = "goal";
      this._lastTap = { idx: -1, t: 0 };
      this._syncSegs();

      const wrap = document.createElement("div");
      wrap.className = "pc-wrap";
      wrap.style.aspectRatio = `${this.W} / ${this.H}`;
      wrap.style.width = `min(100%, calc(${this.o.maxHeight} * ${this.W / this.H}))`;
      const img = document.createElement("img");
      img.className = "pc-img";
      img.alt = opts.alt || "Scene from the robot's point of view";
      img.draggable = false;
      img.src = opts.imageUrl;
      wrap.appendChild(img);
      const svg = el("svg", { class: "pc-svg", viewBox: `0 0 ${this.W} ${this.H}`, preserveAspectRatio: "none" });
      if (this.o.editable) svg.setAttribute("aria-label", "Drawing area: tap to add a point");
      this.gOverlay = el("g", {}, svg);
      this.gUser = el("g", {}, svg);
      this.gAnchors = el("g", {}, svg);
      this.gHandles = el("g", {}, svg);
      wrap.appendChild(svg);
      this.badge = document.createElement("div");
      this.badge.className = "pc-badge";
      this.badge.hidden = true;
      wrap.appendChild(this.badge);
      container.appendChild(wrap);
      this.wrap = wrap; this.img = img; this.svg = svg;
      if (this.o.editable || this.o.anchorEditable) wrap.classList.add("is-editable");

      this.ready = new Promise((res) => {
        if (img.complete && img.naturalWidth) res();
        else { img.onload = () => res(); img.onerror = () => res(); }
      });
      this._bind();
      this._ro = new ResizeObserver(() => this.render());
      this._ro.observe(wrap);
      this.render();
    }

    // ---- coordinates
    _px(n) { return [n[0] * this.W, n[1] * this.H]; }
    _norm(p) { return [+(p[0] / this.W).toFixed(5), +(p[1] / this.H).toFixed(5)]; }
    _k() { const w = this.svg.clientWidth || this.wrap.clientWidth || this.W; return this.W / w; }
    _evtPt(e) {
      const r = this.svg.getBoundingClientRect();
      const x = (e.clientX - r.left) / r.width * this.W, y = (e.clientY - r.top) / r.height * this.H;
      return [Math.min(Math.max(x, 0), this.W), Math.min(Math.max(y, 0), this.H)];
    }
    _off() { return this.start ? 1 : 0; }
    controls() {
      const c = [];
      if (this.start) c.push(this.start);
      c.push(...this.wps);
      if (this.goal) c.push(this.goal);
      return c;
    }
    _syncSegs() {
      const n = Math.max(0, this.controls().length - 1);
      while (this.segs.length < n) this.segs.push({ type: this.style, h1: null, h2: null });
      this.segs.length = n;
    }
    _specs(C) {
      return this.segs.map((s, j) => ({ type: s.type,
        c1: s.h1 ? [C[j][0] + s.h1[0], C[j][1] + s.h1[1]] : null,
        c2: s.h2 ? [C[j + 1][0] + s.h2[0], C[j + 1][1] + s.h2[1]] : null }));
    }
    _built() { const C = this.controls(); return { C, segs: G.buildSegments(C, this._specs(C), this.style) }; }
    _drawn() { return this.wps.length > 0 || this.straight; }

    // ---- public API
    hasPath() { return !this.waiting && this.controls().length >= 2 && this._drawn(); }
    getPath() {
      const { C, segs } = this._built();
      const types = new Set(segs.map((s) => s.type));
      return {
        points: C.map((p) => this._norm(p)),
        segments: segs.map((s, j) => s.type === "line" ? { type: "line" }
          : { type: "curve", c1: this._norm(s.c1), c2: this._norm(s.c2), manual: !!(this.segs[j].h1 || this.segs[j].h2) }),
        style: types.size > 1 ? "mixed" : (types.has("line") ? "line" : "curve"),
      };
    }
    setPath(points, segments) {   // load an existing path (normalized); first/last points may be the anchors
      let pts = (points || []).map((p) => this._px(p));
      if (this.start && pts.length && G.dist(pts[0], this.start) < 1e-3 * this.W) pts = pts.slice(1);
      if (this.goal && pts.length && G.dist(pts[pts.length - 1], this.goal) < 1e-3 * this.W) pts = pts.slice(0, -1);
      this.wps = pts;
      this.straight = !pts.length && !!points && points.length >= 2;
      this.segs = [];
      this._syncSegs();
      const C = this.controls();
      (segments || []).forEach((s, j) => {
        if (!this.segs[j]) return;
        this.segs[j].type = s.type === "line" ? "line" : "curve";
        if (s.type === "curve" && s.manual && s.c1 && s.c2) {
          const c1 = this._px(s.c1), c2 = this._px(s.c2);
          this.segs[j].h1 = [c1[0] - C[j][0], c1[1] - C[j][1]];
          this.segs[j].h2 = [c2[0] - C[j + 1][0], c2[1] - C[j + 1][1]];
        }
      });
      this.history = []; this.sel = null;
      this.render();
    }
    setStyle(type) {               // straight lines / curves for every segment and new ones
      this._push();
      this.style = type === "line" ? "line" : "curve";
      this.segs.forEach((s) => { s.type = this.style; });
      this.edits++; this._changed();
    }
    setSegType(j, type) {
      if (!this.segs[j]) return;
      this._push(); this.segs[j].type = type === "line" ? "line" : "curve"; this.edits++; this._changed();
    }
    resetSeg(j) {
      if (!this.segs[j]) return;
      this._push(); this.segs[j].h1 = null; this.segs[j].h2 = null; this.edits++; this._changed();
    }
    setStraight(v) {
      this._push(); this.straight = v; this.wps = []; this.segs = []; this._syncSegs(); this.sel = null; this.edits++;
      this._changed();
    }
    setWaiting(v) { this.waiting = v; this.sel = null; this.render(); this.o.onChange(this); }
    setEnabled(v) { this.enabled = v; this.wrap.classList.toggle("is-disabled", !v); }
    canUndo() { return this.history.length > 0; }
    undo() {
      if (!this.history.length) return;
      const h = this.history.pop();
      Object.assign(this, { wps: h.wps, segs: h.segs, straight: h.straight, style: h.style, sel: null });
      this.edits++; this.render(); this.o.onChange(this);
    }
    clear() {
      if (!this.wps.length && !this.straight) return;
      this._push(); this.wps = []; this.straight = false; this.segs = []; this._syncSegs(); this.sel = null;
      this.edits++; this._changed();
    }
    removeSelected() {
      if (!this.sel || this.sel.kind !== "pt") return;
      this._removePoint(this.sel.i);
    }
    deselect() { if (this.sel) { this.sel = null; this.render(); this.o.onChange(this); } }
    setOverlays(list) { this.overlays = list || []; this.render(); }
    highlight(id) { this.highlightId = id; this.render(); }
    setAnchors(start, goal) {
      this.start = start ? this._px(start) : null;
      this.goal = goal ? this._px(goal) : null;
      this._syncSegs(); this.render();
    }
    showBadge(text) { this.badge.textContent = text || ""; this.badge.hidden = !text; }
    destroy() { this._ro.disconnect(); window.removeEventListener("keydown", this._onKey); this.wrap.remove(); }

    _push() {
      this.history.push({ wps: clone(this.wps), segs: clone(this.segs), straight: this.straight, style: this.style });
      if (this.history.length > 100) this.history.shift();
    }
    _changed() { this.render(); this.o.onChange(this); }

    // ---- editing operations
    _addPoint(p) {
      this._push();
      const C = this.controls();
      const off = this._off();
      let at = this.wps.length;
      if (this.goal && C.length >= 2) {
        let best = Infinity;   // cheapest insertion: where the route grows least
        for (let i = 0; i < C.length - 1; i++) {
          const cost = G.dist(C[i], p) + G.dist(p, C[i + 1]) - G.dist(C[i], C[i + 1]);
          if (cost < best) { best = cost; at = i + 1 - off; }
        }
      } else if (C.length >= 2) {  // no goal: extend, unless the tap lands right on the path
        const { segs } = this._built();
        let bd = Infinity, bj = -1;
        segs.forEach((_, j) => { for (let k = 0; k <= 16; k++) {
          const q = G.segmentPoint(C, segs, j, k / 16), d = G.dist(q, p);
          if (d < bd) { bd = d; bj = j; } } });
        if (bd < 14 * this._k()) at = bj + 1 - off;
      }
      at = Math.max(0, Math.min(at, this.wps.length));
      const ci = at + off;                      // control index of the new point
      const j = ci - 1;                         // segment being split (if any)
      if (j >= 0 && j < this.segs.length && (this.goal || at < this.wps.length)) {
        const t = this.segs[j].type;
        this.segs.splice(j, 1, { type: t, h1: null, h2: null }, { type: t, h1: null, h2: null });
      } else if (ci > 0) {
        this.segs.splice(ci - 1, 0, { type: this.style, h1: null, h2: null });
      } else if (this.controls().length >= 1) {
        this.segs.unshift({ type: this.style, h1: null, h2: null });
      }
      this.wps.splice(at, 0, p);
      this.straight = false; this.sel = null; this.edits++;
      this._syncSegs(); this._changed();
    }
    _removePoint(i) {
      this._push();
      const ci = i + this._off();
      const j1 = ci - 1, j2 = ci;
      const has1 = j1 >= 0 && j1 < this.segs.length, has2 = j2 < this.segs.length;
      if (has1 && has2) { this.segs.splice(j1, 2, { type: this.segs[j1].type, h1: null, h2: null }); }
      else if (has1) this.segs.splice(j1, 1);
      else if (has2) this.segs.splice(j2, 1);
      this.wps.splice(i, 1);
      this.sel = null; this.edits++;
      this._syncSegs(); this._changed();
    }
    _bend(j, M) {                 // move the segment's midpoint to M by shifting both handles equally
      const { C, segs } = this._built();
      const s = segs[j];
      const a = C[j], b = C[j + 1];
      let c1, c2;
      if (s.type === "line") { c1 = [a[0] + (b[0] - a[0]) / 3, a[1] + (b[1] - a[1]) / 3]; c2 = [a[0] + 2 * (b[0] - a[0]) / 3, a[1] + 2 * (b[1] - a[1]) / 3]; }
      else { c1 = s.c1; c2 = s.c2; }
      const B = G.bezier(a, c1, c2, b, 0.5);
      const d = [(M[0] - B[0]) / 0.75, (M[1] - B[1]) / 0.75];
      this.segs[j] = { type: "curve", h1: [c1[0] + d[0] - a[0], c1[1] + d[1] - a[1]], h2: [c2[0] + d[0] - b[0], c2[1] + d[1] - b[1]] };
    }

    // ---- events
    _bind() {
      const svg = this.svg;
      svg.addEventListener("pointerdown", (e) => {
        if (!this.enabled || (e.button !== undefined && e.button > 0)) return;
        const h = e.target.closest("[data-h]"), kn = e.target.closest("[data-k]"), cp = e.target.closest("[data-c]");
        const a = e.target.closest("[data-anchor]");
        const snap = () => ({ wps: clone(this.wps), segs: clone(this.segs), straight: this.straight, style: this.style });
        if (this.o.anchorEditable && a) this.drag = { anchor: a.dataset.anchor, x: e.clientX, y: e.clientY, moved: false };
        else if (this.o.editable && !this.waiting && cp) this.drag = { ctrl: cp.dataset.c, j: +cp.dataset.j, x: e.clientX, y: e.clientY, moved: false, before: snap() };
        else if (this.o.editable && !this.waiting && kn) this.drag = { knob: +kn.dataset.k, x: e.clientX, y: e.clientY, moved: false, before: snap() };
        else if (this.o.editable && !this.waiting && h) this.drag = { idx: +h.dataset.h, x: e.clientX, y: e.clientY, moved: false, before: snap() };
        else if (this.o.editable || this.o.anchorEditable) this.tap = { x: e.clientX, y: e.clientY };
        else return;
        svg.setPointerCapture(e.pointerId);
        e.preventDefault();
      });
      svg.addEventListener("pointermove", (e) => {
        const d = this.drag;
        if (!d) return;
        if (!d.moved && Math.hypot(e.clientX - d.x, e.clientY - d.y) < 4) return;
        d.moved = true;
        const p = this._evtPt(e);
        if (d.anchor) this[d.anchor] = p;
        else if (d.ctrl) {
          const C = this.controls(), s = this.segs[d.j];
          s.type = "curve";
          if (!s.h1 || !s.h2) {   // freeze the automatic handles before editing one of them
            const built = G.buildSegments(C, this._specs(C), this.style)[d.j];
            s.h1 = [built.c1[0] - C[d.j][0], built.c1[1] - C[d.j][1]];
            s.h2 = [built.c2[0] - C[d.j + 1][0], built.c2[1] - C[d.j + 1][1]];
          }
          if (d.ctrl === "1") s.h1 = [p[0] - C[d.j][0], p[1] - C[d.j][1]];
          else s.h2 = [p[0] - C[d.j + 1][0], p[1] - C[d.j + 1][1]];
        } else if (d.knob !== undefined) { this._bend(d.knob, p); this.sel = { kind: "seg", j: d.knob }; }
        else this.wps[d.idx] = p;
        this.render();
      });
      const end = (e) => {
        const d = this.drag;
        if (d) {
          this.drag = null;
          if (d.anchor) { if (d.moved) this._anchorsChanged(); return; }
          if (d.moved) { this.history.push(d.before); this.edits++; this.render(); this.o.onChange(this); return; }
          if (d.ctrl) return;
          if (d.knob !== undefined) {      // tap on a segment knob = select that segment
            this.sel = this.sel && this.sel.kind === "seg" && this.sel.j === d.knob ? null : { kind: "seg", j: d.knob };
            this.render(); this.o.onChange(this); return;
          }
          const t = performance.now();
          if (this._lastTap.idx === d.idx && t - this._lastTap.t < 400) {   // double tap = remove point
            this._lastTap = { idx: -1, t: 0 }; this._removePoint(d.idx); return;
          }
          this._lastTap = { idx: d.idx, t };
          this.sel = this.sel && this.sel.kind === "pt" && this.sel.i === d.idx ? null : { kind: "pt", i: d.idx };
          this.render(); this.o.onChange(this);
          return;
        }
        if (this.tap) {
          const moved = Math.hypot(e.clientX - this.tap.x, e.clientY - this.tap.y);
          this.tap = null;
          if (moved > 8 || e.type === "pointercancel") return;
          const p = this._evtPt(e);
          if (this.o.anchorEditable) { this[this.anchorTarget] = p; this._anchorsChanged(); return; }
          if (this.waiting) return;
          if (this.sel) { this.deselect(); return; }   // first tap away just deselects
          this._addPoint(p);
        }
      };
      svg.addEventListener("pointerup", end);
      svg.addEventListener("pointercancel", end);
      svg.addEventListener("contextmenu", (e) => {
        const h = e.target.closest("[data-h]");
        if (this.o.editable && h) { e.preventDefault(); this._removePoint(+h.dataset.h); }
      });
      this._onKey = (e) => {
        if (!this.o.editable || !this.enabled || !document.body.contains(this.wrap)) return;
        const tag = (document.activeElement && document.activeElement.tagName) || "";
        if (/INPUT|TEXTAREA|SELECT/.test(tag)) return;
        if ((e.key === "Delete" || e.key === "Backspace") && this.sel && this.sel.kind === "pt") { e.preventDefault(); this.removeSelected(); }
        else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); this.undo(); }
        else if (e.key === "Escape") this.deselect();
      };
      window.addEventListener("keydown", this._onKey);
    }
    _anchorsChanged() {
      this._syncSegs(); this.render();
      this.o.onAnchors({ start: this.start ? this._norm(this.start) : null, goal: this.goal ? this._norm(this.goal) : null });
    }

    // ---- rendering
    render() {
      const k = this._k();
      for (const gr of [this.gOverlay, this.gUser, this.gAnchors, this.gHandles]) gr.replaceChildren();
      this.wrap.classList.toggle("is-waiting", this.waiting);
      this._renderOverlays(k);

      const editing = this.o.editable && !this.waiting;
      let knobs = null;
      if (this.showUser && !this.waiting) {
        const { C, segs } = this._built();
        if (C.length >= 2 && this._drawn()) {
          const d = G.toPathD(C, segs);
          el("path", { d, class: "pc-halo", "vector-effect": "non-scaling-stroke" }, this.gUser);
          el("path", { d, class: "pc-path", stroke: this.o.pathColor, "vector-effect": "non-scaling-stroke" }, this.gUser);
          this._arrows(this.gUser, G.sample(C, segs), k, 72);
          if (editing) knobs = [C, segs];
        } else if (C.length >= 2 && this.goal) {
          el("path", { d: G.toPathD([C[0], C[C.length - 1]], [{ type: "line" }]), class: "pc-preview",
                       "vector-effect": "non-scaling-stroke" }, this.gUser);
        }
      }
      if (this.start) this._robot(this.start, k);
      if (this.goal) this._flag(this.goal, k);
      if (this.showUser && editing) {
        this.wps.forEach((p, i) => {
          const sel = this.sel && this.sel.kind === "pt" && this.sel.i === i;
          const g = el("g", { "data-h": i, class: "pc-handle" + (sel ? " is-selected" : ""),
                              transform: `translate(${p[0]},${p[1]}) scale(${k})` }, this.gHandles);
          el("circle", { r: 20, fill: "transparent" }, g);
          el("circle", { r: 8, class: "pc-dot" }, g);
        });
      }
      if (knobs) this._renderKnobs(knobs[0], knobs[1], k);   // on top, so short segments stay bendable
    }

    _renderKnobs(C, segs, k) {
      const selJ = this.sel && this.sel.kind === "seg" ? this.sel.j : -1;
      if (selJ >= 0 && segs[selJ] && segs[selJ].type === "curve") {   // Bezier handles of the selected segment
        const s = segs[selJ];
        [[C[selJ], s.c1, "1"], [C[selJ + 1], s.c2, "2"]].forEach(([a, c, which]) => {
          el("line", { x1: a[0], y1: a[1], x2: c[0], y2: c[1], class: "pc-ctrl-line", "vector-effect": "non-scaling-stroke" }, this.gUser);
          const g = el("g", { "data-c": which, "data-j": selJ, class: "pc-ctrl", transform: `translate(${c[0]},${c[1]}) scale(${k})` }, this.gHandles);
          el("circle", { r: 18, fill: "transparent" }, g);
          el("rect", { x: -6, y: -6, width: 12, height: 12, rx: 2 }, g);
        });
      }
      segs.forEach((_, j) => {
        if (G.dist(C[j], C[j + 1]) / k < 30 && j !== selJ) return;   // too short to hold a knob on screen
        const m = G.segmentPoint(C, segs, j, 0.5);
        const g = el("g", { "data-k": j, class: "pc-knob" + (j === selJ ? " is-selected" : ""),
                            transform: `translate(${m[0]},${m[1]}) scale(${k})` }, this.gHandles);
        el("circle", { r: 12, fill: "transparent" }, g);
        el("rect", { x: -5, y: -5, width: 10, height: 10, transform: "rotate(45)" }, g);
      });
    }

    _renderOverlays(k) {
      const hasHi = this.highlightId != null;
      this.overlays.forEach((ov, i) => {
        const on = !hasHi || ov.id === this.highlightId;
        const g = el("g", { opacity: on ? (ov.opacity ?? 1) : 0.15 }, this.gOverlay);
        if (ov.type === "band") {
          const pts = [...ov.left.map((p) => this._px(p)), ...ov.right.slice().reverse().map((p) => this._px(p))];
          el("path", { d: G.polylineD(pts) + "Z", fill: ov.fill || "rgba(29,36,51,.25)", stroke: "none" }, g);
          return;
        }
        if (ov.decision === "wait") {
          const c = this.start || [this.W / 2, this.H * 0.95];
          el("circle", { cx: c[0], cy: c[1], r: (26 + 8 * (ov.ring || 0)) * k, fill: "none", stroke: ov.color,
                         "stroke-width": 3, "stroke-dasharray": "6 5", "vector-effect": "non-scaling-stroke" }, g);
          if (ov.label) this._chip(g, [c[0] + (30 + 8 * (ov.ring || 0)) * k, c[1] - 22 * k], ov.label, ov.color, k);
          return;
        }
        if (!ov.points || ov.points.length < 2) return;
        const P = ov.points.map((p) => this._px(p));
        let pts, d;
        if (ov.kind === "waypoints") {
          const specs = (ov.segments || []).map((s) => ({ type: s.type, c1: s.c1 ? this._px(s.c1) : null, c2: s.c2 ? this._px(s.c2) : null }));
          const segs = G.buildSegments(P, specs, ov.style === "line" ? "line" : "curve");
          d = G.toPathD(P, segs); pts = G.sample(P, segs);
        } else { pts = P; d = G.polylineD(P); }
        const w = ov.width || 3;
        if (ov.halo !== false)
          el("path", { d, fill: "none", stroke: "rgba(255,255,255,.85)", "stroke-width": w + 3, "stroke-linecap": "round",
                       "stroke-linejoin": "round", "vector-effect": "non-scaling-stroke" }, g);
        el("path", { d, fill: "none", stroke: ov.color, "stroke-width": w, "stroke-linecap": "round", "stroke-linejoin": "round",
                     "stroke-dasharray": ov.dash || "none", "vector-effect": "non-scaling-stroke" }, g);
        if (ov.arrows) this._arrows(g, pts, k, 90);
        if (ov.label) {
          const L = G.pathLength(pts), frac = ov.labelAt ?? (0.45 + 0.1 * (i % 4));
          const at = G.along(pts, Math.max(L * frac, 1), L * frac)[0] || { x: pts[0][0], y: pts[0][1] };
          this._chip(g, [at.x, at.y], ov.label, ov.color, k);
        }
      });
    }

    _arrows(g, pts, k, spacingPx) {
      for (const a of G.along(pts, spacingPx * k)) {
        el("path", { d: "M-4,-5 L3,0 L-4,5", fill: "none", stroke: "#fff", "stroke-width": 2.2, "stroke-linecap": "round",
                     "stroke-linejoin": "round", transform: `translate(${a.x},${a.y}) rotate(${a.angle * 180 / Math.PI}) scale(${k})` }, g);
      }
    }
    _chip(g, p, text, color, k) {
      const t = el("g", { transform: `translate(${p[0]},${p[1]}) scale(${k})`, class: "pc-chip" }, g);
      el("circle", { r: 12, fill: color, stroke: "#fff", "stroke-width": 2.5 }, t);
      const tx = el("text", { "text-anchor": "middle", "dominant-baseline": "central", fill: "#fff" }, t);
      tx.textContent = text;
    }
    _robot(p, k) {
      const g = el("g", { transform: `translate(${p[0]},${p[1]}) scale(${k})`, class: "pc-anchor", "data-anchor": "start" }, this.gAnchors);
      if (this.o.anchorEditable) el("circle", { r: 22, fill: "transparent" }, g);
      el("circle", { r: 13, fill: "#2459ff", stroke: "#fff", "stroke-width": 3 }, g);
      el("path", { d: "M0,-6 L5.5,4 L0,1.5 L-5.5,4 Z", fill: "#fff" }, g);
    }
    _flag(p, k) {
      const g = el("g", { transform: `translate(${p[0]},${p[1]}) scale(${k})`, class: "pc-anchor", "data-anchor": "goal" }, this.gAnchors);
      if (this.o.anchorEditable) el("circle", { r: 22, fill: "transparent" }, g);
      el("ellipse", { rx: 9, ry: 4, fill: "rgba(0,0,0,.35)" }, g);
      el("path", { d: "M0,0 L0,-30", stroke: "#fff", "stroke-width": 5, "stroke-linecap": "round" }, g);
      el("path", { d: "M0,0 L0,-30", stroke: "#1d2433", "stroke-width": 2, "stroke-linecap": "round" }, g);
      el("path", { d: "M1,-30 L19,-24 L1,-17 Z", fill: "#1f9d55", stroke: "#fff", "stroke-width": 2, "stroke-linejoin": "round" }, g);
    }
  }

  global.PathCanvas = PathCanvas;
  global.PATH_COLOR = PATH_COLOR;
})(window);
