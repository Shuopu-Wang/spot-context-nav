/* Helpers shared by the admin pages. */
(function (global) {
  "use strict";
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function api(path, method = "GET", body) {
    const opt = { method, headers: {} };
    if (body instanceof FormData) opt.body = body;
    else if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
    const res = await fetch(path, opt);
    if (res.status === 401) { location.href = "/admin/login?next=" + encodeURIComponent(location.pathname); throw new Error("signed out"); }
    let data = null;
    try { data = await res.json(); } catch (e) { /* non-JSON */ }
    if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
    return data;
  }

  let toastTimer;
  function toast(msg) {
    let t = document.querySelector(".toast");
    if (!t) { t = document.createElement("div"); t.className = "toast"; t.setAttribute("role", "status"); document.body.appendChild(t); }
    t.textContent = msg; t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 2600);
  }

  async function copy(text) {
    try { await navigator.clipboard.writeText(text); toast("Copied"); }
    catch (e) { toast("Select the text and press Ctrl+C to copy"); }
  }

  const pct = (v, d = 0) => (v == null ? "–" : `${(v * 100).toFixed(d)}%`);
  const dist = (v) => (v == null ? "–" : `${(v * 100).toFixed(1)}%`);   // fraction of image diagonal -> %
  const num = (v, d = 2) => (v == null ? "–" : (+v).toFixed(d));
  const ci = (c, f = pct) => (c ? ` <span class="muted tiny">[${f(c[0])}, ${f(c[1])}]</span>` : "");
  const roleLabel = (r) => (r === "team" ? "Team" : "Survey");

  /* Sequential blue ramp (dataviz reference palette, steps 100 -> 700) for 0..1 values. */
  const RAMP = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf",
                "#1c5cab", "#184f95", "#104281", "#0d366b"];
  const heat = (v) => RAMP[Math.max(0, Math.min(RAMP.length - 1, Math.round(v * (RAMP.length - 1))))];

  /* Match an uploaded VLM image's file name to a training image. */
  const norm = (s) => String(s || "").toLowerCase().replace(/\.[a-z0-9]+$/, "").replace(/[^a-z0-9]+/g, " ").trim();
  function matchScene(filename, scenes) {
    const f = norm(filename), compact = f.replace(/ /g, "");
    let best = null, bestLen = 0;
    for (const s of scenes) {
      if (compact.includes(s.id.slice(0, 8))) return s.id;
      for (const key of [norm(s.source_name), norm(s.title)]) {
        if (key.length >= 3 && (f.startsWith(key) || f.includes(key)) && key.length > bestLen) { best = s.id; bestLen = key.length; }
      }
    }
    return best;
  }

  /* Grab a frame from a video (File or URL) at time t (seconds; null = last frame). */
  function captureFrame(src, t) {
    return new Promise((resolve, reject) => {
      const v = document.createElement("video");
      v.muted = true; v.playsInline = true; v.preload = "auto"; v.crossOrigin = "anonymous";
      const url = src instanceof Blob ? URL.createObjectURL(src) : src;
      v.src = url;
      v.onerror = () => reject(new Error("This browser can't decode that video. Convert it to MP4 (H.264) first."));
      v.onloadedmetadata = () => {
        const target = t == null ? Math.max(0, v.duration - 0.05) : Math.min(t, v.duration);
        v.onseeked = () => {
          const c = document.createElement("canvas");
          c.width = v.videoWidth; c.height = v.videoHeight;
          c.getContext("2d").drawImage(v, 0, 0);
          c.toBlob((b) => { if (src instanceof Blob) URL.revokeObjectURL(url); resolve({ blob: b, time: target, duration: v.duration }); }, "image/png");
        };
        v.currentTime = target;
      };
    });
  }

  /* Wire a drop zone + hidden file input. onFiles(File[]) */
  function dropZone(zone, input, onFiles) {
    zone.addEventListener("click", () => input.click());
    zone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
    zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("is-over"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("is-over"));
    zone.addEventListener("drop", (e) => { e.preventDefault(); zone.classList.remove("is-over"); onFiles([...e.dataTransfer.files]); });
    input.addEventListener("change", () => { onFiles([...input.files]); input.value = ""; });
  }

  global.Admin = { esc, api, toast, copy, pct, dist, num, ci, roleLabel, heat, RAMP, matchScene, captureFrame, dropZone };
})(window);
