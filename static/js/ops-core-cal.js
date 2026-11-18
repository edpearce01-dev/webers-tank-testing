// Weber's Tank Testing — Pure 4-Station Shop Floor Controller (v8.0)
const VIS_FIELDS = [
  { key: 'chk_corrosion', label: 'Corrosion' },
  { key: 'chk_dents_dings', label: 'Dents / Dings' },
  { key: 'chk_cuts_gouges', label: 'Cuts / Gouges' },
  { key: 'chk_leaks', label: 'Leaks' },
  { key: 'chk_fire_damage', label: 'Fire Damage' },
  { key: 'chk_bulges', label: 'Bulges' },
  { key: 'chk_neck_defects', label: 'Neck / Threads' },
];

var STATE = {
  testing_mode: true, shop_phone: '724-266-8008', today: '', selected_date: '',
  calibration_targets: [], cga_specs: [], records: [], in_shop_post_test: [],
  active_valves: [], dropoffs: [], schedule_jobs: [], day_plans: [],
  binder_state: {}, huddle: {}, verifications: [], jacket_ceilings: {},
  customers_summary: [], customers_rolodex: [], compliance_alerts: [],
  low_stock_count: 0, crew: [], feedback_items: [], default_tax_rate: 6.0,
  daily_hydro_capacity: 24, db_version: 0,
};
window.ACTIVE_DROPOFF_ID = null;
window.ACTIVE_SCHEDULE_JOB_ID = null;

let CURRENT_TAB = 'cal';
let CAL_FORCE_EXPANDED = false;
let BATCH_LOCKED = false;
let LOCKED_SNAPSHOT = null;
let LAST_CAL_DATE = '';
let LADDER = [];
let ACTIVE_STEP = 0;
let BENCH_VIS = {};
let EDIT_VIS = {};
let BENCH_SUBMITTING = false;
let LAST_SEARCH_ROWS = [];
let SHOW_ALL_SHELF_CUSTS = false;
let DEFERRED_PWA = null;
let DEEP_LINK_APPLIED = false;
let SHOP_AUDIO_CTX = null;
let CAL_DRAFT_TIMER = null;
let LAST_APPLIED_DRAFT_TS = '';
const DEVICE_ID = (() => {
  let id = localStorage.getItem('webers_device_id');
  if (!id) {
    id = 'dev-' + Math.random().toString(36).slice(2, 10);
    localStorage.setItem('webers_device_id', id);
  }
  return id;
})();

function isShopAudioEnabled() {
  return localStorage.getItem('webers_shop_audio') !== 'off';
}

function syncShopSoundBtn() {
  const btn = document.getElementById('shopSoundBtn');
  if (!btn) return;
  const on = isShopAudioEnabled();
  btn.textContent = on ? '🔊' : '🔇';
  btn.title = on ? 'Shop Audio Cues: ON (Click to Mute)' : 'Shop Audio Cues: MUTED (Click to Enable)';
}

function toggleShopAudio() {
  const next = isShopAudioEnabled() ? 'off' : 'on';
  localStorage.setItem('webers_shop_audio', next);
  syncShopSoundBtn();
  if (next === 'on') playShopTone('pass');
}

function playShopTone(kind = 'pass') {
  if (!isShopAudioEnabled()) return;
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;
    if (!SHOP_AUDIO_CTX) SHOP_AUDIO_CTX = new AudioCtx();
    if (SHOP_AUDIO_CTX.state === 'suspended') SHOP_AUDIO_CTX.resume();
    const now = SHOP_AUDIO_CTX.currentTime;
    const notes = kind === 'condemn'
      ? [{ f: 290, d: 0.16, t: 'sawtooth', g: 0.08, o: 0 }, { f: 205, d: 0.24, t: 'sawtooth', g: 0.09, o: 0.18 }]
      : (kind === 'step'
        ? [{ f: 660, d: 0.055, t: 'sine', g: 0.045, o: 0 }]
        : [{ f: 659.25, d: 0.08, t: 'sine', g: 0.06, o: 0 }, { f: 880, d: 0.13, t: 'sine', g: 0.07, o: 0.085 }]);
    notes.forEach(n => {
      const osc = SHOP_AUDIO_CTX.createOscillator();
      const gain = SHOP_AUDIO_CTX.createGain();
      osc.type = n.t;
      osc.frequency.setValueAtTime(n.f, now + n.o);
      gain.gain.setValueAtTime(n.g, now + n.o);
      gain.gain.exponentialRampToValueAtTime(0.0008, now + n.o + n.d);
      osc.connect(gain);
      gain.connect(SHOP_AUDIO_CTX.destination);
      osc.start(now + n.o);
      osc.stop(now + n.o + n.d + 0.01);
    });
  } catch (_) {}
}

function setOfflineGuard(isOffline) {
  const bar = document.getElementById('offlineGuardBanner');
  if (bar) bar.hidden = !isOffline;
}

window.addEventListener('online', () => { setOfflineGuard(false); loadState(false); });
window.addEventListener('offline', () => setOfflineGuard(true));
document.addEventListener('DOMContentLoaded', syncShopSoundBtn);

const IS_OPS_HOST = !['weberstanktesting.com', 'www.weberstanktesting.com'].includes(location.hostname.toLowerCase());
if (IS_OPS_HOST && 'serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}
window.addEventListener('beforeinstallprompt', (e) => {
  if (!IS_OPS_HOST) return;
  e.preventDefault();
  DEFERRED_PWA = e;
  const btn = document.getElementById('btnInstallPwa');
  if (btn) btn.hidden = false;
});

async function installPwaApp() {
  if (!DEFERRED_PWA) return;
  DEFERRED_PWA.prompt();
  await DEFERRED_PWA.userChoice;
  DEFERRED_PWA = null;
  document.getElementById('btnInstallPwa').hidden = true;
}

function escHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
}

function fmtPctTenth(val) {
  return (val === null || val === undefined || val === '') ? '-' : Number(val).toFixed(1) + '%';
}

function priorityBadgeHtml(r) {
  if (r.rush_fee || r.priority === 'RUSH') return '<span class="rush-badge">RUSH</span>';
  if (r.priority === 'HIGH') return '<span class="high-badge">HIGH</span>';
  return '';
}

function dwellBadgeHtml(days, prefix = '⏳') {
  if (!days || days < 1) return '';
  return `<span class="dwell-badge ${days >= 4 ? 'old' : ''}">${prefix} ${days}d</span>`;
}

function stageBadgeHtml(r) {
  if (r.status === 'QUEUED') return '<span class="stage-chip">Queued</span>';
  const st = r.shop_stage || 'IN_SHOP';
  if (st === 'READY_BACKROOM') return '<span class="stage-chip ready">Dock Ready</span>';
  if (st === 'PICKED_UP') return `<span class="stage-chip">✓ Picked Up${r.picked_up_by ? ' (' + escHtml(r.picked_up_by) + ')' : ''}</span>`;
  if (st === 'CONDEMNED_HOLD' || r.hydro_disposition === 'D') return '<span class="stage-chip" style="background:#fee2e2;color:#991b1b;">⛔ Condemned</span>';
  if (st === 'STAMPED_STICKERED' || r.rin_marked) {
    const lbl = r.rin_mark_type === 'STICKERED' ? 'B386 Stickered' : 'Stamped B386';
    return `<span class="stage-chip stamped">${lbl}</span>`;
  }
  if (st === 'DRYING') return '<span class="stage-chip drying">Drying • Needs B386</span>';
  return '<span class="stage-chip">In Shop</span>';
}

function getActiveCrew() {
  if (window.CURRENT_USER && window.CURRENT_USER.initials) return window.CURRENT_USER.initials;
  return localStorage.getItem('webers_active_crew') || 'RP';
}

function saveActiveCrew() {
  const val = getActiveCrew();
  localStorage.setItem('webers_active_crew', val);
  document.getElementById('menuCrewInitials').textContent = val;
}

function getNextFreeValveTag(curTag) {
  const n = parseInt(String(curTag || '').trim(), 10);
  if (isNaN(n) || n < 1) return '';
  const busy = new Set(
    [...STATE.active_valves, ...STATE.records.filter(r => !r.valve_reinstalled && r.valve_tag)]
      .map(r => String(r.valve_tag).trim().toUpperCase())
  );
  busy.add(String(n));
  let cand = n + 1;
  while (busy.has(String(cand)) && cand < n + 500) cand++;
  return String(cand);
}

