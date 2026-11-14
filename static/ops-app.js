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
let DEFERRED_PWA = null;
let DEEP_LINK_APPLIED = false;

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
  if (r.rush_fee || r.priority === 'RUSH') return '<span class="rush-badge">🔥 RUSH</span>';
  if (r.priority === 'HIGH') return '<span class="high-badge">⚡ HIGH</span>';
  return '';
}

function dwellBadgeHtml(days, prefix = '⏳') {
  if (!days || days < 1) return '';
  return `<span class="dwell-badge ${days >= 4 ? 'old' : ''}">${prefix} ${days}d</span>`;
}

function stageBadgeHtml(r) {
  if (r.status === 'QUEUED') return '<span class="stage-chip">🔧 Queued</span>';
  const st = r.shop_stage || 'IN_SHOP';
  if (st === 'READY_BACKROOM') return '<span class="stage-chip ready">📦 Zone E Shelf</span>';
  if (st === 'PICKED_UP') return `<span class="stage-chip">✓ Picked Up${r.picked_up_by ? ' (' + escHtml(r.picked_up_by) + ')' : ''}</span>`;
  if (st === 'CONDEMNED_HOLD' || r.hydro_disposition === 'D') return '<span class="stage-chip" style="background:#fee2e2;color:#991b1b;">⛔ Condemned</span>';
  if (st === 'STAMPED_STICKERED' || r.rin_marked) {
    const lbl = r.rin_mark_type === 'STICKERED' ? '🏷️ B386 Stickered' : '🔨 Stamped B386';
    return `<span class="stage-chip stamped">${lbl}</span>`;
  }
  if (st === 'DRYING') return '<span class="stage-chip drying">💨 Drying / Needs Mark</span>';
  return '<span class="stage-chip">🔧 In Shop</span>';
}

