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
  } else if (tab && ['cal', 'intake', 'bench', 'logs', 'search'].includes(tab)) {
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
  setMobDot('mobBadgeIntake', waitingDrops.length);
  setMobDot('mobBadgeBench', queued.length);
  setMobDot('mobBadgeValves', needsFinish);

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
}

function onCalPermInput() {
  saveCalDraftLocalAndRemote();
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