// --- ◀ [TODAY] ▶ TOPBAR DATE STEPPER ---
function stepWorkingDate(deltaDays) {
  const el = document.getElementById('globalDate');
  const cur = el.value || STATE.selected_date || STATE.today;
  if (!cur) return;
  const [y, m, d] = cur.split('-').map(Number);
  const dt = new Date(y, m - 1, d + deltaDays);
  const iso = `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
  el.value = iso;
  changeWorkingDate();
}

function jumpWorkingDateToday() {
  sessionStorage.removeItem('webers_working_date');
  const el = document.getElementById('globalDate');
  if (STATE.today) {
    el.value = STATE.today;
  } else {
    el.value = '';
  }
  changeWorkingDate();
}

// --- ROLODEX PHONE AUTO-FILL ---
function prefillPhoneFromRolodex(custInputId, phoneInputId, mirrorCustId = null, mirrorPhoneId = null) {
  const nameVal = (document.getElementById(custInputId).value || '').trim();
  if (mirrorCustId && !document.getElementById(mirrorCustId).value) {
    document.getElementById(mirrorCustId).value = nameVal;
  }
  if (!nameVal) return;
  const match = (STATE.customers_rolodex || []).find(c => c.customer_name.toUpperCase() === nameVal.toUpperCase())
    || (STATE.customers_summary || []).find(c => c.customer_name.toUpperCase() === nameVal.toUpperCase());
  const foundPhone = match ? (match.phone || match.customer_phone || '') : '';
  if (foundPhone) {
    document.getElementById(phoneInputId).value = foundPhone;
    if (mirrorPhoneId) document.getElementById(mirrorPhoneId).value = foundPhone;
  }
}

// --- AUTH, DEEP LINKS & STATION NAVIGATION ---
async function checkAuthAndBoot(showLoginToast = false) {
  const params = new URLSearchParams(location.search);
  const urlDate = params.get('date') || sessionStorage.getItem('webers_working_date') || '';
  if (urlDate) document.getElementById('globalDate').value = urlDate;

  const res = await fetch('/api/auth/status');
  const data = await res.json();
  if (!data.authenticated) {
    document.getElementById('loginScreen').hidden = false;
    document.getElementById('appShell').hidden = true;
    document.getElementById('loginPinInput').focus();
  } else {
    document.getElementById('loginScreen').hidden = true;
    document.getElementById('appShell').hidden = false;
    if (typeof window.syncAuthAndRoleUI === 'function') await window.syncAuthAndRoleUI(data.user, showLoginToast);
    if (typeof syncAutoShutterBtn === 'function') syncAutoShutterBtn();
    await loadState(true);
    if (typeof window.syncAuthAndRoleUI === 'function') window.syncAuthAndRoleUI(data.user, false);
    applyUrlDeepLink();
  }
}

function applyUrlDeepLink() {
  if (DEEP_LINK_APPLIED) return;
  DEEP_LINK_APPLIED = true;
  const params = new URLSearchParams(location.search);
  const jobId = parseInt(params.get('job') || '0', 10);
  const dropId = parseInt(params.get('dropoff') || '0', 10);
  const tab = params.get('tab');
  if (jobId) {
    importScheduleJobToIntake(jobId);
    history.replaceState(null, '', '/ops');
  } else if (dropId) {
    importDropoffToIntake(dropId);
    jumpToTab('intake');
    history.replaceState(null, '', '/ops');
  } else if (tab && ['cal', 'intake', 'bench', 'logs', 'search', 'todo'].includes(tab)) {
    jumpToTab(tab);
    history.replaceState(null, '', '/ops');
  }
}

async function submitLoginPin() {
  const pin = document.getElementById('loginPinInput').value;
  const err = document.getElementById('loginError');
  const res = await fetch('/api/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pin }),
  });
  if (!res.ok) { err.hidden = false; return; }
  err.hidden = true;
  document.getElementById('loginPinInput').value = '';
  checkAuthAndBoot(true);
}

async function logoutCrew() {
  await fetch('/api/auth/logout', { method: 'POST' });
  toggleMenuDrawer(false);
  toggleGuide(false);
  checkAuthAndBoot();
}

function acknowledgeDisclaimer() {
  sessionStorage.setItem('webers_disclaimer_ack', '1');
  document.getElementById('disclaimerModal').hidden = true;
}

function toggleMenuDrawer(show) { if (show && window.openAccountSwitchDrawer) return window.openAccountSwitchDrawer(); document.getElementById('menuDrawerModal').hidden = !show; }
function toggleGuide(show) { document.getElementById('guideModal').hidden = !show; }
function toggleFeedbackModal(show) { document.getElementById('feedbackModal').hidden = !show; }

function jumpToTab(name) {
  CURRENT_TAB = name;
  if (name !== 'intake' && typeof closeLiveCamera === 'function') closeLiveCamera();
  document.querySelectorAll('main.ops-workspace > section').forEach(s => { s.hidden = true; });
  const target = document.getElementById('tab-' + name);
  if (target) target.hidden = false;
  document.querySelectorAll('.rail-btn, .bnav-btn').forEach(b => {
    b.classList.toggle('active', b.getAttribute('data-tab') === name);
  });
  if (name === 'search') runSearch(false);
  if (name === 'todo') renderShopTodoStation();
  if (name === 'bench') {
    setTimeout(() => { const tot = document.getElementById('bTotal'); if (tot) tot.focus(); }, 40);
  }
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function changeWorkingDate() {
  CAL_FORCE_EXPANDED = false;
  const v = document.getElementById('globalDate').value;
  if (v) sessionStorage.setItem('webers_working_date', v);
  else sessionStorage.removeItem('webers_working_date');
  loadState(true);
}

function setCalExpanded(exp) {
  CAL_FORCE_EXPANDED = exp;
  if (exp) {
    const sMax = STATE.jacket_ceilings['Short Jacket']?.max_allowed_test_psi || 0;
    const tMax = STATE.jacket_ceilings['Tall Jacket']?.max_allowed_test_psi || 0;
    const calJacketSel = document.getElementById('calJacket');
    if (calJacketSel) {
      if (sMax > 0 && !tMax) calJacketSel.value = 'Tall Jacket';
      else if (tMax > 0 && !sMax) calJacketSel.value = 'Short Jacket';
      buildCalibrationLadder();
    }
  }
  updateCalCollapseUI();
}

function updateCalCollapseUI() {
  const sMax = STATE.jacket_ceilings['Short Jacket']?.max_allowed_test_psi || 0;
  const tMax = STATE.jacket_ceilings['Tall Jacket']?.max_allowed_test_psi || 0;
  const anyReady = sMax > 0 || tMax > 0;
  const bothReady = sMax > 0 && tMax > 0;
  const hasDraft = LADDER.some(s => String(s.actual || '').trim() !== '');
  const showCollapsed = anyReady && !CAL_FORCE_EXPANDED && !hasDraft;

  document.getElementById('calCollapsedBanner').hidden = !showCollapsed;
  document.getElementById('calFormCard').hidden = showCollapsed;

  if (anyReady) {
    const titleEl = document.getElementById('calCollapsedTitle');
    const expBtn = document.getElementById('btnExpandCalCard');
    if (bothReady) {
      if (titleEl) titleEl.textContent = '✅ Both Water Jackets Certified — Hydro Stand Ready';
      if (expBtn) expBtn.textContent = 'Re-Calibrate / Higher PSI';
      document.getElementById('calCollapsedSummary').textContent =
        `Short ≤ ${sMax} PSI • Tall ≤ ${tMax} PSI (Signed Off: RP)`;
    } else if (sMax > 0) {
      if (titleEl) titleEl.textContent = `✅ Short Jacket Certified (≤ ${sMax} PSI) — Hydro Stand Unlocked & Ready`;
      if (expBtn) expBtn.textContent = '+ Calibrate Tall Jacket';
      document.getElementById('calCollapsedSummary').textContent =
        `Short Jacket ready up to ${sMax} PSI • Tall Jacket not calibrated yet (click "+ Calibrate Tall Jacket" only if needed today)`;
    } else {
      if (titleEl) titleEl.textContent = `✅ Tall Jacket Certified (≤ ${tMax} PSI) — Hydro Stand Unlocked & Ready`;
      if (expBtn) expBtn.textContent = '+ Calibrate Short Jacket';
      document.getElementById('calCollapsedSummary').textContent =
        `Tall Jacket ready up to ${tMax} PSI • Short Jacket not calibrated yet (click "+ Calibrate Short Jacket" only if needed today)`;
    }
  }
}

async function eraseAllTestData() {
  if (!confirm('ERASE ALL PRACTICE / TEST DATA?\n\nWipes practice calibrations, cylinders, drop-offs, schedule jobs, tasks, and invoices while keeping API keys, supplies, crew, and service rates intact.')) return;
  const wipeCusts = confirm('Also clear current Customer Rolodex profiles (e.g. practice test accounts)?\n\nClick OK to clear Rolodex too, or Cancel to KEEP saved Customer Rolodex profiles.');
  const res = await fetch('/api/admin/wipe-test-data?wipe_customers=' + wipeCusts, { method: 'POST' });
  const data = await res.json();
  if (!res.ok) { alert(data.detail || 'Wipe failed.'); return; }
  alert(data.sheets_wiped ? 'App database & Google Sheets wiped clean!' : 'Local database wiped clean!');
  toggleMenuDrawer(false);
  toggleGuide(false);
  loadState(true);
}

// --- BOOTSTRAP & LIVE POLLING ---
async function loadState(rebuildLadder = false) {
  const wDate = document.getElementById('globalDate').value || '';
  const useFastLive = !rebuildLadder && Array.isArray(STATE.cga_specs) && STATE.cga_specs.length > 0;
  const ep = useFastLive ? '/api/ops/live' : '/api/bootstrap';
  const res = await fetch(ep + (wDate ? `?work_date=${encodeURIComponent(wDate)}` : ''));
  if (res.status === 401) { checkAuthAndBoot(); return; }
  const incoming = await res.json();
  STATE = useFastLive ? { ...STATE, ...incoming } : incoming;

  document.getElementById('globalDate').value = STATE.selected_date;

  document.getElementById('testModeChip').hidden = !STATE.testing_mode;
  document.getElementById('drawerWipeBtn').hidden = !STATE.testing_mode;
  document.getElementById('testingModeChk').checked = STATE.testing_mode;
  if (STATE.testing_mode && !sessionStorage.getItem('webers_disclaimer_ack')) {
    document.getElementById('disclaimerModal').hidden = false;
  }

  // Render RIN B386 & Master Gauge Compliance Countdown Warnings
  const urgentCerts = (STATE.compliance_alerts || []).filter(a => a.level === 'EXPIRED' || a.level === 'WARN');
  const certBanner = document.getElementById('complianceAlertBanner');
  if (urgentCerts.length) {
    certBanner.hidden = false;
    certBanner.innerHTML = urgentCerts.map(a => `
      <div class="notice ${a.level === 'EXPIRED' ? 'bad' : 'warn'}" style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">
        <span>${a.level === 'EXPIRED' ? '⛔ EXPIRED:' : '⚠️ DUE SOON:'} <strong>${escHtml(a.title)}</strong> (${escHtml(a.expires_date)} — ${a.days_remaining < 0 ? Math.abs(a.days_remaining) + 'd overdue' : a.days_remaining + 'd remaining'})</span>
        <a class="secondary" href="/owner?pane=facility">Update Cert Date →</a>
      </div>`).join('');
  } else {
    certBanner.hidden = true;
  }
  const lowSups = STATE.low_supplies_alert || [];
  const supBanner = document.getElementById('lowSupplyAlertBanner');
  const isBossView = !window.CURRENT_USER || ['OWNER', 'SYSTEM_ADMIN'].includes(window.CURRENT_USER.role);
  if (supBanner) {
    if (lowSups.length && isBossView) {
      supBanner.hidden = false;
      const itemsTxt = lowSups.map(s => `${escHtml(s.item_name)} (${s.on_hand_qty ?? 0} ${escHtml(s.unit_label || 'left')} • limit ≤${s.reorder_threshold ?? 2})`).join('  •  ');
      supBanner.innerHTML = `<div class="notice warn" style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;border-left:5px solid #d97706;">
        <span>📦 <strong>Supply Replacement Alert (${lowSups.length}):</strong> ${itemsTxt}</span>
        <a class="secondary" style="font-weight:800;" href="/office?tab=supplies">Manage Limits &amp; Stock →</a>
      </div>`;
    } else {
      supBanner.hidden = true;
    }
  }

  const supLink = document.getElementById('railSupplyLink');
  if (supLink) {
    supLink.innerHTML = STATE.low_stock_count
      ? `📦 Reorder Supplies <span class="nav-badge amber">⚠️ ${STATE.low_stock_count}</span>`
      : `📦 Reorder Shop Supplies`;
  }

  const savedCrew = localStorage.getItem('webers_active_crew') || 'RP';
  document.getElementById('activeCrewSelect').innerHTML = STATE.crew.map(c =>
    `<option value="${c.initials}" ${c.initials === savedCrew ? 'selected' : ''}>${c.initials} — ${escHtml(c.full_name)}</option>`
  ).join('');
  document.getElementById('menuCrewInitials').textContent = getActiveCrew();

  document.getElementById('crewRosterList').innerHTML = STATE.crew.map(c => `
    <span class="sub-badge" style="display:inline-flex;align-items:center;gap:5px;">
      <strong>${escHtml(c.initials)}</strong> ${escHtml(c.full_name)}
      ${c.initials !== 'RP' ? `<button onclick="removeCrewMember(${c.id})" style="border:none;background:none;color:#dc2626;cursor:pointer;font-weight:900;">×</button>` : ''}
    </span>`).join('');

  document.getElementById('editSheetNo').value = STATE.starting_sheet_no;
  document.getElementById('editDailyCap').value = STATE.daily_hydro_capacity || 24;
  document.getElementById('editDefaultTax').value = STATE.default_tax_rate ?? 6.0;
  document.getElementById('editRinExp').value = STATE.rin_expires_date || '';
  document.getElementById('editGaugeExp').value = STATE.gauge_cert_expires_date || '';
  document.getElementById('calSerialLabel').textContent = STATE.calibrated_serial;
  document.getElementById('editCalSerial').value = STATE.calibrated_serial;
  document.getElementById('webhookInput').value = STATE.sheets_webhook_url || '';
  document.getElementById('fbWebhookInput').value = STATE.feedback_webhook_url || '';
  if (document.getElementById('editAlertEmail')) document.getElementById('editAlertEmail').value = STATE.alert_email || '';
  if (document.getElementById('editSmtpSender')) document.getElementById('editSmtpSender').value = STATE.smtp_sender_email || '';
  if (document.getElementById('smtpPassStatusTxt')) document.getElementById('smtpPassStatusTxt').textContent = STATE.has_smtp_password ? 'Configured ✓' : 'Optional';
  document.getElementById('keyStatusText').textContent = STATE.has_gemini_key ? 'Configured & Ready' : 'Not Set';
  if (document.getElementById('resendKeyStatusTxt')) document.getElementById('resendKeyStatusTxt').textContent = STATE.has_resend_key ? 'Configured ✓' : 'Not Set';
  if (document.getElementById('editResendFrom') && STATE.resend_from_email) document.getElementById('editResendFrom').value = STATE.resend_from_email;
  if (document.getElementById('editMasterSheetUrl')) document.getElementById('editMasterSheetUrl').value = STATE.master_sheet_url || '';

  const syncTxt = STATE.has_sheets_webhook
    ? (STATE.unsynced_count ? `↻ Syncing (${STATE.unsynced_count})` : '☁ Sheets Synced')
    : `Local (${STATE.unsynced_count} unsynced)`;
  document.getElementById('railSyncChip').textContent = syncTxt;
  document.getElementById('drawerSyncBtn').textContent = syncTxt;

  const sCeil = STATE.jacket_ceilings['Short Jacket'] || { max_calibrated_psi: 0, max_allowed_test_psi: 0 };
  const tCeil = STATE.jacket_ceilings['Tall Jacket'] || { max_calibrated_psi: 0, max_allowed_test_psi: 0 };
  const pShort = document.getElementById('pillShort'), pTall = document.getElementById('pillTall');
  pShort.className = 'j-dot ' + (sCeil.max_calibrated_psi ? 'ready' : 'wait');
  pShort.innerHTML = sCeil.max_calibrated_psi ? `S: <span class="full-lbl">≤${sCeil.max_allowed_test_psi}</span>✓` : `S: <span class="full-lbl">Uncal</span>`;
  pTall.className = 'j-dot ' + (tCeil.max_calibrated_psi ? 'ready' : 'wait');
  pTall.innerHTML = tCeil.max_calibrated_psi ? `T: <span class="full-lbl">≤${tCeil.max_allowed_test_psi}</span>✓` : `T: <span class="full-lbl">Uncal</span>`;

  const calJacketSel = document.getElementById('calJacket');
  const activeTag = document.activeElement ? document.activeElement.id : '';
  const typingCalNow = activeTag.startsWith('stepIn_') || activeTag === 'calFinalPerm' || activeTag === 'calJacket' || activeTag === 'calMaxPsi';
  const hasTypedSteps = LADDER.some(s => String(s.actual || '').trim() !== '');

  if (calJacketSel) {
    const prevJ = calJacketSel.value;
    calJacketSel.innerHTML = [
      `<option value="Short Jacket">Short Jacket ${sCeil.max_calibrated_psi ? `(✓ Certified ≤${sCeil.max_allowed_test_psi}#)` : '(Needs Calibration)'}</option>`,
      `<option value="Tall Jacket">Tall Jacket ${tCeil.max_calibrated_psi ? `(✓ Certified ≤${tCeil.max_allowed_test_psi}#)` : '(Needs Calibration)'}</option>`,
    ].join('');
    calJacketSel.value = prevJ || 'Short Jacket';
    if (!typingCalNow && !hasTypedSteps) {
      if (sCeil.max_calibrated_psi && !tCeil.max_calibrated_psi) calJacketSel.value = 'Tall Jacket';
      else if (!sCeil.max_calibrated_psi && tCeil.max_calibrated_psi) calJacketSel.value = 'Short Jacket';
    }
  }
  const anyCalReady = Boolean(sCeil.max_calibrated_psi || tCeil.max_calibrated_psi);
  updateCalCollapseUI();

  const queued = STATE.records.filter(r => r.status === 'QUEUED');
  const doneToday = STATE.records.filter(r => r.status === 'COMPLETED');
  const openSched = (STATE.schedule_jobs || []).filter(j => j.status !== 'COMPLETED');
  const waitingDrops = (STATE.dropoffs || []).filter(d => ((d.status === 'IN_BACKROOM' || d.status === 'CHECKED_IN') && (d.imported_count || 0) < d.bottle_count) || d.status === 'PICKUP_LOGGED');
  const needsFinish = (STATE.in_shop_post_test || []).filter(r => r.shop_stage !== 'READY_BACKROOM' && r.shop_stage !== 'PICKED_UP').length + STATE.active_valves.length;
  const totalUnbilled = STATE.customers_summary.reduce((s, c) => s + (c.unbilled_count || 0), 0);

  document.getElementById('wsSchedBadge').textContent = openSched.length ? `(${openSched.length})` : '';
  document.getElementById('wsBillBadge').textContent = (totalUnbilled + STATE.low_stock_count) ? `(${totalUnbilled || '!'})` : '';

  document.getElementById('navBadgeCal').textContent = anyCalReady ? '✓' : '!';
  document.getElementById('navBadgeCal').className = 'nav-badge ' + (anyCalReady ? 'green' : 'amber');
  document.getElementById('navBadgeIntake').textContent = waitingDrops.length ? `${waitingDrops.length}🚪` : String(queued.length);
  document.getElementById('navBadgeBench').textContent = String(queued.length);
  document.getElementById('navBadgeBench').className = 'nav-badge ' + ((STATE.huddle?.rush_queued_count || 0) > 0 ? 'red' : '');
  document.getElementById('navBadgeValves').textContent = String((STATE.huddle?.needs_stamp_count || 0) + STATE.active_valves.length);

  const setMobDot = (id, n) => { const el = document.getElementById(id); if (el) { el.hidden = !n; el.textContent = n; } };
  const openTodosCnt = (STATE.shop_tasks || []).filter(t => t.status === 'OPEN').length;
  setMobDot('mobBadgeIntake', waitingDrops.length);
  setMobDot('mobBadgeBench', queued.length);
  setMobDot('mobBadgeValves', needsFinish);
  setMobDot('mobBadgeTodo', openTodosCnt);
  const nbTodo = document.getElementById('navBadgeTodo');
  if (nbTodo) {
    nbTodo.textContent = String(openTodosCnt);
    nbTodo.className = 'nav-badge ' + (openTodosCnt ? 'amber' : 'green');
  }

  const b = STATE.binder_state || {};
  document.getElementById('standBinderBadge').textContent =
    `Sheet #${b.active_sheet_no || STATE.starting_sheet_no} • Row #${b.next_row_number || 1}/12`;

  renderStartOfDayHuddle(queued, openSched, waitingDrops);
  renderLiveFloorPlanMap();
  renderCalibrationControls();
  renderBackroomInbox();
  renderIntakeAndQueue(queued);
  renderStandPillsAndBench(queued);
  renderCompletedSection(doneToday);
  renderFinishValvesAndCustomers();
  renderLiveDotForm393Sheets();
  renderShopTodoStation();
  refreshOpsDispatchRadar();

  document.getElementById('feedbackTable').innerHTML = STATE.feedback_items.length
    ? STATE.feedback_items.map(fb => `
      <tr>
        <td><small>${escHtml(fb.created_at)}</small></td><td><strong>${escHtml(fb.submitted_by)}</strong></td>
        <td>${escHtml(fb.category)}</td><td>${escHtml(fb.message)}</td>
        <td>${fb.status === 'OPEN' ? `<button class="secondary" onclick="resolveFeedback(${fb.id})">Done</button>` : '<span class="pass">DONE</span>'}</td>
      </tr>`).join('')
    : '<tr><td colspan="5">No crew notes logged yet.</td></tr>';
}