function getActiveCrew() {
  const sel = document.getElementById('activeCrewSelect');
  return (sel && sel.value) || localStorage.getItem('webers_active_crew') || 'RP';
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
async function checkAuthAndBoot() {
  const params = new URLSearchParams(location.search);
  const urlDate = params.get('date');
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
    if (typeof syncAutoShutterBtn === 'function') syncAutoShutterBtn();
    await loadState(true);
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
  } else if (dropId) {
    importDropoffToIntake(dropId);
    jumpToTab('intake');
  } else if (tab && ['cal', 'intake', 'bench', 'logs'].includes(tab)) {
    jumpToTab(tab);
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
  checkAuthAndBoot();
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

function toggleMenuDrawer(show) { document.getElementById('menuDrawerModal').hidden = !show; }
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
  if (name === 'logs') runSearch(false);
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function changeWorkingDate() {
  CAL_FORCE_EXPANDED = false;
  loadState(true);
}

function setCalExpanded(exp) {
  CAL_FORCE_EXPANDED = exp;
  updateCalCollapseUI();
}

function updateCalCollapseUI() {
  const sMax = STATE.jacket_ceilings['Short Jacket']?.max_allowed_test_psi || 0;
  const tMax = STATE.jacket_ceilings['Tall Jacket']?.max_allowed_test_psi || 0;
  const bothReady = sMax > 0 && tMax > 0;
  document.getElementById('calCollapsedBanner').hidden = !(bothReady && !CAL_FORCE_EXPANDED);
  document.getElementById('calFormCard').hidden = (bothReady && !CAL_FORCE_EXPANDED);
  if (bothReady) {
    document.getElementById('calCollapsedSummary').textContent =
      `Short ≤ ${sMax} PSI • Tall ≤ ${tMax} PSI (Signed Off: RP)`;
  }
}

async function eraseAllTestData() {
  if (!confirm('ERASE ALL PRACTICE / TEST DATA?\n\nWipes practice calibrations, bottles, drop-offs, schedule jobs, valve tags, and invoices while keeping API keys, Rolodex, supplies, crew, and rates intact.')) return;
  const res = await fetch('/api/admin/wipe-test-data', { method: 'POST' });
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
  const res = await fetch('/api/bootstrap' + (wDate ? `?work_date=${encodeURIComponent(wDate)}` : ''));
  if (res.status === 401) { checkAuthAndBoot(); return; }
  STATE = await res.json();

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
        <button class="secondary" onclick="toggleGuide(true)">Update Cert Date</button>
      </div>`).join('');
  } else {
    certBanner.hidden = true;
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
  document.getElementById('keyStatusText').textContent = STATE.has_gemini_key ? 'Configured & Ready' : 'Not Set';

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
  if (rebuildLadder) {
    if (sCeil.max_calibrated_psi && !tCeil.max_calibrated_psi) calJacketSel.value = 'Tall Jacket';
    else if (!sCeil.max_calibrated_psi && tCeil.max_calibrated_psi) calJacketSel.value = 'Short Jacket';
  }
  updateCalCollapseUI();

  const queued = STATE.records.filter(r => r.status === 'QUEUED');
  const doneToday = STATE.records.filter(r => r.status === 'COMPLETED');
  const openSched = (STATE.schedule_jobs || []).filter(j => j.status !== 'COMPLETED');
  const waitingDrops = (STATE.dropoffs || []).filter(d => d.status === 'IN_BACKROOM' || d.status === 'PICKUP_LOGGED');
  const needsFinish = (STATE.in_shop_post_test || []).filter(r => r.shop_stage !== 'READY_BACKROOM' && r.shop_stage !== 'PICKED_UP').length + STATE.active_valves.length;
  const totalUnbilled = STATE.customers_summary.reduce((s, c) => s + (c.unbilled_count || 0), 0);

  document.getElementById('wsSchedBadge').textContent = openSched.length ? `(${openSched.length})` : '';
  document.getElementById('wsBillBadge').textContent = (totalUnbilled + STATE.low_stock_count) ? `(${totalUnbilled || '!'})` : '';

  document.getElementById('navBadgeCal').textContent = (sCeil.max_calibrated_psi && tCeil.max_calibrated_psi) ? '✓' : '!';
  document.getElementById('navBadgeCal').className = 'nav-badge ' + ((sCeil.max_calibrated_psi && tCeil.max_calibrated_psi) ? 'green' : 'amber');
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
  renderCalibrationControls();
  renderBackroomInbox();
  renderIntakeAndQueue(queued);
  renderStandPillsAndBench(queued);
  renderCompletedSection(doneToday);
  renderFinishValvesAndCustomers();

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
    if (res.ok) {
      const p = await res.json();
      if (p.db_version !== STATE.db_version) loadState(false);
    }
  } catch (_) {}
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
    <div class="huddle-kpi"><small>Zone E Shelf</small><strong>${hud.ready_shelf_count || 0} Ready</strong></div>`;

  const todayRuns = openSched.filter(j => j.scheduled_date <= STATE.selected_date);
  document.getElementById('huddleLineupList').innerHTML = todayRuns.length
    ? `<small style="font-weight:800;color:var(--muted);display:block;margin-bottom:4px;">TODAY'S SCHEDULED BATCHES &amp; ROAD RUNS:</small>` +
      todayRuns.slice(0, 4).map(j => {
        const ico = j.job_type === 'ROAD_PICKUP' ? '🚚 Pickup' : (j.job_type === 'ROAD_DELIVERY' ? '🚚 Delivery' : '📌 Batch');
        return `
          <div class="rec-item">
            <div class="rec-main">
              <div class="rec-title"><span>${ico}: ${escHtml(j.customer_name)} (${j.bottle_count})</span>${j.rush_fee ? '<span class="rush-badge">🔥 RUSH</span>' : ''}</div>
              <div class="rec-sub">${escHtml(j.scheduled_date)}${j.location_notes ? ' • 📍 ' + escHtml(j.location_notes) : ''}${j.tank_summary ? ' • ' + escHtml(j.tank_summary) : ''}</div>
            </div>
            <div class="rec-actions">
              <button class="secondary" style="background:#ecfdf5;color:#065f46;border-color:#6ee7b7;" onclick="importScheduleJobToIntake(${j.id})">↳ Lock &amp; Prep</button>
              <a class="secondary" href="/schedule">📅 Calendar</a>
            </div>
          </div>`;
      }).join('')
    : '';
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

function renderCalibrationControls() {
  if (LAST_CAL_DATE !== STATE.selected_date) { LAST_CAL_DATE = STATE.selected_date; LADDER = []; }
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
    return `<option value="${t.pressure_psi}" ${String(t.pressure_psi) === String(curMax) ? 'selected' : ''}>${t.pressure_psi} PSI (${idx + 1} steps → ≤${allow})</option>`;
  }).join('');
  if (!LADDER.length) buildCalibrationLadder();

  document.getElementById('calTable').innerHTML = STATE.verifications.length
    ? STATE.verifications.map(v => `
      <tr>
        <td>${escHtml(v.test_time)}</td><td>${escHtml(v.jacket_type)}</td><td><strong>${v.target_pressure_psi}</strong></td>
        <td>${v.expected_cc}</td><td>${v.actual_total_cc}</td><td>${v.permanent_cc}</td>
        <td>${v.percent_deviation}%</td><td>${escHtml(v.logged_by || 'RP')}</td>
        <td class="${v.passed ? 'pass' : 'fail'}">${v.passed ? 'PASS' : 'FAIL'}</td>
      </tr>`).join('')
    : '<tr><td colspan="9">No calibrations logged for this date yet.</td></tr>';
}

function buildCalibrationLadder() {
  const jacket = document.getElementById('calJacket').value;
  const maxSel = document.getElementById('calMaxPsi');
  if (jacket === 'Short Jacket' && !LADDER.length && maxSel.querySelector('option[value="3000"]')) maxSel.value = '3000';
  const maxPsi = parseInt(maxSel.value || '4000', 10);
  LADDER = STATE.calibration_targets.filter(t => t.pressure_psi <= maxPsi).map(t => ({ ...t, actual: '', locked: false }));
  ACTIVE_STEP = 0;
  renderLadder(false);
}

function renderLadder(autoFocusActive = false) {
  document.getElementById('ladderSteps').innerHTML = LADDER.map((step, i) => {
    const minP = (Math.ceil(step.expected_cc * 0.99 * 10) / 10).toFixed(1);
    const maxP = (Math.floor(step.expected_cc * 1.01 * 10) / 10).toFixed(1);
    const isActive = i === ACTIVE_STEP, isLocked = i > ACTIVE_STEP;
    let statusHtml = `<small style="color:var(--muted);">${minP}–${maxP} cc</small>`;
    if (step.actual !== '') {
      const dev = (((step.expected_cc - parseFloat(step.actual)) / step.expected_cc) * 100).toFixed(2);
      const ok = Math.abs(parseFloat(dev)) <= 1.0;
      statusHtml = `<span class="${ok ? 'pass' : 'fail'}">${ok ? '✓ PASS' : '⚠ OUT'} (${dev}%)</span>`;
    }
    return `
      <div class="ladder-row ${isActive ? 'active-step' : ''} ${isLocked ? 'locked-step' : ''}">
        <div><strong>Step ${i + 1}: ${step.pressure_psi}#</strong></div>
        <div>Chart: <strong>${step.expected_cc} cc</strong></div>
        <div>
          <input type="number" step="0.1" id="stepIn_${i}" value="${step.actual}" placeholder="Burette cc"
            ${isLocked || step.locked ? 'disabled' : ''}
            oninput="LADDER[${i}].actual=this.value"
            onkeydown="if(event.key==='Enter'){event.preventDefault();lockLadderStep(${i});}" />
        </div>
        <div>
          ${isActive && !step.locked
            ? `<button class="secondary" style="width:100%;background:var(--green);color:#fff;border:none;" onclick="lockLadderStep(${i})">Lock ↓</button>`
            : statusHtml}
        </div>
      </div>`;
  }).join('');

  const allDone = LADDER.length > 0 && LADDER.every(s => s.locked);
  document.getElementById('releaseSection').hidden = !allDone;
  if (autoFocusActive) {
    setTimeout(() => {
      const el = allDone ? document.getElementById('calFinalPerm') : document.getElementById('stepIn_' + ACTIVE_STEP);
      if (el) { el.focus(); if (allDone) el.select(); }
    }, 30);
  }
}

function lockLadderStep(idx) {
  const step = LADDER[idx];
  if (step.actual === '' || isNaN(parseFloat(step.actual))) { alert('Enter burette reading first.'); return; }
  const dev = Math.abs(((step.expected_cc - parseFloat(step.actual)) / step.expected_cc) * 100);
  if (dev > 1.0 && !confirm(`Deviates by ${dev.toFixed(2)}% (>1.0% limit). Record anyway?`)) return;
  step.locked = true;
  if (idx + 1 < LADDER.length) ACTIVE_STEP = idx + 1;
  renderLadder(true);
}

async function submitCalibrationLadder() {
  const btn = document.getElementById('btnCertCal');
  btn.disabled = true;
  try {
    const res = await fetch('/api/verify-ladder', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        test_date: STATE.selected_date,
        jacket_type: document.getElementById('calJacket').value,
        max_target_psi: parseInt(document.getElementById('calMaxPsi').value, 10),
        steps: LADDER.map(s => ({ pressure_psi: s.pressure_psi, actual_total_cc: parseFloat(s.actual) })),
        permanent_cc: parseFloat(document.getElementById('calFinalPerm').value || '0'),
        logged_by: getActiveCrew(),
      }),
    });
    const data = await res.json();
    if (!res.ok) { alert(data.detail); return; }
    CAL_FORCE_EXPANDED = false;
    LADDER = [];
    alert(data.passed ? `CERTIFIED ≤ ${data.max_allowed_test_psi} PSI (RP)!` : 'Calibration FAILED — check burette zero.');
    await loadState(true);
  } finally {
    btn.disabled = false;
  }
}
// --- STATION 2: BACKROOM INBOX, BATCH LOCK & INTAKE QUEUE ---
function renderBackroomInbox() {
  const active = STATE.dropoffs || [];
  document.getElementById('backroomInboxCard').hidden = !active.length;
  document.getElementById('backroomCountBadge').textContent = `${active.length} Waiting`;
  document.getElementById('backroomInboxList').innerHTML = active.map(d => {
    const isPickup = d.entry_type === 'PICKUP_NOTICE' || d.status === 'PICKUP_LOGGED';
    const rem = Math.max(0, d.bottle_count - (d.imported_count || 0));
    const acctBadge = d.account_type && d.account_type !== 'WALK_IN'
      ? `<span class="acct-tag ${escHtml(d.account_type)}">${d.account_type === 'FIRE_EMS' ? '🚒 Fire/EMS' : '🏢 Commercial'}</span>`
      : '';
    return `
      <div class="dropoff-card ${isPickup ? 'pickup' : ''}">
        <div>
          <div style="font-weight:800;font-size:0.91rem;display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
            <span>${isPickup ? '🚚 VERIFIED PICKUP:' : '📥 DROP-OFF:'} ${escHtml(d.customer_name)}</span>
            ${acctBadge}
            <span class="tag-chip">${isPickup ? d.bottle_count + ' Cyl' : rem + ' of ' + d.bottle_count + ' Left'}</span>
            ${d.rush_requested ? '<span class="rush-badge">🔥 RUSH</span>' : ''}
            
            ${!isPickup && d.valves_attached === 0 ? '<span class="stage-chip">🔧 Bare / No Valves</span>' : ''}
            ${dwellBadgeHtml(d.dwell_days)}
          </div>
          <div style="font-size:0.79rem;color:var(--muted);margin-top:2px;">
            ${isPickup
              ? `Picked up by: <strong>${escHtml(d.picked_up_by || d.customer_name)}</strong>${d.pickup_ticket_ref ? ' • Ref: ' + escHtml(d.pickup_ticket_ref) : ''}`
              : `${escHtml(d.service_requested)}${d.promised_date ? ` • Est Ready: <strong>${escHtml(d.promised_date)}</strong>` : ''}${d.tank_summary ? ' • ' + escHtml(d.tank_summary) : ''}`
            }${d.customer_phone ? ' • 📞 ' + escHtml(d.customer_phone) : ''}
          </div>
          ${d.notes ? `<div style="font-size:0.77rem;color:#92400e;margin-top:2px;">Note: ${escHtml(d.notes)}</div>` : ''}
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;">
          ${!isPickup ? `<button class="primary" style="width:auto;margin:0;padding:6px 11px;font-size:0.79rem;" onclick="importDropoffToIntake(${d.id})">↳ Lock &amp; Scan Batch</button>` : ''}
          ${!isPickup ? `<button class="secondary" title="Toggle Paid Rush" onclick="toggleDropoffRush(${d.id}, ${Boolean(d.rush_requested)})">🔥</button>` : ''}
          <button class="secondary" onclick="archiveDropoff(${d.id})">✓ Clear</button>
        </div>
      </div>`;
  }).join('');

  if (window.ACTIVE_DROPOFF_ID) {
    const cur = active.find(d => d.id === window.ACTIVE_DROPOFF_ID);
    if (cur) {
      document.getElementById('activeBatchBanner').hidden = false;
      document.getElementById('activeBatchText').textContent =
        `📥 Drop-Off Batch: ${cur.customer_name} (${cur.imported_count || 0}/${cur.bottle_count} scanned${cur.rush_requested ? ' • 🔥 RUSH' : ''})`;
    } else {
      clearActiveDropoffBatch();
    }
  }
}

function setBatchLockState(locked) {
  BATCH_LOCKED = locked;
  const box = document.getElementById('batchLockBox');
  const btn = document.getElementById('btnBatchLock');
  const txt = document.getElementById('batchLockStatusTxt');
  box.classList.toggle('locked', locked);
  btn.textContent = locked ? '🔓 Unlock Batch' : '🔒 Lock Batch Specs';
  txt.textContent = locked
    ? '🔒 Batch Specs Locked — Only Serial # & Tag # Reset Between Bottles'
    : '🔓 Single-Bottle Mode (Unlocks All Fields After Queue)';
  if (locked) captureBatchSnapshot();
  else LOCKED_SNAPSHOT = null;
}

function toggleBatchLock() {
  setBatchLockState(!BATCH_LOCKED);
}

function captureBatchSnapshot() {
  LOCKED_SNAPSHOT = {
    customer: document.getElementById('fCustomer').value || document.getElementById('inCustomer').value,
    phone: document.getElementById('fPhone').value || document.getElementById('inPhone').value,
    spec: document.getElementById('fSpec').value,
    svcPsi: document.getElementById('fSvcPsi').value,
    testPsi: document.getElementById('fTestPsi').value,
    mfg: document.getElementById('fMfg').value,
    gas: document.getElementById('fGas').value || 'AIR',
    dia: document.getElementById('fDia').value,
    len: document.getElementById('fLen').value,
    ree: document.getElementById('fRee').value,
    priority: document.getElementById('fPriority').value || 'STANDARD',
    promised: document.getElementById('fPromisedDate').value,
    zone: document.getElementById('fHoldingZone').value || 'ZONE_C_READY_RACK',
  };
}

