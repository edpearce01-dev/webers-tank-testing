// Weber's Tank Testing — 360° Guided Panorama Scanner & Anti-Blur Auto-Shutter
const PANO_ARC_LABELS = ['1: 0°–90°', '2: 90°–180°', '3: 180°–270°', '4: 270°–360°', '5: Tank/Tag'];
const ANALYZE_W = 120;
const ANALYZE_H = 80;
const AUTO_HOLD_TARGET = 8; // ~0.88s of uninterrupted rock-steady sharp focus

let MEDIA_STREAM = null;
let FACING_MODE = 'environment';
let TORCH_ON = false;
let CAPTURED_B64 = [];
let CAPTURED_URLS = [];

let PANO_DIR = 'ltr';
let AUTO_SHUTTER_ON = localStorage.getItem('webers_auto_shutter') !== '0';
let PANO_TIMER = null;
let MOTION_CANVAS = null;
let PREV_LUMA = null;
let LAST_SNAP_LUMA = null;
let LAST_CUR_LUMA = null;
let PANO_MOVED_SINCE_SNAP = true;

let IS_STEADY = false;
let IS_ULTRA_STEADY = false;
let IS_SHARP = true;
let PEAK_EDGE_SCORE = 0;
let PREV_EDGE_SCORE = 0;
let AUTO_HOLD_TICKS = 0;
let AUTO_SNAPPING_LOCK = false;

let HAS_GYRO = false;
let LIVE_BETA = null, LIVE_GAMMA = null;
let LOCK_BETA = null, LOCK_GAMMA = null;
let AUDIO_CTX = null;

function zoomPhoto(src) {
  document.getElementById('photoZoomImg').src = src;
  document.getElementById('photoZoomModal').hidden = false;
}

function syncAutoShutterBtn() {
  const btn = document.getElementById('btnAutoShutter');
  if (!btn) return;
  btn.classList.toggle('on', AUTO_SHUTTER_ON);
  btn.textContent = AUTO_SHUTTER_ON ? '🤖 Auto-Snap: ON' : '🤖 Auto-Snap: OFF';
}

function toggleAutoShutter() {
  AUTO_SHUTTER_ON = !AUTO_SHUTTER_ON;
  localStorage.setItem('webers_auto_shutter', AUTO_SHUTTER_ON ? '1' : '0');
  AUTO_HOLD_TICKS = 0;
  syncAutoShutterBtn();
  updatePanoAlignmentHUD();
}

function playShutterFeedback(allFourDone = false) {
  try {
    const fl = document.getElementById('panoFlash');
    if (fl) {
      fl.classList.add('pop');
      setTimeout(() => fl.classList.remove('pop'), 60);
    }
    if (navigator.vibrate) navigator.vibrate(allFourDone ? [40, 60, 55] : 35);
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    if (!AUDIO_CTX) AUDIO_CTX = new Ctx();
    if (AUDIO_CTX.state === 'suspended') AUDIO_CTX.resume();
    const now = AUDIO_CTX.currentTime;
    const beep = (freq, start, dur) => {
      const osc = AUDIO_CTX.createOscillator();
      const gain = AUDIO_CTX.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(freq, start);
      gain.gain.setValueAtTime(0.14, start);
      gain.gain.exponentialRampToValueAtTime(0.001, start + dur);
      osc.connect(gain);
      gain.connect(AUDIO_CTX.destination);
      osc.start(start);
      osc.stop(start + dur);
    };
    beep(880, now, 0.07);
    if (allFourDone) beep(1175, now + 0.09, 0.11);
  } catch (_) {}
}

function handlePanoOrientation(e) {
  if (e.beta === null || e.gamma === null) return;
  HAS_GYRO = true;
  LIVE_BETA = e.beta;
  LIVE_GAMMA = e.gamma;
}

function togglePanoDir() {
  PANO_DIR = PANO_DIR === 'ltr' ? 'rtl' : 'ltr';
  document.getElementById('btnPanoDir').textContent = PANO_DIR === 'ltr' ? 'Sweep: L→R' : 'Sweep: R→L';
  document.getElementById('panoGhostWrap').classList.toggle('rtl', PANO_DIR === 'rtl');
  clearAngles();
}