setInterval(async () => {
  if (document.getElementById('appShell').hidden || BENCH_SUBMITTING) return;
  try {
    const res = await fetch('/api/ping');
    if (res.status === 401) { checkAuthAndBoot(); return; }
    if (res.ok) {
      setOfflineGuard(false);
      const p = await res.json();
      if (p.db_version !== STATE.db_version) loadState(false);
    }
  } catch (_) {
    setOfflineGuard(true);
  }
}, 3500);

// --- STATION 1: START-OF-DAY HUDDLE & CALIBRATION LADDER ---
function renderStartOfDayHuddle(queued, openSched, waitingDrops) {
  const b = STATE.binder_state || {};
  const hud = STATE.huddle || {};
  const plan = hud.selected_day_plan || {};

  document.getElementById('huddleShiftBadge').textContent = plan.shift_label || '🟢 Full Hydro & Prep Window';
  const shNo = b.active_sheet_no || STATE.starting_sheet_no || 8095;
  const used = b.rows_used_on_active || 0;
  const nxt = b.next_row_number || 1;
  document.getElementById('binderSheetTitle').textContent =
    `Active DOT Retest Sheet #${shNo} — Next Completed Row: #${nxt} of 12 (${12 - used} open left)`;

  if (b.last_recorded) {
    const lr = b.last_recorded;
    document.getElementById('binderLastNote').textContent =
      `Last completed: Sheet #${lr.sheet_no} Row ${lr.row_ended_at}/12 on ${lr.work_date} (SN ${lr.serial_number} • ${lr.customer_name}).`;
  } else {
    document.getElementById('binderLastNote').textContent =
      `Starting clean on Sheet #${shNo}, Row 1 (sheet # assigns strictly when each hydro test completes).`;
  }

  let slotsHtml = '';
  for (let i = 1; i <= 12; i++) {
    const cls = i <= used ? 'used' : (i === nxt ? 'next' : '');
    slotsHtml += `<div class="sheet-slot ${cls}">${i}</div>`;
  }
  document.getElementById('binderSlotsGrid').innerHTML = slotsHtml;

  document.getElementById('huddleKpiRow').innerHTML = `
    <div class="huddle-kpi"><small>Queued Ready</small><strong>${queued.length} Cyl${hud.rush_queued_count ? ` (${hud.rush_queued_count} 🔥)` : ''}</strong></div>
    <div class="huddle-kpi"><small>Backroom Door</small><strong>${waitingDrops.length} Waiting</strong></div>
    <div class="huddle-kpi"><small>Needs B386 Mark</small><strong>${hud.needs_stamp_count || 0} Dried</strong></div>
    <div class="huddle-kpi"><small>Dock Ready</small><strong>${hud.ready_shelf_count || 0} Ready</strong></div>`;

  const dayNoteHtml = (plan.day_notes || '').trim()
    ? `<div class="notice warn" style="margin-top:8px;margin-bottom:6px;border-left:4px solid #d97706;">📝 <strong>Day Note (${escHtml(STATE.selected_date)}):</strong> ${escHtml(plan.day_notes)}</div>`
    : '';
  const todayRuns = openSched.filter(j => j.scheduled_date <= STATE.selected_date);
  document.getElementById('huddleLineupList').innerHTML = dayNoteHtml + (todayRuns.length
    ? `<small style="font-weight:800;color:var(--muted);display:block;margin-bottom:4px;">TODAY'S SCHEDULED BATCHES &amp; ROAD RUNS:</small>` +
      todayRuns.slice(0, 4).map(j => {
        const ico = j.job_type === 'ROAD_ROUNDTRIP' ? '🔄 Pickup+Deliv' : (j.job_type === 'ROAD_PICKUP' ? '🚚 Pickup' : (j.job_type === 'ROAD_DELIVERY' ? '🚚 Delivery' : '📌 Batch'));
        return `
          <div class="rec-item job-card-clickable" onclick="openOpsJobModal(${j.id})">
            <div class="rec-main">
              <div class="rec-title"><span>${ico}: ${escHtml(j.customer_name)} (${j.bottle_count} Cyl)</span>${j.pickup_time ? `<span class="time-window-pill">🕒 ${escHtml(j.pickup_time)}</span>` : ""}${j.leave_by_time ? `<span class="leave-by-pill">🚦 Leave by ${escHtml(j.leave_by_time)}</span>` : ""}${j.drive_eta_mins ? `<span class="eta-route-pill">🚗 ~${j.drive_eta_mins}m (${j.drive_miles} mi)</span>` : ""}${j.rush_fee ? '<span class="rush-badge">🔥 RUSH</span>' : ''}</div>
              <div class="rec-sub">${escHtml(j.scheduled_date)}${j.location_notes ? ' • 📍 ' + escHtml(j.location_notes) : ''}${j.tank_summary ? ' • ' + escHtml(j.tank_summary) : ''}</div>
            </div>
            <div class="rec-actions" onclick="event.stopPropagation()">
              <button class="secondary" onclick="openOpsJobModal(${j.id})">🔍 Details</button>
              ${(j.job_type === 'ROAD_PICKUP' || j.job_type === 'ROAD_ROUNDTRIP') && !j.pickup_leg_done ? `<button class="secondary" style="background:#fef3c7;color:#92400e;border-color:#f59e0b;font-weight:800;" onclick="stageJobToBackroomFromOps(${j.id})">🚚 Picked Up → Backroom</button>` : ''}
              <button class="secondary" style="background:#ecfdf5;color:#065f46;border-color:#6ee7b7;" onclick="importScheduleJobToIntake(${j.id})">↳ Lock &amp; Prep</button>
              <a class="secondary" href="/schedule">📅 Calendar</a>
            </div>
          </div>`;
      }).join('')
    : '');
}