function importScheduleJobToIntake(jobId) {
  const j = (STATE.schedule_jobs || []).find(x => x.id === jobId);
  if (!j) return;
  window.ACTIVE_SCHEDULE_JOB_ID = j.id;
  window.ACTIVE_DROPOFF_ID = null;
  document.getElementById('inCustomer').value = j.customer_name;
  document.getElementById('inPhone').value = j.customer_phone || '';
  document.getElementById('fCustomer').value = j.customer_name;
  document.getElementById('fPhone').value = j.customer_phone || '';
  document.getElementById('fPriority').value = j.rush_fee ? 'RUSH' : (j.priority || 'STANDARD');
  document.getElementById('fPromisedDate').value = j.due_date || '';
  setBatchLockState(true);
  document.getElementById('activeBatchBanner').hidden = false;
  document.getElementById('activeBatchText').textContent =
    `📌 Scheduled Batch: ${j.customer_name} (${j.bottle_count} cyl${j.rush_fee ? ' • 🔥 RUSH' : ''})`;
  jumpToTab('intake');
}

function importDropoffToIntake(id) {
  const d = (STATE.dropoffs || []).find(x => x.id === id);
  if (!d) return;
  window.ACTIVE_DROPOFF_ID = d.id;
  window.ACTIVE_SCHEDULE_JOB_ID = null;
  document.getElementById('inCustomer').value = d.customer_name;
  document.getElementById('inPhone').value = d.customer_phone || '';
  document.getElementById('fCustomer').value = d.customer_name;
  document.getElementById('fPhone').value = d.customer_phone || '';
  document.getElementById('fPriority').value = d.rush_requested ? 'RUSH' : 'STANDARD';
  document.getElementById('fPromisedDate').value = d.promised_date || '';
  if ((d.service_requested || '').toLowerCase().includes('visual inspection only')) {
    document.getElementById('inMode').value = 'VISUAL_ONLY';
  }
  setBatchLockState(true);
  renderBackroomInbox();
}

function clearActiveDropoffBatch() {
  window.ACTIVE_DROPOFF_ID = null;
  window.ACTIVE_SCHEDULE_JOB_ID = null;
  document.getElementById('activeBatchBanner').hidden = true;
  setBatchLockState(false);
}

async function toggleDropoffRush(id, curRush) {
  await fetch(`/api/dropoffs/${id}/status`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rush_requested: !curRush }),
  });
  loadState(false);
}

async function archiveDropoff(id) {
  await fetch(`/api/dropoffs/${id}/status`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: 'ARCHIVED' }),
  });
  if (window.ACTIVE_DROPOFF_ID === id) clearActiveDropoffBatch();
  loadState(false);
}

function renderIntakeAndQueue(queued) {
  const presetSel = document.getElementById('cgaPreset');
  const prevPreset = presetSel.value;
  presetSel.innerHTML = '<option value="">-- Auto-Detect or Pick Size --</option>' +
    STATE.cga_specs.map((s, idx) => `<option value="${idx}" ${String(idx) === String(prevPreset) ? 'selected' : ''}>${escHtml(s.category)}: ${escHtml(s.label)} (${s.dot_spec}-${s.service_psi})</option>`).join('');

  // Merge Customer Rolodex + Summary for instant autocomplete
  const allNames = [...new Set([
    ...(STATE.customers_rolodex || []).map(c => c.customer_name),
    ...(STATE.customers_summary || []).map(c => c.customer_name),
  ])].sort((a, b) => a.localeCompare(b));
  document.getElementById('customerSuggest').innerHTML = allNames.map(n => `<option value="${escHtml(n)}">`).join('');

  document.getElementById('serialSuggest').innerHTML = STATE.records.map(r =>
    `<option value="${escHtml(r.serial_number)}">${escHtml(r.customer_name)} (${r.dot_spec}-${r.service_psi})</option>`
  ).join('');

  document.getElementById('queueCountBadge').textContent = `${queued.length} Ranked`;
  const activeTagInput = document.activeElement && document.activeElement.classList.contains('inline-vtag');
  if (!activeTagInput) {
    document.getElementById('queuePreviewList').innerHTML = queued.length
      ? queued.map((r, idx) => `
        <div class="rec-item">
          <div class="rec-main">
            <div class="rec-title">
              <span style="color:var(--muted);font-size:0.78rem;">#${idx + 1}</span>
              <span>${escHtml(r.serial_number)}</span>
              ${priorityBadgeHtml(r)}
              ${dwellBadgeHtml(r.pre_test_days)}
              <small style="color:var(--muted);">${escHtml(r.dot_spec)}-${r.service_psi} (${r.test_psi}#)</small>
            </div>
            <div class="rec-sub">
              ${escHtml(r.customer_name)}${r.promised_date ? ` • <strong>Due ${escHtml(r.promised_date)}</strong>` : ''} • <span class="zone-badge">${escHtml((r.holding_zone_label || 'Zone C').split('•')[0])}</span>
            </div>
            ${r.composite_warning ? `<div style="color:#b91c1c;font-size:0.74rem;font-weight:800;margin-top:2px;">⛔ ${escHtml(r.composite_warning)}</div>` : ''}
          </div>
          <div class="rec-actions">
            <button class="secondary" style="background:#ecfdf5;color:#065f46;border-color:#6ee7b7;" onclick="loadBottleToStand(${r.id})">💧 Stand</button>
            <button class="secondary" title="Cycle Rush / High Priority" onclick="cycleRecordPriority(${r.id}, '${escHtml(r.priority || 'STANDARD')}', ${Boolean(r.rush_fee)})">🔥</button>
            <input class="inline-vtag" value="${r.valve_tag && !r.valve_reinstalled ? escHtml(r.valve_tag) : ''}" placeholder="Tag #"
              style="width:66px;padding:5px 6px;font-weight:800;font-size:0.81rem;"
              onkeydown="if(event.key==='Enter'){event.preventDefault();this.blur();}"
              onchange="saveInlineValveTag(${r.id}, this)" />
            <button class="secondary" onclick="openEditModal(${r.id})">Edit</button>
            <button class="secondary" style="color:#b91c1c;" onclick="removeRecord(${r.id})">✕</button>
          </div>
        </div>`).join('')
      : '<div style="color:var(--muted);font-size:0.86rem;padding:8px 0;">Priority queue is clear.</div>';
  }
}

function checkIntakeLifespanLive(serverLife = null) {
  const box = document.getElementById('compositeAlertBox');
  const sp = (document.getElementById('fSpec').value || '').trim().toUpperCase().replace('SP-', 'SP');
  const mfgRaw = (document.getElementById('fMfgDate').value || '').trim();
  const dia = parseFloat(document.getElementById('fDia').value || '0');
  const len = parseFloat(document.getElementById('fLen').value || '0');

  if (serverLife && serverLife.warning) {
    box.className = serverLife.expired ? 'notice bad' : 'notice warn';
    box.textContent = (serverLife.expired ? '⛔ ' : '⚠️ ') + serverLife.warning;
    box.hidden = false;
    return;
  }

  const isComp = sp.startsWith('SP') || sp.startsWith('E-') || sp.includes('CFFC');
  const m = mfgRaw.match(/^(\d{1,2})\s*[-/\.]\s*(\d{2,4})$/);
  if (isComp && m) {
    const mo = parseInt(m[1], 10);
    let yr = parseInt(m[2], 10);
    const now = new Date();
    if (yr < 100) yr = yr <= ((now.getFullYear() % 100) + 1) ? 2000 + yr : 1900 + yr;
    const monthsLeft = ((yr + 15) - now.getFullYear()) * 12 + (mo - (now.getMonth() + 1));
    if (monthsLeft < 0) {
      box.className = 'notice bad';
      box.textContent = `⛔ EXPIRED COMPOSITE (>15 YRS): Born ${String(mo).padStart(2, '0')}-${yr} retired in ${String(mo).padStart(2, '0')}-${yr + 15}. Do NOT hydro unless covered by a valid 30-yr MAE permit.`;
      box.hidden = false;
      return;
    }
    if (monthsLeft <= 6) {
      box.className = 'notice warn';
      box.textContent = `⚠️ COMPOSITE NEAR RETIREMENT: 15-year life ends ${String(mo).padStart(2, '0')}-${yr + 15} (${monthsLeft} mo left).`;
      box.hidden = false;
      return;
    }
  }

  if (sp === '3AL' && dia > 0 && dia <= 2.0 && len > 0 && len < 24.0) {
    box.className = 'notice warn';
    box.textContent = `ℹ️ Small Cylinder Note: ≤ 2.0" OD × < 24" length (e.g. 13ci HPA) falls under the 49 CFR §180.209 small-cylinder hydro exemption unless requested.`;
    box.hidden = false;
    return;
  }
  box.hidden = true;
}

