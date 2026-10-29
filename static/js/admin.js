/* Admin dashboard: overview, training images, VLM images, results, participants, data, settings. */
(function () {
  "use strict";
  const { esc, api, toast, copy, pct, dist, num, ci, roleLabel, matchScene, captureFrame, dropZone } = Admin;
  const $ = (id) => document.getElementById(id);
  let ov = null;

  function seg(el, onChange) {
    el.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-v]"); if (!b) return;
      el.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      onChange(b.dataset.v);
    });
  }

  // ---------------------------------------------------------------- tabs
  const TABS = ["overview", "images", "collect", "vlm", "results", "people", "data", "settings"];
  function route() {
    const tab = TABS.includes(location.hash.slice(1)) ? location.hash.slice(1) : "overview";
    TABS.forEach((t) => { $(`sec-${t}`).hidden = t !== tab; });
    document.querySelectorAll(".admin-nav a.tab").forEach((a) => a.classList.toggle("is-active", a.hash === `#${tab}`));
    if (tab === "images") loadSummaries();
    if (tab === "collect" && window.AdminCollect) AdminCollect.load();
    if (tab === "vlm") loadVlm();
    if (tab === "results") loadResults();
    if (tab === "people") loadPeople();
  }
  window.addEventListener("hashchange", route);

  // ---------------------------------------------------------------- overview
  async function loadOverview() {
    ov = await api("/api/admin/overview");
    const t = ov.totals, s = ov.settings;
    $("pw-warn").hidden = !ov.default_password;
    const active = ov.scenes.filter((x) => x.active).length;
    $("stats").innerHTML = [
      [active, `training image${active === 1 ? "" : "s"} shown (${ov.scenes.length} total)`],
      [t.np, `participants (${t.nteam} team, ${t.np - t.nteam} survey)`],
      [t.nd, "annotated images"],
      [t.ncand, "VLM images"],
      [t.nr, "VLM ratings"],
    ].map(([v, l]) => `<div class="stat"><b>${v}</b><span>${l}</span></div>`).join("");
    $("team-code").textContent = s.team_code;
    $("dataset-dir").textContent = ov.dataset_dir;
    $("dataset-dir-2").textContent = ov.dataset_dir;
    document.querySelectorAll(".n-survey").forEach((e) => { e.textContent = s.survey_draw_count; });
    document.querySelectorAll(".n-rate").forEach((e) => { e.textContent = s.rate_per_participant; });
    $("survey-label").textContent = `Survey participants (${s.survey_draw_count} random photos, then rate ${s.rate_per_participant} VLM images)`;
    updateLinks();
    renderScenes();
    fillSettings(s);
    $("scenario-list").innerHTML = [...new Set(ov.scenes.map((x) => x.scenario).filter(Boolean))].map((x) => `<option value="${esc(x)}">`).join("");
    $("model-list").innerHTML = ov.models.map((m) => `<option value="${esc(m)}">`).join("");
  }
  function updateLinks() {
    if (!ov) return;
    const p = new URLSearchParams();
    const g = $("share-group").value.trim();
    if (g) p.set("group", g);
    if ($("share-kiosk").checked) p.set("kiosk", "1");
    const tail = p.toString();
    $("link-survey").value = `${location.origin}/${tail ? "?" + tail : ""}`;
    $("link-team").value = `${location.origin}/?team=${encodeURIComponent(ov.settings.team_code)}${tail ? "&" + tail : ""}`;
  }
  $("share-group").addEventListener("input", updateLinks);
  $("share-kiosk").addEventListener("change", updateLinks);
  document.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", () => copy($(b.dataset.copy).value)));

  // ---------------------------------------------------------------- training images
  let summaries = {}, imgRole = "";
  function renderScenes() {
    const rows = ov.scenes, tbl = $("scene-table");
    if (!rows.length) { tbl.innerHTML = `<tbody><tr><td class="muted" style="padding:1.5rem">No training images yet. Upload some above.</td></tr></tbody>`; return; }
    tbl.innerHTML = `<thead><tr><th></th><th>Training image</th><th>Split</th><th>Start / goal</th><th>Shown</th>
      <th class="num">Team</th><th class="num">Survey</th><th>Agreement</th><th class="num">VLM</th><th></th></tr></thead><tbody>${
      rows.map((s) => {
        const sm = summaries[s.id];
        let agree = '<span class="muted">…</span>';
        if (sm) {
          if (!sm.enough_data) agree = '<span class="muted small">needs 3+</span>';
          else agree = `${pct(sm.top_choice_share)} chose the top route ${sm.ambiguous ? '<span class="pill pill-warn">ambiguous</span>' : ""}
            <div class="tiny muted">${sm.n_route_modes} route${sm.n_route_modes === 1 ? "" : "s"}${sm.n_wait ? `, ${sm.n_wait} wait` : ""}${sm.n_outliers ? `, ${sm.n_outliers} outlier${sm.n_outliers === 1 ? "" : "s"}` : ""}</div>`;
        }
        return `<tr class="${s.active ? "" : "is-off"}">
          <td><a href="/admin/image/${s.id}"><img class="thumb" src="${esc(s.image_url)}" alt="" loading="lazy"></a></td>
          <td><a href="/admin/image/${s.id}"><b>${esc(s.title || "Untitled")}</b></a>${s.media_type === "video" ? ' <span class="pill">clip</span>' : ""}
            <div class="tiny muted">${esc(s.source_name || "")}${s.scenario ? " · " + esc(s.scenario) : ""}</div></td>
          <td>${s.split ? `<span class="pill">${esc(s.split)}</span>` : '<span class="muted">–</span>'}</td>
          <td>${s.goal ? '<span class="pill pill-ok">set</span>' : '<span class="pill pill-warn">no goal</span>'}</td>
          <td><label class="check" style="padding:0"><input type="checkbox" data-active="${s.id}" ${s.active ? "checked" : ""}><span class="small">active</span></label></td>
          <td class="num">${s.n_team}</td><td class="num">${s.n_drawings - s.n_team}</td><td>${agree}</td>
          <td class="num">${s.n_vlm}</td><td><a class="btn" href="/admin/image/${s.id}">Open</a></td></tr>`;
      }).join("")}</tbody>`;
  }
  $("scene-table").addEventListener("change", async (e) => {
    const id = e.target.dataset.active; if (!id) return;
    await api(`/api/admin/scenes/${id}`, "PATCH", { active: e.target.checked ? 1 : 0 });
    ov.scenes.find((x) => x.id === id).active = e.target.checked ? 1 : 0;
    renderScenes(); toast(e.target.checked ? "Shown to participants" : "Hidden from participants");
  });
  async function loadSummaries() {
    try { summaries = await api(`/api/admin/scene_summaries?role=${imgRole}`); renderScenes(); } catch (e) { /* ignore */ }
  }
  seg($("img-role"), (v) => { imgRole = v; summaries = {}; renderScenes(); loadSummaries(); });

  dropZone($("drop"), $("file-input"), async (files) => {
    const status = $("upload-status");
    const fd = new FormData();
    fd.append("scenario", $("upload-scenario").value.trim());
    let i = 0;
    for (const f of files) {
      if (f.type.startsWith("video/") || /\.(mp4|webm|mov|m4v)$/i.test(f.name)) {
        status.textContent = `Reading the last frame of ${f.name}…`;
        try {
          const fr = await captureFrame(f, null);
          fd.append("files", f); fd.append(`frame_${i}`, fr.blob, `frame_${i}.png`); fd.append(`time_${i}`, String(fr.time)); i++;
        } catch (err) { status.textContent = `${f.name}: ${err.message}`; return; }
      } else if (f.type.startsWith("image/") || /\.(jpe?g|png|webp|gif|bmp|zip)$/i.test(f.name) || f.type.includes("zip")) {
        fd.append("files", f); i++;
      }
    }
    if (!i) { status.textContent = "No images, zip files or clips in that selection."; return; }
    status.textContent = `Uploading ${i} file${i === 1 ? "" : "s"}…`;
    try {
      const r = await api("/api/admin/scenes", "POST", fd);
      status.textContent = `Added ${r.created.length} training image${r.created.length === 1 ? "" : "s"}. Open each one to place the robot's start and the goal flag.`;
      await loadOverview(); loadSummaries();
    } catch (err) { status.textContent = err.message; }
  });

  // ---------------------------------------------------------------- VLM images
  let pending = [], vlm = null;
  dropZone($("vlm-drop"), $("vlm-input"), (files) => {
    files = files.filter((f) => f.type.startsWith("image/") || /\.(jpe?g|png|webp|gif|bmp)$/i.test(f.name));
    pending.forEach((p) => URL.revokeObjectURL(p.url));
    pending = files.map((f) => ({ file: f, url: URL.createObjectURL(f), scene: matchScene(f.name, ov.scenes) }));
    renderPending();
  });
  function renderPending() {
    $("vlm-preview").hidden = !pending.length;
    const opts = (sel) => `<option value="">Not linked to a training image</option>` +
      ov.scenes.map((s) => `<option value="${s.id}" ${s.id === sel ? "selected" : ""}>${esc(s.title)}</option>`).join("");
    const matched = pending.filter((p) => p.scene).length;
    $("vlm-match").innerHTML = `<thead><tr><th></th><th>File</th><th>Training image</th><th></th></tr></thead><tbody>${
      pending.map((p, i) => `<tr><td><img class="thumb" src="${p.url}" alt=""></td><td class="small">${esc(p.file.name)}</td>
        <td><select data-i="${i}">${opts(p.scene)}</select>${p.scene ? "" : ' <span class="pill pill-warn">no match</span>'}</td>
        <td><button class="btn btn-quiet btn-danger" data-rm="${i}">Remove</button></td></tr>`).join("")}</tbody>`;
    $("vlm-upload").textContent = `Upload ${pending.length} image${pending.length === 1 ? "" : "s"}`;
    $("vlm-status").textContent = `${matched} of ${pending.length} matched to a training image.`;
  }
  $("vlm-match").addEventListener("change", (e) => { const i = e.target.dataset.i; if (i != null) { pending[+i].scene = e.target.value || null; renderPending(); } });
  $("vlm-match").addEventListener("click", (e) => { const i = e.target.dataset.rm; if (i != null) { URL.revokeObjectURL(pending[+i].url); pending.splice(+i, 1); renderPending(); } });
  $("vlm-cancel").addEventListener("click", () => { pending.forEach((p) => URL.revokeObjectURL(p.url)); pending = []; renderPending(); });
  $("vlm-upload").addEventListener("click", async () => {
    const model = $("vlm-model").value.trim();
    if (!model) { $("vlm-status").textContent = "Name the model that drew these images first."; $("vlm-model").focus(); return; }
    const fd = new FormData();
    fd.append("model", model); fd.append("source", $("vlm-source").value);
    pending.forEach((p, i) => { fd.append("files", p.file, p.file.name); if (p.scene) fd.append(`scene_${i}`, p.scene); });
    $("vlm-upload").disabled = true; $("vlm-status").textContent = "Uploading…";
    try {
      const r = await api("/api/admin/vlm_images", "POST", fd);
      toast(`Added ${r.created.length} VLM image${r.created.length === 1 ? "" : "s"}`);
      pending.forEach((p) => URL.revokeObjectURL(p.url)); pending = []; renderPending();
      await loadOverview(); loadVlm();
    } catch (err) { $("vlm-status").textContent = err.message; }
    $("vlm-upload").disabled = false;
  });

  async function loadVlm() {
    vlm = await api("/api/admin/vlm_images");
    const models = [...new Set(vlm.items.map((c) => c.model))].sort();
    const cur = $("vlm-filter").value;
    $("vlm-filter").innerHTML = `<option value="">All models</option>` + models.map((m) => `<option ${m === cur ? "selected" : ""}>${esc(m)}</option>`).join("");
    renderVlm();
  }
  function renderVlm() {
    const f = $("vlm-filter").value;
    const items = vlm.items.filter((c) => !f || c.model === f);
    const yn = vlm.questions.find((q) => q.type === "yesno");
    const scales = vlm.questions.filter((q) => q.type === "scale");
    $("vlm-count").textContent = `${items.length} image${items.length === 1 ? "" : "s"}`;
    const tbl = $("vlm-table");
    if (!items.length) { tbl.innerHTML = `<tbody><tr><td class="muted" style="padding:1.5rem">No VLM images yet. Add some above.</td></tr></tbody>`; return; }
    tbl.innerHTML = `<thead><tr><th></th><th>Model</th><th>Training image</th><th>Path coordinates</th><th class="num">Ratings</th>
      ${yn ? `<th class="num">${esc(yn.key)} (yes)</th>` : ""}<th class="num">Mean of 1–7 answers</th><th>In ratings</th><th></th></tr></thead><tbody>${
      items.map((c) => {
        const r = c.ratings || {};
        const sc = scales.map((q) => r[q.key]).filter((v) => v != null);
        const coords = c.decision === "wait" ? '<span class="pill">waits</span>' : c.points ? '<span class="pill pill-ok">yes</span>' : '<span class="muted small">image only</span>';
        return `<tr class="${c.include_in_rating ? "" : "is-off"}">
          <td><a href="/admin/vlm/${c.id}">${c.image_url ? `<img class="thumb" src="${esc(c.image_url)}" alt="" loading="lazy">` : ""}</a></td>
          <td><b>${esc(c.model)}</b><div class="tiny muted">${esc(c.original_filename || "")}</div></td>
          <td>${c.scene_id ? `<a href="/admin/image/${c.scene_id}">${esc(c.scene_title)}</a>` : '<span class="pill pill-warn">not linked</span>'}</td>
          <td>${coords}</td><td class="num">${r.n || 0}</td>
          ${yn ? `<td class="num">${pct(r[yn.key])}</td>` : ""}<td class="num">${sc.length ? num(sc.reduce((a, b) => a + b, 0) / sc.length, 1) : "–"}</td>
          <td><input type="checkbox" data-rate="${c.id}" ${c.include_in_rating ? "checked" : ""} aria-label="Include in rating tasks"></td>
          <td><a class="btn" href="/admin/vlm/${c.id}">Open</a></td></tr>`;
      }).join("")}</tbody>`;
  }
  $("vlm-filter").addEventListener("change", renderVlm);
  $("vlm-table").addEventListener("change", async (e) => {
    const id = e.target.dataset.rate; if (!id) return;
    await api(`/api/admin/candidates/${id}`, "PATCH", { include_in_rating: e.target.checked });
    vlm.items.find((c) => c.id === id).include_in_rating = e.target.checked; renderVlm();
    toast(e.target.checked ? "Included in rating tasks" : "Left out of new rating tasks");
  });

  // ---------------------------------------------------------------- results
  let resRole = "";
  seg($("res-role"), (v) => { resRole = v; loadResults(); });
  async function loadResults() {
    const r = await api(`/api/admin/results?role=${resRole}`);
    const qs = r.questions;
    $("res-n").textContent = `${r.n_ratings} rating${r.n_ratings === 1 ? "" : "s"} from ${r.n_raters} ${r.n_raters === 1 ? "person" : "people"}`;
    const rated = r.models.filter((m) => m.ratings);
    const fmt = (q, v) => (q.type === "yesno" ? pct(v) : num(v, 2));
    const cf = (q, c) => (c ? ` <span class="muted tiny">[${fmt(q, c[0])}–${fmt(q, c[1])}]</span>` : "");
    $("res-table").innerHTML = !rated.length ? `<tbody><tr><td class="muted" style="padding:1rem">No ratings yet.</td></tr></tbody>` :
      `<thead><tr><th>Model</th><th class="num">Images rated</th><th class="num">Ratings</th>${qs.map((q) =>
        `<th class="num" title="${esc(q.text)}">${esc(q.key)}${q.type === "yesno" ? " (yes)" : " (1–7)"}</th>`).join("")}</tr></thead><tbody>${
      rated.map((m) => `<tr${m.control ? ' class="is-hot"' : ""}><td><b>${esc(m.model)}</b></td><td class="num">${m.ratings.n_items}</td>
        <td class="num">${m.ratings.n_ratings}</td>${qs.map((q) => `<td class="num">${fmt(q, m.ratings[q.key])}${cf(q, m.ratings[q.key + "_ci"])}</td>`).join("")}</tr>`).join("")}
      <tr><td class="muted">Rater agreement (α)</td><td></td><td></td>${qs.map((q) => `<td class="num muted">${num(r.reliability[q.key], 2)}</td>`).join("")}</tr></tbody>`;
    renderForest(rated, qs.filter((q) => q.type === "scale"));
    const withPaths = r.models.filter((m) => m.paths);
    $("path-table").innerHTML = !withPaths.length ? `<tbody><tr><td class="muted" style="padding:1rem">No VLM paths with coordinates on images that have 3+ human responses yet.</td></tr></tbody>` :
      `<thead><tr><th>Model</th><th class="num">Paths</th><th class="num">Same route as people</th><th class="num">Picked the top route</th>
        <th class="num">Fréchet to compiled path</th><th class="num">Similarity</th><th class="num">Human percentile</th>
        <th class="num">Agreement, ambiguous / clear images</th><th class="num">Confidence tracks agreement</th></tr></thead><tbody>${
      withPaths.map((m) => { const p = m.paths; return `<tr><td><b>${esc(m.model)}</b></td><td class="num">${p.n_paths}</td>
        <td class="num">${pct(p.choice_agreement)}${ci(p.choice_agreement_ci)}</td><td class="num">${pct(p.top_choice_match_rate)}</td>
        <td class="num">${dist(p.frechet_to_compiled)}${ci(p.frechet_to_compiled_ci, dist)}</td><td class="num">${pct(p.similarity_to_compiled)}</td>
        <td class="num">${pct(p.human_percentile)}</td><td class="num">${pct(p.agreement_on_ambiguous)} / ${pct(p.agreement_on_clear)}</td>
        <td class="num">${num(p.confidence_vs_human_agreement_spearman)}</td></tr>`; }).join("")}</tbody>`;
  }

  function renderForest(models, qs) {
    const el = $("res-chart");
    if (!models.length || !qs.length) { el.innerHTML = '<p class="muted small">No ratings yet.</p>'; return; }
    const W = 780, L = 210, R = 24, rowH = 22, headH = 30, top = 8;
    const H = top + qs.length * (headH + models.length * rowH + 8) + 30;
    const lo = 1, hi = Math.max(...qs.map((q) => +q.points || 7));
    const x = (v) => L + (v - lo) / (hi - lo) * (W - L - R);
    let svg = "", y = top;
    for (let v = lo; v <= hi; v++) svg += `<line x1="${x(v)}" x2="${x(v)}" y1="${top}" y2="${H - 24}" stroke="var(--line)"/>
      <text x="${x(v)}" y="${H - 8}" text-anchor="middle" font-size="11" fill="var(--ink-2)">${v}</text>`;
    qs.forEach((q) => {
      svg += `<text x="0" y="${y + 19}" font-size="12.5" font-weight="650" fill="var(--ink)">${esc(q.key)}</text>
              <text x="${L}" y="${y + 19}" font-size="11" fill="var(--ink-2)">${esc(q.low || "")}</text>
              <text x="${W - R}" y="${y + 19}" text-anchor="end" font-size="11" fill="var(--ink-2)">${esc(q.high || "")}</text>`;
      y += headH;
      models.forEach((m) => {
        const v = m.ratings[q.key], c = m.ratings[q.key + "_ci"], col = m.control ? "#7a8396" : "#2a78d6";
        svg += `<text x="12" y="${y + 15}" font-size="12" fill="var(--ink-2)">${esc(m.model.length > 26 ? m.model.slice(0, 25) + "…" : m.model)}</text>`;
        if (v != null) {
          if (c) svg += `<line x1="${x(c[0])}" x2="${x(c[1])}" y1="${y + 11}" y2="${y + 11}" stroke="${col}" stroke-width="2" stroke-linecap="round"/>`;
          svg += `<circle cx="${x(v)}" cy="${y + 11}" r="5.5" fill="${col}" stroke="var(--surface)" stroke-width="2"><title>${esc(m.model)}: ${num(v, 2)}${c ? ` (95% CI ${num(c[0], 2)}–${num(c[1], 2)})` : ""}</title></circle>`;
        }
        y += rowH;
      });
      y += 8;
    });
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" style="max-width:${W}px" role="img" aria-label="Mean answer per model for each 1 to 7 question">${svg}</svg>`;
  }

  // ---------------------------------------------------------------- participants
  let people = [], peopleRole = "";
  async function loadPeople() { people = await api("/api/admin/participants"); renderPeople(); }
  function renderPeople() {
    const q = $("people-search").value.trim().toLowerCase();
    const rows = people.filter((p) => (!peopleRole || p.role === peopleRole) &&
      (!q || [p.name, p.email, p.code, p.external_id].some((v) => String(v || "").toLowerCase().includes(q))));
    const tbl = $("people-table");
    if (!rows.length) { tbl.innerHTML = `<tbody><tr><td class="muted" style="padding:1.5rem">${people.length ? "Nobody matches." : "Nobody has signed up yet."}</td></tr></tbody>`; return; }
    const fastMs = ov.settings.fast_response_ms;
    tbl.innerHTML = `<thead><tr><th>Code</th><th>Name</th><th>Email</th><th>Role</th><th>ID</th><th class="num">Drawn</th><th class="num">Rated</th>
      <th class="num">Avg time per drawing</th><th>About</th><th>Status</th><th>Use</th><th></th></tr></thead><tbody>${
      rows.map((p) => `<tr class="${p.excluded ? "is-off" : ""}">
        <td><b>${esc(p.code)}</b></td><td>${esc(p.name || "")}</td><td class="small">${esc(p.email || "")}</td>
        <td><span class="pill${p.role === "team" ? " pill-ok" : ""}">${roleLabel(p.role)}</span></td><td class="small">${esc(p.external_id || "")}</td>
        <td class="num">${p.n_drawings} / ${p.n_draw_assigned}</td><td class="num">${p.n_ratings} / ${p.n_rate_assigned ?? "–"}</td>
        <td class="num">${p.avg_draw_ms ? (p.avg_draw_ms / 1000).toFixed(1) + " s" : "–"}${p.avg_draw_ms && p.avg_draw_ms < fastMs ? ' <span class="pill pill-warn">fast</span>' : ""}</td>
        <td class="tiny muted">${esc(Object.values(p.demographics || {}).join(", "))}</td>
        <td>${p.completed ? `<span class="pill pill-ok">finished</span> <span class="tiny muted">${esc(p.completion_code || "")}</span>` : '<span class="pill">in progress</span>'}</td>
        <td><input type="checkbox" data-pid="${p.id}" ${p.excluded ? "" : "checked"} aria-label="Use in analysis"></td>
        <td><button class="btn btn-quiet btn-danger" data-del="${p.id}">Delete</button></td></tr>`).join("")}</tbody>`;
  }
  seg($("people-role"), (v) => { peopleRole = v; renderPeople(); });
  $("people-search").addEventListener("input", renderPeople);
  $("people-table").addEventListener("change", async (e) => {
    const pid = e.target.dataset.pid; if (!pid) return;
    await api(`/api/admin/participants/${pid}`, "PATCH", { excluded: !e.target.checked });
    people.find((p) => p.id === pid).excluded = !e.target.checked; renderPeople();
    toast(e.target.checked ? "Included in analysis" : "Left out of analysis");
  });
  $("people-table").addEventListener("click", async (e) => {
    const pid = e.target.dataset.del; if (!pid) return;
    const p = people.find((x) => x.id === pid);
    if (!confirm(`Delete ${p.code} (${p.name || "no name"}) and all their drawings and ratings? This can't be undone.`)) return;
    await api(`/api/admin/participants/${pid}`, "DELETE");
    people = people.filter((x) => x.id !== pid); renderPeople(); toast("Participant deleted"); loadOverview();
  });

  // ---------------------------------------------------------------- data
  $("merge-file").addEventListener("change", async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const st = $("merge-status"); st.textContent = "Merging…";
    const fd = new FormData(); fd.append("file", f);
    try {
      const r = await api("/api/admin/import", "POST", fd);
      st.innerHTML = Object.entries(r).map(([k, v]) => `${esc(k)}: ${v.added} new of ${v.in_file}`).join("<br>");
      loadOverview();
    } catch (err) { st.textContent = err.message; }
    e.target.value = "";
  });
  $("cand-file").addEventListener("change", async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const st = $("cand-status"); st.textContent = "Uploading…";
    const fd = new FormData(); fd.append("file", f);
    try {
      const r = await api("/api/admin/candidates/import", "POST", fd);
      st.innerHTML = `Added ${r.imported} path${r.imported === 1 ? "" : "s"}.` + (r.errors.length ? `<br><span class="form-error">${r.errors.map(esc).join("<br>")}</span>` : "");
      loadOverview();
    } catch (err) { st.textContent = err.message; }
    e.target.value = "";
  });
  $("rebuild").addEventListener("click", async () => {
    $("rebuild").disabled = true;
    try { await api("/api/admin/rebuild_folders", "POST", {}); toast("Folders rebuilt"); } catch (err) { toast(err.message); }
    $("rebuild").disabled = false;
  });
  $("api-example").textContent =
