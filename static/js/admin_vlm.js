/* Admin VLM image page: view, trace the drawn path, rating results, details. */
(function () {
  "use strict";
  const { esc, api, toast, pct, dist, num } = Admin;
  const CID = window.CANDIDATE_ID;
  const $ = (id) => document.getElementById(id);
  let d = null, c = null, canvas = null, mode = "view", style = "curve";

  function seg(el, onChange) {
    el.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-v]"); if (!b) return;
      el.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      onChange(b.dataset.v);
    });
  }

  async function load() {
    d = await api(`/api/admin/candidates/${CID}`);
    c = d.candidate;
    document.title = `${c.model} · VLM image · TrajLab admin`;
    $("title").textContent = c.model;
    $("subtitle").innerHTML = d.scene ? `on <a href="/admin/image/${d.scene.id}">${esc(d.scene.title)}</a>` : "Not linked to a training image";
    $("in-rating").checked = c.include_in_rating;
    const f = $("meta-form");
    f.model.value = c.model; f.source.value = c.source || "vlm"; f.decision.value = c.decision; f.rationale.value = c.rationale || "";
    f.scene_id.innerHTML = `<option value="">Not linked</option>` + d.scenes.map((s) => `<option value="${s.id}" ${s.id === c.scene_id ? "selected" : ""}>${esc(s.title)}</option>`).join("");
    $("file-info").textContent = c.original_filename ? `Uploaded as ${c.original_filename}${c.image_w ? ` (${c.image_w}×${c.image_h})` : ""}` : "Drawn by the site from coordinates";
    renderRatings(); renderMetrics(); buildCanvas();
  }

  function dims() {
    if (c.image_w) return [c.image_w, c.image_h];
    if (d.scene) return [d.scene.width, d.scene.height];
    return [1600, 900];
  }
  function buildCanvas() {
    if (canvas) canvas.destroy();
    const [w, h] = dims();
    const sc = d.scene;
    canvas = new PathCanvas($("stage-host"), { imageUrl: c.image_url, width: w, height: h, editable: mode === "trace", lineStyle: style,
      start: sc ? sc.start : null, goal: sc ? sc.goal : null, maxHeight: "72vh", onChange: hint });
    if (mode === "view") {
      canvas.showUser = false;
      canvas.setOverlays(c.points && c.original_filename
        ? [{ id: "trace", points: c.points, segments: c.segments, style: c.style, kind: "waypoints", color: "#ff2d87", width: 3, dash: "7 5", opacity: .95 }] : []);
    } else if (c.points) {
      canvas.setPath(c.points, c.segments);
      if (c.style === "line") { canvas.style = "line"; }
    }
    $("trace-tools").hidden = mode !== "trace";
    hint();
  }
  function hint() {
    let t;
    if (mode === "view") t = c.points ? (c.original_filename ? "Dashed pink = the traced path used for comparison with people." : "")
                                      : "No coordinates yet. Choose “Trace the path” and click along the line the model drew to compare it with people.";
    else t = d.scene ? "Click along the model's line from the robot to its end point. Drag the diamonds to bend segments. This assumes the VLM image shows the same view as the training image (resized is fine, cropped is not)."
                     : "Link this image to a training image first, so the robot's start and the goal are known.";
    $("hint").textContent = t;
  }
  seg($("mode-seg"), (v) => { mode = v; buildCanvas(); });
  seg($("t-style"), (v) => { style = v; if (canvas) canvas.setStyle(v); });
  $("t-undo").addEventListener("click", () => canvas.undo());
  $("t-clear").addEventListener("click", () => canvas.clear());
  $("t-save").addEventListener("click", async () => {
    if (!canvas.hasPath()) { toast("Click along the path first"); return; }
    const p = canvas.getPath();
    await api(`/api/admin/candidates/${CID}`, "PATCH", { points: p.points, segments: p.segments, style: p.style, decision: "path" });
    toast("Traced path saved"); mode = "view";
    $("mode-seg").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === "view")));
    await load();
  });

  function renderRatings() {
    const r = c.ratings || {}, n = d.n_ratings;
    if (!n) { $("ratings").innerHTML = '<p class="muted small">No ratings yet.</p>'; }
    else {
      $("ratings").innerHTML = `<p class="small muted" style="margin:0 0 .3rem">${n} rating${n === 1 ? "" : "s"}</p>` + d.questions.map((q) => {
        const dist_ = d.distribution[q.key] || {};
        const vals = q.type === "yesno" ? [["yes", "Yes"], ["no", "No"]] : [...Array(+q.points || 7).keys()].map((i) => [String(i + 1), String(i + 1)]);
        const max = Math.max(1, ...vals.map(([v]) => dist_[v] || 0));
        const mean = q.type === "yesno" ? `${pct(r[q.key])} yes` : `${num(r[q.key], 2)} / ${q.points || 7}`;
        return `<div class="qblock"><span class="q-title">${esc(q.text)}</span><div class="qmean">${mean}</div>
          ${vals.map(([v, lab]) => `<div class="dist-row" style="grid-template-columns:${q.type === "yesno" ? "40px" : "24px"} 1fr 30px">
            <span class="small muted">${lab}</span><div class="bar"><i style="width:${(dist_[v] || 0) / max * 100}%;background:#2a78d6"></i></div>
            <span class="num small">${dist_[v] || 0}</span></div>`).join("")}
          ${q.type === "scale" ? `<div class="scale-ends"><span>1 = ${esc(q.low || "")}</span><span>${q.points || 7} = ${esc(q.high || "")}</span></div>` : ""}</div>`;
      }).join("");
    }
    const labels = Object.fromEntries(d.norms.map((x) => [x.key, x.label]));
    const rows = Object.entries(d.norm_counts).sort((a, b) => b[1] - a[1]);
    $("norms").innerHTML = !rows.length ? '<p class="muted small">No tags yet.</p>' : rows.map(([k, v]) => `<div class="mode-row" style="grid-template-columns:1fr 90px 50px">
      <span class="small">${esc(labels[k] || k)}</span><div class="bar"><i style="width:${v / n * 100}%;background:var(--ink-2)"></i></div><span class="num small">${pct(v / n)}</span></div>`).join("");
    $("comments-card").hidden = !d.comments.length;
    $("comments").innerHTML = d.comments.map((x) => `<li>${esc(x.comment)} <span class="tiny muted">${esc(x.code)}</span></li>`).join("");
  }

  function renderMetrics() {
    const m = d.metrics;
    $("metrics-card").hidden = !m;
    if (!m) return;
    const route = m.mode === "wait" ? "Wait" : m.mode == null ? "A route no one chose" : `Route ${String.fromCharCode(65 + m.mode)}`;
    $("metrics").innerHTML = `
      <dt>Choice</dt><dd>${route}${m.matches_top_choice ? ' <span class="pill pill-ok">most popular</span>' : ""}</dd>
      <dt>Same route as people</dt><dd>${pct(m.choice_agreement)}</dd>
      <dt>Fréchet to compiled path</dt><dd>${dist(m.frechet_to_compiled)}</dd>
      <dt>Similarity to compiled path</dt><dd>${pct(m.similarity_to_compiled)}</dd>
      <dt>RMSE / MSE to compiled</dt><dd>${dist(m.rmse_to_compiled)} / ${m.mse_to_compiled == null ? "–" : (m.mse_to_compiled * 1e4).toFixed(1) + " ×10⁻⁴"}</dd>
      <dt>DTW to compiled</dt><dd>${dist(m.dtw_to_compiled)}</dd>
      <dt>Average similarity to each person</dt><dd>${pct(m.mean_similarity_to_humans)}</dd>
      <dt>Human percentile</dt><dd>${pct(m.human_percentile)}</dd>`;
  }

  $("in-rating").addEventListener("change", async (e) => {
    await api(`/api/admin/candidates/${CID}`, "PATCH", { include_in_rating: e.target.checked });
    toast(e.target.checked ? "Included in rating tasks" : "Left out of new rating tasks");
  });
  $("meta-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    await api(`/api/admin/candidates/${CID}`, "PATCH", { model: f.model.value.trim() || c.model, source: f.source.value,
      decision: f.decision.value, rationale: f.rationale.value, scene_id: f.scene_id.value || null });
    toast("Details saved"); await load();
  });
  $("delete").addEventListener("click", async () => {
    if (!confirm("Delete this VLM image and all its ratings? This can't be undone.")) return;
    await api(`/api/admin/candidates/${CID}`, "DELETE"); location.href = "/admin#vlm";
  });
  load();
})();