function applyPreset() {
  const idx = document.getElementById('cgaPreset').value;
  if (idx === '') return;
  const s = STATE.cga_specs[idx];
  if (document.getElementById('inCustomer').value && !document.getElementById('fCustomer').value) {
    document.getElementById('fCustomer').value = document.getElementById('inCustomer').value;
  }
  if (document.getElementById('inPhone').value && !document.getElementById('fPhone').value) {
    document.getElementById('fPhone').value = document.getElementById('inPhone').value;
  }
  document.getElementById('fSpec').value = s.dot_spec;
  document.getElementById('fSvcPsi').value = s.service_psi;
  document.getElementById('fTestPsi').value = s.test_psi;
  document.getElementById('fDia').value = s.outside_dia;
  document.getElementById('fLen').value = s.length_in;
  document.getElementById('fRee').value = s.ree_limit ?? '';
  if (BATCH_LOCKED) captureBatchSnapshot();
  checkIntakeLifespanLive();
}

function autoMatchSpecDimensions() {
  const sp = (document.getElementById('fSpec').value || '').trim().toUpperCase().replace('SP-', 'SP');
  const svc = parseInt(document.getElementById('fSvcPsi').value || '0', 10);
  if (svc > 0) {
    const matches = STATE.cga_specs.filter(s => s.dot_spec.toUpperCase() === sp && s.service_psi === svc);
    if (matches.length === 1) {
      document.getElementById('fTestPsi').value = matches[0].test_psi;
      if (!document.getElementById('fDia').value) document.getElementById('fDia').value = matches[0].outside_dia;
      if (!document.getElementById('fLen').value) document.getElementById('fLen').value = matches[0].length_in;
      if (!document.getElementById('fRee').value && matches[0].ree_limit) document.getElementById('fRee').value = matches[0].ree_limit;
    } else if (matches.length > 1) {
      document.getElementById('fTestPsi').value = matches[0].test_psi;
    } else {
      document.getElementById('fTestPsi').value = Math.round((svc * 5) / 3);
    }
  }
  checkIntakeLifespanLive();
}

function clearIntakeForm(forceUnlock = false) {
  if (forceUnlock) {
    setBatchLockState(false);
    ['fSerial', 'fValveTag', 'fCustomer', 'fPhone', 'fMfg', 'fMfgDate', 'fSpec', 'fSvcPsi', 'fTestPsi', 'fDia', 'fLen', 'fRee', 'fPromisedDate'].forEach(id => {
      document.getElementById(id).value = '';
    });
    document.getElementById('fGas').value = 'AIR';
    document.getElementById('fPriority').value = 'STANDARD';
    document.getElementById('cgaPreset').value = '';
  } else if (BATCH_LOCKED && LOCKED_SNAPSHOT) {
    document.getElementById('fSerial').value = '';
    document.getElementById('fMfgDate').value = '';
    document.getElementById('fCustomer').value = LOCKED_SNAPSHOT.customer;
    document.getElementById('fPhone').value = LOCKED_SNAPSHOT.phone;
    document.getElementById('fSpec').value = LOCKED_SNAPSHOT.spec;
    document.getElementById('fSvcPsi').value = LOCKED_SNAPSHOT.svcPsi;
    document.getElementById('fTestPsi').value = LOCKED_SNAPSHOT.testPsi;
    document.getElementById('fMfg').value = LOCKED_SNAPSHOT.mfg;
    document.getElementById('fGas').value = LOCKED_SNAPSHOT.gas;
    document.getElementById('fDia').value = LOCKED_SNAPSHOT.dia;
    document.getElementById('fLen').value = LOCKED_SNAPSHOT.len;
    document.getElementById('fRee').value = LOCKED_SNAPSHOT.ree;
    document.getElementById('fPriority').value = LOCKED_SNAPSHOT.priority;
    document.getElementById('fPromisedDate').value = LOCKED_SNAPSHOT.promised;
    document.getElementById('fHoldingZone').value = LOCKED_SNAPSHOT.zone;
  } else {
    ['fSerial', 'fValveTag', 'fMfg', 'fMfgDate', 'fSpec', 'fSvcPsi', 'fTestPsi', 'fDia', 'fLen', 'fRee'].forEach(id => {
      document.getElementById(id).value = '';
    });
    document.getElementById('fGas').value = 'AIR';
    document.getElementById('cgaPreset').value = '';
  }
  document.getElementById('compositeAlertBox').hidden = true;
  document.getElementById('intakeFieldsGrid').classList.remove('ai-filled');
  document.getElementById('sizeSourceBadge').className = 'sub-badge';
  document.getElementById('sizeSourceBadge').textContent = BATCH_LOCKED ? '🔒 Batch Locked' : 'Ready';
}

async function submitIntake(allowDup = false) {
  const cust = document.getElementById('fCustomer').value.trim() || document.getElementById('inCustomer').value.trim();
  const ph = document.getElementById('fPhone').value.trim() || document.getElementById('inPhone').value.trim();
  const pri = document.getElementById('fPriority').value || 'STANDARD';
  const prevTag = document.getElementById('fValveTag').value.trim();
  if (BATCH_LOCKED) captureBatchSnapshot();

  const res = await fetch('/api/intake', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      work_date: STATE.selected_date,
      customer_name: cust,
      customer_phone: ph,
      dropoff_id: window.ACTIVE_DROPOFF_ID || null,
      schedule_job_id: window.ACTIVE_SCHEDULE_JOB_ID || null,
      priority: pri,
      rush_fee: pri === 'RUSH',
      promised_date: document.getElementById('fPromisedDate').value || '',
      holding_zone: document.getElementById('fHoldingZone').value || 'ZONE_C_READY_RACK',
      service_mode: document.getElementById('inMode').value,
      serial_number: document.getElementById('fSerial').value,
      valve_tag: prevTag,
      mfg_id: document.getElementById('fMfg').value,
      mfg_date: document.getElementById('fMfgDate').value,
      gas_service: document.getElementById('fGas').value,
      dot_spec: document.getElementById('fSpec').value,
      service_psi: parseInt(document.getElementById('fSvcPsi').value || '0', 10),
      test_psi: parseInt(document.getElementById('fTestPsi').value || '0', 10),
      outside_dia: document.getElementById('fDia').value,
      length_in: document.getElementById('fLen').value,
      ree_limit: document.getElementById('fRee').value ? parseFloat(document.getElementById('fRee').value) : null,
      intake_by: getActiveCrew(),
      allow_duplicate: allowDup,
    }),
  });
  const data = await res.json();
  if (res.status === 409 && (data.detail || '').includes('intake queue')) {
    if (confirm(data.detail + '\n\nQueue a second entry anyway?')) return submitIntake(true);
    return;
  }
  if (!res.ok) { alert(data.detail || 'Verify Customer, Serial #, Spec, and Service PSI.'); return; }

  document.getElementById('inCustomer').value = cust;
  document.getElementById('inPhone').value = ph;
  await loadState(false);

  clearIntakeForm(false);
  if (typeof clearReferenceGallery === 'function') clearReferenceGallery();
  document.getElementById('fCustomer').value = cust;
  document.getElementById('fPhone').value = ph;
  if (prevTag && document.getElementById('chkAutoIncTag').checked) {
    document.getElementById('fValveTag').value = getNextFreeValveTag(prevTag);
  } else {
    document.getElementById('fValveTag').value = '';
  }
  document.getElementById('scanFeedback').hidden = true;
  document.getElementById('fSerial').focus();
}

async function cycleRecordPriority(id, curPri, curRush) {
  const nextPri = (curRush || curPri === 'RUSH') ? 'STANDARD' : (curPri === 'HIGH' ? 'RUSH' : 'HIGH');
  await fetch(`/api/records/${id}/priority`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ priority: nextPri, rush_fee: nextPri === 'RUSH' }),
  });
  loadState(false);
}

async function assignTagBySerial(serialInputId, tagInputId) {
  const sn = document.getElementById(serialInputId).value.trim();
  const vtag = document.getElementById(tagInputId).value.trim();
  if (!sn || !vtag) { alert('Enter both Serial # and Tag #.'); return; }
  const res = await fetch('/api/valve-tag', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ serial_number: sn, work_date: STATE.selected_date, valve_tag: vtag, valve_reinstalled: false }),
  });
  if (!res.ok) { alert((await res.json()).detail); return; }
  document.getElementById(serialInputId).value = '';
  document.getElementById(tagInputId).value = document.getElementById('chkAutoIncTag').checked ? getNextFreeValveTag(vtag) : '';
  loadState(false);
}

async function saveInlineValveTag(recordId, inputEl) {
  const res = await fetch('/api/valve-tag', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ record_id: recordId, valve_tag: inputEl.value, valve_reinstalled: false }),
  });
  if (!res.ok) { alert((await res.json()).detail); loadState(false); return; }
  inputEl.classList.add('saved-flash');
  setTimeout(() => { inputEl.classList.remove('saved-flash'); loadState(false); }, 350);
}

async function releaseValveTag(recordId) {
  await fetch('/api/valve-tag', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ record_id: recordId, valve_reinstalled: true }),
  });
  loadState(false);
}

async function removeRecord(id) {
  if (!confirm('Delete this cylinder record?')) return;
  const res = await fetch(`/api/records/${id}`, { method: 'DELETE' });
  if (!res.ok) { alert((await res.json()).detail || 'Delete failed.'); return; }
  loadState(false);
}

// --- STATION 3: HYDRO STAND (1-TAP PILLS + ADD-ON PARTS + CODE D CONDEMN LOG) ---
function loadBottleToStand(recordId) {
  jumpToTab('bench');
  selectStandBottle(recordId);
}