function renderPanoStepBar() {
  const n = CAPTURED_B64.length;
  const bar = document.getElementById('panoStepsBar');
  if (!bar) return;
  bar.innerHTML = PANO_ARC_LABELS.map((lbl, idx) => {
    const cls = idx < n ? 'done' : (idx === n ? 'active' : '');
    return `<span class="pano-step-pill ${cls}">${idx < n ? '✓ ' : ''}${lbl}</span>`;
  }).join('');

  const hint = document.getElementById('panoHintText');
  const snapBtn = document.getElementById('btnSnapAngle');
  const track = document.getElementById('panoTrack');

  if (n === 0) {
    track.style.top = '24%'; track.style.height = '52%';
    hint.textContent = AUTO_SHUTTER_ON
      ? 'Arc 1/4: Hold steady on starting stamps — Auto-Snap fires when sharp'
      : 'Arc 1/4: Frame starting shoulder stamps on center line';
    snapBtn.textContent = '📸 Snap Arc 1/4';
  } else if (n < 4) {
    track.style.top = '24%'; track.style.height = '52%';
    hint.textContent = `Arc ${n + 1}/4: Line last stamp into blue ghost box & keep square centered`;
    snapBtn.textContent = `📸 Snap Arc ${n + 1}/4`;
  } else if (n === 4) {
    track.style.top = '10%'; track.style.height = '78%';
    document.getElementById('panoGhostWrap').hidden = true;
    hint.textContent = '360° Crown Done! Optional Step 5: Step back for Full Tank + Tag, or tap Populate';
    snapBtn.textContent = '📸 + Full Tank / Tag';
  } else {
    hint.textContent = 'All angles captured — tap "✅ Populate Form" below!';
    snapBtn.textContent = `📸 Max Angles (${n})`;
  }
}

function isGyroOnTrack() {
  if (!HAS_GYRO || LIVE_BETA === null) return { onTrack: true, dy: 0, rot: 0 };
  const refBeta = LOCK_BETA !== null ? LOCK_BETA : LIVE_BETA;
  const refGamma = LOCK_GAMMA !== null ? LOCK_GAMMA : 0;
  const dBeta = LIVE_BETA - refBeta;
  const dGamma = LIVE_GAMMA - refGamma;
  const dy = Math.max(-42, Math.min(42, dBeta * 3.0));
  const rot = Math.max(-16, Math.min(16, dGamma * 0.75));
  const onTrack = Math.abs(dBeta) <= 6.5 && Math.abs(dGamma) <= 9.0;
  return { onTrack, dy, rot };
}

function updatePanoAlignmentHUD() {
  const floatBox = document.getElementById('panoFloatBox');
  const targetBox = document.getElementById('panoTargetBox');
  const track = document.getElementById('panoTrack');
  const steadyPill = document.getElementById('panoSteadyPill');
  const snapBtn = document.getElementById('btnSnapAngle');
  const fillEl = document.getElementById('autoSnapFill');
  const pctEl = document.getElementById('autoSnapPct');
  if (!floatBox) return;

  const { onTrack, dy, rot } = isGyroOnTrack();
  floatBox.style.transform = `translateY(${dy.toFixed(1)}px) rotate(${rot.toFixed(1)}deg)`;

  const readyManual = onTrack && IS_STEADY && IS_SHARP;
  floatBox.classList.toggle('aligned', onTrack);
  targetBox.classList.toggle('aligned', readyManual);
  track.classList.toggle('aligned', readyManual);
  snapBtn.classList.toggle('ready-pulse', readyManual && CAPTURED_B64.length < 6);

  const pct = Math.min(100, Math.round((AUTO_HOLD_TICKS / AUTO_HOLD_TARGET) * 100));
  fillEl.style.height = pct + '%';
  pctEl.textContent = pct > 0 ? `AUTO ${pct}%` : '';

  if (CAPTURED_B64.length >= 4) {
    steadyPill.className = 'pano-steady-pill ok';
    steadyPill.textContent = '✅ 360° Stamps Captured';
  } else if (AUTO_SHUTTER_ON && !PANO_MOVED_SINCE_SNAP) {
    steadyPill.className = 'pano-steady-pill wait-pan';
    steadyPill.textContent = '↻ Rotate to Next Arc';
  } else if (!onTrack) {
    steadyPill.className = 'pano-steady-pill';
    steadyPill.textContent = dy > 0 ? '↑ Tilt Camera Up' : (dy < 0 ? '↓ Tilt Camera Down' : '⟲ Level Camera');
  } else if (!IS_STEADY) {
    steadyPill.className = 'pano-steady-pill';
    steadyPill.textContent = '🟡 Hold Steady...';
  } else if (!IS_SHARP) {
    steadyPill.className = 'pano-steady-pill';
    steadyPill.textContent = '🔍 Focusing Stamps...';
  } else if (AUTO_SHUTTER_ON && pct > 0) {
    steadyPill.className = 'pano-steady-pill ok';
    steadyPill.textContent = `🟢 Locking Sharp (${pct}%)`;
  } else {
    steadyPill.className = 'pano-steady-pill ok';
    steadyPill.textContent = '🟢 Sharp & Steady';
  }
}

