/* SCAND bag-video still collection tab. */
(function () {
  "use strict";
  const { esc, api, toast } = Admin;
  const $ = (id) => document.getElementById(id);

  const DEFAULT_MODES = [
    "Wait", "Follow", "Overtake", "Merge", "Leave walkway", "Regular Walk",
  ];

  // Equal split of bags 0–137 across 4 teammates.
  const SECTIONS = {
    1: { lo: 0, hi: 34, label: "0–34" },
    2: { lo: 35, hi: 69, label: "35–69" },
    3: { lo: 70, hi: 103, label: "70–103" },
    4: { lo: 104, hi: 137, label: "104–137" },
  };
  const SECTION_KEY = "trajlab.collect.section";

  let videos = [];
  let labels = { behavior_modes: DEFAULT_MODES, environments: [] };
  let current = null;
  let captures = [];
  let loadedOnce = false;
  let section = localStorage.getItem(SECTION_KEY) || "all";
  if (section !== "all" && !SECTIONS[section]) section = "all";

  function fmtTime(s) {
    if (!Number.isFinite(s) || s < 0) return "0:00";
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${String(sec).padStart(2, "0")}`;
  }

  function fillModeChecks() {
    const modes = (labels.behavior_modes && labels.behavior_modes.length)
      ? labels.behavior_modes : DEFAULT_MODES;
    const el = $("collect-behavior-modes");
    const prev = new Set(selectedModes());
    el.innerHTML = modes.map((m) => `
      <label class="check"><input type="checkbox" name="behavior_mode" value="${esc(m)}"${prev.has(m) ? " checked" : ""}>
        <span>${esc(m)}</span></label>`).join("");
  }

  function fillEnvList() {
    $("collect-env-list").innerHTML = (labels.environments || []).map((x) => `<option value="${esc(x)}">`).join("");
  }

  function selectedModes() {
    return [...document.querySelectorAll("#collect-behavior-modes input[type=checkbox]:checked")]
      .map((el) => el.value);
  }

  function videoNum(v) {
    const n = parseInt(v.stem, 10);
    return Number.isFinite(n) ? n : null;
  }

  function inSection(v) {
    if (section === "all") return true;
    const s = SECTIONS[section];
    if (!s) return true;
    const n = videoNum(v);
    return n != null && n >= s.lo && n <= s.hi;
  }

  function syncSectionUI() {
    $("collect-section").querySelectorAll("button[data-section]").forEach((b) => {
      b.setAttribute("aria-pressed", String(b.dataset.section === section));
    });
    const s = SECTIONS[section];
    $("collect-section-hint").textContent = s
      ? `Showing bags ${s.label} (${s.hi - s.lo + 1} bags). Saved on this browser so you keep your section.`
      : "138 bags split across 4 people — pick yours so the list only shows your range.";
  }

  function filteredVideos() {
    const q = ($("collect-filter").value || "").trim().toLowerCase();
    return videos.filter((v) => {
      if (!inSection(v)) return false;
      if (!q) return true;
      return v.name.toLowerCase().includes(q) || v.stem.includes(q);
    });
  }

  function renderVideoList() {
    const rows = filteredVideos();
    const el = $("collect-video-list");
    const totalInSection = videos.filter(inSection).length;
    $("collect-video-count").textContent = totalInSection
      ? `(${rows.length}${rows.length !== totalInSection ? ` / ${totalInSection}` : ""})`
      : "";
    if (!rows.length) {
      el.innerHTML = `<p class="muted small" style="padding:.5rem">${
        videos.length ? "No videos in this section yet (still converting)…" : "No videos yet. Waiting on bag conversion…"
      }</p>`;
      return;
    }
    el.innerHTML = rows.map((v) => {
      const size = v.has_preview && v.preview_mb != null ? `${v.preview_mb} MB preview` : `${v.size_mb} MB`;
      return `<button type="button" class="collect-video-item${current && current.name === v.name ? " is-active" : ""}" data-name="${esc(v.name)}">
        <span class="collect-video-name">${esc(v.stem)}</span>
        <span class="tiny muted">${size}${v.n_captures ? ` · ${v.n_captures} still${v.n_captures === 1 ? "" : "s"}` : ""}${v.has_preview ? "" : " · full"}</span>
      </button>`;
    }).join("");
  }

  function modeLabel(c) {
    const modes = c.behavior_modes && c.behavior_modes.length
      ? c.behavior_modes
      : (c.behavior_mode ? [c.behavior_mode] : []);
    return modes.join(", ") || "—";
  }

  function captureInView(c) {
    if ($("collect-this-video-only").checked && current) return c.video_name === current.name;
    if (section === "all") return true;
    const s = SECTIONS[section];
    const n = parseInt(String(c.video_name).replace(/\.mp4$/i, ""), 10);
    return Number.isFinite(n) && n >= s.lo && n <= s.hi;
  }

  function renderGallery() {
    const rows = captures.filter(captureInView);
    $("collect-count").textContent = rows.length ? `(${rows.length})` : "";
    const el = $("collect-gallery");
    if (!rows.length) {
      el.innerHTML = `<p class="muted small">No stills captured yet${section !== "all" ? " in this section" : ""}.</p>`;
      return;
    }
    el.innerHTML = rows.map((c) => `
      <figure class="collect-thumb">
        <img src="${esc(c.image_url)}" alt="" loading="lazy">
        <figcaption>
          <div><b>${esc(modeLabel(c))}</b> · ${esc(c.environment)}</div>
          <div class="tiny muted">${esc(c.video_name)} @ ${fmtTime(c.frame_time)}</div>
          <button type="button" class="btn btn-quiet btn-danger small" data-del="${esc(c.id)}">Delete</button>
        </figcaption>
      </figure>`).join("");
  }

  function updateTime() {
    const v = $("collect-video");
    $("collect-time").textContent = `${fmtTime(v.currentTime)} / ${fmtTime(v.duration)}`;
  }

  function unloadVideo() {
    const v = $("collect-video");
    v.pause();
    v.removeAttribute("src");
    v.load();
  }

  async function selectVideo(name) {
    const vmeta = videos.find((v) => v.name === name);
    if (!vmeta) return;
    if (current && current.name === name) return;
    current = vmeta;
    renderVideoList();
    const v = $("collect-video");
    unloadVideo();
    $("collect-empty").hidden = true;
    v.hidden = false;
    v.preload = "metadata";
    v.src = vmeta.url;
    v.playbackRate = +(document.querySelector("#collect-speed button[aria-pressed=true]")?.dataset.v || 1);
    $("collect-current-name").textContent = vmeta.has_preview
      ? `${vmeta.name} (light preview)`
      : `${vmeta.name} (full file — preview not ready yet)`;
    $("collect-status").textContent = vmeta.has_preview
      ? ""
      : "Playing the full video until a small preview is ready — seeking may feel heavy.";
    await loadCaptures();
  }

  async function loadVideos() {
    const data = await api("/api/admin/collect/videos");
    videos = data.videos || [];
    labels = data.labels || { behavior_modes: DEFAULT_MODES, environments: [] };
    if (!labels.behavior_modes || !labels.behavior_modes.length) labels.behavior_modes = DEFAULT_MODES;
    fillModeChecks();
    fillEnvList();
    const nPrev = videos.filter((v) => v.has_preview).length;
    $("collect-dirs").innerHTML =
      `Videos: <code>${esc(data.videos_dir)}</code> · Dataset: <code>${esc(data.dataset_dir)}</code> · ` +
      `${data.n_captures} still${data.n_captures === 1 ? "" : "s"} · ${nPrev}/${videos.length} light previews`;
    renderVideoList();
    if (current) {
      const still = videos.find((v) => v.name === current.name);
      if (still) {
        const urlChanged = still.url !== current.url;
        current = still;
        if (urlChanged) {
          const t = $("collect-video").currentTime || 0;
          $("collect-video").src = still.url;
          $("collect-video").currentTime = t;
          $("collect-current-name").textContent = `${still.name} (light preview)`;
          $("collect-status").textContent = "";
        }
      } else {
        current = null;
        unloadVideo();
        $("collect-video").hidden = true;
        $("collect-empty").hidden = false;
      }
    }
    await loadCaptures();
    loadedOnce = true;
  }

  async function loadCaptures() {
    const q = $("collect-this-video-only").checked && current ? `?video=${encodeURIComponent(current.name)}` : "";
    captures = await api(`/api/admin/collect/captures${q}`);
    renderGallery();
  }

  async function capture() {
    const v = $("collect-video");
    const status = $("collect-status");
    if (!current || !v.src || !Number.isFinite(v.currentTime)) {
      status.textContent = "Load a video and pause on a frame first.";
      return;
    }
    const modes = selectedModes();
    const environment = $("collect-environment").value.trim();
    if (!modes.length) {
      status.textContent = "Select at least one behavior mode.";
      return;
    }
    if (!environment) {
      status.textContent = "Environment is required.";
      return;
    }
    v.pause();
    status.textContent = "Saving still from full video…";
    const fd = new FormData();
    fd.append("video_name", current.name);
    fd.append("frame_time", String(v.currentTime));
    modes.forEach((m) => fd.append("behavior_modes", m));
    fd.append("environment", environment);
    fd.append("notes", $("collect-notes").value.trim());
    try {
      const row = await api("/api/admin/collect/capture", "POST", fd);
      if (environment && !(labels.environments || []).includes(environment)) {
        labels.environments = [...(labels.environments || []), environment].sort((a, b) => a.localeCompare(b));
        fillEnvList();
      }
      current.n_captures = (current.n_captures || 0) + 1;
      const vrow = videos.find((x) => x.name === current.name);
      if (vrow) vrow.n_captures = current.n_captures;
      renderVideoList();
      captures = [row, ...captures.filter((c) => c.id !== row.id)];
      renderGallery();
      status.textContent = `Saved ${row.image_file} (${modeLabel(row)} · ${environment})`;
      toast("Still saved");
    } catch (err) {
      status.textContent = err.message || String(err);
    }
  }

  $("collect-video-list").addEventListener("click", (e) => {
    const b = e.target.closest("[data-name]");
    if (b) selectVideo(b.dataset.name);
  });
  $("collect-filter").addEventListener("input", renderVideoList);
  $("collect-refresh").addEventListener("click", () => loadVideos().catch((e) => toast(e.message)));
  $("collect-capture").addEventListener("click", () => capture());
  $("collect-this-video-only").addEventListener("change", () => renderGallery());
  $("collect-section").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-section]");
    if (!b) return;
    section = b.dataset.section;
    localStorage.setItem(SECTION_KEY, section);
    syncSectionUI();
    renderVideoList();
    renderGallery();
    if (current && !inSection(current)) {
      current = null;
      unloadVideo();
      $("collect-video").hidden = true;
      $("collect-empty").hidden = false;
      $("collect-current-name").textContent = "";
    }
  });
  syncSectionUI();
  $("collect-gallery").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-del]");
    if (!b) return;
    if (!confirm("Delete this still from the SCAND dataset?")) return;
    try {
      await api(`/api/admin/collect/captures/${b.dataset.del}`, "DELETE");
      captures = captures.filter((c) => c.id !== b.dataset.del);
      if (current) {
        current.n_captures = Math.max(0, (current.n_captures || 1) - 1);
        const vrow = videos.find((x) => x.name === current.name);
        if (vrow) vrow.n_captures = current.n_captures;
        renderVideoList();
      }
      renderGallery();
      toast("Deleted");
    } catch (err) { toast(err.message); }
  });

  $("collect-speed").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-v]");
    if (!b) return;
    $("collect-speed").querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    $("collect-video").playbackRate = +b.dataset.v;
  });

  const video = $("collect-video");
  video.hidden = true;
  video.preload = "none";
  video.addEventListener("timeupdate", updateTime);
  video.addEventListener("loadedmetadata", updateTime);

  fillModeChecks();

  window.AdminCollect = {
    load: () => {
      if (loadedOnce) return Promise.resolve();
      return loadVideos().catch((e) => { $("collect-status").textContent = e.message; });
    },
  };
})();