function selectStandBottle(recordId) {
  const sel = document.getElementById('benchSelect');
  sel.value = String(recordId);
  document.querySelectorAll('#benchPillBar .tap-pill').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-id') === String(recordId));
  });
  renderBenchForm();
  setTimeout(() => {
    const tot = document.getElementById('bTotal');
    if (tot) tot.focus();
  }, 40);
}

function pickBestCalibratedJacket(rec) {
  const reqPsi = parseInt(rec.test_psi || '0', 10);
  const sAllow = STATE.jacket_ceilings['Short Jacket']?.max_allowed_test_psi || 0;
  const tAllow = STATE.jacket_ceilings['Tall Jacket']?.max_allowed_test_psi || 0;
  const sCovers = sAllow >= reqPsi && sAllow > 0;
  const tCovers = tAllow >= reqPsi && tAllow > 0;
  if (sCovers && !tCovers) return 'Short Jacket';
  if (tCovers && !sCovers) return 'Tall Jacket';
  if (sCovers && tCovers) {
    const len = parseFloat(rec.length_in || '0');
    return (len > 0 && len <= 16.5) ? 'Short Jacket' : 'Tall Jacket';
  }
  return tAllow > 0 ? 'Tall Jacket' : (sAllow > 0 ? 'Short Jacket' : 'Tall Jacket');
}

function renderStandPillsAndBench(queued) {
  const benchSel = document.getElementById('benchSelect');
  const prevBenchId = benchSel.value;
  const activeId = document.activeElement ? document.activeElement.id : '';
  const typingOnBench = BENCH_SUBMITTING || ['bJacket', 'bTotal', 'bPerm', 'bValveTag', 'bCoat', 'bNotes', 'bValveReinst', 'bRinType', 'bCondemnAct', 'bOring', 'bBurstDisc', 'bClean'].includes(activeId);

  benchSel.innerHTML = queued.length
    ? queued.map(r => `<option value="${r.id}" ${String(r.id) === String(prevBenchId) ? 'selected' : ''}>${escHtml(r.serial_number)}</option>`).join('')
    : '<option value="">Empty</option>';

  const curSelectedId = benchSel.value;
  document.getElementById('benchPillBar').innerHTML = queued.length
    ? queued.map((r, idx) => {
        const isRush = Boolean(r.rush_fee || r.priority === 'RUSH');
        const isAct = String(r.id) === String(curSelectedId);
        return `<button type="button" class="tap-pill ${isRush ? 'rush' : ''} ${isAct ? 'active' : ''}" data-id="${r.id}" onclick="selectStandBottle(${r.id})">
          <span>#${idx + 1} ${isRush ? '🔥 ' : ''}${escHtml(r.serial_number)}</span>
          <small style="opacity:0.86;">${r.test_psi}#${r.valve_tag && !r.valve_reinstalled ? ' • 🏷️#' + escHtml(r.valve_tag) : ''}</small>
        </button>`;
      }).join('')
    : '';

  if (!typingOnBench && (benchSel.value !== prevBenchId || !document.getElementById('btnCompleteBench') || !queued.length)) {
    renderBenchForm();
  }
}

function renderVisPillsHtml(stateObj, toggleFnName) {
  return VIS_FIELDS.map(f => {
    const isFail = (stateObj[f.key] || 'OK') !== 'OK';
    return `<button type="button" class="vis-pill ${isFail ? 'fail-state' : ''}" onclick="${toggleFnName}('${f.key}')">
      <span>${f.label}</span><span>${isFail ? '✗ FAIL' : '✓ OK'}</span>
    </button>`;
  }).join('');
}

function toggleBenchVis(key) {
  BENCH_VIS[key] = BENCH_VIS[key] === 'OK' ? 'FAIL' : 'OK';
  document.getElementById('benchVisGrid').innerHTML = renderVisPillsHtml(BENCH_VIS, 'toggleBenchVis');
  const rec = STATE.records.find(r => r.id === parseInt(document.getElementById('benchSelect').value || '0', 10));
  if (rec) previewBenchMath(rec.ree_limit ?? null);
}