function measureCenterStampMetrics() {
  const video = document.getElementById('camVideo');
  if (!video || !video.videoWidth || video.paused) return null;
  if (!MOTION_CANVAS) {
    MOTION_CANVAS = document.createElement('canvas');
    MOTION_CANVAS.width = ANALYZE_W;
    MOTION_CANVAS.height = ANALYZE_H;
  }
  const ctx = MOTION_CANVAS.getContext('2d', { willReadFrequently: true });
  const sx = video.videoWidth * 0.20;
  const sy = video.videoHeight * 0.26;
  const sw = video.videoWidth * 0.60;
  const sh = video.videoHeight * 0.48;
  ctx.drawImage(video, sx, sy, sw, sh, 0, 0, ANALYZE_W, ANALYZE_H);

  const data = ctx.getImageData(0, 0, ANALYZE_W, ANALYZE_H).data;
  const len = ANALYZE_W * ANALYZE_H;
  const cur = new Uint8Array(len);
  let diffSum = 0;
  let snapDiffSum = 0;

  for (let i = 0, p = 0; i < len; i++, p += 4) {
    const luma = (data[p] * 3 + data[p + 1] * 4 + data[p + 2]) >> 3;
    cur[i] = luma;
    if (PREV_LUMA) diffSum += Math.abs(luma - PREV_LUMA[i]);
    if (LAST_SNAP_LUMA) snapDiffSum += Math.abs(luma - LAST_SNAP_LUMA[i]);
  }

  let edgeSum = 0;
  let edgeCount = 0;
  for (let y = 1; y < ANALYZE_H - 1; y++) {
    const rowOff = y * ANALYZE_W;
    for (let x = 1; x < ANALYZE_W - 1; x++) {
      const idx = rowOff + x;
      const gx = Math.abs(cur[idx + 1] - cur[idx - 1]);
      const gy = Math.abs(cur[idx + ANALYZE_W] - cur[idx - ANALYZE_W]);
      edgeSum += (gx + gy);
      edgeCount++;
    }
  }

  return {
    cur,
    meanDiff: PREV_LUMA ? (diffSum / len) : 99,
    panDiff: LAST_SNAP_LUMA ? (snapDiffSum / len) : 99,
    edgeScore: edgeSum / Math.max(1, edgeCount),
  };
}

function sampleVideoStability() {
  if (AUTO_SNAPPING_LOCK) return;
  const m = measureCenterStampMetrics();
  if (!m) return;

  PREV_LUMA = m.cur;
  LAST_CUR_LUMA = m.cur;

  if (!PANO_MOVED_SINCE_SNAP && m.panDiff > 11.0) {
    PANO_MOVED_SINCE_SNAP = true;
    PEAK_EDGE_SCORE = m.edgeScore;
  }

  IS_STEADY = m.meanDiff < 6.0;
  IS_ULTRA_STEADY = m.meanDiff < 3.1;

  if (IS_STEADY) {
    PEAK_EDGE_SCORE = Math.max(PEAK_EDGE_SCORE * 0.985, m.edgeScore);
  }
  const focusStillClimbing = m.edgeScore > PREV_EDGE_SCORE * 1.09;
  IS_SHARP = m.edgeScore >= 3.2 && m.edgeScore >= (PEAK_EDGE_SCORE * 0.80) && !focusStillClimbing;
  PREV_EDGE_SCORE = m.edgeScore;

  const { onTrack } = isGyroOnTrack();
  const canAutoFire = AUTO_SHUTTER_ON && CAPTURED_B64.length < 4 && PANO_MOVED_SINCE_SNAP;

  if (canAutoFire && onTrack && IS_ULTRA_STEADY && IS_SHARP) {
    AUTO_HOLD_TICKS++;
    if (AUTO_HOLD_TICKS >= AUTO_HOLD_TARGET) {
      AUTO_HOLD_TICKS = 0;
      verifyAndFireAutoSnap();
      return;
    }
  } else {
    AUTO_HOLD_TICKS = 0;
  }
  updatePanoAlignmentHUD();
}