async function advanceToFreshSheet() {
  const b = STATE.binder_state || {};
  const nextNo = (b.active_sheet_no || 8095) + ((b.rows_used_on_active || 0) > 0 ? 1 : 0);
  if ((b.rows_used_on_active || 0) > 0 && !confirm(`Close out Sheet #${b.active_sheet_no} at Row ${b.rows_used_on_active}/12 and start fresh on Sheet #${nextNo} (Row 1)?`)) return;
  await fetch('/api/binder', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'fresh_next' }) });
  loadState(false);
}

async function setCustomBinderSheet() {
  const val = parseInt(document.getElementById('customBinderInput').value || '0', 10);
  if (!val || val < 1) { alert('Enter a valid Retest Sheet # first.'); return; }
  await fetch('/api/binder', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'set_custom', sheet_no: val }) });
  document.getElementById('customBinderInput').value = '';
  loadState(false);
}

function localCalDraftKey() {
  return `webers_cal_draft_${STATE.selected_date || STATE.today || 'today'}`;
}

function saveCalDraftLocalAndRemote() {
  const jacket = document.getElementById('calJacket')?.value || 'Short Jacket';
  const maxPsi = parseInt(document.getElementById('calMaxPsi')?.value || '4000', 10);
  const permVal = document.getElementById('calFinalPerm')?.value ?? '0';
  const values = {};
  LADDER.forEach(s => {
    if (String(s.actual ?? '').trim() !== '') values[String(s.pressure_psi)] = String(s.actual).trim();
  });
  const payload = {
    test_date: STATE.selected_date || STATE.today,
    jacket_type: jacket,
    max_target_psi: maxPsi,
    values,
    permanent_cc: String(permVal),
    device_id: DEVICE_ID,
  };
  try { localStorage.setItem(localCalDraftKey(), JSON.stringify(payload)); } catch (_) {}
  clearTimeout(CAL_DRAFT_TIMER);
  CAL_DRAFT_TIMER = setTimeout(() => {
    fetch('/api/ops/cal-draft', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }).catch(() => {});
  }, 320);
}

function onCalJacketOrMaxChange() {
  const prevVals = {};
  LADDER.forEach(s => { if (String(s.actual ?? '').trim() !== '') prevVals[String(s.pressure_psi)] = s.actual; });
  buildCalibrationLadder(prevVals);
  saveCalDraftLocalAndRemote();
}

function onCalStepInput(idx, val) {
  if (!LADDER[idx]) return;
  LADDER[idx].actual = val;
  updateSingleStepStatusDom(idx);
  saveCalDraftLocalAndRemote();
  if (ACTIVE_BINDER_VIEW === 'verification') renderLiveDotForm393Sheets();
}

function onCalPermInput() {
  saveCalDraftLocalAndRemote();
  if (ACTIVE_BINDER_VIEW === 'verification') renderLiveDotForm393Sheets();
}

function updateSingleStepStatusDom(i) {
  const step = LADDER[i];
  const stEl = document.getElementById('stepSt_' + i);
  if (!step || !stEl) return;
  const minP = (Math.ceil(step.expected_cc * 0.99 * 10) / 10).toFixed(1);
  const maxP = (Math.floor(step.expected_cc * 1.01 * 10) / 10).toFixed(1);
  const raw = String(step.actual ?? '').trim();
  if (!raw || isNaN(parseFloat(raw))) {
    stEl.innerHTML = `<small style="color:var(--muted);">${minP}–${maxP} cc</small>`;
    return;
  }
  const actualNum = parseFloat(raw);
  const dev = (((step.expected_cc - actualNum) / step.expected_cc) * 100).toFixed(2);
  const ok = Math.abs(parseFloat(dev)) <= 1.0 || Math.abs(step.expected_cc - actualNum) <= 0.15;
  stEl.innerHTML = `<span class="${ok ? 'pass' : 'fail'}">${ok ? '✓ PASS' : '⚠ OUT'} (${dev}%)</span>`;
}

