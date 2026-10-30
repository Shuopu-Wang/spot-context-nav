(() => {
  const canvas = document.getElementById('canvas');
  const ctx = canvas.getContext('2d');
  const logEl = document.getElementById('log');
  const projMeta = document.getElementById('projMeta');

  let mode = 'live'; // live | capture | depth
  let drawMode = 'click';
  let img = new Image();
  let points = []; // display-space points {x,y} relative to canvas CSS box... we store in canvas pixel coords
  let projectedPath = []; // image-space overlay from server optional
  let imagePixels = [];
  let captureId = '';
  let imageW = 0;
  let imageH = 0;
  let drawing = false;
  let status = {};
  let showDepthValid = false;
  let depthReturnMode = 'live'; // mode to restore when toggling depth off

  function log(msg) {
    const t = new Date().toLocaleTimeString();
    logEl.textContent = `[${t}] ${msg}\n` + logEl.textContent;
  }

  function setDot(id, ok, label) {
    const el = document.getElementById(id);
    el.textContent = label;
    el.className = ok === true ? 'ok' : ok === false ? 'bad' : 'warn';
  }

  async function refreshStatus() {
    try {
      const r = await fetch('/api/status');
      status = await r.json();
      setDot('sCamera', status.camera_ready, `camera: ${status.camera_ready ? 'ok' : 'waiting'}`);
      setDot('sOdom', status.odom_ready, `odom: ${status.odom_ready ? 'ok' : 'waiting'}`);
      setDot('sTf', status.tf_ready, `tf: ${status.tf_ready ? 'ok' : 'missing'}`);
      setDot('sCapture', status.capture_ready, `capture: ${status.capture_id || 'none'}`);
      setDot('sArmed', status.armed, `armed: ${status.armed}${status.allow_cmd_vel ? '' : ' (dry-run)'}`);
      if (status.deadman_required) {
        setDot(
          'sDeadman',
          status.deadman_held,
          `deadman: ${status.deadman_held ? 'HELD' : 'release'} (${status.deadman_hint || 'R1'})`
        );
      } else {
        setDot('sDeadman', null, 'deadman: off');
      }
      const stateOk = status.state === 'following';
      setDot('sState', stateOk ? true : (status.state === 'waiting_deadman' ? null : false), `state: ${status.state || '—'}`);
      const banner = document.getElementById('deadmanBanner');
      if (banner) {
        banner.innerHTML = status.deadman_hint
          ? `Live motion requires <strong>${status.deadman_hint}</strong>. Flip Spektrum out of AUTONOMOUS to stop. Manual mode still overrides.`
          : 'Live motion requires Spektrum <strong>D-switch in AUTONOMOUS</strong>.';
      }
      captureId = status.capture_id || captureId;
      if (status.projection && status.projection.message) {
        projMeta.textContent =
          `projection: ${status.projection.message} | length=${(status.projection.length_m || 0).toFixed(2)} m`;
      }
    } catch (e) {
      setDot('sCamera', false, 'camera: ui offline');
    }
  }

  async function heartbeat() {
    try { await fetch('/api/heartbeat', { method: 'POST' }); } catch (_) {}
  }

  function canvasDisplaySize() {
    const rect = canvas.getBoundingClientRect();
    return { width: rect.width, height: rect.height };
  }

  function eventToCanvasPixel(ev) {
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    return {
      x: (ev.clientX - rect.left) * scaleX,
      y: (ev.clientY - rect.top) * scaleY,
      // Also keep display (CSS) coords for letterbox-aware server mapping against natural image
      dx: ev.clientX - rect.left,
      dy: ev.clientY - rect.top,
      dw: rect.width,
      dh: rect.height,
    };
  }

  function redraw() {
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    if (img.complete && img.naturalWidth > 0) {
      // object-fit: contain equivalent
      const scale = Math.min(canvas.width / img.naturalWidth, canvas.height / img.naturalHeight);
      const dw = img.naturalWidth * scale;
      const dh = img.naturalHeight * scale;
      const ox = (canvas.width - dw) / 2;
      const oy = (canvas.height - dh) / 2;
      ctx.drawImage(img, ox, oy, dw, dh);
    }
    // path overlay in canvas pixels
    if (points.length) {
      ctx.strokeStyle = '#3d9cf0';
      ctx.fillStyle = '#e0a106';
      ctx.lineWidth = 2;
      ctx.beginPath();
      points.forEach((p, i) => {
        if (i === 0) ctx.moveTo(p.cx, p.cy);
        else ctx.lineTo(p.cx, p.cy);
      });
      ctx.stroke();
      points.forEach((p, i) => {
        ctx.beginPath();
        ctx.arc(p.cx, p.cy, 5, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = '#fff';
        ctx.font = '12px sans-serif';
        ctx.fillText(String(i + 1), p.cx + 7, p.cy - 7);
        ctx.fillStyle = '#e0a106';
      });
    }
  }

  function loadImage(url) {
    return new Promise((resolve, reject) => {
      const next = new Image();
      next.onload = () => {
        img = next;
        // Keep internal canvas resolution matching image for crisp overlay if capture known
        if (mode === 'capture' && imageW && imageH) {
          canvas.width = imageW;
          canvas.height = imageH;
        } else if (next.naturalWidth) {
          canvas.width = next.naturalWidth;
          canvas.height = next.naturalHeight;
        }
        redraw();
        resolve();
      };
      next.onerror = reject;
      next.src = url + (url.includes('?') ? '&' : '?') + 't=' + Date.now();
    });
  }

  async function tickLive() {
    if (mode === 'live') {
      try { await loadImage('/api/live.jpg'); } catch (_) {}
      return;
    }
    if (mode === 'depth' && depthReturnMode === 'live') {
      try { await loadImage('/api/depth_valid.jpg?source=live'); } catch (_) {}
    }
  }

  function depthValidUrl() {
    // Prefer frozen capture depth when available so overlay matches projection.
    if (captureId || depthReturnMode === 'capture') {
      return '/api/depth_valid.jpg?source=capture';
    }
    return '/api/depth_valid.jpg?source=live';
  }

  async function setDepthValidVisible(on) {
    showDepthValid = on;
    const btn = document.getElementById('btnDepthValid');
    if (on) {
      if (mode !== 'depth') depthReturnMode = mode;
      mode = 'depth';
      btn.textContent = 'Hide valid depth';
      try {
        await loadImage(depthValidUrl());
        log('Valid depth overlay: green=usable, magenta=invalid');
      } catch (_) {
        log('Depth overlay unavailable (need camera or capture)');
        showDepthValid = false;
        mode = depthReturnMode;
        btn.textContent = 'Show valid depth';
      }
    } else {
      btn.textContent = 'Show valid depth';
      mode = depthReturnMode;
      if (mode === 'capture') {
        await loadImage('/api/capture.jpg');
      } else {
        mode = 'live';
        await tickLive();
      }
    }
  }

  document.querySelectorAll('input[name=mode]').forEach((el) => {
    el.addEventListener('change', () => { drawMode = el.value; });
  });

  canvas.addEventListener('pointerdown', (ev) => {
    if (mode === 'depth' && depthReturnMode === 'capture') {
      // Allow clicking while viewing capture depth overlay.
    } else if (mode !== 'capture') {
      log('Capture/freeze a frame before drawing');
      return;
    }
    const p = eventToCanvasPixel(ev);
    if (drawMode === 'click') {
      points.push({ cx: p.x, cy: p.y, dx: p.dx, dy: p.dy, dw: p.dw, dh: p.dh });
      redraw();
    } else {
      drawing = true;
      points.push({ cx: p.x, cy: p.y, dx: p.dx, dy: p.dy, dw: p.dw, dh: p.dh });
      canvas.setPointerCapture(ev.pointerId);
      redraw();
    }
  });

  canvas.addEventListener('pointermove', (ev) => {
    if (!drawing || mode !== 'capture') return;
    const p = eventToCanvasPixel(ev);
    const last = points[points.length - 1];
    if (!last || Math.hypot(p.x - last.cx, p.y - last.cy) > 8) {
      points.push({ cx: p.x, cy: p.y, dx: p.dx, dy: p.dy, dw: p.dw, dh: p.dh });
      redraw();
    }
  });

  canvas.addEventListener('pointerup', () => { drawing = false; });

  document.getElementById('btnLive').onclick = async () => {
    showDepthValid = false;
    document.getElementById('btnDepthValid').textContent = 'Show valid depth';
    mode = 'live';
    depthReturnMode = 'live';
    points = [];
    log('Showing live camera');
    await tickLive();
  };

  document.getElementById('btnCapture').onclick = async () => {
    const r = await fetch('/api/capture', { method: 'POST' });
    const data = await r.json();
    log(data.message || JSON.stringify(data));
    if (!data.success) return;
    showDepthValid = false;
    document.getElementById('btnDepthValid').textContent = 'Show valid depth';
    mode = 'capture';
    depthReturnMode = 'capture';
    points = [];
    imagePixels = [];
    captureId = data.capture_id;
    imageW = data.image_width;
    imageH = data.image_height;
    await loadImage('/api/capture.jpg');
  };

  document.getElementById('btnDepthValid').onclick = async () => {
    await setDepthValidVisible(!showDepthValid);
  };

  document.getElementById('btnClear').onclick = () => {
    points = [];
    imagePixels = [];
    projectedPath = [];
    redraw();
    log('Cleared drawn path');
  };

  document.getElementById('btnProject').onclick = async () => {
    if (!points.length) {
      log('No points to project');
      return;
    }
    // Send display coordinates relative to the canvas CSS box; server applies letterbox mapping
    // against the frozen image. Because we set canvas.width=imageW, CSS letterboxing of the
    // <canvas> element itself is handled here by converting canvas-pixel -> normalized display.
    // Simplest correct path: convert canvas pixels directly (canvas buffer == image pixels when captured).
    const pixels = points.map((p) => ({
      u: Math.round(p.cx),
      v: Math.round(p.cy),
    }));
    const body = {
      capture_id: captureId,
      pixels,
    };
    const r = await fetch('/api/project', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    log(data.message || JSON.stringify(data));
    if (data.point_statuses) {
      log('statuses: ' + data.point_statuses.join(', '));
    }
    imagePixels = data.image_pixels || pixels;
    projMeta.textContent =
      `accepted=${data.accepted_count} rejected=${data.rejected_count} length=${(data.length_m || 0).toFixed(2)} m`;
    if (data.path) {
      projectedPath = data.path;
    }
  };

  document.getElementById('btnArm').onclick = async () => {
    const r = await fetch('/api/arm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ armed: true }),
    });
    const data = await r.json();
    log(data.message || JSON.stringify(data));
    refreshStatus();
  };

  document.getElementById('btnExecute').onclick = async () => {
    const r = await fetch('/api/execute', { method: 'POST' });
    const data = await r.json();
    log(data.message || JSON.stringify(data));
    refreshStatus();
  };

  document.getElementById('btnStop').onclick = async () => {
    const r = await fetch('/api/stop', { method: 'POST' });
    const data = await r.json();
    log(data.message || JSON.stringify(data));
    refreshStatus();
  };

  // When the tab is hidden/closed, heartbeats stop -> follower disarms if UI-executing.
  window.addEventListener('beforeunload', () => {
    navigator.sendBeacon('/api/stop');
  });

  setInterval(refreshStatus, 500);
  setInterval(heartbeat, 400);
  setInterval(tickLive, 400);
  refreshStatus();
  tickLive();
  log('UI ready. Default robot launch is dry-run/disarmed.');
})();