function renderBenchForm() {
  const id = parseInt(document.getElementById('benchSelect').value || '0', 10);
  const area = document.getElementById('benchArea');
  const rec = STATE.records.find(r => r.id === id);
  if (!rec) {
    area.innerHTML = '<p style="color:var(--muted);margin:0;">No queued bottles waiting — tap <strong>2. Prep &amp; Scan</strong> to queue the next batch.</p>';
    return;
  }

  BENCH_VIS = {};
  VIS_FIELDS.forEach(f => { BENCH_VIS[f.key] = rec[f.key] || 'OK'; });
  const isHydro = rec.service_mode === 'HYDRO_AND_VISUAL';
  const defJacket = pickBestCalibratedJacket(rec);
  const sAllow = STATE.jacket_ceilings['Short Jacket']?.max_allowed_test_psi || 0;
  const tAllow = STATE.jacket_ceilings['Tall Jacket']?.max_allowed_test_psi || 0;
  const effRee = (['3A', '3AA', '3AL'].includes((rec.dot_spec || '').toUpperCase()) && rec.ree_limit === 7.2) ? null : rec.ree_limit;
  const b = STATE.binder_state || {};

  area.innerHTML = `
    <div class="notice" id="benchPreview">
      ${priorityBadgeHtml(rec)} <strong>[${escHtml(rec.customer_name)}]</strong> • SN <strong>${escHtml(rec.serial_number)}</strong> • Target: <strong>${rec.test_psi} PSI</strong>${rec.valve_tag && !rec.valve_reinstalled ? ` • 🏷️ #<strong>${escHtml(rec.valve_tag)}</strong>` : ''}${effRee ? ` • REE ≤ <strong>${effRee} cc</strong>` : ''} • Binder: <strong>#${b.active_sheet_no || 8095} Row ${b.next_row_number || 1}</strong>
    </div>
    ${rec.composite_warning ? `<div class="notice bad">⛔ ${escHtml(rec.composite_warning)}</div>` : ''}
    <div class="grid">
      ${isHydro ? `
        <div><label>Water Jacket (Auto-Matched)</label>
          <select id="bJacket">
            <option value="Short Jacket" ${defJacket === 'Short Jacket' ? 'selected' : ''}>Short Jacket (${sAllow ? '≤' + sAllow + '#' : 'Uncal'})</option>
            <option value="Tall Jacket" ${defJacket === 'Tall Jacket' ? 'selected' : ''}>Tall Jacket (${tAllow ? '≤' + tAllow + '#' : 'Uncal'})</option>
          </select>
        </div>
        <div><label>Total Exp (cc) *</label><input type="number" step="0.1" id="bTotal" oninput="previewBenchMath(${effRee ?? 'null'})" onkeydown="if(event.key==='Enter')document.getElementById('bPerm').focus()" /></div>
        <div><label>Perm Exp (cc) *</label><input type="number" step="0.1" id="bPerm" value="0" oninput="previewBenchMath(${effRee ?? 'null'})" onkeydown="if(event.key==='Enter')completeBottle(${rec.id}, ${isHydro})" /></div>
      ` : ''}
      <div><label>🏷️ Valve / Reg Tag #</label><input id="bValveTag" value="${rec.valve_tag && !rec.valve_reinstalled ? escHtml(rec.valve_tag) : ''}" placeholder="Tag #" /></div>
      <div><label>Coating</label><select id="bCoat"><option>Excellent</option><option selected>Good</option><option>Fair</option><option>Poor</option></select></div>
      <div>
        <label>RIN Mark (If Done Now)</label>
        <select id="bRinType">
          <option value="">Mark After Hot-Air Dry</option>
          <option value="STAMPED">🔨 Neck Stamped B386</option>
          <option value="STICKERED">🏷️ B386 Sticker Applied</option>
        </select>
      </div>
      <div style="grid-column:1/-1;"><label>Row Notes</label><input id="bNotes" value="${escHtml(rec.notes || '')}" placeholder="Optional DOT note..." /></div>
    </div>

    <div id="bCondemnWrap" class="card" style="background:#fef2f2;border:1.5px solid #f87171;margin-top:10px;padding:10px;" ${rec.composite_expired ? '' : 'hidden'}>
      <label style="color:#991b1b;">⛔ PHMSA Code D Condemned Legal Disposition (49 CFR §180.205(i))</label>
      <select id="bCondemnAct">
        <option value="STAMPED_CONDEMNED">CONDEMNED Stamped Over DOT Markings</option>
        <option value="DEVALVED_UNSERVICEABLE">Returned Devalved / Threads Rendered Unserviceable</option>
        <option value="EXPIRED_15YR" ${rec.composite_expired ? 'selected' : ''}>Retired: >15-Year Composite Service Life</option>
        <option value="CUSTOMER_SCRAP">Retained for Scrap per Owner Authorization</option>
      </select>
    </div>

    <label style="margin-top:11px;">7-Point Visual Inspection (Tap Any Pill to Toggle)</label>
    <div id="benchVisGrid" class="vis-pills-grid">${renderVisPillsHtml(BENCH_VIS, 'toggleBenchVis')}</div>

    <div style="display:flex;gap:14px;flex-wrap:wrap;margin-top:11px;padding:9px 11px;background:#f8fafc;border-radius:8px;border:1px solid var(--border-soft);">
      <label class="inline-check"><input type="checkbox" id="bOring" ${rec.addon_oring ? 'checked' : ''} /><span>+ Neck O-Ring</span></label>
      <label class="inline-check"><input type="checkbox" id="bBurstDisc" ${(rec.addon_burst_disc || rec.relief_valve_changed === 'Yes') ? 'checked' : ''} /><span>+ Burst Disc / Relief</span></label>
      <label class="inline-check"><input type="checkbox" id="bClean" ${rec.addon_clean ? 'checked' : ''} /><span>+ Internal Clean</span></label>
      <label class="inline-check"><input type="checkbox" id="bValveReinst" /><span>Valve Reinstalled</span></label>
    </div>

    <button class="primary" id="btnCompleteBench" onclick="completeBottle(${rec.id}, ${isHydro})">Certify &amp; Advance to Next Cylinder (RP)</button>`;
}

function previewBenchMath(reeLimit) {
  const box = document.getElementById('benchPreview');
  const totEl = document.getElementById('bTotal');
  const condemnWrap = document.getElementById('bCondemnWrap');
  const visFailed = Object.values(BENCH_VIS).some(v => v !== 'OK');
  if (!totEl) {
    if (condemnWrap) condemnWrap.hidden = !visFailed;
    return;
  }
  const tot = parseFloat(totEl.value || '0'), perm = parseFloat(document.getElementById('bPerm').value || '0');
  if (tot <= 0) {
    if (condemnWrap) condemnWrap.hidden = !visFailed;
    return;
  }
  const elastic = (tot - perm).toFixed(2), pct = ((perm / tot) * 100).toFixed(1);
  const bad = visFailed || (reeLimit !== null && parseFloat(elastic) > reeLimit) || parseFloat(pct) > 10.0;
  box.className = bad ? 'notice bad' : 'notice';
  box.textContent = `Elastic: ${elastic} cc | % Perm: ${pct}%${visFailed ? ' | VISUAL FAIL' : ''} — ${bad ? 'CONDEMN (CODE D) — Select Legal Action Below' : 'PASS (Code A)'}`;
  if (condemnWrap) condemnWrap.hidden = !bad;
}

async function completeBottle(id, isHydro) {
  if (BENCH_SUBMITTING) return;
  if (isHydro && !(parseFloat(document.getElementById('bTotal').value) > 0)) {
    alert('Enter Burette Total Expansion (cc) first.');
    document.getElementById('bTotal').focus();
    return;
  }
  const btn = document.getElementById('btnCompleteBench');
  const rinType = document.getElementById('bRinType').value;
  const burstChecked = document.getElementById('bBurstDisc').checked;
  const condemnHidden = document.getElementById('bCondemnWrap').hidden;

  BENCH_SUBMITTING = true;
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Certifying...'; }
  try {
    const res = await fetch(`/api/records/${id}/complete`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        test_date: STATE.selected_date,
        jacket_type: isHydro ? document.getElementById('bJacket').value : 'Road/Visual',
        total_cc: isHydro ? parseFloat(document.getElementById('bTotal').value) : null,
        permanent_cc: isHydro ? parseFloat(document.getElementById('bPerm').value || '0') : null,
        valve_tag: document.getElementById('bValveTag').value,
        valve_reinstalled: document.getElementById('bValveReinst').checked,
        rin_marked: Boolean(rinType),
        rin_mark_type: rinType,
        coating_condition: document.getElementById('bCoat').value,
        relief_valve_changed: burstChecked ? 'Yes' : 'No',
        addon_oring: document.getElementById('bOring').checked,
        addon_burst_disc: burstChecked,
        addon_clean: document.getElementById('bClean').checked,
        condemn_action: condemnHidden ? '' : document.getElementById('bCondemnAct').value,
        tested_by: getActiveCrew(),
        notes: document.getElementById('bNotes').value,
        ...BENCH_VIS,
      }),
    });
    if (!res.ok) { alert((await res.json()).detail); return; }
  } finally {
    BENCH_SUBMITTING = false;
    if (btn) { btn.disabled = false; btn.textContent = 'Certify & Advance to Next Cylinder (RP)'; }
  }
  await loadState(false);
  const nextTot = document.getElementById('bTotal');
  if (nextTot) nextTot.focus();
}
// --- COMPLETED TODAY & 1-TAP REVALVE ADD-ON CHIPS ---
function renderCompletedSection(done) {
  document.getElementById('doneCountBadge').textContent = `${done.length} Done`;
  const custs = [...new Set(done.map(r => r.customer_name))];
  document.getElementById('doneBatchActions').innerHTML = custs.map(c => {
    const cRows = done.filter(r => r.customer_name === c);
    const ph = (cRows.find(r => r.customer_phone) || {}).customer_phone || '';
    const safeC = c.replace(/'/g, "\\'");
    return `
      <div style="display:flex;justify-content:space-between;align-items:center;gap:6px;padding:6px 10px;background:#f8fafc;border-radius:8px;margin-bottom:6px;flex-wrap:wrap;">
        <strong style="font-size:0.82rem;">${escHtml(c)} (${cRows.length})</strong>
        <div style="display:flex;gap:5px;flex-wrap:wrap;">
          <button class="secondary" onclick="stageCustomerBatch('${safeC}', 'STAMPED_STICKERED', 'MARKED')">🔨 Mark Batch B386</button>
          <button class="secondary" onclick="stageCustomerBatch('${safeC}', 'READY_BACKROOM', '')">📦 Revalve &amp; Shelf</button>
          <button class="secondary" onclick="openClaimSlipPdf('${safeC}')">🏷️ Claim Slip</button>
          <button class="secondary" onclick="sendPickupSms('${safeC}', '${escHtml(ph)}', ${cRows.length}, '')">📲 Text</button>
        </div>
      </div>`;
  }).join('');

  document.getElementById('doneList').innerHTML = done.length
    ? done.map(r => recordFinishRowHtml(r)).join('')
    : '<div style="color:var(--muted);font-size:0.86rem;padding:8px 0;">No completed cylinders on this date yet.</div>';
}

function recordFinishRowHtml(r) {
  const isPass = r.hydro_disposition === 'A';
  const isComp = (r.dot_spec || '').toUpperCase().startsWith('SP');
  const defMark = isComp ? 'STICKERED' : 'STAMPED';
  const hasBurst = Boolean(r.addon_burst_disc || r.relief_valve_changed === 'Yes');

  const addonsHtml = r.shop_stage !== 'PICKED_UP' ? `
    <div class="addon-bar">
      <button type="button" class="addon-chip ${r.addon_oring ? 'on' : ''}" onclick="toggleRecordAddon(${r.id}, 'addon_oring', ${!r.addon_oring})">${r.addon_oring ? '✓' : '+'} Neck O-Ring</button>
      <button type="button" class="addon-chip ${hasBurst ? 'on' : ''}" onclick="toggleRecordAddon(${r.id}, 'addon_burst_disc', ${!hasBurst})">${hasBurst ? '✓' : '+'} Burst Disc</button>
      <button type="button" class="addon-chip ${r.addon_clean ? 'on' : ''}" onclick="toggleRecordAddon(${r.id}, 'addon_clean', ${!r.addon_clean})">${r.addon_clean ? '✓' : '+'} Tumble/Clean</button>
      ${!isPass && r.condemn_action ? `<span class="acct-tag FIRE_EMS">⛔ ${escHtml(r.condemn_action.replace(/_/g, ' '))}</span>` : ''}
    </div>` : '';

  return `
    <div class="rec-item">
      <div class="rec-main">
        <div class="rec-title">
          <span>${escHtml(r.serial_number)}</span>
          ${priorityBadgeHtml(r)}
          <span class="${isPass ? 'pass' : 'fail'}">${isPass ? 'PASS (A)' : 'CONDEMN (' + escHtml(r.hydro_disposition) + ')'}</span>
          ${stageBadgeHtml(r)}
          ${r.post_test_days >= 2 ? dwellBadgeHtml(r.post_test_days, '📦') : ''}
          ${r.valve_tag && !r.valve_reinstalled ? `<span class="tag-chip">#${escHtml(r.valve_tag)}</span>` : ''}
        </div>
        <div class="rec-sub">${escHtml(r.customer_name)} • ${escHtml(r.dot_spec)}-${r.service_psi} • Sheet #${r.sheet_no} • Perm: ${fmtPctTenth(r.percent_permanent)}</div>
        ${addonsHtml}
      </div>
      <div class="rec-actions">
        ${isPass && !r.rin_marked && r.shop_stage !== 'PICKED_UP'
          ? `<button class="secondary" style="background:#ede9fe;color:#5b21b6;border-color:#c4b5fd;" onclick="setRecordStage(${r.id}, 'STAMPED_STICKERED', '${defMark}')">${isComp ? '🏷️ Stickered' : '🔨 Stamped'}</button>`
          : ''}
        ${isPass && r.shop_stage !== 'READY_BACKROOM' && r.shop_stage !== 'PICKED_UP'
          ? `<button class="secondary" style="background:#dcfce7;color:#166534;border-color:#86efac;" onclick="setRecordStage(${r.id}, 'READY_BACKROOM', '${defMark}')">📦 Revalve &amp; Shelf</button>`
          : ''}
        ${r.shop_stage === 'READY_BACKROOM' || (!isPass && r.shop_stage !== 'PICKED_UP')
          ? `<button class="secondary" onclick="setRecordStage(${r.id}, 'PICKED_UP', '')">✓ Released</button>`
          : ''}
        <button class="secondary" onclick="openEditModal(${r.id})">Edit</button>
      </div>
    </div>`;
}

async function toggleRecordAddon(recordId, field, nextVal) {
  await fetch(`/api/records/${recordId}/addons`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ [field]: nextVal }),
  });
  loadState(false);
}

function openClaimSlipPdf(custName) {
  window.open(`/api/pdf/claim-slip?customer=${encodeURIComponent(custName)}&work_date=${encodeURIComponent(STATE.selected_date)}`, '_blank');
}

async function setRecordStage(id, stage, markType = '', force = false) {
  const body = { shop_stage: stage, force };
  if (stage === 'STAMPED_STICKERED') {
    body.rin_marked = true;
    body.rin_mark_type = markType || 'STAMPED';
  }
  if (stage === 'READY_BACKROOM' || stage === 'PICKED_UP') {
    body.valve_reinstalled = true;
  }
  if (stage === 'PICKED_UP') {
    const who = prompt('Picked up / received by (leave blank for customer name):', '');
    if (who === null) return;
    if (who.trim()) body.picked_up_by = who.trim();
  }
  const res = await fetch(`/api/records/${id}/stage`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  if (res.status === 400 && stage === 'READY_BACKROOM') {
    const err = await res.json();
    if (confirm(err.detail + '\n\nConfirm it IS Stamped/Stickered & Revalved now?')) {
      await fetch(`/api/records/${id}/stage`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ shop_stage: 'READY_BACKROOM', rin_marked: true, rin_mark_type: markType || 'MARKED', valve_reinstalled: true }),
      });
    } else return;
  }
  loadState(false);
}

async function stageCustomerBatch(cust, stage, markType = '', allDates = false) {
  let pickedBy = undefined;
  if (stage === 'PICKED_UP') {
    const who = prompt(`Log pickup for ${cust} — Picked up by (optional):`, cust);
    if (who === null) return;
    pickedBy = who.trim() || cust;
  }
  const payload = {
    work_date: allDates ? null : STATE.selected_date,
    customer_name: cust,
    shop_stage: stage,
    rin_mark_type: markType || undefined,
    picked_up_by: pickedBy,
    release_valves: stage === 'READY_BACKROOM' || stage === 'PICKED_UP',
  };
  const res = await fetch('/api/batch/stage', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  });
  if (res.status === 400 && stage === 'READY_BACKROOM') {
    const err = await res.json();
    if (confirm(err.detail + '\n\nMark entire batch Stamped/Stickered, Revalved, AND move to Zone E Shelf now?')) {
      payload.rin_marked = true;
      payload.rin_mark_type = 'MARKED';
      await fetch('/api/batch/stage', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
    } else return;
  }
  loadState(false);
}

// --- STATION 4: FINISH LANE, ACTIVE TAGS, ZONE E CLAIM SLIPS & MASTER LOOKUP ---
function sendPickupSms(custName, phone, count, invExtra) {
  let ph = (phone || '').trim();
  if (!ph) {
    ph = prompt(`Enter cell phone number for ${custName}:`, '') || '';
    if (!ph.trim()) return;
  }
  const cleanDigits = ph.replace(/\D/g, '');
  const msg = `Weber's Tank Testing (RIN B386): Hi ${custName}, your ${count} cylinder(s) are tested, stamped/labeled, and ready on our Zone E backroom pickup shelf at 3086 Legionville Rd${invExtra ? ' (' + invExtra + ')' : ''}. Questions? Call/text ${STATE.shop_phone || '724-266-8008'}.`;
  const sep = /iPad|iPhone|iPod/i.test(navigator.userAgent) ? '&' : '?';
  window.location.href = `sms:${cleanDigits}${sep}body=${encodeURIComponent(msg)}`;
}