function verifyAndFireAutoSnap() {
  AUTO_SNAPPING_LOCK = true;
  setTimeout(() => {
    const check = measureCenterStampMetrics();
    if (check) {
      PREV_LUMA = check.cur;
      LAST_CUR_LUMA = check.cur;
    }
    const { onTrack } = isGyroOnTrack();
    if (!check || check.meanDiff > 3.3 || !onTrack || check.edgeScore < (PEAK_EDGE_SCORE * 0.78)) {
      AUTO_HOLD_TICKS = Math.max(0, AUTO_HOLD_TARGET - 3);
      AUTO_SNAPPING_LOCK = false;
      updatePanoAlignmentHUD();
      return;
    }
    captureAngle(true);
    AUTO_SNAPPING_LOCK = false;
  }, 40);
}

async function openLiveCamera() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    alert('Live camera requires HTTPS.');
    return;
  }
  if (typeof DeviceOrientationEvent !== 'undefined' && typeof DeviceOrientationEvent.requestPermission === 'function') {
    try { await DeviceOrientationEvent.requestPermission(); } catch (_) {}
  }
  closeLiveCamera();
  try {
    MEDIA_STREAM = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: FACING_MODE }, width: { ideal: 1920 }, height: { ideal: 1440 } },
      audio: false,
    });
    const track = MEDIA_STREAM.getVideoTracks()[0];
    const caps = track.getCapabilities ? track.getCapabilities() : {};
    const adv = {};
    if (Array.isArray(caps.focusMode) && caps.focusMode.includes('continuous')) adv.focusMode = 'continuous';
    if (Array.isArray(caps.exposureMode) && caps.exposureMode.includes('continuous')) adv.exposureMode = 'continuous';
    if (Object.keys(adv).length) {
      try { await track.applyConstraints({ advanced: [adv] }); } catch (_) {}
    }

    document.getElementById('camVideo').srcObject = MEDIA_STREAM;
    document.getElementById('camContainer').hidden = false;
    document.getElementById('btnOpenCam').hidden = true;
    document.getElementById('btnTorch').hidden = !caps.torch;
    syncAutoShutterBtn();

    window.addEventListener('deviceorientation', handlePanoOrientation, true);
    PREV_LUMA = null;
    PEAK_EDGE_SCORE = 0;
    PREV_EDGE_SCORE = 0;
    PANO_TIMER = setInterval(sampleVideoStability, 110);
    clearAngles();
  } catch (err) {
    alert('Camera error: ' + err.message);
  }
}

function closeLiveCamera() {
  if (PANO_TIMER) { clearInterval(PANO_TIMER); PANO_TIMER = null; }
  window.removeEventListener('deviceorientation', handlePanoOrientation, true);
  if (MEDIA_STREAM) { MEDIA_STREAM.getTracks().forEach(t => t.stop()); MEDIA_STREAM = null; }
  const box = document.getElementById('camContainer');
  const openBtn = document.getElementById('btnOpenCam');
  if (box) box.hidden = true;
  if (openBtn) openBtn.hidden = false;
}

function switchCamera() {
  FACING_MODE = FACING_MODE === 'environment' ? 'user' : 'environment';
  openLiveCamera();
}

async function toggleTorch() {
  if (!MEDIA_STREAM) return;
  TORCH_ON = !TORCH_ON;
  await MEDIA_STREAM.getVideoTracks()[0].applyConstraints({ advanced: [{ torch: TORCH_ON }] });
}

function clearAngles() {
  CAPTURED_B64 = [];
  CAPTURED_URLS = [];
  LOCK_BETA = null;
  LOCK_GAMMA = null;
  LAST_SNAP_LUMA = null;
  PANO_MOVED_SINCE_SNAP = true;
  AUTO_HOLD_TICKS = 0;
  document.getElementById('panoGhostWrap').hidden = true;
  document.getElementById('thumbStrip').innerHTML =
    '<small style="color:#cbd5e1;">Sweep 4 overlapping arcs around shoulder stamps, then tap Populate Form.</small>';
  document.getElementById('btnReadAll').disabled = true;
  document.getElementById('btnReadAll').textContent = '✅ Populate Form';
  renderPanoStepBar();
  updatePanoAlignmentHUD();
}

