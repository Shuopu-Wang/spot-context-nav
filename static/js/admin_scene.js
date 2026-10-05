/* Admin training-image page: overlay of all paths, compiled path, similarity matrix, VLM outputs. */
(function () {
  "use strict";
  const { esc, api, toast, pct, dist, num, roleLabel, heat, RAMP, captureFrame } = Admin;
  const SID = window.SCENE_ID;
  const MODE_COLORS = ["#2a78d6", "#eb6834", "#1baf7a"];   // validated categorical slots; further routes fold into grey
  const OTHER = "#8a93a6";
  const AI_COLOR = "#ff2d87";
  const COMP_COLOR = "#1d2433";
  const modeColor = (m) => (m == null ? OTHER : MODE_COLORS[m] || OTHER);
  const routeName = (m) => (m == null ? "own route" : `Route ${String.fromCharCode(65 + m)}`);
  const $ = (id) => document.getElementById(id);

  let data = null, scene = null, settings = null, canvas = null, mode = "results";
  let tau = null, role = "", anchorTarget = "goal", drawStyle = "curve";

  function seg(el, onChange) {
    el.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-v],button[data-t],button[data-mode]"); if (!b) return;
      el.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      onChange(b.dataset.v ?? b.dataset.t ?? b.dataset.mode);
    });
  }

  async function load() {
    const q = new URLSearchParams();
    if (role) q.set("role", role);
    if (tau != null) q.set("cluster_threshold", String(tau / 100));
    data = await api(`/api/admin/scenes/${SID}/analysis?${q}`);
    scene = data.scene; settings = data.settings;
    if (tau == null) tau = +(settings.cluster_threshold * 100).toFixed(1);
    $("tau").value = tau; $("tau-val").textContent = `${tau}% of diagonal`;
    $("tau-save").hidden = Math.abs(tau / 100 - settings.cluster_threshold) < 1e-9;
    renderHeader(); renderSummary(); renderNorms(); renderVlm(); renderDrawings(); renderStrip(); renderHeatmap();
    if (!canvas) buildCanvas(); else refreshOverlays();
  }

  function renderHeader() {
    document.title = `${scene.title || "Training image"} · TrajLab admin`;
    $("scene-title").textContent = scene.title || "Untitled";
    $("folder").textContent = `dataset/${scene.folder}/`;
    $("scene-active").checked = !!scene.active;
    const f = $("meta-form");
    if (!f.dataset.filled) { for (const k of ["title", "scenario", "split", "prompt", "notes"]) f[k].value = scene[k] || ""; f.dataset.filled = "1"; }
    if (scene.media_type === "video") {
      $("video-tools").hidden = false;
      const v = $("admin-video");
      if (!v.src) v.src = scene.video_url;
      $("dtime").textContent = scene.decision_time != null ? `Current: ${(+scene.decision_time).toFixed(2)} s` : "";
    }
  }

  // ---------------------------------------------------------------- canvas
  function buildCanvas() {
    if (canvas) canvas.destroy();
    canvas = new PathCanvas($("stage-host"), {
      imageUrl: scene.image_url, width: scene.width, height: scene.height, start: scene.start, goal: scene.goal,
      editable: mode === "draw", anchorEditable: mode === "anchors", maxHeight: "72vh", lineStyle: drawStyle, onAnchors: saveAnchors });
    canvas.anchorTarget = anchorTarget;
    refreshOverlays();
  }
  function overlays() {
    const a = data.analysis, out = [], dim = mode !== "results";
    const comp = a.compiled || {};
    if ($("show-compiled").checked && comp.corridor_left)
      out.push({ type: "band", id: "band", left: comp.corridor_left, right: comp.corridor_right, fill: "rgba(29,36,51,.28)", opacity: dim ? 0.4 : 1 });
    if ($("show-humans").checked) {
      for (const h of a.humans) if (h.curve) out.push({ id: h.id, points: h.curve, kind: "curve",
        color: h.outlier ? OTHER : modeColor(h.mode), width: 2, dash: h.outlier || h.trimmed ? "5 4" : "none",
        opacity: dim ? 0.35 : 0.9, halo: false });
    }
    if ($("show-compiled").checked && comp.curve)
      out.push({ id: "compiled", points: comp.curve, kind: "curve", color: COMP_COLOR, width: 5, label: "C", arrows: true, opacity: dim ? 0.4 : 1, labelAt: 0.3 });
    if ($("show-ai").checked) {
      let ring = 0;
      data.candidates.forEach((c, i) => {
        const m = c.metrics;
        if (!m) return;
        if (c.decision === "wait") out.push({ id: c.id, decision: "wait", color: AI_COLOR, label: String(i + 1), ring: ring++, opacity: dim ? 0.4 : 1 });
        else if (m.curve) out.push({ id: c.id, points: m.curve, kind: "curve", color: AI_COLOR, width: 3, dash: "7 5",
                                     label: String(i + 1), opacity: dim ? 0.4 : 1, labelAt: 0.55 + 0.08 * (i % 4) });
      });
    }
    return out;
  }
  function refreshOverlays() { if (canvas) canvas.setOverlays(overlays()); renderLegend(); }

  function renderLegend() {
    const a = data.analysis, s = a.summary, comp = a.compiled || {};
    const items = a.modes.map((m) => `<span><span class="swatch" style="background:${modeColor(m.mode)}"></span>${routeName(m.mode)} (${m.count})</span>`);
    if (s.n_wait) items.push(`<span>${s.n_wait} chose to wait</span>`);
    if (s.n_outliers) items.push(`<span><span class="swatch" style="background:repeating-linear-gradient(90deg,${OTHER} 0 5px,transparent 5px 9px)"></span>Outlier${s.n_outliers > 1 ? "s" : ""} (${s.n_outliers})</span>`);
    if (comp.curve) items.push(`<span><span class="swatch" style="background:${COMP_COLOR};height:6px"></span>C = compiled path${comp.corridor_left ? ", shaded band = ±1 SD spread" : ""}</span>`);
    if (data.candidates.some((c) => c.metrics)) items.push(`<span><span class="swatch" style="background:repeating-linear-gradient(90deg,${AI_COLOR} 0 7px,transparent 7px 11px)"></span>Numbered = VLM paths with coordinates</span>`);
    $("legend").innerHTML = items.join("") || "<span>No paths yet. Share the study link to start collecting.</span>";
  }

  seg($("mode-seg"), (m) => {
    mode = m;
    $("panel-results").hidden = mode !== "results";
    $("panel-anchors").hidden = mode !== "anchors";
    $("panel-draw").hidden = mode !== "draw";
    buildCanvas();
  });
  for (const id of ["show-humans", "show-compiled", "show-ai"]) $(id).addEventListener("change", refreshOverlays);
  seg($("role-seg"), (v) => { role = v; load(); });
  $("tau").addEventListener("input", (e) => { $("tau-val").textContent = `${e.target.value}% of diagonal`; });
  $("tau").addEventListener("change", (e) => { tau = +e.target.value; load(); });
  $("tau-save").addEventListener("click", async () => {
    await api("/api/admin/settings", "POST", { cluster_threshold: tau / 100 });
    settings.cluster_threshold = tau / 100; $("tau-save").hidden = true; toast("Saved as the default for every image");
  });

  seg($("anchor-seg"), (t) => { anchorTarget = t; if (canvas) canvas.anchorTarget = t; });
  async function saveAnchors(a) {
    scene = await api(`/api/admin/scenes/${SID}`, "PATCH", { start: a.start, goal: a.goal });
    toast("Start and goal saved");
  }
  $("remove-goal").addEventListener("click", async () => {
    scene = await api(`/api/admin/scenes/${SID}`, "PATCH", { goal: null });
    canvas.setAnchors(scene.start, null); toast("Goal removed");
  });

  seg($("c-style"), (v) => { drawStyle = v; if (canvas && mode === "draw") canvas.setStyle(v); });
  $("c-undo").addEventListener("click", () => canvas && canvas.undo());
  $("c-clear").addEventListener("click", () => canvas && canvas.clear());
  $("c-save").addEventListener("click", async () => {
    const wait = $("c-wait").checked;
    $("c-err").textContent = "";
    if (!$("c-model").value.trim()) { $("c-err").textContent = "Name the model or method first."; return; }
    if (!wait && !canvas.hasPath()) { $("c-err").textContent = "Click the image to draw a path, or tick 'chose to wait'."; return; }
    const p = wait ? null : canvas.getPath();
    try {
      await api(`/api/admin/scenes/${SID}/candidates`, "POST", {
        model: $("c-model").value.trim(), source: $("c-source").value, rationale: $("c-rationale").value.trim(),
        decision: wait ? "wait" : "path", kind: "waypoints", points: p && p.points, segments: p && p.segments, style: p && p.style,
        consensus_override: $("c-override").checked, include_in_rating: !$("c-override").checked });
      toast("Path saved"); canvas.clear(); $("c-override").checked = false; $("c-wait").checked = false;
      await load();
    } catch (err) { $("c-err").textContent = err.message; }
  });

  // ---------------------------------------------------------------- side panels
  function renderSummary() {
    const s = data.analysis.summary, a = data.analysis, comp = a.compiled || {};
    if (!s.n_responses) { $("summary").innerHTML = '<p class="muted small">No annotations yet.</p>'; return; }
    const bars = a.modes.map((m) => `<div class="mode-row"><span><span class="swatch" style="background:${modeColor(m.mode)}"></span>${routeName(m.mode)}</span>
        <div class="bar"><i style="width:${m.share * 100}%;background:${modeColor(m.mode)}"></i></div><span class="num small">${pct(m.share)} (${m.count})</span></div>`);
    if (s.n_wait) bars.push(`<div class="mode-row"><span>Wait</span><div class="bar"><i style="width:${s.wait_share * 100}%;background:var(--ink-3)"></i></div>
        <span class="num small">${pct(s.wait_share)} (${s.n_wait})</span></div>`);
    const method = { mean: "robust average of the top route", medoid: "most central path of the top route",
                     override: "set by you", majority_wait: "most people waited" }[comp.source] || "–";
    $("summary").innerHTML = `
      ${s.enough_data ? (s.ambiguous ? '<p><span class="pill pill-warn">Ambiguous</span> <span class="small">People are split on this image.</span></p>'
                                     : '<p><span class="pill pill-ok">Clear majority</span></p>') : '<p><span class="pill">Needs 3+ responses</span></p>'}
      ${bars.join("")}
      <dl class="kv" style="margin-top:.8rem">
        <dt>Responses</dt><dd>${s.n_responses} (${s.n_paths} paths, ${s.n_wait} wait)</dd>
        <dt>Outliers</dt><dd>${s.n_outliers}${s.n_outliers ? (settings.exclude_outliers ? " (left out)" : " (flagged only)") : ""}</dd>
        <dt>Similarity, everyone</dt><dd>${pct(s.mean_similarity)} <span class="muted tiny">mean pairwise</span></dd>
        <dt>Similarity, top route</dt><dd>${pct(s.top_route_similarity)}</dd>
        <dt>Typical gap</dt><dd>${dist(s.median_pairwise_frechet)} Fréchet, ${dist(s.median_pairwise_rmse)} RMSE <span class="muted tiny">(median)</span></dd>
        <dt>Mean squared error</dt><dd>${s.mean_pairwise_mse == null ? "–" : (s.mean_pairwise_mse * 1e4).toFixed(1) + " ×10⁻⁴"}</dd>
        <dt>Compiled path</dt><dd>${method}${comp.member_ids ? `<div class="tiny muted">from ${comp.member_ids.length} paths${comp.trimmed_ids && comp.trimmed_ids.length ? `, ${comp.trimmed_ids.length} trimmed` : ""}</div>` : ""}</dd>
        <dt>Choice entropy</dt><dd>${num(s.choice_entropy_bits)} bits</dd>
        <dt>Rule-tag overlap</dt><dd>${s.norm_jaccard == null ? "–" : num(s.norm_jaccard)} <span class="muted tiny">(mean Jaccard)</span></dd>
      </dl>`;
  }
  function renderNorms() {
    const counts = data.analysis.norm_counts, n = data.analysis.summary.n_responses;
    const labels = Object.fromEntries(settings.norms.map((x) => [x.key, x.label]));
    const rows = Object.entries(counts).sort((a, b) => b[1] - a[1]);
    $("norms").innerHTML = !rows.length ? '<p class="muted small">No tags yet.</p>' : rows.map(([k, c]) => `<div class="mode-row" style="grid-template-columns:1fr 90px 50px">
      <span class="small">${esc(labels[k] || k)}</span><div class="bar"><i style="width:${c / n * 100}%;background:var(--ink-2)"></i></div>
      <span class="num small">${pct(c / n)}</span></div>`).join("");
  }

  // ---------------------------------------------------------------- VLM cards
  function renderVlm() {
    const yn = settings.questions.find((q) => q.type === "yesno");
    const scales = settings.questions.filter((q) => q.type === "scale");
    const el = $("vlm-grid");
    $("model-list").innerHTML = [...new Set(data.candidates.map((c) => c.model))].map((m) => `<option value="${esc(m)}">`).join("");
    if (!data.candidates.length) { el.innerHTML = '<p class="muted small">None yet. Upload them under <a href="/admin#vlm">VLM images</a>, or use <b>Add a path</b> above.</p>'; return; }
    el.innerHTML = data.candidates.map((c, i) => {
      const r = c.ratings || {}, m = c.metrics;
      const sc = scales.map((q) => r[q.key]).filter((v) => v != null);
      return `<a class="vlm-card" href="/admin/vlm/${c.id}" data-id="${c.id}">
        ${c.image_url ? `<img src="${esc(c.image_url)}" alt="" loading="lazy">` : ""}
        <div><b>${i + 1}. ${esc(c.model)}</b>${c.consensus_override ? ' <span class="pill pill-ok">compiled path</span>' : ""}
          <div class="tiny muted">${r.n || 0} rating${r.n === 1 ? "" : "s"}${yn && r[yn.key] != null ? ` · ${pct(r[yn.key])} acceptable` : ""}${sc.length ? ` · ${num(sc.reduce((a, b) => a + b, 0) / sc.length, 1)} / 7` : ""}</div>
          <div class="tiny muted">${m && m.choice_agreement != null ? `Same route as ${pct(m.choice_agreement)} of people` : (c.has_path ? "" : "No coordinates (trace it to compare)")}</div></div></a>`;
    }).join("");
    el.querySelectorAll("[data-id]").forEach((n) => {
      n.addEventListener("mouseenter", () => canvas && canvas.highlight(n.dataset.id));
      n.addEventListener("mouseleave", () => canvas && canvas.highlight(null));
    });
  }

  // ---------------------------------------------------------------- similarity heatmap
  function renderHeatmap() {
    const a = data.analysis, S = a.pairwise_similarity, D = a.pairwise_frechet, codes = data.pairwise_codes, lab = a.route_labels;
    const n = codes.length, el = $("heatmap");
    if (n < 2) { el.innerHTML = '<p class="muted small">Needs at least 2 paths.</p>'; return; }
    const order = [...Array(n).keys()].sort((i, j) => lab[i] - lab[j] || String(codes[i]).localeCompare(String(codes[j])));
    const cell = Math.max(9, Math.min(30, Math.floor(620 / n)));
    const showLabels = cell >= 14, L = showLabels ? 62 : 22, T = showLabels ? 56 : 8;
    let svg = "";
    order.forEach((i, r) => {
      order.forEach((j, c) => {
        svg += `<rect x="${L + c * cell}" y="${T + r * cell}" width="${cell - 1}" height="${cell - 1}" fill="${heat(S[i][j])}">
          <title>${esc(codes[i])} vs ${esc(codes[j])}: ${pct(S[i][j])} similar (Fréchet ${dist(D[i][j])})</title></rect>`;
      });
      if (showLabels) {
        svg += `<text x="${L - 6}" y="${T + r * cell + cell / 2 + 4}" text-anchor="end" font-size="10.5" fill="var(--ink-2)">${esc(codes[i])}</text>`;
        svg += `<text transform="translate(${L + r * cell + cell / 2 + 4},${T - 6}) rotate(-60)" font-size="10.5" fill="var(--ink-2)">${esc(codes[i])}</text>`;
      }
      if (r === 0 || lab[order[r - 1]] !== lab[i]) {   // route boundary + route marker
        if (r) svg += `<line x1="${L}" x2="${L + n * cell}" y1="${T + r * cell - .5}" y2="${T + r * cell - .5}" stroke="var(--surface)" stroke-width="3"/>
                      <line y1="${T}" y2="${T + n * cell}" x1="${L + r * cell - .5}" x2="${L + r * cell - .5}" stroke="var(--surface)" stroke-width="3"/>`;
        const cnt = order.filter((k) => lab[k] === lab[i]).length;
        svg += `<rect x="${showLabels ? 2 : 4}" y="${T + r * cell}" width="5" height="${cnt * cell - 1}" fill="${modeColor(lab[i])}"><title>${routeName(lab[i])}</title></rect>`;
      }
    });
    el.innerHTML = `<svg viewBox="0 0 ${L + n * cell + 4} ${T + n * cell + 4}" width="${L + n * cell + 4}" role="img" aria-label="Pairwise path similarity matrix">${svg}</svg>
      <div class="heat-legend"><span>0%</span><i style="background:linear-gradient(90deg,${RAMP.join(",")})"></i><span>100% similar</span>
      <span style="margin-left:1rem">Left bar = route</span></div>`;
  }

  // ---------------------------------------------------------------- annotations table
  function renderDrawings() {
    const rows = data.drawings, tbl = $("draw-table");
    const labels = Object.fromEntries(settings.norms.map((x) => [x.key, x.label]));
    if (!rows.length) { tbl.innerHTML = `<tbody><tr><td class="muted" style="padding:1rem">No annotations yet.</td></tr></tbody>`; return; }
    const comp = data.analysis.compiled || {};
    const members = new Set(comp.member_ids || []);
    const styleName = { line: "straight", curve: "curves", mixed: "mixed" };
    tbl.innerHTML = `<thead><tr><th>Code</th><th>Name</th><th>Role</th><th>Choice</th><th class="num">Similarity to compiled</th>
      <th class="num">Avg similarity to others</th><th>Lines</th><th>Rules bent</th><th class="num">Sure</th><th class="num">Time</th><th>Comment</th><th>Image</th><th>Use</th></tr></thead><tbody>${
      rows.map((d) => {
        const choice = d.decision === "wait" ? "Wait" : `<span class="swatch" style="background:${d.outlier ? OTHER : modeColor(d.mode)}"></span>${d.outlier ? "Outlier" : routeName(d.mode)}`;
        const flags = [members.has(d.id) ? '<span class="pill pill-ok">in compiled</span>' : "",
                       d.trimmed ? '<span class="pill pill-warn">trimmed</span>' : "",
                       d.fast ? '<span class="pill pill-warn">fast</span>' : ""].join(" ");
        return `<tr data-id="${d.id}" class="${d.excluded ? "is-off" : ""}">
          <td><b>${esc(d.code)}</b></td><td class="small">${esc(d.name || "")}</td><td><span class="pill${d.role === "team" ? " pill-ok" : ""}">${roleLabel(d.role)}</span></td>
          <td>${choice} ${flags}</td><td class="num">${pct(d.similarity_to_compiled)}</td><td class="num">${pct(d.mean_similarity)}</td>
          <td class="small">${d.decision === "wait" ? "–" : styleName[d.style] || esc(d.style || "")}</td>
          <td class="small">${d.norms.length ? d.norms.map((nm) => esc((labels[nm] || nm).split("(")[0].trim())).join("; ") : '<span class="muted">none</span>'}</td>
          <td class="num">${d.confidence ?? "–"}</td><td class="num">${d.duration_ms ? (d.duration_ms / 1000).toFixed(1) + " s" : "–"}</td>
          <td class="small" style="max-width:26ch">${esc(d.comment || "")}</td>
          <td class="small"><code>annotations/${d.excluded ? "excluded/" : ""}${esc(d.code)}.jpg</code></td>
          <td><input type="checkbox" data-draw="${d.id}" ${d.excluded ? "" : "checked"} aria-label="Use in analysis"></td></tr>`;
      }).join("")}</tbody>`;
    tbl.querySelectorAll("tr[data-id]").forEach((tr) => {
      tr.addEventListener("mouseenter", () => { canvas && canvas.highlight(tr.dataset.id); tr.classList.add("is-hot"); });
      tr.addEventListener("mouseleave", () => { canvas && canvas.highlight(null); tr.classList.remove("is-hot"); });
    });
  }
  $("draw-table").addEventListener("change", async (e) => {
    const id = e.target.dataset.draw; if (!id) return;
    await api(`/api/admin/drawings/${id}`, "PATCH", { excluded: !e.target.checked });
    toast(e.target.checked ? "Annotation included" : "Annotation left out (moved to annotations/excluded)"); await load();
  });

  // ---------------------------------------------------------------- strip plot
  function renderStrip() {
    const a = data.analysis;
    const hum = a.humans.filter((h) => h.loo_mean_frechet != null);
    const ai = data.candidates.map((c, i) => ({ ...c, n: i + 1 })).filter((c) => c.metrics && c.metrics.mean_frechet_to_humans != null);
    if (hum.length < 2) { $("strip").innerHTML = '<p class="muted small">Needs at least 2 paths.</p>'; return; }
    const W = 760, H = 150, L = 16, R = 16, T = 18, B = 34;
    const xmax = Math.max(...hum.map((h) => h.loo_mean_frechet), ...ai.map((c) => c.metrics.mean_frechet_to_humans)) * 1.08 || 0.1;
    const x = (v) => L + (v / xmax) * (W - L - R);
    const step = xmax > 0.3 ? 0.1 : xmax > 0.12 ? 0.05 : 0.02;
    let ticks = "";
    for (let v = 0; v <= xmax + 1e-9; v += step) ticks += `<line x1="${x(v)}" x2="${x(v)}" y1="${T}" y2="${H - B}" stroke="var(--line)"/>
      <text x="${x(v)}" y="${H - B + 16}" text-anchor="middle" font-size="11" fill="var(--ink-2)">${Math.round(v * 100)}%</text>`;
    const rnd = (i) => ((Math.sin(i * 12.9898) * 43758.5453) % 1 + 1) % 1;
    const dots = hum.map((h, i) => `<circle cx="${x(h.loo_mean_frechet)}" cy="${T + 10 + rnd(i + 1) * (H - B - T - 46)}" r="5"
        fill="${h.outlier ? OTHER : modeColor(h.mode)}" stroke="var(--surface)" stroke-width="2" data-id="${h.id}">
        <title>${h.outlier ? "Outlier" : routeName(h.mode)}: ${dist(h.loo_mean_frechet)} average gap</title></circle>`).join("");
    const dia = ai.map((c) => `<g transform="translate(${x(c.metrics.mean_frechet_to_humans)},${H - B - 14})" data-id="${c.id}">
        <path d="M0,-8 L8,0 L0,8 L-8,0 Z" fill="${AI_COLOR}" stroke="var(--surface)" stroke-width="2"/>
        <text y="-12" text-anchor="middle" font-size="11" font-weight="700" fill="var(--ink)">${c.n}</text>
        <title>${esc(c.model)}: ${dist(c.metrics.mean_frechet_to_humans)} average gap to people</title></g>`).join("");
    $("strip").innerHTML = `<svg viewBox="0 0 ${W} ${H}" style="max-width:960px" role="img" aria-label="Average Fréchet distance to other people for each path">
      ${ticks}<line x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}" stroke="var(--ink-3)"/>${dots}${dia}
      <text x="${W - R}" y="${H - 4}" text-anchor="end" font-size="11" fill="var(--ink-2)">average Fréchet distance to people (% of image diagonal)</text></svg>`;
    $("strip").querySelectorAll("[data-id]").forEach((n) => {
      n.addEventListener("mouseenter", () => canvas && canvas.highlight(n.dataset.id));
      n.addEventListener("mouseleave", () => canvas && canvas.highlight(null));
    });
  }

  // ---------------------------------------------------------------- details form
  $("meta-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    scene = await api(`/api/admin/scenes/${SID}`, "PATCH", { title: f.title.value, scenario: f.scenario.value,
      split: f.split.value, prompt: f.prompt.value, notes: f.notes.value });
    $("scene-title").textContent = scene.title || "Untitled"; $("folder").textContent = `dataset/${scene.folder}/`; toast("Details saved");
  });
  $("scene-active").addEventListener("change", async (e) => {
    await api(`/api/admin/scenes/${SID}`, "PATCH", { active: e.target.checked ? 1 : 0 });
    toast(e.target.checked ? "Shown to participants" : "Hidden from participants");
  });
  $("delete-scene").addEventListener("click", async () => {
    if (!confirm("Delete this training image, its folder and every annotation of it? VLM images of it are kept but unlinked. This can't be undone. (To stop showing it, untick 'Shown to participants' instead.)")) return;
    await api(`/api/admin/scenes/${SID}`, "DELETE"); location.href = "/admin#images";
  });
  $("use-frame").addEventListener("click", async () => {
    try {
      const fr = await captureFrame(scene.video_url, $("admin-video").currentTime);
      const fd = new FormData(); fd.append("frame", fr.blob, "frame.png"); fd.append("time", String(fr.time));
      scene = await api(`/api/admin/scenes/${SID}/frame`, "POST", fd);
      toast("Decision moment saved"); buildCanvas(); renderHeader();
    } catch (err) { toast(err.message); }
  });

  api("/api/admin/overview").then((ov) => {
    const ids = ov.scenes.map((s) => s.id), i = ids.indexOf(SID);
    if (i > 0) { $("prev-scene").hidden = false; $("prev-scene").href = `/admin/image/${ids[i - 1]}`; }
    if (i >= 0 && i < ids.length - 1) { $("next-scene").hidden = false; $("next-scene").href = `/admin/image/${ids[i + 1]}`; }
  });
  load();
})();