function renderCalibrationControls() {
  const activeId = document.activeElement ? document.activeElement.id : '';
  const userTypingCal = activeId.startsWith('stepIn_') || activeId === 'calFinalPerm' || activeId === 'calJacket' || activeId === 'calMaxPsi';

  if (LAST_CAL_DATE !== STATE.selected_date) {
    LAST_CAL_DATE = STATE.selected_date;
    LADDER = [];
  }
  const recCal = STATE.huddle?.recommended_cal_psi || 0;
  const recBadge = document.getElementById('calRecommendedBadge');
  recBadge.textContent = recCal ? `Queue Max: ${STATE.huddle.max_queued_test_psi}# → Calibrate to ${recCal} PSI` : '49 CFR §180.215 (±1.0%)';
  recBadge.className = recCal ? 'sub-badge ai-verified' : 'sub-badge';

  const maxSel = document.getElementById('calMaxPsi');
  const calJacketSel = document.getElementById('calJacket');
  const fallbackMax = calJacketSel.value === 'Short Jacket' ? '3000' : String(recCal || 4000);
  const curMax = !LADDER.length ? fallbackMax : (maxSel.value || fallbackMax);
  maxSel.innerHTML = STATE.calibration_targets.map((t, idx) => {
    const allow = t.pressure_psi >= 3000 ? t.pressure_psi + 500 : Math.round(t.pressure_psi * 1.1);
    const stepWord = idx === 0 ? 'step' : 'steps';
    return `<option value="${t.pressure_psi}" ${String(t.pressure_psi) === String(curMax) ? 'selected' : ''}>${t.pressure_psi} PSI (${idx + 1} ${stepWord} → ≤${allow})</option>`;
  }).join('');

  // Apply remote/local draft if user is not actively typing in a calibration field
  const remoteDraft = STATE.cal_draft && STATE.cal_draft.test_date === STATE.selected_date ? STATE.cal_draft : null;
  if (!userTypingCal && remoteDraft && remoteDraft.updated_at && remoteDraft.updated_at !== LAST_APPLIED_DRAFT_TS && remoteDraft.device_id !== DEVICE_ID) {
    LAST_APPLIED_DRAFT_TS = remoteDraft.updated_at;
    if (remoteDraft.jacket_type && calJacketSel) calJacketSel.value = remoteDraft.jacket_type;
    if (remoteDraft.max_target_psi && maxSel) maxSel.value = String(remoteDraft.max_target_psi);
    buildCalibrationLadder(remoteDraft.values || {});
    const permIn = document.getElementById('calFinalPerm');
    if (permIn && remoteDraft.permanent_cc !== undefined) permIn.value = remoteDraft.permanent_cc;
    if (Object.keys(remoteDraft.values || {}).length > 0) {
      CAL_FORCE_EXPANDED = true;
      updateCalCollapseUI();
    }
  } else if (!LADDER.length) {
    let seedVals = {};
    if (remoteDraft && Object.keys(remoteDraft.values || {}).length > 0) {
      if (remoteDraft.jacket_type && calJacketSel) calJacketSel.value = remoteDraft.jacket_type;
      if (remoteDraft.max_target_psi && maxSel) maxSel.value = String(remoteDraft.max_target_psi);
      seedVals = remoteDraft.values || {};
    } else {
      try {
        const loc = JSON.parse(localStorage.getItem(localCalDraftKey()) || 'null');
        if (loc && loc.values && Object.keys(loc.values).length > 0) {
          if (loc.jacket_type && calJacketSel) calJacketSel.value = loc.jacket_type;
          if (loc.max_target_psi && maxSel) maxSel.value = String(loc.max_target_psi);
          seedVals = loc.values;
        }
      } catch (_) {}
    }
    buildCalibrationLadder(seedVals);
  }

  const canDeleteCal = !window.CURRENT_USER || ['OWNER', 'SYSTEM_ADMIN'].includes(window.CURRENT_USER.role);
  const seenRunIds = new Set();
  document.getElementById('calTable').innerHTML = STATE.verifications.length
    ? STATE.verifications.map(v => {
        const firstOfRun = v.run_id && !seenRunIds.has(v.run_id);
        if (v.run_id) seenRunIds.add(v.run_id);
        const safeRunId = String(v.run_id || '').replace(/'/g, "\\'");
        const delBtn = (canDeleteCal && firstOfRun)
          ? ` <button type="button" class="secondary" title="Delete this calibration run" style="padding:1px 6px;font-size:0.7rem;color:#b91c1c;margin-left:5px;" onclick="deleteCalibrationRun('${safeRunId}', '${escHtml(v.jacket_type)}', '${escHtml(v.test_time)}')">✕</button>`
          : '';
        return `
      <tr>
        <td>${escHtml(v.test_time)}</td><td>${escHtml(v.jacket_type)}</td><td><strong>${v.target_pressure_psi}</strong></td>
        <td>${v.expected_cc}</td><td>${v.actual_total_cc}</td><td>${v.permanent_cc}</td>
        <td>${v.percent_deviation}%</td><td>${escHtml(v.logged_by || 'RP')}</td>
        <td style="white-space:nowrap;"><span class="${v.passed ? 'pass' : 'fail'}">${v.passed ? 'PASS' : 'FAIL'}</span>${delBtn}</td>
      </tr>`;
      }).join('')
    : '<tr><td colspan="9">No calibrations logged for this date yet.</td></tr>';
}

async function deleteCalibrationRun(runId, jacket, timeLbl) {
  if (!runId) return;
  if (!confirm(`Delete the ${jacket} calibration run (${timeLbl}) from this date's DOT verification log?`)) return;
  const res = await fetch(`/api/verifications/run/${encodeURIComponent(runId)}`, { method: 'DELETE' });
  if (!res.ok) { alert((await res.json()).detail || 'Could not delete calibration run.'); return; }
  await loadState(true);
  if (typeof window.syncAuthAndRoleUI === 'function') window.syncAuthAndRoleUI(window.CURRENT_USER, false);
}

function buildCalibrationLadder(preserveValues = {}) {
  const jacket = document.getElementById('calJacket').value;
  const maxSel = document.getElementById('calMaxPsi');
  if (jacket === 'Short Jacket' && !LADDER.length && !Object.keys(preserveValues || {}).length && maxSel.querySelector('option[value="3000"]')) {
    maxSel.value = '3000';
  }
  const maxPsi = parseInt(maxSel.value || '4000', 10);
  LADDER = STATE.calibration_targets
    .filter(t => t.pressure_psi <= maxPsi)
    .map(t => ({ ...t, actual: preserveValues[String(t.pressure_psi)] ?? '', locked: false }));
  ACTIVE_STEP = 0;
  renderLadder(false);
}

function renderLadder(autoFocusActive = false) {
  document.getElementById('ladderSteps').innerHTML = LADDER.map((step, i) => {
    const minP = (Math.ceil(step.expected_cc * 0.99 * 10) / 10).toFixed(1);
    const maxP = (Math.floor(step.expected_cc * 1.01 * 10) / 10).toFixed(1);
    let statusHtml = `<small style="color:var(--muted);">${minP}–${maxP} cc</small>`;
    if (String(step.actual ?? '').trim() !== '' && !isNaN(parseFloat(step.actual))) {
      const actualNum = parseFloat(step.actual);
      const dev = (((step.expected_cc - actualNum) / step.expected_cc) * 100).toFixed(2);
      const ok = Math.abs(parseFloat(dev)) <= 1.0 || Math.abs(step.expected_cc - actualNum) <= 0.15;
      statusHtml = `<span class="${ok ? 'pass' : 'fail'}">${ok ? '✓ PASS' : '⚠ OUT'} (${dev}%)</span>`;
    }
    return `
      <div class="ladder-row ${i === ACTIVE_STEP ? 'active-step' : ''}">
        <div><strong>Step ${i + 1}: ${step.pressure_psi}#</strong></div>
        <div>Chart: <strong>${step.expected_cc} cc</strong></div>
        <div>
          <input type="number" step="0.1" inputmode="decimal" id="stepIn_${i}" value="${escHtml(step.actual)}" placeholder="Burette cc"
            onfocus="ACTIVE_STEP=${i}"
            oninput="onCalStepInput(${i}, this.value)"
            onkeydown="if(event.key==='Enter'){event.preventDefault();advanceLadderFocus(${i});}" />
        </div>
        <div id="stepSt_${i}">${statusHtml}</div>
      </div>`;
  }).join('');

  const rel = document.getElementById('releaseSection');
  if (rel) rel.hidden = false;
  if (autoFocusActive) {
    setTimeout(() => {
      const el = document.getElementById('stepIn_' + ACTIVE_STEP);
      if (el) el.focus();
    }, 30);
  }
}

function advanceLadderFocus(idx) {
  playShopTone('step');
  if (idx + 1 < LADDER.length) {
    ACTIVE_STEP = idx + 1;
    const nxt = document.getElementById('stepIn_' + ACTIVE_STEP);
    if (nxt) { nxt.focus(); nxt.select(); }
  } else {
    const perm = document.getElementById('calFinalPerm');
    if (perm) { perm.focus(); perm.select(); }
  }
}

function lockLadderStep(idx) {
  advanceLadderFocus(idx);
}

async function submitCalibrationLadder(forcePass = false) {
  const filledSteps = LADDER
    .map((s, idx) => {
      const domVal = document.getElementById('stepIn_' + idx)?.value ?? s.actual;
      return { ...s, actual: String(domVal ?? '').trim() };
    })
    .filter(s => s.actual !== '' && !isNaN(parseFloat(s.actual)) && parseFloat(s.actual) > 0);

  const saveNotice = document.getElementById('calSaveNotice');
  if (!filledSteps.length) {
    if (saveNotice) {
      saveNotice.className = 'notice bad';
      saveNotice.textContent = '⚠️ Enter at least one burette calibration reading (cc) above before clicking Save.';
      saveNotice.hidden = false;
    }
    document.getElementById('stepIn_0')?.focus();
    return;
  }

  const permVal = parseFloat(document.getElementById('calFinalPerm')?.value || '0') || 0;
  const maxExp = Math.max(...filledSteps.map(s => Number(s.expected_cc || 0)));
  const permLimit = Math.max(0.15, Number((maxExp * 0.01).toFixed(2)));
  const outSteps = filledSteps.filter(s => {
    const a = parseFloat(s.actual);
    const dev = Math.abs(((s.expected_cc - a) / s.expected_cc) * 100);
    return dev > 1.0 && Math.abs(s.expected_cc - a) > 0.15;
  });

  if (!forcePass && (outSteps.length > 0 || Math.abs(permVal) > permLimit)) {
    playShopTone('condemn');
    const warnMsg = outSteps.length
      ? `Step ${outSteps[0].pressure_psi} PSI deviates from chart (${outSteps[0].actual} cc vs ${outSteps[0].expected_cc} cc — >1.0%).\n\nClick OK to force-certify anyway, or Cancel to re-check burette zero.`
      : `Permanent expansion (${permVal} cc) exceeds ${permLimit} cc.\n\nClick OK to force-certify anyway, or Cancel to re-bleed to zero.`;
    if (!confirm(warnMsg)) return;
    forcePass = true;
  }

  const btn = document.getElementById('btnCertCal');
  const jacketChosen = document.getElementById('calJacket').value;
  const maxFilledPsi = Math.max(...filledSteps.map(s => s.pressure_psi));
  btn.disabled = true;
  const origTxt = btn.textContent;
  btn.textContent = '⏳ Saving Calibration...';
  try {
    const res = await fetch('/api/verify-ladder', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        test_date: STATE.selected_date,
        jacket_type: jacketChosen,
        max_target_psi: maxFilledPsi,
        steps: filledSteps.map(s => ({ pressure_psi: s.pressure_psi, actual_total_cc: parseFloat(s.actual) })),
        permanent_cc: permVal,
        logged_by: getActiveCrew(),
        force_pass: forcePass,
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      if (saveNotice) {
        saveNotice.className = 'notice bad';
        saveNotice.textContent = '⛔ ' + (data.detail || 'Could not save calibration.');
        saveNotice.hidden = false;
      } else {
        alert(data.detail || 'Could not save calibration.');
      }
      return;
    }
    try { localStorage.removeItem(localCalDraftKey()); } catch (_) {}
    CAL_FORCE_EXPANDED = false;
    LADDER = [];
    playShopTone(data.passed ? 'pass' : 'condemn');
    if (saveNotice) {
      saveNotice.className = data.passed ? 'notice' : 'notice bad';
      saveNotice.textContent = data.passed
        ? `✅ SAVED! ${jacketChosen} Certified ≤ ${data.max_allowed_test_psi} PSI (${data.steps_saved || filledSteps.length} step(s) logged).`
        : '⚠️ Calibration logged as FAILED — check burette zero and re-run.';
      saveNotice.hidden = false;
    }
    await loadState(true);
    if (typeof window.syncAuthAndRoleUI === 'function') window.syncAuthAndRoleUI(window.CURRENT_USER, false);
  } finally {
    btn.disabled = false;
    btn.textContent = origTxt;
  }
}

// =========================================================================
// v16.0 LIVE INTERACTIVE DOT FORM 393-001 VISUAL PAPER SHEET
// =========================================================================
function fmtDotSpecCell(rec) {
  let sp = String(rec.dot_spec || '').trim().toUpperCase().replace('SP-', 'SP');
  const psi = parseInt(rec.service_psi || '0', 10);
  if (!sp) return psi ? String(psi) : '';
  const star = (rec.star_10yr && (rec.hydro_disposition || 'A') === 'A') ? ' ★' : '';
  if (psi && sp.endsWith(String(psi))) return sp + star;
  if (sp.startsWith('SP') || sp.startsWith('E-')) {
    return (psi ? `${sp}-${psi}` : sp) + star;
  }
  return (psi ? `${sp}${psi}` : sp) + star;
}

function fmtDotNum(v) {
  if (v === null || v === undefined || v === '') return '';
  const n = Number(v);
  if (isNaN(n)) return '';
  return Number.isInteger(n) ? n.toFixed(1) : n.toFixed(2).replace(/0$/, '');
}

function cleanReeForSheet(rec) {
  const sp = String(rec.dot_spec || '').trim().toUpperCase();
  if (['3A', '3AA', '3AL'].includes(sp) && Number(rec.ree_limit) === 7.2) return '';
  return (rec.ree_limit !== null && rec.ree_limit !== undefined && rec.ree_limit !== '') ? fmtDotNum(rec.ree_limit) : '';
}

let ACTIVE_BINDER_VIEW = localStorage.getItem('webers_binder_view') || 'retest';

function setLiveBinderView(view) {
  ACTIVE_BINDER_VIEW = view;
  try { localStorage.setItem('webers_binder_view', view); } catch (_) {}
  renderLiveDotForm393Sheets();
}

function buildVerificationMethodBPaperHtml(moS, dyS, yrS) {
  const calSn = STATE.calibrated_serial || '1SF11904';
  const savedVers = STATE.verifications || [];
  const draftJacket = document.getElementById('calJacket')?.value || 'Short Jacket';
  const draftPermNum = parseFloat(document.getElementById('calFinalPerm')?.value || '0') || 0;
  const draftSteps = (LADDER || []).filter(s => String(s.actual ?? '').trim() !== '' && !isNaN(parseFloat(s.actual)));

  const combined = [
    ...savedVers.map(v => ({
      kind: 'saved',
      time: v.test_time || '',
      jacket: v.jacket_type || '',
      psi: v.target_pressure_psi,
      expA: v.expected_cc,
      actB: v.actual_total_cc,
      perm: v.permanent_cc ?? 0,
      elastic: v.elastic_cc,
      dev: v.percent_deviation,
      passed: Boolean(v.passed),
    })),
    ...draftSteps.map((s, idx) => {
      const actNum = parseFloat(s.actual);
      const pVal = idx === draftSteps.length - 1 ? draftPermNum : 0;
      const elVal = actNum - pVal;
      const devVal = (((s.expected_cc - actNum) / s.expected_cc) * 100).toFixed(2);
      const ok = Math.abs(parseFloat(devVal)) <= 1.0 || Math.abs(s.expected_cc - actNum) <= 0.15;
      return {
        kind: 'draft',
        time: 'LIVE',
        jacket: draftJacket,
        psi: s.pressure_psi,
        expA: s.expected_cc,
        actB: actNum,
        perm: pVal,
        elastic: elVal,
        dev: devVal,
        passed: ok,
      };
    }),
  ];

  const totalRows = Math.max(10, combined.length);
  let bodyHtml = '';
  for (let i = 0; i < totalRows; i++) {
    const r = combined[i];
    if (r) {
      const cls = r.kind === 'draft' ? 'stand-live-row' : 'done-row';
      bodyHtml += `
        <tr class="dot-data-tr ${cls}" onclick="jumpToTab('cal')">
          <td class="dot-num-td">${i + 1}</td>
          <td class="mono-ink">${escHtml(r.time)} • ${escHtml(r.jacket.replace(' Jacket', ''))}</td>
          <td class="mono-ink"><strong>${escHtml(r.psi)}</strong></td>
          <td class="mono-ink">${fmtDotNum(r.expA)}</td>
          <td class="mono-ink live-val"><strong>${fmtDotNum(r.actB)}</strong></td>
          <td class="mono-ink">${fmtDotNum(r.perm)}</td>
          <td class="mono-ink">${fmtDotNum(r.elastic)}</td>
          <td class="mono-ink ${r.passed ? '' : 'bad'}"><strong>${escHtml(r.dev)}%</strong></td>
          <td class="chk-ink ${r.passed ? '' : 'bad'}">${r.passed ? '✓ PASS' : '✗ OUT'}</td>
        </tr>`;
    } else {
      bodyHtml += `
        <tr class="dot-data-tr blank-row" onclick="jumpToTab('cal')">
          <td class="dot-num-td">${i + 1}</td>
          <td><span class="blank-row-hint">+ Cal Step</span></td>
          <td></td><td></td><td></td><td></td><td></td><td></td><td></td>
        </tr>`;
    }
  }

  return `
    <div class="dot-paper-sheet">
      <div class="dot-paper-top">
        <div class="dot-paper-sheetno" style="font-size:0.86rem;color:#334155;">LEFT PAGE</div>
        <div class="dot-paper-title">VERIFICATION DATA FOR WATER JACKET TESTING</div>
        <div class="dot-paper-rin-row">
          <span><strong>Date:</strong> <u class="dot-ink-u">&nbsp;${escHtml(String(parseInt(moS || '1', 10)))} / ${escHtml(String(parseInt(dyS || '1', 10)))} / ${escHtml(yrS)}&nbsp;</u></span>
          <span><strong>Serial No. of Calibrated Cylinder:</strong> <u class="dot-ink-u">&nbsp;${escHtml(calSn)} (7,500# Chart)&nbsp;</u></span>
          <span><strong>Operator's Name:</strong> <u class="dot-ink-u">&nbsp;${window.CURRENT_USER?.role === 'SYSTEM_ADMIN' ? 'ADMIN DIAGNOSTIC' : 'Ryan Pearce (RP)'}&nbsp;</u></span>
        </div>
        <div style="font-size:0.76rem;margin-top:4px;color:#1e293b;">
          <strong>Method B</strong> — Verification procedure using the Pressure Indicating Device (PID) against Expansion Indicating Device (EID)
        </div>
      </div>
      <table class="dot-paper-table">
        <thead>
          <tr>
            <th style="width:26px;">#</th>
            <th>Time &amp; Jacket</th>
            <th>Pressure Reading<br/><small>(From Chart PSI)</small></th>
            <th>Expected Expansion<br/><small>Reading (cc's) (A)</small></th>
            <th>Actual Expansion<br/><small>Reading from E.I.D. (B)</small></th>
            <th>Permanent<br/><small>After Release</small></th>
            <th>Elastic<br/><small>(Total - Perm)</small></th>
            <th>% Deviation<br/><small>((A-B)/A × 100)</small></th>
            <th>±1.0%<br/><small>Check</small></th>
          </tr>
        </thead>
        <tbody>${bodyHtml}</tbody>
      </table>
      <div class="dot-paper-footer">
        <div>
          <strong>Example (Method B):</strong> 3000 PSI | Expected (A): 54.0 | Actual (B): 53.8 | Perm: 0 | Elastic: 53.8 | % Dev: 0.37%<br/>
          <strong>Formula:</strong> (A − B) ÷ A × 100 = % Deviation (±1.0% max) • Actual (B) − Permanent = Elastic Expansion
        </div>
        <div style="text-align:right;">
          <div>Reorder no. 393-001 • CFR Title 49 §180.215</div>
          <div><strong>Master Chart ${escHtml(calSn)}:</strong> 2000→34.2 • 3000→51.4 • 5000→86.2 • 7500→130.4 cc</div>
        </div>
      </div>
    </div>`;
}

function buildVisualInspectionPaperHtml(moS, yrS) {
  const recs = STATE.records || [];
  const visRin = STATE.visual_rin || 'V143159';
  const dotRin = STATE.dot_rin || 'B386';
  const inspMy = `${String(moS || '11').padStart(2, '0')}/${String(yrS || '2026').slice(-2)}`;
  const totalRows = Math.max(10, recs.length);
  let bodyHtml = '';

  for (let i = 0; i < totalRows; i++) {
    const r = recs[i];
    if (r) {
      const phys = String(r.physical_size || ((r.outside_dia && r.length_in) ? `${r.outside_dia}x${r.length_in}` : '')).replace(/\s*x\s*/gi, 'x');
      const disp = r.hydro_disposition === 'D' ? 'SC' : (r.visual_disposition || 'OK');
      const rvChg = (r.relief_valve_changed === 'Yes' || r.addon_burst_disc) ? 'Yes' : 'No';
      const op = String(r.operator_initials || '').includes('ADMIN') ? 'ADM*' : 'RP';
      const rCls = r.status === 'COMPLETED' ? 'done-row' : 'queued-preview-row';
      bodyHtml += `
        <tr class="dot-data-tr ${rCls}" onclick="openEditModal(${r.id})" title="Click to edit visual checks or stamps for SN ${escHtml(r.serial_number)}">
          <td class="mono-ink"><strong>${escHtml(r.serial_number)}</strong></td>
          <td class="mono-ink">${escHtml(String(r.mfg_date || '').replace(/^'+/, ''))}</td>
          <td class="mono-ink">${escHtml(fmtDotSpecCell(r))}</td>
          <td class="mono-ink">${escHtml(r.mfg_id || '')}</td>
          <td class="dot-owner-td">${escHtml(r.customer_name || 'Walk-In')}</td>
          <td class="mono-ink">${escHtml(phys)}</td>
          <td class="mono-ink">${escHtml(r.coating_type || 'Painted')}</td>
          <td class="mono-ink">${escHtml(r.coating_condition || 'Good')}</td>
          <td class="mono-ink">${escHtml(r.chk_corrosion || 'OK')}</td>
          <td class="mono-ink">${escHtml(r.chk_dents_dings || 'OK')}</td>
          <td class="mono-ink">${escHtml(r.chk_cuts_gouges || 'OK')}</td>
          <td class="mono-ink">${escHtml(r.chk_leaks || 'OK')}</td>
          <td class="mono-ink">${escHtml(r.chk_fire_damage || 'OK')}</td>
          <td class="mono-ink">${escHtml(r.chk_bulges || 'OK')}</td>
          <td class="mono-ink">${escHtml(r.chk_neck_defects || 'OK')}</td>
          <td class="mono-ink ${disp !== 'OK' ? 'bad' : ''}"><strong>${escHtml(disp)}</strong></td>
          <td class="mono-ink">${escHtml(inspMy)}</td>
          <td class="mono-ink">${rvChg}</td>
          <td class="mono-ink">${op}</td>
        </tr>`;
    } else {
      bodyHtml += `
        <tr class="dot-data-tr blank-row" onclick="jumpToTab('intake')">
          <td><span class="blank-row-hint">+ Visual Row</span></td>
          <td></td><td></td><td></td><td></td><td></td><td></td><td></td>
          <td></td><td></td><td></td><td></td><td></td><td></td><td></td>
          <td></td><td></td><td></td><td></td>
        </tr>`;
    }
  }

  return `
    <div class="dot-paper-sheet">
      <div class="dot-paper-top">
        <div class="dot-paper-sheetno" style="font-size:0.84rem;color:#92400e;">${escHtml(visRin)} E</div>
        <div class="dot-paper-title">D.O.T./I.C.C. CYLINDER VISUAL INSPECTION AND REQUALIFICATION LOG</div>
        <div class="dot-paper-rin-row">
          <span><strong>Company:</strong> <u class="dot-ink-u">&nbsp;Weber's Tank Testing&nbsp;</u></span>
          <span><strong>Plant / City:</strong> <u class="dot-ink-u">&nbsp;3086 Legionville Rd, Baden, PA 15005&nbsp;</u></span>
          <span><strong>RIN:</strong> <u class="dot-ink-u">&nbsp;${escHtml(visRin)} (Propane/Visual) / ${escHtml(dotRin)}&nbsp;</u></span>
        </div>
      </div>
      <table class="dot-paper-table">
        <thead>
          <tr>
            <th colspan="6">CYLINDER IDENTIFICATION</th>
            <th colspan="2">PROTECTIVE COATING</th>
            <th colspan="7">CYLINDER CHECKED FOR</th>
            <th rowspan="2">Disp.<br/><small>Code</small></th>
            <th rowspan="2">Date Insp.<br/><small>(MM/YY)</small></th>
            <th rowspan="2">Relief<br/><small>Changed</small></th>
            <th rowspan="2">Init.</th>
          </tr>
          <tr>
            <th>Serial No.</th><th>Mfg Date</th><th>ICC/DOT Spec</th><th>Mfg</th><th>Owner</th><th>Size</th>
            <th>Type</th><th>Cond.</th>
            <th>Corr.</th><th>Dents</th><th>Cuts</th><th>Leaks</th><th>Fire</th><th>Bulges</th><th>Neck</th>
          </tr>
        </thead>
        <tbody>${bodyHtml}</tbody>
      </table>
      <div class="dot-paper-footer">
        <div><strong>Original</strong> — Retain in Service Unit files for five years after last inspection date listed.</div>
        <div><strong>Disposition Code:</strong> OK - Return to Service • SC - Scrap (Condemned) • RM - Removed from Service</div>
        <div style="text-align:right;"><strong>Responsible Manager:</strong> ${window.CURRENT_USER?.role === 'SYSTEM_ADMIN' ? 'ADMIN DIAGNOSTIC' : 'Ryan Pearce'}</div>
      </div>
    </div>`;
}

function buildDotForm393PaperHtml(hostKind) {
  const b = STATE.binder_state || {};
  const shNo = b.active_sheet_no || STATE.starting_sheet_no || 8095;
  const completedOnSheet = Array.isArray(STATE.active_sheet_records)
    ? STATE.active_sheet_records.slice(0, 12)
    : (STATE.records || []).filter(r => r.status === 'COMPLETED' && r.service_mode !== 'VISUAL_ONLY' && r.sheet_no === shNo).slice(0, 12);

  const queuedHydro = (STATE.records || []).filter(r => r.status === 'QUEUED' && r.service_mode !== 'VISUAL_ONLY');
  const activeStandId = parseInt(document.getElementById('benchSelect')?.value || '0', 10);

  // Put currently selected Stand bottle first in the upcoming open rows!
  let orderedQueue = [...queuedHydro];
  if (activeStandId) {
    const idx = orderedQueue.findIndex(r => r.id === activeStandId);
    if (idx > 0) {
      const [picked] = orderedQueue.splice(idx, 1);
      orderedQueue.unshift(picked);
    }
  }

  // Optional live intake preview if typing on Station 2
  const liveInSn = (document.getElementById('fSerial')?.value || '').trim();
  const liveInSpec = (document.getElementById('fSpec')?.value || '').trim();
  const liveInCust = (document.getElementById('fCustomer')?.value || '').trim();
  const hasIntakeDraft = Boolean(liveInSn || liveInSpec);

  const rawDate = (completedOnSheet.length ? completedOnSheet[completedOnSheet.length - 1].work_date : '') || STATE.selected_date || STATE.today || '';
  const [yrS, moS, dyS] = rawDate.includes('-') ? rawDate.split('-') : ['2026', '11', '18'];
  const sMax = STATE.jacket_ceilings?.['Short Jacket']?.max_allowed_test_psi || 0;
  const tMax = STATE.jacket_ceilings?.['Tall Jacket']?.max_allowed_test_psi || 0;
  const calOk = Boolean(sMax || tMax);

  let qCursor = 0;
  let draftShown = false;
  let rowsHtml = '';

  for (let i = 0; i < 12; i++) {
    const rowNum = i + 1;
    let rec = completedOnSheet[i] || null;
    let rowKind = 'blank';

    if (rec) {
      rowKind = 'done';
    } else if (qCursor < orderedQueue.length) {
      rec = orderedQueue[qCursor++];
      rowKind = (rec.id === activeStandId) ? 'stand' : 'queued';
    } else if (hasIntakeDraft && !draftShown) {
      draftShown = true;
      rowKind = 'draft';
      const dia = (document.getElementById('fDia')?.value || '').trim();
      const len = (document.getElementById('fLen')?.value || '').trim();
      rec = {
        customer_name: liveInCust || 'Walk-In (Typing...)',
        serial_number: liveInSn || '—',
        mfg_id: (document.getElementById('fMfg')?.value || '').trim(),
        mfg_date: (document.getElementById('fMfgDate')?.value || '').trim(),
        gas_service: (document.getElementById('fGas')?.value || 'AIR').trim(),
        physical_size: (dia && len) ? `${dia}x${len}` : (dia || len || ''),
        dot_spec: liveInSpec,
        service_psi: document.getElementById('fSvcPsi')?.value || '',
        test_psi: document.getElementById('fTestPsi')?.value || '',
        ree_limit: document.getElementById('fRee')?.value || '',
        star_10yr: Boolean(document.getElementById('fStar10Yr')?.checked),
      };
    }

    if (rowKind === 'done') {
      const visOk = rec.visual_pass !== 0 && rec.visual_pass !== false;
      const disp = rec.hydro_disposition || 'A';
      const opCell = (String(rec.operator_initials || '').includes('ADMIN') || String(rec.tested_by || '').includes('ADMIN')) ? 'ADM*' : 'RP';
      const phys = String(rec.physical_size || ((rec.outside_dia && rec.length_in) ? `${rec.outside_dia}x${rec.length_in}` : '')).replace(/\s*x\s*/gi, 'x');
      const stampBadge = disp !== 'A'
        ? '<span class="dot-row-pill bad">⛔ Condemned</span>'
        : (rec.rin_marked
          ? `<span class="dot-row-pill ok">${rec.rin_mark_type === 'STICKERED' ? '🏷️ B386 Stickered' : '🔨 B386 Stamped'}</span>`
          : '<span class="dot-row-pill warn">⏳ Drying • Needs B386</span>');
      const noteTxt = rec.dot_row_note || rec.notes || '';
      rowsHtml += `
        <tr class="dot-data-tr done-row ${disp !== 'A' ? 'condemn-row' : ''}" onclick="openEditModal(${rec.id})" title="Click row to edit any field on SN ${escHtml(rec.serial_number)}">
          <td rowspan="2" class="dot-num-td">${rowNum}</td>
          <td class="dot-owner-td">${escHtml(rec.customer_name || 'Walk-In')}</td>
          <td class="mono-ink"><strong>${escHtml(rec.serial_number)}</strong></td>
          <td class="mono-ink">${escHtml(rec.mfg_id || '')}</td>
          <td class="mono-ink">${escHtml(String(rec.mfg_date || '').replace(/^'+/, ''))}</td>
          <td class="mono-ink">${escHtml(rec.gas_service || 'AIR')}</td>
          <td class="mono-ink">${escHtml(phys)}</td>
          <td class="mono-ink">${escHtml(fmtDotSpecCell(rec))}</td>
          <td class="mono-ink">${escHtml(rec.test_psi || '')}</td>
          <td class="mono-ink">${fmtDotNum(rec.total_cc)}</td>
          <td class="mono-ink">${fmtDotNum(rec.permanent_cc)}</td>
          <td class="mono-ink">${fmtDotNum(rec.elastic_cc)}</td>
          <td class="mono-ink">${rec.percent_permanent !== null && rec.percent_permanent !== undefined ? Number(rec.percent_permanent).toFixed(1) + '%' : ''}</td>
          <td class="mono-ink">${cleanReeForSheet(rec)}</td>
          <td class="chk-ink">${visOk ? '✓' : ''}</td>
          <td class="chk-ink bad">${!visOk ? '✓' : ''}</td>
          <td class="mono-ink"><strong>${escHtml(disp)}</strong></td>
          <td class="mono-ink">${opCell}</td>
        </tr>
        <tr class="dot-note-tr done-row" onclick="openEditModal(${rec.id})" title="Click to edit SN ${escHtml(rec.serial_number)}">
          <td colspan="17">
            <div class="dot-note-flex">
              <span><strong>Notes:</strong> ${escHtml(noteTxt || 'Certified — 49 CFR §180.205')}</span>
              <span class="dot-note-actions">${stampBadge} <span class="dot-edit-hint">✎ Edit</span></span>
            </div>
          </td>
        </tr>`;
    } else if (rowKind === 'stand' || rowKind === 'queued') {
      const phys = String(rec.physical_size || ((rec.outside_dia && rec.length_in) ? `${rec.outside_dia}x${rec.length_in}` : '')).replace(/\s*x\s*/gi, 'x');
      let totStr = '', permStr = '', elStr = '', pctStr = '', visPassMark = '', visFailMark = '', dispStr = '';
      if (rowKind === 'stand') {
        const bTotRaw = document.getElementById('bTotal')?.value || '';
        const bPermRaw = document.getElementById('bPerm')?.value || '0';
        const bTot = parseFloat(bTotRaw);
        const bPerm = parseFloat(bPermRaw || '0');
        const visFailed = Object.values(BENCH_VIS || {}).some(v => v !== 'OK');
        visPassMark = visFailed ? '' : '✓';
        visFailMark = visFailed ? '✓' : '';
        if (bTotRaw && bTot > 0) {
          const elNum = bTot - bPerm;
          const pctNum = (bPerm / bTot) * 100;
          const effReeStr = cleanReeForSheet(rec);
          const reeFail = effReeStr !== '' && elNum > parseFloat(effReeStr);
          const permFail = pctNum > 10.0 || bPerm < 0 || bPerm > bTot;
          totStr = fmtDotNum(bTot);
          permStr = fmtDotNum(bPerm);
          elStr = fmtDotNum(elNum);
          pctStr = pctNum.toFixed(1) + '%';
          dispStr = (visFailed || reeFail || permFail) ? 'D' : 'A';
        }
      }
      const clickAct = rowKind === 'stand'
        ? (CURRENT_TAB === 'bench' ? `document.getElementById('bTotal')?.focus()` : `loadBottleToStand(${rec.id})`)
        : `loadBottleToStand(${rec.id})`;
      rowsHtml += `
        <tr class="dot-data-tr ${rowKind === 'stand' ? 'stand-live-row' : 'queued-preview-row'}" onclick="${clickAct}" title="${rowKind === 'stand' ? 'Currently active on Hydro Stand' : 'Click to load onto Hydro Stand'}">
          <td rowspan="2" class="dot-num-td">${rowNum}</td>
          <td class="dot-owner-td">${escHtml(rec.customer_name || 'Walk-In')}</td>
          <td class="mono-ink"><strong>${escHtml(rec.serial_number)}</strong></td>
          <td class="mono-ink">${escHtml(rec.mfg_id || '')}</td>
          <td class="mono-ink">${escHtml(String(rec.mfg_date || '').replace(/^'+/, ''))}</td>
          <td class="mono-ink">${escHtml(rec.gas_service || 'AIR')}</td>
          <td class="mono-ink">${escHtml(phys)}</td>
          <td class="mono-ink">${escHtml(fmtDotSpecCell(rec))}</td>
          <td class="mono-ink">${escHtml(rec.test_psi || '')}</td>
          <td class="mono-ink live-val">${totStr}</td>
          <td class="mono-ink live-val">${permStr}</td>
          <td class="mono-ink live-val">${elStr}</td>
          <td class="mono-ink live-val">${pctStr}</td>
          <td class="mono-ink">${cleanReeForSheet(rec)}</td>
          <td class="chk-ink">${visPassMark}</td>
          <td class="chk-ink bad">${visFailMark}</td>
          <td class="mono-ink live-val"><strong>${dispStr}</strong></td>
          <td class="mono-ink" style="opacity:0.45;">RP</td>
        </tr>
        <tr class="dot-note-tr ${rowKind === 'stand' ? 'stand-live-row' : 'queued-preview-row'}" onclick="${clickAct}">
          <td colspan="17">
            <div class="dot-note-flex">
              <span><strong>Notes:</strong> ${rowKind === 'stand' ? '<strong style="color:#b45309;">▶ ON HYDRO STAND NOW — type Total &amp; Perm cc to certify into this row</strong>' : '<em>Queued &amp; Prepped — tap row to load onto Hydro Stand</em>'}${rec.valve_tag && !rec.valve_reinstalled ? ` • Tag #${escHtml(rec.valve_tag)}` : ''}</span>
              <span class="dot-note-actions" onclick="event.stopPropagation()">
                <button type="button" class="dot-mini-btn" onclick="openEditModal(${rec.id})">✎ Edit Stamps</button>
              </span>
            </div>
          </td>
        </tr>`;
    } else if (rowKind === 'draft') {
      rowsHtml += `
        <tr class="dot-data-tr draft-preview-row">
          <td rowspan="2" class="dot-num-td">${rowNum}</td>
          <td class="dot-owner-td">${escHtml(rec.customer_name)}</td>
          <td class="mono-ink">${escHtml(rec.serial_number)}</td>
          <td class="mono-ink">${escHtml(rec.mfg_id)}</td>
          <td class="mono-ink">${escHtml(rec.mfg_date)}</td>
          <td class="mono-ink">${escHtml(rec.gas_service)}</td>
          <td class="mono-ink">${escHtml(rec.physical_size)}</td>
          <td class="mono-ink">${escHtml(fmtDotSpecCell(rec))}</td>
          <td class="mono-ink">${escHtml(rec.test_psi)}</td>
          <td></td><td></td><td></td><td></td><td class="mono-ink">${escHtml(rec.ree_limit)}</td><td></td><td></td><td></td><td></td>
        </tr>
        <tr class="dot-note-tr draft-preview-row">
          <td colspan="17"><strong>Notes:</strong> <em style="color:#0369a1;">📷 Live Prep Preview — click "Queue Bottle &amp; Next" to lock stamps</em></td>
        </tr>`;
    } else {
      rowsHtml += `
        <tr class="dot-data-tr blank-row" onclick="jumpToTab('intake')">
          <td rowspan="2" class="dot-num-td">${rowNum}</td>
          <td class="dot-owner-td"><span class="blank-row-hint">+ Open Row</span></td>
          <td></td><td></td><td></td><td></td><td></td><td></td><td></td>
          <td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td>
        </tr>
        <tr class="dot-note-tr blank-row" onclick="jumpToTab('intake')">
          <td colspan="17"><strong>Notes:</strong></td>
        </tr>`;
    }
  }

  const storageKey = `webers_dot393_open_${hostKind}`;
  const defaultOpen = hostKind !== 'intake';
  const savedState = localStorage.getItem(storageKey);
  const isOpen = savedState === null ? defaultOpen : savedState === '1';

  const retestSheetHtml = `
    <div class="dot-paper-sheet">
      <div class="dot-paper-top">
        <div class="dot-paper-sheetno">${shNo}</div>
        <div class="dot-paper-title">HYDROSTATIC WATER JACKET TEST RETEST DATA SHEET</div>
        <div class="dot-paper-rin-row">
          <span><strong>D.O.T. Requalification Identification Number (RIN):</strong> <u class="dot-ink-u">${escHtml(STATE.dot_rin || 'B386')}</u></span>
          <span><strong>and/or Transport Canada (TC) Registration Number:</strong> <u class="dot-ink-u">&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</u></span>
        </div>
        <div class="dot-paper-date-row">
          <span><strong>Date:</strong> <u class="dot-ink-u">&nbsp;${escHtml(String(parseInt(moS || '1', 10)))} / ${escHtml(String(parseInt(dyS || '1', 10)))} / ${escHtml(yrS)}&nbsp;</u></span>
          <span class="dot-cal-stamp ${calOk ? 'ok' : 'wait'}">
            ${calOk
              ? `✓ Master ${escHtml(STATE.calibrated_serial || '1SF11904')} Verified (${sMax ? `Short ≤${sMax}#` : ''}${sMax && tMax ? ' • ' : ''}${tMax ? `Tall ≤${tMax}#` : ''})`
              : `⚠ Verify Master Cyl ${escHtml(STATE.calibrated_serial || '1SF11904')} Prior to Testing`}
          </span>
        </div>
      </div>

      <table class="dot-paper-table">
        <thead>
          <tr>
            <th rowspan="2" style="width:24px;">#</th>
            <th rowspan="2" style="min-width:118px;">Cylinder Owner</th>
            <th rowspan="2" style="min-width:96px;">Cylinder Serial<br/>Number</th>
            <th rowspan="2" style="min-width:52px;">MFG ID</th>
            <th rowspan="2" style="min-width:50px;">MFG<br/>Date</th>
            <th rowspan="2" style="min-width:44px;">Gas<br/>Service</th>
            <th rowspan="2" style="min-width:68px;">Physical Size (*)<br/><small>(dia. x length)</small></th>
            <th rowspan="2" style="min-width:86px;">Specification &amp;<br/>Service Pressure</th>
            <th rowspan="2" style="min-width:56px;">Actual Test<br/>Pressure</th>
            <th colspan="5">EXPANSION READINGS (cc's)</th>
            <th colspan="2">Visual Insp.</th>
            <th rowspan="2" style="min-width:48px;">Disposition<br/>Code (*)</th>
            <th rowspan="2" style="min-width:46px;">Retest<br/>Operator</th>
          </tr>
          <tr>
            <th style="min-width:44px;">Total</th>
            <th style="min-width:52px;">Permanent</th>
            <th style="min-width:44px;">Elastic</th>
            <th style="min-width:52px;">%<br/>Permanent</th>
            <th style="min-width:38px;">REE</th>
            <th style="min-width:32px;">Pass</th>
            <th style="min-width:32px;">Fail</th>
          </tr>
          <tr class="dot-cal-notice-tr">
            <td></td>
            <td colspan="17"><strong><em>Calibrated Cylinder:</em></strong> <em>Be sure to verify the system accuracy with the calibrated cylinder prior to each day of testing. Use the back of this form to properly record the verification procedure.</em></td>
          </tr>
        </thead>
        <tbody>
          ${rowsHtml}
        </tbody>
      </table>

      <div class="dot-paper-footer">
        <div>
          <div>I hereby certify that all of the above tests were made under my supervision and in accordance with DOT and/or TC regulations.</div>
          <div class="dot-sig-line">
            <span class="dot-sig-script">${window.CURRENT_USER?.role === 'SYSTEM_ADMIN' ? 'ADMIN DIAGNOSTIC (NO RP SIGN-OFF)' : 'Ryan Pearce'}</span>
            <small>(Facility Manager)</small>
          </div>
        </div>
        <div class="dot-disp-legend">
          <strong>(*) Disposition Codes:</strong>
          <span>A - Return to Service • B - Hold for Retest • C - Rejected • D - Condemned • E - Other</span>
        </div>
        <div style="text-align:right;">
          <div>49 CFR §180.215 Compliant Record • <strong>Weber's Tank Testing (RIN ${escHtml(STATE.dot_rin || 'B386')})</strong></div>
          <div>Reorder no. 393-001 • 3086 Legionville Rd, Baden, PA 15005 • 5-Year Vault</div>
        </div>
      </div>
    </div>`;

  const activePaperHtml = ACTIVE_BINDER_VIEW === 'verification'
    ? buildVerificationMethodBPaperHtml(moS, dyS, yrS)
    : (ACTIVE_BINDER_VIEW === 'visual' ? buildVisualInspectionPaperHtml(moS, yrS) : retestSheetHtml);

  const calStepCnt = (STATE.verifications || []).length;
  const visRowCnt = (STATE.records || []).length;

  return `
    <details class="dot-sheet-CollapseWrap" ${isOpen ? 'open' : ''} ontoggle="localStorage.setItem('${storageKey}', this.open ? '1' : '0')">
      <summary class="dot-sheet-summary-bar">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
          <span>📋 <strong>Live DOT Shop Logbook — Sheet #${shNo}</strong></span>
          <span class="sub-badge">${completedOnSheet.length}/12 Certified${orderedQueue.length ? ` • +${orderedQueue.length} Queued` : ''}</span>
          <span style="font-size:0.75rem;color:var(--ui-muted);font-weight:600;">Click any row to edit stamps/readings</span>
        </div>
        <div style="display:flex;align-items:center;gap:8px;" onclick="event.stopPropagation()">
          <a class="secondary" style="padding:4px 10px;font-size:0.74rem;" href="/print?tab=print" title="Print Hydro (B386) or Propane (V143159 E) stickers">🏷️ Stickers</a>
          <a class="secondary" style="padding:4px 10px;font-size:0.74rem;" href="/print?sheet=${shNo}">🗄️ Filing Cabinet →</a>
        </div>
      </summary>

      <div class="dot-binder-tabs">
        <div class="dot-binder-tab-group">
          <button type="button" class="dot-btab ${ACTIVE_BINDER_VIEW === 'verification' ? 'active' : ''}" onclick="setLiveBinderView('verification')">📊 Left Page: Verification (${calStepCnt} Steps)</button>
          <button type="button" class="dot-btab ${ACTIVE_BINDER_VIEW === 'retest' ? 'active' : ''}" onclick="setLiveBinderView('retest')">📖 Right Page: Retest Sheet #${shNo}</button>
          <button type="button" class="dot-btab ${ACTIVE_BINDER_VIEW === 'visual' ? 'active' : ''}" onclick="setLiveBinderView('visual')">🔍 Visual &amp; Propane Log (${visRowCnt})</button>
        </div>
        <a class="dot-binder-print-shortcut" href="/print?sheet=${shNo}">🖨️ Open Printable PDF Studio →</a>
      </div>

      <div class="dot-paper-scroll">
        ${activePaperHtml}
      </div>
    </details>`;
}

function renderLiveDotForm393Sheets() {
  const startHost = document.getElementById('liveDotSheetStartHost');
  const intakeHost = document.getElementById('liveDotSheetIntakeHost');
  const benchHost = document.getElementById('liveDotSheetBenchHost');
  if (startHost) startHost.innerHTML = buildDotForm393PaperHtml('start');
  if (intakeHost) intakeHost.innerHTML = buildDotForm393PaperHtml('intake');
  if (benchHost) benchHost.innerHTML = buildDotForm393PaperHtml('bench');
}

document.addEventListener('DOMContentLoaded', () => {
  ['fCustomer', 'fSerial', 'fSpec', 'fSvcPsi', 'fTestPsi', 'fMfg', 'fMfgDate', 'fDia', 'fLen', 'fGas', 'fRee', 'fStar10Yr'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('input', () => {
      const intakeHost = document.getElementById('liveDotSheetIntakeHost');
      if (intakeHost && CURRENT_TAB === 'intake') intakeHost.innerHTML = buildDotForm393PaperHtml('intake');
    });
  });
});

// =========================================================================
// v16.0 PERSISTENT SHOP & PROPERTY TO-DO BOARD (CREW + OWNER + LIVE TV)
// =========================================================================
let SHOP_TODO_FILTER = 'OPEN';
let EDITING_TODO_ID = null;

function setShopTodoFilter(mode) {
  SHOP_TODO_FILTER = mode;
  document.querySelectorAll('#shopTodoFilterBar .tap-pill').forEach(b => {
    b.classList.toggle('active', b.getAttribute('data-tfilter') === mode);
  });
  renderShopTodoStation();
}

function renderShopTodoStation() {
  const area = document.getElementById('shopTodoListArea');
  if (!area) return;

  // Populate assignee dropdown cleanly without losing selection
  const assignSel = document.getElementById('shopTodoAssign');
  if (assignSel && document.activeElement !== assignSel) {
    const prev = assignSel.value || 'ALL';
    const crewOpts = (STATE.crew || []).map(c => `<option value="${escHtml(c.initials)}">${escHtml(c.full_name)} (${escHtml(c.initials)})</option>`).join('');
    assignSel.innerHTML = `<option value="ALL">All Crew</option>${crewOpts}`;
    if ([...assignSel.options].some(o => o.value === prev)) assignSel.value = prev;
  }

  const allTasks = STATE.shop_tasks || [];
  const openCnt = allTasks.filter(t => t.status === 'OPEN').length;
  const bEl = document.getElementById('todoOpenCountBadge');
  if (bEl) bEl.textContent = `${openCnt} Open`;

  const rows = allTasks.filter(t => {
    if (SHOP_TODO_FILTER === 'OPEN') return t.status === 'OPEN';
    if (SHOP_TODO_FILTER === 'DONE') return t.status === 'DONE';
    if (SHOP_TODO_FILTER === 'PROPERTY') return t.category === 'PROPERTY' && t.status === 'OPEN';
    if (SHOP_TODO_FILTER === 'SHOP') return t.category !== 'PROPERTY' && t.status === 'OPEN';
    return true;
  });

  if (!rows.length) {
    area.innerHTML = `<div style="padding:18px;text-align:center;color:var(--muted);font-size:0.88rem;background:var(--ui-surface-2);border-radius:14px;border:1px dashed var(--ui-border-strong);">
      ${SHOP_TODO_FILTER === 'DONE' ? 'No completed tasks logged yet.' : '🎉 All tasks in this view are checked off! Add a new shop or property item above.'}
    </div>`;
    return;
  }

  area.innerHTML = rows.map(t => {
    const isDone = t.status === 'DONE';
    const catLbl = t.category === 'PROPERTY' ? '🚜 Property & Grounds' : (t.category === 'CYLINDERS' ? '🧪 Cylinders & Stand' : '🔧 Shop & Garage');
    const catCls = t.category === 'PROPERTY' ? 'todo-cat-prop' : 'todo-cat-shop';

    if (EDITING_TODO_ID === t.id) {
      return `
        <div class="todo-card editing">
          <div class="todo-edit-grid">
            <div style="grid-column:1/-1;">
              <label>Task Description</label>
              <input id="editTodoTitle_${t.id}" value="${escHtml(t.title)}" onkeydown="if(event.key==='Enter')saveEditShopTodo(${t.id})" />
            </div>
            <div>
              <label>Area</label>
              <select id="editTodoCat_${t.id}">
                <option value="PROPERTY" ${t.category === 'PROPERTY' ? 'selected' : ''}>🚜 Property &amp; Grounds</option>
                <option value="SHOP" ${t.category === 'SHOP' ? 'selected' : ''}>🔧 Shop &amp; Garage</option>
                <option value="CYLINDERS" ${t.category === 'CYLINDERS' ? 'selected' : ''}>🧪 Cylinders &amp; Stand</option>
              </select>
            </div>
            <div>
              <label>Priority</label>
              <select id="editTodoPrio_${t.id}">
                <option value="NORMAL" ${t.priority !== 'HIGH' ? 'selected' : ''}>Normal</option>
                <option value="HIGH" ${t.priority === 'HIGH' ? 'selected' : ''}>⚡ High Priority</option>
              </select>
            </div>
            <div>
              <label>Crew Note (Optional)</label>
              <input id="editTodoNote_${t.id}" value="${escHtml(t.crew_note || '')}" placeholder="Status note or parts needed..." />
            </div>
          </div>
          <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:10px;">
            <button type="button" class="secondary" onclick="EDITING_TODO_ID=null;renderShopTodoStation()">Cancel</button>
            <button type="button" class="primary" style="width:auto;margin:0;padding:8px 16px;" onclick="saveEditShopTodo(${t.id})">✓ Save Changes</button>
          </div>
        </div>`;
    }

    return `
      <div class="todo-card ${isDone ? 'is-done' : ''} ${t.priority === 'HIGH' && !isDone ? 'is-high' : ''}">
        <button type="button" class="todo-check-btn ${isDone ? 'checked' : ''}" onclick="toggleShopTodoStatus(${t.id}, '${isDone ? 'OPEN' : 'DONE'}')" title="${isDone ? 'Mark Open again' : 'Check off task'}">
          ${isDone ? '✓' : ''}
        </button>
        <div class="todo-body">
          <div class="todo-title-row">
            <span class="todo-title-txt">${escHtml(t.title)}</span>
            <span class="todo-cat-pill ${catCls}">${catLbl}</span>
            ${t.priority === 'HIGH' && !isDone ? '<span class="rush-badge">⚡ HIGH</span>' : ''}
            ${t.assigned_to && t.assigned_to !== 'ALL' ? `<span class="sub-badge">👤 ${escHtml(t.assigned_name || t.assigned_to)}</span>` : ''}
          </div>
          <div class="todo-meta-row">
            ${isDone
              ? `✓ Checked off by <strong>${escHtml(t.completed_by || 'Crew')}</strong>${t.completed_at ? ' at ' + escHtml(t.completed_at) : ''}`
              : `Added by ${escHtml(t.created_by || 'RP')}${t.work_date ? ' • ' + escHtml(t.work_date) : ''} • Broadcasts on Shop TV`}
            ${t.crew_note ? ` • 📝 <em>${escHtml(t.crew_note)}</em>` : ''}
          </div>
        </div>
        <div class="todo-actions">
          <button type="button" class="secondary" onclick="startEditShopTodo(${t.id})">✎ Edit</button>
          <button type="button" class="secondary" style="color:var(--ui-danger);" onclick="deleteShopTodoItem(${t.id})" title="Delete task">✕</button>
        </div>
      </div>`;
  }).join('');
}

async function addShopTodoItem() {
  const inp = document.getElementById('shopTodoTitle');
  const title = (inp?.value || '').trim();
  if (!title) { inp?.focus(); return; }
  const category = document.getElementById('shopTodoCat')?.value || 'SHOP';
  const priority = document.getElementById('shopTodoPrio')?.value || 'NORMAL';
  const assigned_to = document.getElementById('shopTodoAssign')?.value || 'ALL';
  const res = await fetch('/api/day/tasks', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, category, priority, assigned_to }),
  });
  if (!res.ok) { alert((await res.json()).detail || 'Could not add task.'); return; }
  inp.value = '';
  if (SHOP_TODO_FILTER === 'DONE') SHOP_TODO_FILTER = 'OPEN';
  playShopTone('step');
  await loadState(false);
}

async function toggleShopTodoStatus(id, nextStatus) {
  const res = await fetch(`/api/day/tasks/${id}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status: nextStatus }),
  });
  if (!res.ok) { alert((await res.json()).detail || 'Could not update task.'); return; }
  if (nextStatus === 'DONE') playShopTone('pass');
  await loadState(false);
}

function startEditShopTodo(id) {
  EDITING_TODO_ID = id;
  renderShopTodoStation();
  setTimeout(() => document.getElementById('editTodoTitle_' + id)?.focus(), 30);
}

async function saveEditShopTodo(id) {
  const title = (document.getElementById('editTodoTitle_' + id)?.value || '').trim();
  if (!title) { alert('Task description cannot be empty.'); return; }
  const category = document.getElementById('editTodoCat_' + id)?.value || 'SHOP';
  const priority = document.getElementById('editTodoPrio_' + id)?.value || 'NORMAL';
  const crew_note = document.getElementById('editTodoNote_' + id)?.value ?? '';
  const res = await fetch(`/api/day/tasks/${id}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, category, priority, crew_note }),
  });
  if (!res.ok) { alert((await res.json()).detail || 'Could not save task.'); return; }
  EDITING_TODO_ID = null;
  await loadState(false);
}

async function deleteShopTodoItem(id) {
  if (!confirm('Remove this item from the Shop & Property To-Do list?')) return;
  const res = await fetch(`/api/day/tasks/${id}`, { method: 'DELETE' });
  if (!res.ok) { alert((await res.json()).detail || 'Could not delete task.'); return; }
  await loadState(false);
}