function clearReferenceGallery() {
  document.getElementById('lastScanGalleryWrap').hidden = true;
  document.getElementById('lastScanGallery').innerHTML = '';
}

function showReferenceGallery(dataUrls) {
  if (!dataUrls || !dataUrls.length) return;
  const wrap = document.getElementById('lastScanGalleryWrap');
  const strip = document.getElementById('lastScanGallery');
  strip.innerHTML = dataUrls.map(u => `<img src="${u}" alt="Scanned Angle" onclick="zoomPhoto('${u}')" />`).join('');
  wrap.hidden = false;
}

function grabVideoBase64() {
  const video = document.getElementById('camVideo');
  const canvas = document.getElementById('camCanvas');
  if (!video.videoWidth) return null;

  const scale = Math.min(1, 1600 / Math.max(video.videoWidth, video.videoHeight));
  canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
  canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
  canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
  const dataUrl = canvas.toDataURL('image/jpeg', 0.88);

  const ew = video.clientWidth || canvas.width;
  const eh = video.clientHeight || canvas.height;
  const covScale = Math.max(ew / video.videoWidth, eh / video.videoHeight);
  const ox = (video.videoWidth * covScale - ew) / 2;
  const oy = (video.videoHeight * covScale - eh) / 2;

  const ghostFracW = 0.88 * 0.26;
  const rx = PANO_DIR === 'ltr' ? (0.94 - ghostFracW) : 0.06;
  const ry = 0.24, rh = 0.52;

  const gCanvas = document.createElement('canvas');
  gCanvas.width = 220;
  gCanvas.height = 240;
  gCanvas.getContext('2d').drawImage(
    video,
    (rx * ew + ox) / covScale, (ry * eh + oy) / covScale,
    (ghostFracW * ew) / covScale, (rh * eh) / covScale,
    0, 0, gCanvas.width, gCanvas.height
  );
  return { dataUrl, b64: dataUrl.split(',')[1], ghostUrl: gCanvas.toDataURL('image/jpeg', 0.82) };
}

function fileToResizedObj(file) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      URL.revokeObjectURL(url);
      const canvas = document.getElementById('camCanvas');
      const scale = Math.min(1, 1600 / Math.max(img.width, img.height));
      canvas.width = Math.max(1, Math.round(img.width * scale));
      canvas.height = Math.max(1, Math.round(img.height * scale));
      canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
      const dataUrl = canvas.toDataURL('image/jpeg', 0.88);
      resolve({ dataUrl, b64: dataUrl.split(',')[1] });
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('Image load error')); };
    img.src = url;
  });
}

async function quickSingleShot() {
  const s = grabVideoBase64();
  if (s) {
    playShutterFeedback(true);
    await sendBase64Batch([s.b64], [s.dataUrl]);
  }
}

function captureAngle() {
  if (CAPTURED_B64.length >= 6) return;
  const s = grabVideoBase64();
  if (!s) return;

  if (CAPTURED_B64.length === 0 && HAS_GYRO) {
    LOCK_BETA = LIVE_BETA;
    LOCK_GAMMA = LIVE_GAMMA;
  }
  if (LAST_CUR_LUMA) {
    LAST_SNAP_LUMA = LAST_CUR_LUMA.slice();
    PANO_MOVED_SINCE_SNAP = false;
  }
  AUTO_HOLD_TICKS = 0;

  CAPTURED_B64.push(s.b64);
  CAPTURED_URLS.push(s.dataUrl);
  playShutterFeedback(CAPTURED_B64.length === 4);

  if (CAPTURED_B64.length < 4 && s.ghostUrl) {
    document.getElementById('panoGhostImg').src = s.ghostUrl;
    document.getElementById('panoGhostWrap').hidden = false;
  } else {
    document.getElementById('panoGhostWrap').hidden = true;
  }

  const strip = document.getElementById('thumbStrip');
  if (CAPTURED_B64.length === 1) strip.innerHTML = '';
  const img = document.createElement('img');
  img.src = s.dataUrl;
  img.style.cssText = 'height:48px;border-radius:6px;border:2px solid #22c55e;cursor:zoom-in;';
  img.onclick = () => zoomPhoto(s.dataUrl);
  strip.appendChild(img);

  document.getElementById('btnReadAll').disabled = false;
  document.getElementById('btnReadAll').textContent = `✅ Populate Form (${CAPTURED_B64.length})`;
  renderPanoStepBar();
  updatePanoAlignmentHUD();
}