function renderFinishValvesAndCustomers() {
  const postRows = (STATE.in_shop_post_test || []).filter(r => r.shop_stage !== 'PICKED_UP');
  const needsWork = postRows.filter(r => r.shop_stage !== 'READY_BACKROOM');
  document.getElementById('finishCountBadge').textContent = `${needsWork.length} Need Finish • ${postRows.length - needsWork.length} On Shelf`;
  document.getElementById('finishBenchList').innerHTML = postRows.length
    ? postRows.map(r => recordFinishRowHtml(r)).join('')
    : '<div style="color:var(--muted);font-size:0.86rem;">All tested cylinders have been stamped/stickered, revalved, and picked up!</div>';

  document.getElementById('valveCountBadge').textContent = `${STATE.active_valves.length} Out`;
  document.getElementById('valveTrackerList').innerHTML = STATE.active_valves.length
    ? STATE.active_valves.map(r => `
      <div class="rec-item">
        <div class="rec-main">
          <div class="rec-title"><span class="tag-chip">Tag #${escHtml(r.valve_tag)}</span> <span>→ SN ${escHtml(r.serial_number)}</span></div>
          <div class="rec-sub">${escHtml(r.customer_name)} • ${escHtml(r.dot_spec)}-${r.service_psi} (${escHtml(r.status)})</div>
        </div>
        <button class="secondary" style="background:#dcfce7;color:#166534;border-color:#86efac;" onclick="releaseValveTag(${r.id})">✓ Reinstall &amp; Free #${escHtml(r.valve_tag)}</button>
      </div>`).join('')
    : '<div style="color:var(--muted);font-size:0.86rem;">All reusable valve &amp; HPA regulator tags are clear.</div>';

  document.getElementById('customersListArea').innerHTML = STATE.customers_summary.length
    ? STATE.customers_summary.map(c => {
        const safeC = c.customer_name.replace(/'/g, "\\'");
        return `
          <div class="rec-item">
            <div class="rec-main">
              <div class="rec-title">
                <span>${escHtml(c.customer_name)}</span>
                ${c.tax_exempt ? '<span class="tax-exempt-pill">Tax-Exempt</span>' : ''}
                ${c.needs_stamp_count ? `<span class="stage-chip drying">🔨 ${c.needs_stamp_count} Need Mark</span>` : ''}
                ${c.ready_backroom_count ? `<span class="stage-chip ready">📦 ${c.ready_backroom_count} Zone E</span>` : ''}
                ${c.shelf_dwell_days >= 2 ? dwellBadgeHtml(c.shelf_dwell_days, '📦') : ''}
              </div>
              <div class="rec-sub">${c.total_bottles} total • ${c.unbilled_count} unbilled • Last: ${escHtml(c.last_date)}</div>
            </div>
            <div class="rec-actions">
              ${(c.ready_backroom_count || c.needs_stamp_count) ? `<button class="secondary" onclick="openClaimSlipPdf('${safeC}')" title="Print Zone E Shelf Claim Slip">🏷️ Claim Slip</button>` : ''}
              ${c.ready_backroom_count ? `<button class="secondary" onclick="stageCustomerBatch('${safeC}', 'PICKED_UP', '', true)">✓ Picked Up</button>` : ''}
              <button class="secondary" onclick="sendPickupSms('${safeC}', '${escHtml(c.customer_phone || '')}', ${c.ready_backroom_count || c.total_bottles}, '')">📲 Text</button>
              ${c.unbilled_count ? `<a class="secondary" href="/office?tab=invoice&customer=${encodeURIComponent(c.customer_name)}">💲 Bill (${c.unbilled_count})</a>` : ''}
              <button class="secondary" onclick="filterCustomerHistory('${safeC}')">History</button>
            </div>
          </div>`;
      }).join('')
    : '<div style="color:var(--muted);font-size:0.86rem;">No customer records yet.</div>';
}

async function filterCustomerHistory(cust) {
  const res = await fetch('/api/search?customer=' + encodeURIComponent(cust));
  renderSearchRows(await res.json());
}

async function runSearch(activeTagOnly = false) {
  const q = document.getElementById('searchBox').value;
  if (activeTagOnly && !q.trim()) { renderSearchRows(STATE.active_valves); return; }
  const res = await fetch(`/api/search?q=${encodeURIComponent(q)}${activeTagOnly ? '&active_tag_only=true' : ''}`);
  renderSearchRows(await res.json());
}

function renderSearchRows(rows) {
  LAST_SEARCH_ROWS = rows;
  document.getElementById('searchResultsList').innerHTML = rows.length
    ? rows.map(r => {
        const isQueued = r.status === 'QUEUED';
        const dispTxt = isQueued ? 'QUEUED' : (r.hydro_disposition || r.status);
        const dispCls = isQueued ? 'sub-badge' : (r.hydro_disposition === 'A' ? 'pass' : 'fail');
        return `
          <div class="rec-item">
            <div class="rec-main">
              <div class="rec-title">
                <span>${escHtml(r.serial_number)}</span>
                ${priorityBadgeHtml(r)}
                <span class="${dispCls}">${escHtml(dispTxt)}</span>
                ${stageBadgeHtml(r)}
                ${r.valve_tag && !r.valve_reinstalled ? `<span class="tag-chip">#${escHtml(r.valve_tag)}</span>` : ''}
              </div>
              <div class="rec-sub">${escHtml(r.work_date)} • ${escHtml(r.customer_name)} • ${escHtml(r.dot_spec)}-${r.service_psi} • ${isQueued ? 'Awaiting Stand' : 'Sheet #' + r.sheet_no} • Perm: ${fmtPctTenth(r.percent_permanent)}</div>
            </div>
            <button class="secondary" onclick="openEditModal(${r.id})">Edit</button>
          </div>`;
      }).join('')
    : '<div style="color:var(--muted);font-size:0.86rem;padding:8px 0;">No matching cylinder records found.</div>';
}

// --- EDIT MODAL, SYNC, CREW & FACILITY SETTINGS ---
function toggleEditVis(key) {
  EDIT_VIS[key] = EDIT_VIS[key] === 'OK' ? 'FAIL' : 'OK';
  document.getElementById('erVisPills').innerHTML = renderVisPillsHtml(EDIT_VIS, 'toggleEditVis');
}

function openEditModal(recordId) {
  const rec = STATE.records.find(r => r.id === recordId)
    || (STATE.in_shop_post_test || []).find(r => r.id === recordId)
    || STATE.active_valves.find(r => r.id === recordId)
    || LAST_SEARCH_ROWS.find(r => r.id === recordId);
  if (!rec) return;
  document.getElementById('erId').value = rec.id;
  document.getElementById('editModalTitle').textContent = `Edit SN ${rec.serial_number} (${rec.status})`;
  document.getElementById('erCustomer').value = rec.customer_name || '';
  document.getElementById('erPhone').value = rec.customer_phone || '';
  document.getElementById('erSerial').value = rec.serial_number || '';
  document.getElementById('erValveTag').value = rec.valve_tag || '';
  document.getElementById('erPriority').value = (rec.rush_fee || rec.priority === 'RUSH') ? 'RUSH' : (rec.priority || 'STANDARD');
  document.getElementById('erPromisedDate').value = rec.promised_date || '';
  document.getElementById('erZone').value = rec.holding_zone || 'ZONE_C_READY_RACK';
  document.getElementById('erStage').value = rec.shop_stage || 'IN_SHOP';
  document.getElementById('erRinType').value = rec.rin_marked ? (rec.rin_mark_type || 'STAMPED') : '';
  document.getElementById('erMfg').value = rec.mfg_id || '';
  document.getElementById('erMfgDate').value = rec.mfg_date || '';
  document.getElementById('erGas').value = rec.gas_service || 'AIR';
  document.getElementById('erSpec').value = rec.dot_spec || '';
  document.getElementById('erSvcPsi').value = rec.service_psi || '';
  document.getElementById('erTestPsi').value = rec.test_psi || '';
  document.getElementById('erDia').value = rec.outside_dia || '';
  document.getElementById('erLen').value = rec.length_in || '';
  document.getElementById('erRee').value = rec.ree_limit ?? '';
  document.getElementById('erTotal').value = rec.total_cc ?? '';
  document.getElementById('erPerm').value = rec.permanent_cc ?? '';
  document.getElementById('erCoat').value = rec.coating_condition || 'Good';
  document.getElementById('erDispOvr').value = '';
  document.getElementById('erCondemnAct').value = rec.condemn_action || '';
  document.getElementById('erNotes').value = rec.notes || '';
  document.getElementById('erOring').checked = Boolean(rec.addon_oring);
  document.getElementById('erBurstDisc').checked = Boolean(rec.addon_burst_disc || rec.relief_valve_changed === 'Yes');
  document.getElementById('erClean').checked = Boolean(rec.addon_clean);
  document.getElementById('erReinst').checked = Boolean(rec.valve_reinstalled);

  EDIT_VIS = {};
  VIS_FIELDS.forEach(f => { EDIT_VIS[f.key] = rec[f.key] || 'OK'; });
  document.getElementById('erVisPills').innerHTML = renderVisPillsHtml(EDIT_VIS, 'toggleEditVis');
  document.getElementById('editRecordModal').hidden = false;
}

function closeEditModal() { document.getElementById('editRecordModal').hidden = true; }

async function saveEditedRecord() {
  const id = document.getElementById('erId').value;
  const pri = document.getElementById('erPriority').value;
  const rinType = document.getElementById('erRinType').value;
  const burstChecked = document.getElementById('erBurstDisc').checked;
  const res = await fetch(`/api/records/${id}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      customer_name: document.getElementById('erCustomer').value,
      customer_phone: document.getElementById('erPhone').value,
      priority: pri,
      rush_fee: pri === 'RUSH',
      promised_date: document.getElementById('erPromisedDate').value,
      holding_zone: document.getElementById('erZone').value,
      serial_number: document.getElementById('erSerial').value,
      valve_tag: document.getElementById('erValveTag').value,
      valve_reinstalled: document.getElementById('erReinst').checked,
      rin_marked: Boolean(rinType),
      rin_mark_type: rinType,
      shop_stage: document.getElementById('erStage').value,
      mfg_id: document.getElementById('erMfg').value,
      mfg_date: document.getElementById('erMfgDate').value,
      gas_service: document.getElementById('erGas').value,
      outside_dia: document.getElementById('erDia').value,
      length_in: document.getElementById('erLen').value,
      dot_spec: document.getElementById('erSpec').value,
      service_psi: parseInt(document.getElementById('erSvcPsi').value || '0', 10),
      test_psi: parseInt(document.getElementById('erTestPsi').value || '0', 10),
      ree_limit: document.getElementById('erRee').value !== '' ? parseFloat(document.getElementById('erRee').value) : null,
      total_cc: document.getElementById('erTotal').value !== '' ? parseFloat(document.getElementById('erTotal').value) : null,
      permanent_cc: document.getElementById('erPerm').value !== '' ? parseFloat(document.getElementById('erPerm').value) : null,
      coating_condition: document.getElementById('erCoat').value,
      relief_valve_changed: burstChecked ? 'Yes' : 'No',
      addon_oring: document.getElementById('erOring').checked,
      addon_burst_disc: burstChecked,
      addon_clean: document.getElementById('erClean').checked,
      condemn_action: document.getElementById('erCondemnAct').value,
      hydro_disposition_override: document.getElementById('erDispOvr').value || null,
      notes: document.getElementById('erNotes').value,
      ...EDIT_VIS,
    }),
  });
  if (!res.ok) { alert((await res.json()).detail || 'Save failed.'); return; }
  closeEditModal();
  await loadState(false);
  if (CURRENT_TAB === 'logs') runSearch(false);
}