`# training images (URLs, start/goal, split)
curl -H "Authorization: Bearer $API_TOKEN" ${location.origin}/api/v1/scenes

# compiled human trajectory per image (in-context examples)
curl -H "Authorization: Bearer $API_TOKEN" "${location.origin}/api/v1/consensus?split=train"

# upload VLM images (scene_0 = training image id for files[0], ...)
curl -H "Authorization: Bearer $API_TOKEN" -F model=gpt-4o \\
     -F files=@welch_crowd_gpt4o.png -F scene_0=<id> \\
     ${location.origin}/api/v1/vlm_images

# or upload coordinates
curl -X POST -H "Authorization: Bearer $API_TOKEN" -H "Content-Type: application/json" \\
     -d @paths.json ${location.origin}/api/v1/candidates`;

  // ---------------------------------------------------------------- settings
  function fillSettings(s) {
    const f = $("settings-form");
    for (const k of ["study_title", "study_subtitle", "consent_text", "default_prompt", "rating_intro", "rating_prompt",
                     "completion_message", "survey_draw_count", "team_draw_count", "rate_per_participant", "consensus_controls",
                     "team_code", "default_line_style", "rating_display", "compiled_method"]) f[k].value = s[k];
    f.require_email.checked = !!s.require_email;
    f.ask_demographics.checked = !!s.ask_demographics;
    f.ask_norms_in_rating.checked = !!s.ask_norms_in_rating;
    f.exclude_outliers.value = String(!!s.exclude_outliers);
    f.cluster_threshold.value = +(s.cluster_threshold * 100).toFixed(2);
    f.ambiguity_threshold.value = +(s.ambiguity_threshold * 100).toFixed(1);
    f.min_mode_share.value = +(s.min_mode_share * 100).toFixed(1);
    f.fast_response_ms.value = s.fast_response_ms / 1000;
    f.norms.value = s.norms.map((n) => `${n.key} | ${n.label}`).join("\n");
    f.questions.value = s.questions.map((q) => q.type === "yesno" ? `${q.key} | yesno | ${q.text}`
      : `${q.key} | 1-${q.points || 7} | ${q.text} | ${q.low || ""} | ${q.high || ""}`).join("\n");
  }
  function parseQuestions(text) {
    return text.split("\n").map((l) => l.trim()).filter(Boolean).map((l, i) => {
      const parts = l.split("|").map((x) => x.trim());
      const key = (parts[0] || `q${i + 1}`).toLowerCase().replace(/[^a-z0-9_]+/g, "_");
      const kind = (parts[1] || "").toLowerCase();
      if (kind === "yesno" || kind === "yes/no") return { key, type: "yesno", text: parts[2] || key };
      const m = kind.match(/^1\s*-\s*(\d+)$/);
      if (!m) throw new Error(`Line ${i + 1}: the second part must be "yesno" or "1-7" (or 1-5 etc.).`);
      return { key, type: "scale", points: +m[1], text: parts[2] || key, low: parts[3] || "", high: parts[4] || "" };
    });
  }
  $("settings-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, st = $("settings-status");
    st.textContent = "";
    let questions;
    try { questions = parseQuestions(f.questions.value); } catch (err) { st.textContent = err.message; return; }
    const norms = f.norms.value.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
      const [k, ...rest] = l.split("|");
      return { key: k.trim().toLowerCase().replace(/[^a-z0-9_]+/g, "_"), label: rest.join("|").trim() || k.trim() };
    });
    const body = {
      study_title: f.study_title.value, study_subtitle: f.study_subtitle.value, consent_text: f.consent_text.value,
      default_prompt: f.default_prompt.value, rating_intro: f.rating_intro.value, rating_prompt: f.rating_prompt.value,
      completion_message: f.completion_message.value, require_email: f.require_email.checked, ask_demographics: f.ask_demographics.checked,
      survey_draw_count: Math.max(1, +f.survey_draw_count.value || 5), team_draw_count: Math.max(0, +f.team_draw_count.value || 0),
      rate_per_participant: Math.max(0, +f.rate_per_participant.value || 0), consensus_controls: Math.max(0, +f.consensus_controls.value || 0),
      team_code: f.team_code.value.trim(), default_line_style: f.default_line_style.value, rating_display: f.rating_display.value,
      questions, ask_norms_in_rating: f.ask_norms_in_rating.checked, norms,
      cluster_threshold: (+f.cluster_threshold.value || 10) / 100, ambiguity_threshold: (+f.ambiguity_threshold.value || 60) / 100,
      min_mode_share: (+f.min_mode_share.value || 15) / 100, fast_response_ms: Math.round((+f.fast_response_ms.value || 0) * 1000),
      compiled_method: f.compiled_method.value, exclude_outliers: f.exclude_outliers.value === "true",
    };
    try { const s = await api("/api/admin/settings", "POST", body); ov.settings = s; fillSettings(s); toast("Settings saved"); loadOverview(); }
    catch (err) { st.textContent = err.message; }
  });

  loadOverview().then(route);
})();