async function uploadSavedAngles(input) {
  const files = Array.from(input.files || []).slice(0, 6);
  input.value = '';
  if (!files.length) return;
  const b64s = [], urls = [];
  for (const f of files) {
    const obj = await fileToResizedObj(f);
    b64s.push(obj.b64);
    urls.push(obj.dataUrl);
  }
  await sendBase64Batch(b64s, urls);
}

async function sendCapturedAngles() {
  if (CAPTURED_B64.length) await sendBase64Batch([...CAPTURED_B64], [...CAPTURED_URLS]);
}

async function sendBase64Batch(b64List, urlList) {
  const fb = document.getElementById('scanFeedback');
  fb.hidden = false;
  fb.className = 'notice';
  fb.textContent = `⏳ Stitching ${b64List.length} angle(s) for Serial #, Valve Tag, Spec, & OD × Length...`;

  showReferenceGallery(urlList);
  clearAngles();
  closeLiveCamera();

  const res = await fetch('/api/scan-bottle', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      images_b64: b64List,
      work_date: STATE.selected_date,
      customer_name: document.getElementById('inCustomer').value,
      customer_phone: document.getElementById('inPhone').value,
      dropoff_id: window.ACTIVE_DROPOFF_ID || null,
      service_mode: document.getElementById('inMode').value,
      intake_by: getActiveCrew(),
      auto_import: document.getElementById('chkAutoQueue').checked,
    }),
  });
  const data = await res.json();
  if (!res.ok) {
    fb.className = 'notice bad';
    fb.textContent = data.detail || 'AI scan failed.';
    return;
  }

  if (data.imported && data.record) {
    fb.className = 'notice';
    fb.textContent = `⚡ Auto-Queued SN ${data.record.serial_number} (${data.record.dot_spec}-${data.record.service_psi}, Size ${data.record.physical_size || 'N/A'})!`;
    await loadState(false);
    return;
  }

  const f = data.form_fill || {};
  document.getElementById('fCustomer').value = f.customer_name || document.getElementById('inCustomer').value || '';
  document.getElementById('fPhone').value = document.getElementById('inPhone').value || '';
  document.getElementById('fSerial').value = f.serial_number || '';
  document.getElementById('fValveTag').value = f.valve_tag || '';
  document.getElementById('fMfg').value = f.mfg_id || '';
  document.getElementById('fMfgDate').value = f.mfg_date || '';
  document.getElementById('fGas').value = f.gas_service || 'AIR';
  document.getElementById('fSpec').value = f.dot_spec || '';
  document.getElementById('fSvcPsi').value = f.service_psi || '';
  document.getElementById('fTestPsi').value = f.test_psi || '';
  document.getElementById('fDia').value = f.outside_dia || '';
  document.getElementById('fLen').value = f.length_in || '';
  document.getElementById('fRee').value = f.ree_limit !== '' && f.ree_limit !== null ? f.ree_limit : '';

  document.getElementById('intakeFieldsGrid').classList.add('ai-filled');
  const badge = document.getElementById('sizeSourceBadge');
  badge.className = 'sub-badge ai-verified';
  const srcLabel = f.size_source === 'LABEL' ? 'Label' : (f.size_source === 'CGA_CHART' ? 'Spec Chart' : 'AI Estimate');
  badge.textContent = `✓ AI Populated (${srcLabel})`;

  if (typeof checkIntakeLifespanLive === 'function') {
    checkIntakeLifespanLive(data.composite_life);
  }

  const missing = data.missing || [];
  if (data.duplicate_warning) {
    fb.className = 'notice warn';
    fb.textContent = `⚠️ ${data.duplicate_warning}`;
  } else if (missing.length) {
    fb.className = 'notice warn';
    fb.textContent = `Complete missing field(s) below: ${missing.join(', ')}`;
  } else {
    fb.className = 'notice';
    fb.textContent = `✅ Scan Ready! Tap reference photos to zoom stamps, verify ${f.outside_dia || '?'}" × ${f.length_in || '?'}" size, then click Verify & Add.`;
  }
  document.getElementById('verifyFormCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
}