async function deleteRecordFromModal() {
  const id = document.getElementById('erId').value;
  closeEditModal();
  await removeRecord(id);
  if (CURRENT_TAB === 'logs') runSearch(false);
}

async function triggerManualSync() {
  document.getElementById('railSyncChip').textContent = '↻ Syncing...';
  const res = await fetch('/api/sync-now', { method: 'POST' });
  const data = await res.json();
  alert(res.ok ? (data.synced ? `Synced ${data.pushed} item(s) to Google Sheets!` : data.reason) : data.detail);
  loadState(false);
}

async function addCrewMember() {
  const full_name = document.getElementById('newCrewName').value.trim();
  const initials = document.getElementById('newCrewInit').value.trim().toUpperCase();
  if (!full_name || !initials) { alert('Enter crew name and initials.'); return; }
  const res = await fetch('/api/crew', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ full_name, initials }),
  });
  if (!res.ok) { alert((await res.json()).detail); return; }
  document.getElementById('newCrewName').value = '';
  document.getElementById('newCrewInit').value = '';
  loadState(false);
}

async function removeCrewMember(id) {
  if (!confirm('Remove crew member from dropdown?')) return;
  const res = await fetch(`/api/crew/${id}`, { method: 'DELETE' });
  if (!res.ok) { alert((await res.json()).detail); return; }
  loadState(false);
}

async function submitCrewFeedback() {
  const message = document.getElementById('fbMsg').value.trim();
  if (!message) { alert('Type a note first.'); return; }
  const res = await fetch('/api/feedback', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      submitted_by: getActiveCrew(),
      category: document.getElementById('fbCat').value,
      priority: document.getElementById('fbPrio').value,
      page_tab: CURRENT_TAB,
      message,
    }),
  });
  if (!res.ok) { alert((await res.json()).detail); return; }
  document.getElementById('fbMsg').value = '';
  loadState(false);
}

async function resolveFeedback(id) {
  await fetch(`/api/feedback/${id}/status`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: 'DONE' }),
  });
  loadState(false);
}

async function saveAllSettings() {
  const body = {
    calibrated_serial: document.getElementById('editCalSerial').value,
    starting_sheet_no: parseInt(document.getElementById('editSheetNo').value || '8095', 10),
    daily_hydro_capacity: parseInt(document.getElementById('editDailyCap').value || '24', 10),
    default_tax_rate: parseFloat(document.getElementById('editDefaultTax').value || '6.0'),
    rin_expires_date: document.getElementById('editRinExp').value || '',
    gauge_cert_expires_date: document.getElementById('editGaugeExp').value || '',
    gemini_api_key: document.getElementById('apiKeyInput').value || null,
    sheets_webhook_url: document.getElementById('webhookInput').value,
    feedback_webhook_url: document.getElementById('fbWebhookInput').value,
    shop_pin: document.getElementById('editShopPin').value || null,
    testing_mode: document.getElementById('testingModeChk').checked,
  };
  await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  document.getElementById('apiKeyInput').value = '';
  document.getElementById('editShopPin').value = '';
  alert('Facility settings & compliance dates saved!');
  loadState(true);
}

checkAuthAndBoot();