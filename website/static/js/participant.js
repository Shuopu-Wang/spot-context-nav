/* Participant flow: sign in -> draw paths on assigned images -> rate VLM images -> done. */
(function () {
  "use strict";
  const S = JSON.parse(document.getElementById("settings").textContent);
  const qs = new URLSearchParams(location.search);
  const GROUP = (qs.get("group") || "").slice(0, 64);
  const EXT_ID = qs.get("pid") || qs.get("PROLIFIC_PID") || "";
  const TEAM_CODE = qs.get("team") || "";
  const KIOSK = qs.get("kiosk") === "1";
  const KEY = "trajlab:v2:participant";

  const store = {
    get() { try { return localStorage.getItem(KEY); } catch (e) { return null; } },
    set(v) { try { localStorage.setItem(KEY, v); } catch (e) { /* private mode */ } },
    clear() { try { localStorage.removeItem(KEY); } catch (e) { /* ignore */ } },
  };

  const app = document.getElementById("app");
  let session = null;
  let canvas = null;
  let coachShown = false;

  const svg = (body, extra = "") => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" ${extra}>${body}</svg>`;
  const ICON = {
    undo: svg('<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>'),
    clear: svg('<path d="M6 6l12 12M18 6 6 18"/>'),
    straight: svg('<path d="M12 20V4M6 10l6-6 6 6"/>'),
    wait: svg('<rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/>'),
    replay: svg('<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>'),
    remove: svg('<circle cx="12" cy="12" r="8"/><path d="M8 12h8"/>'),
    lines: svg('<path d="M4 19 10 9l5 6 5-11"/>'),
    curves: svg('<path d="M4 19C6 8 11 7 12 12s6 4 8-8"/>'),
    reset: svg('<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>'),
    eye: svg('<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>'),
    zoom: svg('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5M8 11h6M11 8v6"/>'),
  };

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let data = null;
    try { data = await res.json(); } catch (e) { /* non-JSON */ }
    if (!res.ok) throw new Error((data && data.error) || `Something went wrong (${res.status}).`);
    return data;
  }

  function counts() {
    if (!session) return { done: 0, total: 0 };
    const d = session.draw, r = session.rate || [];
    const total = d.length + (session.rate ? r.length : session.rate_planned);
    const done = d.filter((t) => t.done).length + r.filter((t) => t.done).length;
    return { done, total };
  }
  function setProgress() {
    const bar = document.getElementById("progress");
    const { done, total } = counts();
    bar.hidden = !session || total === 0;
    if (bar.hidden) return;
    document.getElementById("progress-text").textContent = `${Math.min(done + 1, total)} of ${total}`;
    document.getElementById("progress-fill").style.width = `${(done / Math.max(total, 1)) * 100}%`;
    document.getElementById("who").textContent = session.code ? `Participant ${session.code}` : "";
  }

  function mount(html) {
    if (canvas) { canvas.destroy(); canvas = null; }
    app.innerHTML = html;
    window.scrollTo(0, 0);
    setProgress();
  }

  let toastTimer;
  function toast(msg) {
    let t = document.querySelector(".toast");
    if (!t) { t = document.createElement("div"); t.className = "toast"; t.setAttribute("role", "status"); document.body.appendChild(t); }
    t.textContent = msg; t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 3200);
  }

  // ------------------------------------------------------------------ sign in
  const WELCOME_ART = `
    <svg viewBox="0 0 400 300" role="img" aria-label="A robot's path weaving between groups of people toward a flag">
      <rect width="400" height="300" fill="#1d2433"/>
      <path d="M0 210 L400 150 L400 300 L0 300 Z" fill="#243049"/>
      <path d="M250 0 L400 0 L400 150 L285 167 Z" fill="#21402f" opacity=".9"/>
      <g fill="#5d6a88">
        <circle cx="120" cy="150" r="9"/><circle cx="142" cy="140" r="9"/><circle cx="135" cy="166" r="9"/><circle cx="158" cy="160" r="9"/>
        <circle cx="220" cy="120" r="8"/><circle cx="236" cy="108" r="8"/><circle cx="244" cy="128" r="8"/>
        <circle cx="70" cy="95" r="7"/><circle cx="300" cy="215" r="10"/><circle cx="322" cy="232" r="10"/><circle cx="90" cy="230" r="10"/>
        <circle cx="185" cy="70" r="7"/><circle cx="330" cy="90" r="7"/>
      </g>
      <circle cx="139" cy="153" r="34" fill="none" stroke="#7a8396" stroke-width="1.5" stroke-dasharray="3 5"/>
      <path class="welcome-path" d="M200 282 L 218 214 C 232 182, 196 170, 200 150 S 216 104, 228 86 L 272 40" fill="none" stroke="#fff" stroke-opacity=".9" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/>
      <path class="welcome-path" d="M200 282 L 218 214 C 232 182, 196 170, 200 150 S 216 104, 228 86 L 272 40" fill="none" stroke="#ff2d87" stroke-width="5.5" stroke-linecap="round" stroke-linejoin="round"/>
      <g fill="#fff" stroke="#ff2d87" stroke-width="2.5"><circle cx="218" cy="214" r="5"/><circle cx="200" cy="150" r="5"/><circle cx="228" cy="86" r="5"/></g>
      <circle cx="200" cy="282" r="11" fill="#2459ff" stroke="#fff" stroke-width="3"/>
      <path d="M200 276 L205 286 L200 283.5 L195 286 Z" fill="#fff"/>
      <path d="M272 40 L272 14" stroke="#fff" stroke-width="3.5" stroke-linecap="round"/>
      <path d="M273 14 L289 19.5 L273 25 Z" fill="#1f9d55" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/>
    </svg>
    <style>
      .welcome-path { stroke-dasharray: 420; stroke-dashoffset: 420; animation: drawpath 2.2s .3s ease-out forwards; }
      @keyframes drawpath { to { stroke-dashoffset: 0; } }
    </style>`;

  const opt = (vals) => `<option value="">Prefer not to say</option>` + vals.map((v) => `<option>${esc(v)}</option>`).join("");

  function showRegister() {
    session = null;
    const n = +S.survey_draw_count || 5;
    const demo = S.ask_demographics ? `
      <details class="optional">
        <summary>About you (optional)</summary>
        <p class="small muted" style="margin-top:.4rem">This helps us see whether different people judge these situations differently. Skip anything you like.</p>
        <div class="demo-grid">
          <label class="field"><span>Age</span><select name="age">${opt(["Under 18", "18–24", "25–34", "35–49", "50–64", "65+"])}</select></label>
          <label class="field"><span>Gender</span><select name="gender">${opt(["Woman", "Man", "Non-binary", "Another identity"])}</select></label>
          <label class="field"><span>Where you grew up</span><select name="region">${opt(["United States", "Mexico, Central or South America", "Canada",
            "Europe", "East Asia", "South Asia", "Southeast Asia", "Middle East or North Africa", "Sub-Saharan Africa", "Oceania", "Other"])}</select></label>
          <label class="field"><span>How often you walk on this campus</span><select name="campus">${opt(["Daily", "Weekly", "Rarely", "Never"])}</select></label>
          <label class="field"><span>Experience with robots</span><select name="robots">${opt(["None", "Seen them around", "Worked with them", "Build or research them"])}</select></label>
          <label class="field"><span>Field of study or work</span><input type="text" name="field" maxlength="80" autocomplete="off"></label>
        </div>
      </details>` : "";
    mount(`
      <section class="screen">
        <div class="welcome">
          <div>
            <h1>${esc(S.study_title)}</h1>
            <p class="lead">${esc(S.study_subtitle)}</p>
            <div class="consent" tabindex="0">${esc(S.consent_text)}</div>
            <form id="reg-form" style="margin-top:1.4rem" novalidate>
              <div class="row-2">
                <label class="field"><span>Your name</span><input type="text" name="name" autocomplete="name" maxlength="120" required></label>
                <label class="field"><span>Email${S.require_email ? "" : ' <span class="muted" style="font-weight:400">(optional)</span>'}</span>
                  <input type="email" name="email" autocomplete="email" maxlength="200" ${S.require_email ? "required" : ""}></label>
              </div>
              ${EXT_ID ? "" : `<label class="field"><span>Student or participant ID <span class="muted" style="font-weight:400">(optional)</span></span>
                <input type="text" name="external_id" autocomplete="off" maxlength="128"></label>`}
              <fieldset class="field role-pick">
                <legend>How are you taking part?</legend>
                <label class="role-card"><input type="radio" name="role" value="survey" ${TEAM_CODE ? "" : "checked"}>
                  <span><b>Survey participant</b><span class="muted small">Draw paths on ${n} photos, then rate some AI paths.</span></span></label>
                <label class="role-card"><input type="radio" name="role" value="team" ${TEAM_CODE ? "checked" : ""}>
                  <span><b>Research team member</b><span class="muted small">Draw on every photo. Needs the team access code.</span></span></label>
              </fieldset>
              <label class="field" id="code-field" ${TEAM_CODE ? "" : "hidden"}><span>Team access code</span>
                <input type="text" name="team_code" autocomplete="off" value="${esc(TEAM_CODE)}"></label>
              ${demo}
              <label class="check" style="margin:.6rem 0 1rem"><input type="checkbox" name="agree">
                <span>I've read the information above and agree to take part.</span></label>
              <button class="btn btn-primary btn-big" type="submit" id="start-btn" disabled>Start</button>
              <p class="small muted" style="margin-top:.6rem">Already started? Enter the same email to pick up where you left off.</p>
              <div class="form-error" id="start-error" role="alert"></div>
            </form>
          </div>
          <div class="welcome-art">${WELCOME_ART}</div>
        </div>
      </section>`);
    const form = document.getElementById("reg-form");
    form.agree.addEventListener("change", () => { document.getElementById("start-btn").disabled = !form.agree.checked; });
    form.addEventListener("change", (e) => {
      if (e.target.name === "role") document.getElementById("code-field").hidden = form.role.value !== "team";
    });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const err = document.getElementById("start-error");
      const name = form.name.value.trim(), email = form.email.value.trim();
      const role = form.role.value;
      if (!name) { err.textContent = "Please enter your name."; form.name.focus(); return; }
      if (S.require_email && !email) { err.textContent = "Please enter your email."; form.email.focus(); return; }
      if (role === "team" && !form.team_code.value.trim()) { err.textContent = "Enter the team access code, or choose Survey participant."; form.team_code.focus(); return; }
      const btn = document.getElementById("start-btn");
      btn.disabled = true; err.textContent = "";
      const demographics = {};
      for (const k of ["age", "gender", "region", "campus", "robots", "field"]) if (form[k] && form[k].value.trim()) demographics[k] = form[k].value.trim();
      try {
        session = await api("/api/register", { name, email, role, team_code: form.team_code.value.trim(), group: GROUP,
          external_id: EXT_ID || (form.external_id ? form.external_id.value.trim() : ""), demographics });
        store.set(session.participant_id);
        if (session.resumed) toast("Welcome back. Picking up where you left off.");
        next();
      } catch (ex) {
        err.textContent = ex.message;
        btn.disabled = false;
      }
    });
  }

  // ------------------------------------------------------------------ routing
  async function next() {
    const d = session.draw.findIndex((t) => !t.done);
    if (d >= 0) return showDraw(session.draw[d]);
    if (session.rate === null) {             // rating set is assigned once drawing is finished
      session = await api(`/api/session/${session.participant_id}`);
      if (session.rate === null) session.rate = [];
    }
    const r = session.rate.findIndex((t) => !t.done);
    if (r >= 0) {
      if (!session._introShown && !session.rate.some((t) => t.done)) return showRateIntro();
      return showRate(session.rate[r], r);
    }
    return finish();
  }

  function showRateIntro() {
    session._introShown = true;
    const n = session.rate.length;
    mount(`<section class="screen narrow" style="padding-top:clamp(2rem,8vh,5rem)">
      <h1>${session.draw.length ? "Now, rate " : "Rate "}${n} AI path${n === 1 ? "" : "s"}</h1>
      <p class="lead">${esc(S.rating_intro)}</p>
      <button class="btn btn-primary btn-big" id="go">Continue</button></section>`);
    document.getElementById("go").onclick = () => next();
  }

  async function finish() {
    if (!session.draw.length && !(session.rate || []).length) {
      mount(`<section class="screen narrow done"><h1>Nothing to do yet</h1>
        <p class="lead" style="margin:0 auto 1.5rem">No photos have been set up yet. Please check back later.</p>
        <button class="btn" id="again">Back to start</button></section>`);
      document.getElementById("again").onclick = () => { store.clear(); showRegister(); };
      return;
    }
    let code = session.completion_code;
    try { code = (await api(`/api/session/${session.participant_id}/complete`, {})).completion_code; } catch (e) { /* show anyway */ }
    const team = session.role === "team";
    mount(`<section class="screen narrow done">
      <h1>All done</h1>
      <p class="lead" style="margin:0 auto 1rem">${esc(S.completion_message)}</p>
      ${code ? `<p class="muted small" style="margin:0">Your completion code</p><div class="code">${esc(code)}</div>` : ""}
      ${team ? `<p class="muted">New photos added later will show up when you sign in again with the same email.</p>` : ""}
      ${KIOSK ? `<p><button class="btn btn-primary btn-big" id="again">Start for the next person</button></p>
                 <p class="small muted">This screen resets on its own in a minute.</p>` : `<p class="muted">You can close this tab now.</p>`}
    </section>`);
    document.getElementById("progress-fill").style.width = "100%";
    store.clear();
    if (KIOSK) {
      const reset = () => { clearTimeout(timer); showRegister(); };
      const timer = setTimeout(reset, 60000);
      document.getElementById("again").onclick = reset;
    }
  }

  // ------------------------------------------------------------------ shared widgets
  function normsHtml(name = "norm") {
    return S.norms.map((n) => `<label class="check"><input type="checkbox" name="${name}" value="${esc(n.key)}"><span>${esc(n.label)}</span></label>`).join("")
      + `<label class="check norm-none"><input type="checkbox" name="${name}-none" value="none"><span>None of these.</span></label>`;
  }
  function bindNorms(form, name = "norm") {
    form.addEventListener("change", (e) => {
      if (e.target.name === `${name}-none` && e.target.checked) form.querySelectorAll(`input[name=${name}]`).forEach((c) => (c.checked = false));
      if (e.target.name === name && e.target.checked) form[`${name}-none`].checked = false;
    });
  }
  function scaleHtml(name, n, lo, hi, labels) {
    let b = "";
    for (let i = 1; i <= n; i++) {
      const v = labels ? labels[i - 1][0] : i, t = labels ? labels[i - 1][1] : i;
      b += `<button type="button" data-v="${v}" aria-pressed="false" aria-label="${esc(labels ? t : `${i} of ${n}`)}">${esc(t)}</button>`;
    }
    return `<div class="scale${labels ? " scale-yn" : ""}" style="--n:${n}" data-scale="${esc(name)}" role="group">${b}</div>
            ${lo || hi ? `<div class="scale-ends"><span>${esc(lo)}</span><span>${esc(hi)}</span></div>` : ""}`;
  }
  function bindScales(root, onPick) {
    root.querySelectorAll(".scale").forEach((sc) => {
      sc.addEventListener("click", (e) => {
        const b = e.target.closest("button[data-v]");
        if (!b) return;
        sc.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
        onPick(sc.dataset.scale, b.dataset.v);
      });
    });
  }

  // ------------------------------------------------------------------ draw task
  function showDraw(task) {
    const sc = task.scene;
    const isVideo = sc.media_type === "video" && sc.video_url;
    const i = session.draw.indexOf(task) + 1;
    mount(`
      <section class="screen task-screen">
        <div class="task">
          <div class="main">
            <div class="stage">
              <div id="stage-host"></div>
              <div class="stage-tools">
                <div class="seg seg-dark" id="t-style" role="group" aria-label="Connect points with">
                  <button type="button" data-style="line" aria-pressed="false">${ICON.lines}Straight lines</button>
                  <button type="button" data-style="curve" aria-pressed="false">${ICON.curves}Curves</button>
                </div>
                <button class="btn" id="t-undo" disabled>${ICON.undo}Undo</button>
                <button class="btn" id="t-clear" disabled>${ICON.clear}Clear</button>
                ${sc.goal ? `<button class="btn" id="t-straight" aria-pressed="false">${ICON.straight}Go straight</button>` : ""}
                <span class="spacer"></span>
                ${isVideo ? `<button class="btn" id="t-replay">${ICON.replay}Replay clip</button>` : ""}
                <button class="btn" id="t-wait" aria-pressed="false">${ICON.wait}I'd wait</button>
              </div>
              <div class="stage-tools context-tools" id="ctx" hidden></div>
              <p class="stage-hint" id="hint"></p>
            </div>
          </div>
          <div class="side">
            <form class="panel" id="draw-form" novalidate>
              <p class="small muted" style="margin:0 0 .2rem">Photo ${i} of ${session.draw.length}</p>
              <h2>Where should the robot go?</h2>
              <p class="prompt">${esc(sc.prompt || S.default_prompt)}</p>
              <fieldset class="q" style="border:0;padding:0;margin-inline:0">
                <legend class="q-title">Which unwritten rules does your choice bend?</legend>
                <p class="q-help">Select all that apply.</p>
                <div class="norm-list">${normsHtml()}</div>
              </fieldset>
              <div class="q">
                <span class="q-title">How sure are you that most people would choose something similar?</span>
                ${scaleHtml("confidence", 5, "Not sure", "Very sure")}
              </div>
              <label class="q" style="display:block"><span class="q-title">Anything we should know? <span class="muted" style="font-weight:400">(optional)</span></span>
                <textarea name="comment" maxlength="2000" rows="2"></textarea></label>
              <div class="submit-row sticky-submit">
                <div class="form-error" id="err" role="alert"></div>
                <button class="btn btn-primary btn-big btn-block" type="submit" id="submit">Submit and continue</button>
              </div>
            </form>
          </div>
        </div>
      </section>`);

    const mq = window.matchMedia("(max-width: 960px)");
    canvas = new PathCanvas(document.getElementById("stage-host"), {
      imageUrl: sc.image_url, width: sc.width, height: sc.height, start: sc.start, goal: sc.goal, editable: true,
      lineStyle: S.default_line_style, maxHeight: mq.matches ? "60vh" : "calc(100vh - 250px)", onChange: refresh,
    });
    const $ = (id) => document.getElementById(id);
    const form = $("draw-form");
    let t0 = performance.now();
    let confidence = null;
    let playing = false;
    bindScales(form, (_, v) => { confidence = +v; $("err").textContent = ""; });
    bindNorms(form);
    form.addEventListener("input", () => { $("err").textContent = ""; });

    function refresh() {
      if (canvas.hasPath() || canvas.waiting) $("err").textContent = "";
      const wait = canvas.waiting;
      $("t-undo").disabled = !canvas.canUndo() || wait;
      $("t-clear").disabled = (!canvas.wps.length && !canvas.straight) || wait;
      $("t-style").querySelectorAll("button").forEach((b) => {
        b.setAttribute("aria-pressed", String(b.dataset.style === canvas.style)); b.disabled = wait; });
      const st = $("t-straight");
      if (st) { st.classList.toggle("is-on", canvas.straight); st.setAttribute("aria-pressed", String(canvas.straight)); st.disabled = wait; }
      const w = $("t-wait");
      w.classList.toggle("is-on", wait); w.setAttribute("aria-pressed", String(wait));
      w.innerHTML = wait ? `${ICON.straight}Draw a path instead` : `${ICON.wait}I'd wait`;
      canvas.showBadge(wait ? "The robot waits here" : (playing ? "Watch the clip" : ""));

      const ctx = $("ctx"), sel = canvas.sel;
      if (sel && sel.kind === "pt") {
        ctx.hidden = false;
        ctx.innerHTML = `<span class="ctx-label">Point selected</span><button class="btn" id="c-remove">${ICON.remove}Remove point</button>`;
        $("c-remove").onclick = () => canvas.removeSelected();
      } else if (sel && sel.kind === "seg" && canvas.segs[sel.j]) {
        const s = canvas.segs[sel.j];
        ctx.hidden = false;
        ctx.innerHTML = `<span class="ctx-label">This segment</span>
          <div class="seg seg-dark" role="group" aria-label="Segment shape">
            <button type="button" data-t="line" aria-pressed="${s.type === "line"}">${ICON.lines}Straight</button>
            <button type="button" data-t="curve" aria-pressed="${s.type === "curve"}">${ICON.curves}Curved</button></div>
          ${s.type === "curve" && (s.h1 || s.h2) ? `<button class="btn" id="c-reset">${ICON.reset}Reset curve</button>` : ""}`;
        ctx.querySelectorAll("[data-t]").forEach((b) => { b.onclick = () => canvas.setSegType(sel.j, b.dataset.t); });
        if ($("c-reset")) $("c-reset").onclick = () => canvas.resetSeg(sel.j);
      } else { ctx.hidden = true; ctx.innerHTML = ""; }

      let hint;
      if (playing) hint = "Watch what happens. You'll draw on the last frame.";
      else if (wait) hint = "You chose to wait. Tap “Draw a path instead” to change your mind.";
      else if (sel && sel.kind === "seg") hint = s_hint(canvas.segs[sel.j]);
      else if (sel) hint = "Drag the point to move it, or remove it.";
      else if (canvas.wps.length) hint = "Drag a point to move it, or the small diamond on a segment to bend it. Tap a diamond to change just that segment.";
      else if (canvas.straight) hint = "Straight to the flag. Tap the photo to add a point instead.";
      else hint = sc.goal ? "Tap the photo to place points between the robot and the flag." : "Tap the photo to place points. The path starts at the robot.";
      $("hint").textContent = hint;
    }
    const s_hint = (s) => s && s.type === "curve"
      ? "Drag the square handles to shape this curve, or drag the diamond to bend it."
      : "Drag the diamond to bend this segment into a curve, or switch it to Curved.";

    $("t-style").addEventListener("click", (e) => { const b = e.target.closest("[data-style]"); if (b) canvas.setStyle(b.dataset.style); });
    $("t-undo").onclick = () => canvas.undo();
    $("t-clear").onclick = () => canvas.clear();
    if ($("t-straight")) $("t-straight").onclick = () => canvas.setStraight(!canvas.straight);
    $("t-wait").onclick = () => canvas.setWaiting(!canvas.waiting);

    if (isVideo) {   // context clip: play to the decision moment, then draw on that frame
      const v = document.createElement("video");
      v.className = "pc-video"; v.src = sc.video_url; v.muted = true; v.playsInline = true; v.preload = "auto";
      v.setAttribute("playsinline", "");
      canvas.wrap.insertBefore(v, canvas.svg);
      const stopAt = sc.decision_time || null;
      const stop = () => { v.pause(); v.hidden = true; playing = false; canvas.setEnabled(true); refresh(); t0 = performance.now(); };
      const play = () => { v.hidden = false; v.currentTime = 0; playing = true; canvas.setEnabled(false); refresh(); v.play().catch(() => stop()); };
      v.addEventListener("timeupdate", () => { if (playing && stopAt && v.currentTime >= stopAt) stop(); });
      v.addEventListener("ended", () => { if (playing) stop(); });
      v.addEventListener("error", () => stop());
      $("t-replay").onclick = play;
      play();
    }
    canvas.ready.then(() => { if (!playing) t0 = performance.now(); refresh(); });
    refresh();

    if (!coachShown) {
      coachShown = true;
      const coach = document.createElement("div");
      coach.className = "coach";
      coach.innerHTML = `<h3>How to draw a path</h3>
        <ol><li>Tap the photo to place points. They connect${sc.goal ? " from the robot to the flag" : " from the robot"}.</li>
        <li>Choose <b>Straight lines</b> or <b>Curves</b>. Drag the small diamond on a segment to bend it.</li>
        <li>Drag a point to move it. Double-tap it to remove it.</li>
        <li>If no path feels acceptable, choose <b>I'd wait</b>.</li></ol>
        <button class="btn btn-primary" type="button">Got it</button>`;
      document.querySelector(".stage").appendChild(coach);
      coach.querySelector("button").onclick = () => coach.remove();
    }

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const norms = [...form.querySelectorAll("input[name=norm]:checked")].map((c) => c.value);
      const none = form["norm-none"].checked;
      const missing = [];
      if (!canvas.waiting && !canvas.hasPath()) missing.push(sc.goal ? "draw a path (or tap Go straight or I'd wait)" : "draw a path (or tap I'd wait)");
      if (!norms.length && !none) missing.push("say which rules your choice bends");
      if (!confidence) missing.push("say how sure you are");
      if (missing.length) { $("err").textContent = "Please " + missing.join(", ") + "."; return; }
      const btn = $("submit"); btn.disabled = true; $("err").textContent = "";
      const coach = document.querySelector(".coach"); if (coach) coach.remove();
      const path = canvas.waiting ? null : canvas.getPath();
      try {
        await api("/api/drawings", {
          participant_id: session.participant_id, scene_id: sc.id, decision: canvas.waiting ? "wait" : "path",
          points: path && path.points, segments: path && path.segments, style: path && path.style,
          norms: none ? [] : norms, confidence, comment: form.comment.value.trim(),
          duration_ms: Math.round(performance.now() - t0), n_edits: canvas.edits,
          viewport: { w: window.innerWidth, h: window.innerHeight, img_w: canvas.wrap.clientWidth, dpr: window.devicePixelRatio },
        });
        task.done = true;
        next();
      } catch (err) {
        $("err").textContent = err.message + " Please try again.";
        btn.disabled = false;
      }
    });
  }

  // ------------------------------------------------------------------ rate task
  function showRate(item, idx) {
    const n = session.rate.length;
    const qHtml = S.questions.map((q, qi) => `
      <div class="q rq" data-key="${esc(q.key)}">
        <span class="q-title"><span class="q-num">${qi + 1}.</span> ${esc(q.text)}</span>
        ${q.type === "yesno" ? scaleHtml(q.key, 2, "", "", [["yes", "Yes"], ["no", "No"]])
                             : scaleHtml(q.key, +q.points || 7, q.low || "", q.high || "")}
      </div>`).join("");
    mount(`
      <section class="screen task-screen rate-screen">
        <div class="task">
          <div class="main">
            <div class="stage">
              <div class="rate-frame" id="frame">
                <img id="rate-img" src="${esc(item.image_url)}" alt="A path drawn by an AI system on a scene from the robot's point of view">
                ${item.original_url ? `<img id="orig-img" src="${esc(item.original_url)}" alt="The same scene without the path" hidden>` : ""}
                <div class="pc-badge" id="orig-badge" hidden>Without the path</div>
              </div>
              <div class="stage-tools">
                ${item.original_url ? `<button class="btn" id="t-orig" type="button">${ICON.eye}Hold to see without the path</button>` : ""}
                <span class="spacer"></span>
                <button class="btn" id="t-zoom" type="button">${ICON.zoom}Enlarge</button>
              </div>
            </div>
          </div>
          <div class="side">
            <form class="panel" id="rate-form" novalidate>
              <p class="small muted" style="margin:0 0 .2rem">AI path ${idx + 1} of ${n}</p>
              <h2>How does this path seem to you?</h2>
              <p class="prompt">${esc(S.rating_prompt)}</p>
              ${qHtml}
              ${S.ask_norms_in_rating ? `<fieldset class="q" style="border:0;padding:0;margin-inline:0">
                <legend class="q-title">Which unwritten rules does this path break?</legend>
                <p class="q-help">Select all that apply.</p><div class="norm-list">${normsHtml("rnorm")}</div></fieldset>` : ""}
              <label class="q" style="display:block"><span class="q-title">Anything else? <span class="muted" style="font-weight:400">(optional)</span></span>
                <textarea name="comment" maxlength="2000" rows="2"></textarea></label>
              <div class="submit-row sticky-submit">
                <div class="form-error" id="err" role="alert"></div>
                <button class="btn btn-primary btn-big btn-block" type="submit" id="submit">Submit and continue</button>
              </div>
            </form>
          </div>
        </div>
      </section>`);
    const $ = (id) => document.getElementById(id);
    const form = $("rate-form"), img = $("rate-img"), frame = $("frame");
    const answers = {};
    const t0 = performance.now();
    const fit = () => {
      if (!img.naturalWidth) return;
      frame.style.aspectRatio = `${img.naturalWidth} / ${img.naturalHeight}`;
      frame.style.setProperty("--ar", (img.naturalWidth / img.naturalHeight).toFixed(4));
    };
    img.complete ? fit() : img.addEventListener("load", fit);
    bindScales(form, (k, v) => { answers[k] = v; $("err").textContent = ""; form.querySelector(`.rq[data-key="${k}"]`).classList.remove("is-missing"); });
    if (S.ask_norms_in_rating) bindNorms(form, "rnorm");

    if ($("t-orig")) {
      const orig = $("orig-img"), badge = $("orig-badge");
      const show = (v) => { orig.hidden = !v; badge.hidden = !v; };
      const b = $("t-orig");
      b.addEventListener("pointerdown", (e) => { e.preventDefault(); show(true); });
      ["pointerup", "pointerleave", "pointercancel"].forEach((ev) => b.addEventListener(ev, () => show(false)));
      b.addEventListener("keydown", (e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); show(true); } });
      b.addEventListener("keyup", () => show(false));
    }
    const zoom = () => {
      const ov = document.createElement("div");
      ov.className = "lightbox"; ov.tabIndex = 0;
      ov.innerHTML = `<img src="${esc(item.image_url)}" alt=""><p>Tap anywhere to close</p>`;
      const close = () => ov.remove();
      ov.addEventListener("click", close);
      ov.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
      document.body.appendChild(ov); ov.focus();
    };
    $("t-zoom").onclick = zoom;
    img.addEventListener("click", zoom);

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const missing = S.questions.filter((q) => answers[q.key] === undefined);
      form.querySelectorAll(".rq").forEach((el) => el.classList.toggle("is-missing", missing.some((q) => q.key === el.dataset.key)));
      const norms = S.ask_norms_in_rating ? [...form.querySelectorAll("input[name=rnorm]:checked")].map((c) => c.value) : [];
      const noneNorm = S.ask_norms_in_rating ? form["rnorm-none"].checked : true;
      const msgs = [];
      if (missing.length) msgs.push(`answer question${missing.length > 1 ? "s" : ""} ${missing.map((q) => S.questions.indexOf(q) + 1).join(", ")}`);
      if (!norms.length && !noneNorm) msgs.push("say which rules the path breaks (or None of these)");
      if (msgs.length) {
        $("err").textContent = "Please " + msgs.join(" and ") + ".";
        const first = form.querySelector(".rq.is-missing"); if (first) first.scrollIntoView({ block: "center", behavior: "smooth" });
        return;
      }
      const btn = $("submit"); btn.disabled = true;
      try {
        await api("/api/vlm_ratings", { participant_id: session.participant_id, k: item.k, answers, norms,
          comment: form.comment.value.trim(), duration_ms: Math.round(performance.now() - t0) });
        item.done = true;
        next();
      } catch (err) {
        $("err").textContent = err.message + " Please try again.";
        btn.disabled = false;
      }
    });
  }

  // ------------------------------------------------------------------ boot
  (async function boot() {
    const pid = store.get();
    if (pid && !KIOSK) {
      try {
        session = await api(`/api/session/${pid}`);
        const open = session.draw.some((t) => !t.done) || session.rate === null || session.rate.some((t) => !t.done);
        if (open) return next();
      } catch (e) { /* start fresh */ }
      store.clear();
    }
    showRegister();
  })();
})();
