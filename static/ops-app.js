// Weber's Tank Testing — Operations, Dispatch & Priority Controller (v6.0)
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
  testing_mode: true, shop_phone: '724-266-8008', calibration_targets: [], cga_specs: [],
  records: [], in_shop_post_test: [], active_valves: [], dropoffs: [], schedule_jobs: [],
  day_plans: [], binder_state: {}, huddle: {}, verifications: [], jacket_ceilings: {},
  pricing_rates: [], invoices: [], dates_summary: [], customers_summary: [],
  crew: [], feedback_items: [], default_tax_rate: 0, daily_hydro_capacity: 24, db_version: 0,
};
window.ACTIVE_DROPOFF_ID = null;
window.ACTIVE_SCHEDULE_JOB_ID = null;

let CURRENT_TAB = 'cal';
let CAL_FORCE_EXPANDED = false;
let LADDER = [];
let ACTIVE_STEP = 0;
let BENCH_VIS = {};
let EDIT_VIS = {};
let BENCH_SUBMITTING = false;
let DRAFT_ITEMS = [];
let EDITABLE_RATES = [];
let LAST_SEARCH_ROWS = [];
let LAST_QUOTE = null;
let DEFERRED_PWA = null;

const IS_OPS_HOST = !['weberstanktesting.com', 'www.weberstanktesting.com'].includes(location.hostname.toLowerCase());
if (IS_OPS_HOST && 'serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}
window.addEventListener('beforeinstallprompt', (e) => {
  if (!IS_OPS_HOST) return;
  e.preventDefault();
  DEFERRED_PWA = e;
  document.getElementById('btnInstallPwa').hidden = false;
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
  return `<span class="dwell-badge ${days >= 5 ? 'old' : ''}">${prefix} ${days}d</span>`;
}

function stageBadgeHtml(r) {
  const st = r.shop_stage || 'IN_SHOP';
  if (st === 'READY_BACKROOM') return '<span class="stage-chip ready">📦 Zone E Shelf</span>';
  if (st === 'PICKED_UP') return '<span class="stage-chip">✓ Picked Up</span>';
  if (st === 'STAMPED_STICKERED' || r.rin_marked) {
    const lbl = r.rin_mark_type === 'STICKERED' ? '🏷️ B386 Stickered' : '🔨 Stamped B386';
    return `<span class="stage-chip stamped">${lbl}</span>`;
  }
  if (st === 'DRYING') return '<span class="stage-chip drying">💨 Drying / Needs Mark</span>';
  if (st === 'CONDEMNED_HOLD') return '<span class="stage-chip" style="background:#fee2e2;color:#991b1b;">⛔ Condemned</span>';
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

// --- AUTH, NAVIGATION & MODALS ---
async function checkAuthAndBoot() {
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
  document.getElementById('tab-' + name).hidden = false;
  document.querySelectorAll('.rail-btn, .bnav-btn').forEach(b => {
    b.classList.toggle('active', b.getAttribute('data-tab') === name);
  });
  if (name === 'schedule') fetchLiveTurnaroundQuote();
  if (name === 'logs') runSearch(false);
  if (name === 'billing') loadUnbilledItems();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function changeWorkingDate() {
  CAL_FORCE_EXPANDED = false;
  const d = document.getElementById('globalDate').value;
  document.getElementById('billDate').value = d;
  document.getElementById('sjDate').value = d;
  loadState(true);
}

function selectCalendarDay(isoDate) {
  document.getElementById('globalDate').value = isoDate;
  changeWorkingDate();
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
  if (!confirm('ERASE ALL PRACTICE / TEST DATA?\n\nWipes practice calibrations, bottles, drop-offs, schedule jobs, valve tags, and invoices while keeping API keys, crew, and rates intact.')) return;
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
  const prevVer = STATE.db_version;
  STATE = await res.json();

  if (!document.getElementById('globalDate').value) {
    document.getElementById('globalDate').value = STATE.selected_date;
    document.getElementById('billDate').value = STATE.selected_date;
  }
  if (!document.getElementById('sjDate').value) {
    document.getElementById('sjDate').value = STATE.selected_date;
  }

  document.getElementById('testModeChip').hidden = !STATE.testing_mode;
  document.getElementById('drawerWipeBtn').hidden = !STATE.testing_mode;
  document.getElementById('testingModeChk').checked = STATE.testing_mode;
  if (STATE.testing_mode && !sessionStorage.getItem('webers_disclaimer_ack')) {
    document.getElementById('disclaimerModal').hidden = false;
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

  document.getElementById('pdfDateLabel').textContent = STATE.selected_date;
  document.getElementById('pdfSheetNo').value = STATE.binder_state?.active_sheet_no || STATE.starting_sheet_no;
  document.getElementById('editSheetNo').value = STATE.starting_sheet_no;
  document.getElementById('editDailyCap').value = STATE.daily_hydro_capacity || 24;
  document.getElementById('editDefaultTax').value = STATE.default_tax_rate ?? 0;
  if (prevVer === 0) document.getElementById('billTax').value = STATE.default_tax_rate ?? 0;
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

  // Jacket Pills & Nav Badges
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

  document.getElementById('navBadgeCal').textContent = (sCeil.max_calibrated_psi && tCeil.max_calibrated_psi) ? '✓' : '!';
  document.getElementById('navBadgeCal').className = 'nav-badge ' + ((sCeil.max_calibrated_psi && tCeil.max_calibrated_psi) ? 'green' : 'amber');
  document.getElementById('navBadgeSched').textContent = String(openSched.length);
  document.getElementById('navBadgeIntake').textContent = waitingDrops.length ? `${waitingDrops.length}🚪` : String(queued.length);
  document.getElementById('navBadgeBench').textContent = String(queued.length);
  document.getElementById('navBadgeBench').className = 'nav-badge ' + ((STATE.huddle?.rush_queued_count || 0) > 0 ? 'red' : '');
  document.getElementById('navBadgeValves').textContent = String((STATE.huddle?.needs_stamp_count || 0) + STATE.active_valves.length);
  document.getElementById('navBadgeBill').textContent = String(totalUnbilled);

  const setMobDot = (id, n) => { const el = document.getElementById(id); if (el) { el.hidden = !n; el.textContent = n; } };
  setMobDot('mobBadgeSched', openSched.length);
  setMobDot('mobBadgeIntake', waitingDrops.length);
  setMobDot('mobBadgeBench', queued.length);
  setMobDot('mobBadgeValves', needsFinish);

  document.getElementById('stickerCount').value = Math.max(1, doneToday.filter(r => r.hydro_disposition === 'A').length);

  renderStartOfDayHuddle(queued, openSched, waitingDrops);
  renderScheduleTab(openSched);

  // Calibration Ladder & Smart Recommended Max PSI
  const recCal = STATE.huddle?.recommended_cal_psi || 0;
  const recBadge = document.getElementById('calRecommendedBadge');
  recBadge.textContent = recCal
    ? `Queue Max: ${STATE.huddle.max_queued_test_psi}# → Calibrate to ${recCal} PSI`
    : '49 CFR §180.215 (±1.0%)';
  recBadge.className = recCal ? 'sub-badge ai-verified' : 'sub-badge';

  const maxSel = document.getElementById('calMaxPsi');
  const fallbackMax = calJacketSel.value === 'Short Jacket' ? '3000' : String(recCal || 4000);
  const curMax = rebuildLadder ? fallbackMax : (maxSel.value || fallbackMax);
  maxSel.innerHTML = STATE.calibration_targets.map((t, idx) => {
    const allow = t.pressure_psi >= 3000 ? t.pressure_psi + 500 : Math.round(t.pressure_psi * 1.1);
    return `<option value="${t.pressure_psi}" ${String(t.pressure_psi) === String(curMax) ? 'selected' : ''}>${t.pressure_psi} PSI (${idx + 1} steps → ≤${allow})</option>`;
  }).join('');
  if (rebuildLadder || !LADDER.length) buildCalibrationLadder();

  document.getElementById('calTable').innerHTML = STATE.verifications.length
    ? STATE.verifications.map(v => `
      <tr>
        <td>${escHtml(v.test_time)}</td><td>${escHtml(v.jacket_type)}</td><td><strong>${v.target_pressure_psi}</strong></td>
        <td>${v.expected_cc}</td><td>${v.actual_total_cc}</td><td>${v.permanent_cc}</td>
        <td>${v.percent_deviation}%</td><td>${escHtml(v.logged_by || 'RP')}</td>
        <td class="${v.passed ? 'pass' : 'fail'}">${v.passed ? 'PASS' : 'FAIL'}</td>
      </tr>`).join('')
    : '<tr><td colspan="9">No calibrations logged for this date yet.</td></tr>';

  renderBackroomInbox();

  // Presets & Autocomplete
  document.getElementById('cgaPreset').innerHTML = '<option value="">-- Auto-Detect or Pick Standard / Paintball Size --</option>' +
    STATE.cga_specs.map((s, idx) => `<option value="${idx}">${escHtml(s.category)}: ${escHtml(s.label)} (${s.dot_spec}-${s.service_psi})</option>`).join('');
  document.getElementById('customerSuggest').innerHTML = STATE.customers_summary.map(c => `<option value="${escHtml(c.customer_name)}">`).join('');
  document.getElementById('serialSuggest').innerHTML = STATE.records.map(r =>
    `<option value="${escHtml(r.serial_number)}">${escHtml(r.customer_name)} (${r.dot_spec}-${r.service_psi})</option>`
  ).join('');

  // Priority-Ranked Intake Queue
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
            <button class="secondary" title="Toggle Paid Rush / High Priority" onclick="cycleRecordPriority(${r.id}, '${escHtml(r.priority || 'STANDARD')}', ${Boolean(r.rush_fee)})">🔥</button>
            <input class="inline-vtag" value="${r.valve_tag && !r.valve_reinstalled ? escHtml(r.valve_tag) : ''}" placeholder="Tag #"
              style="width:70px;padding:5px 7px;font-weight:800;font-size:0.81rem;"
              onkeydown="if(event.key==='Enter'){event.preventDefault();this.blur();}"
              onchange="saveInlineValveTag(${r.id}, this)" />
            <button class="secondary" onclick="openEditModal(${r.id})">Edit</button>
            <button class="secondary" style="color:#b91c1c;" onclick="removeRecord(${r.id})">✕</button>
          </div>
        </div>`).join('')
      : '<div style="color:var(--muted);font-size:0.86rem;padding:8px 0;">Priority queue is clear.</div>';
  }

  // Hydro Stand & Completed Section
  const benchSel = document.getElementById('benchSelect');
  const prevBenchId = benchSel.value;
  const activeId = document.activeElement ? document.activeElement.id : '';
  const typingOnBench = BENCH_SUBMITTING || ['bJacket', 'bTotal', 'bPerm', 'bValveTag', 'bCoat', 'bValve', 'bNotes', 'bValveReinst', 'bRinType'].includes(activeId);
  benchSel.innerHTML = queued.length
    ? queued.map((r, i) => `<option value="${r.id}" ${String(r.id) === String(prevBenchId) ? 'selected' : ''}>#${i + 1} ${(r.rush_fee || r.priority === 'RUSH') ? '🔥 RUSH ' : ''}[${escHtml(r.customer_name)}] SN ${escHtml(r.serial_number)} — ${r.test_psi}#${r.valve_tag && !r.valve_reinstalled ? ' • Tag #' + escHtml(r.valve_tag) : ''}</option>`).join('')
    : '<option value="">No bottles waiting in queue</option>';
  if (!typingOnBench && (benchSel.value !== prevBenchId || !document.getElementById('btnCompleteBench') || !queued.length)) {
    renderBenchForm();
  }

  renderCompletedSection(doneToday);
  renderFinishValvesAndCustomers();

  const allDayCustomers = [...new Set(STATE.records.map(r => r.customer_name))];
  document.getElementById('pdfCustomerFilter').innerHTML = '<option value="">-- All Customers on Selected Date --</option>' +
    allDayCustomers.map(c => `<option value="${escHtml(c)}">${escHtml(c)}</option>`).join('');

  document.getElementById('datesListTable').innerHTML = STATE.dates_summary.length
    ? STATE.dates_summary.map(d => `
      <tr>
        <td><strong>${escHtml(d.work_date)}</strong></td><td>${d.customer_count}</td>
        <td>${d.completed_bottles} / ${d.total_bottles}</td>
        <td><button class="secondary" onclick="jumpToDate('${d.work_date}')">Open</button></td>
      </tr>`).join('')
    : '<tr><td colspan="4">No dates recorded yet.</td></tr>';

  const prevBillCust = document.getElementById('billCustomer').value;
  document.getElementById('billCustomer').innerHTML = STATE.customers_summary.map(c =>
    `<option value="${escHtml(c.customer_name)}" ${c.customer_name === prevBillCust ? 'selected' : ''}>${escHtml(c.customer_name)} (${c.unbilled_count} unbilled)</option>`
  ).join('');

  if (prevVer !== STATE.db_version) {
    EDITABLE_RATES = STATE.pricing_rates.map(p => ({ ...p, isNew: false }));
    renderRateScheduleTable();
  }
  renderInvoicesList();

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

// --- START-OF-DAY HUDDLE & BINDER CONTINUITY ---
function renderStartOfDayHuddle(queued, openSched, waitingDrops) {
  const b = STATE.binder_state || {};
  const hud = STATE.huddle || {};
  const plan = hud.selected_day_plan || {};

  document.getElementById('huddleShiftBadge').textContent = plan.shift_label || '🟢 Full Hydro & Prep Window';
  const shNo = b.active_sheet_no || STATE.starting_sheet_no || 8095;
  const used = b.rows_used_on_active || 0;
  const nxt = b.next_row_number || 1;
  document.getElementById('binderSheetTitle').textContent =
    `Active DOT Retest Sheet #${shNo} — Next Hydro Entry: Row ${nxt} of 12 (${12 - used} open rows left)`;

  if (b.last_recorded) {
    const lr = b.last_recorded;
    document.getElementById('binderLastNote').textContent =
      `Last logged: Sheet #${lr.sheet_no} ended at Row ${lr.row_ended_at}/12 on ${lr.work_date} (SN ${lr.serial_number} • ${lr.customer_name}).`;
  } else {
    document.getElementById('binderLastNote').textContent = `No prior hydro rows in binder yet — starting clean on Sheet #${shNo}, Row 1.`;
  }

  let slotsHtml = '';
  for (let i = 1; i <= 12; i++) {
    const cls = i <= used ? 'used' : (i === nxt ? 'next' : '');
    slotsHtml += `<div class="sheet-slot ${cls}">${i}</div>`;
  }
  document.getElementById('binderSlotsGrid').innerHTML = slotsHtml;

  const todayRuns = openSched.filter(j => j.scheduled_date <= STATE.selected_date);
  document.getElementById('huddleKpiRow').innerHTML = `
    <div class="huddle-kpi"><small>Queued Ready</small><strong>${queued.length} Cyl${hud.rush_queued_count ? ` (${hud.rush_queued_count} 🔥)` : ''}</strong></div>
    <div class="huddle-kpi"><small>Backroom Door</small><strong>${waitingDrops.length} Check-In(s)</strong></div>
    <div class="huddle-kpi"><small>Needs Stamp/Lbl</small><strong>${hud.needs_stamp_count || 0} Dried</strong></div>
    <div class="huddle-kpi"><small>Zone E Shelf</small><strong>${hud.ready_shelf_count || 0} Ready</strong></div>`;

  const lineupItems = [];
  todayRuns.slice(0, 4).forEach(j => {
    const ico = j.job_type === 'ROAD_PICKUP' ? '🚚 Pickup Run' : (j.job_type === 'ROAD_DELIVERY' ? '🚚 Delivery Run' : '📌 Scheduled Batch');
    lineupItems.push(`
      <div class="rec-item">
        <div class="rec-main">
          <div class="rec-title"><span>${ico}: ${escHtml(j.customer_name)} (${j.bottle_count})</span>${j.rush_fee ? '<span class="rush-badge">🔥 RUSH</span>' : ''}</div>
          <div class="rec-sub">${escHtml(j.scheduled_date)}${j.location_notes ? ' • ' + escHtml(j.location_notes) : ''}${j.tank_summary ? ' • ' + escHtml(j.tank_summary) : ''}</div>
        </div>
        <div class="rec-actions">
          <button class="secondary" onclick="importScheduleJobToIntake(${j.id})">↳ Prep / Scan</button>
          <button class="secondary" onclick="jumpToTab('schedule')">Calendar</button>
        </div>
      </div>`);
  });

  if (lineupItems.length) {
    document.getElementById('huddleLineupList').innerHTML =
      `<small style="font-weight:800;color:var(--muted);display:block;margin-bottom:4px;">TODAY'S PRIORITY DISPATCH &amp; RUNS:</small>` + lineupItems.join('');
  } else {
    document.getElementById('huddleLineupList').innerHTML = '';
  }
}

async function advanceToFreshSheet() {
  const b = STATE.binder_state || {};
  const nextNo = (b.active_sheet_no || 8095) + ((b.rows_used_on_active || 0) > 0 ? 1 : 0);
  if ((b.rows_used_on_active || 0) > 0 && !confirm(`Close out Sheet #${b.active_sheet_no} at Row ${b.rows_used_on_active}/12 and start fresh on Sheet #${nextNo} (Row 1)?`)) return;
  await fetch('/api/binder', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'fresh_next' }),
  });
  loadState(false);
}

async function setCustomBinderSheet() {
  const val = parseInt(document.getElementById('customBinderInput').value || '0', 10);
  if (!val || val < 1) { alert('Enter a valid Retest Sheet # first.'); return; }
  await fetch('/api/binder', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'set_custom', sheet_no: val }),
  });
  document.getElementById('customBinderInput').value = '';
  loadState(false);
}

// --- TAB 2: 14-DAY WALL CALENDAR, SHIFT WINDOWS & PHONE QUOTE ENGINE ---
function renderScheduleTab(openSched) {
  const plans = STATE.day_plans || [];
  const selDate = STATE.selected_date;
  const selPlan = plans.find(p => p.work_date === selDate) || STATE.huddle?.selected_day_plan || {};

  document.getElementById('planDateLabel').textContent = selDate;
  if (document.activeElement !== document.getElementById('planShiftMode')) {
    document.getElementById('planShiftMode').value = selPlan.shift_mode || 'FULL_HYDRO';
  }
  if (document.activeElement !== document.getElementById('planCapacity')) {
    document.getElementById('planCapacity').value = selPlan.bottle_capacity ?? 24;
  }
  if (document.activeElement !== document.getElementById('planNotes')) {
    document.getElementById('planNotes').value = selPlan.day_notes || '';
  }

  document.getElementById('calStripGrid').innerHTML = plans.map(p => {
    const dayJobs = openSched.filter(j => j.scheduled_date === p.work_date);
    const modeCls = p.shift_mode === 'PREP_ONLY' ? 'prep-mode'
      : (p.shift_mode === 'ROAD_RUN' ? 'road-mode' : (p.shift_mode === 'FULL_HYDRO' ? 'hydro-mode' : 'closed-mode'));
    const shortMode = p.shift_mode === 'PREP_ONLY' ? '🔧 Prep Only'
      : (p.shift_mode === 'ROAD_RUN' ? `🚚 Road (${p.bottle_capacity})`
      : (p.shift_mode === 'FULL_HYDRO' ? `🟢 Hydro (${p.bottle_capacity})` : '🚪 Backroom'));
    const rushCnt = dayJobs.filter(j => j.rush_fee || j.priority === 'RUSH').length;
    return `
      <div class="day-tile ${modeCls} ${p.work_date === selDate ? 'selected' : ''}" onclick="selectCalendarDay('${p.work_date}')">
        <div style="display:flex;justify-content:space-between;font-weight:900;font-size:0.8rem;">
          <span>${escHtml(p.weekday)} ${escHtml(p.work_date.slice(5))}</span>
          ${rushCnt ? '<span class="rush-badge">🔥</span>' : ''}
        </div>
        <div style="font-size:0.73rem;font-weight:800;color:var(--green);">${shortMode}</div>
        <div style="font-size:0.72rem;color:var(--muted);">
          ${dayJobs.length ? `<strong>${dayJobs.length} run(s)</strong> (${dayJobs.reduce((s, x) => s + x.bottle_count, 0)} cyl)` : (p.day_notes ? escHtml(p.day_notes.slice(0, 22)) : 'Open')}
        </div>
      </div>`;
  }).join('');

  if (!LAST_QUOTE && STATE.huddle?.quote_preview) {
    LAST_QUOTE = STATE.huddle.quote_preview;
    renderQuoteCards(LAST_QUOTE);
  }

  document.getElementById('schedActiveBadge').textContent = `${openSched.length} Active`;
  document.getElementById('scheduleJobsList').innerHTML = openSched.length
    ? openSched.map(j => {
        const ico = j.job_type === 'ROAD_PICKUP' ? '🚚 Pickup' : (j.job_type === 'ROAD_DELIVERY' ? '🚚 Delivery' : (j.job_type === 'ONSITE_VISUAL' ? '🔍 On-Site' : '📌 Batch'));
        const isPrepped = j.prep_status === 'DEVALVED_TAGGED';
        return `
          <div class="rec-item">
            <div class="rec-main">
              <div class="rec-title">
                <span>${ico}: ${escHtml(j.customer_name)} (${j.bottle_count})</span>
                ${j.rush_fee || j.priority === 'RUSH' ? '<span class="rush-badge">🔥 RUSH</span>' : (j.priority === 'HIGH' ? '<span class="high-badge">⚡ HIGH</span>' : '')}
                ${isPrepped ? '<span class="stage-chip ready">✓ Prepped Zone C</span>' : '<span class="stage-chip">Needs Prep</span>'}
                ${dwellBadgeHtml(j.dwell_days)}
              </div>
              <div class="rec-sub">
                Sched: <strong>${escHtml(j.scheduled_date)}</strong>${j.due_date ? ` • Due: <strong>${escHtml(j.due_date)}</strong>` : ''} • <span class="zone-badge">${escHtml((j.holding_zone_label || '').split('•')[0])}</span>
                ${j.location_notes ? ` • 📍 ${escHtml(j.location_notes)}` : ''}${j.tank_summary ? ` • ${escHtml(j.tank_summary)}` : ''}
              </div>
              ${j.notes ? `<div style="font-size:0.75rem;color:#92400e;margin-top:2px;">Note: ${escHtml(j.notes)}</div>` : ''}
            </div>
            <div class="rec-actions">
              <button class="secondary" style="background:#ecfdf5;color:#065f46;border-color:#6ee7b7;" onclick="importScheduleJobToIntake(${j.id})">↳ Scan / Intake</button>
              ${!isPrepped ? `<button class="secondary" onclick="markScheduleJobPrepped(${j.id})">🔧 Prepped</button>` : ''}
              <button class="secondary" title="Toggle Paid Rush" onclick="toggleJobRush(${j.id}, ${Boolean(j.rush_fee)})">🔥</button>
              <button class="secondary" onclick="completeScheduleJob(${j.id})">✓ Done</button>
              <button class="secondary" style="color:#b91c1c;" onclick="deleteScheduleJob(${j.id})">✕</button>
            </div>
          </div>`;
      }).join('')
    : '<div style="color:var(--muted);font-size:0.86rem;padding:8px 0;">No open pickups, deliveries, or scheduled batches yet.</div>';
}

function autoFillPlanCap() {
  const m = document.getElementById('planShiftMode').value;
  document.getElementById('planCapacity').value = m === 'FULL_HYDRO' ? (STATE.daily_hydro_capacity || 24) : (m === 'ROAD_RUN' ? 10 : 0);
}

async function saveDayPlan() {
  await fetch('/api/schedule/day-plan', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      work_date: STATE.selected_date,
      shift_mode: document.getElementById('planShiftMode').value,
      bottle_capacity: parseInt(document.getElementById('planCapacity').value || '0', 10),
      day_notes: document.getElementById('planNotes').value,
    }),
  });
  await loadState(false);
  fetchLiveTurnaroundQuote();
}

async function fetchLiveTurnaroundQuote() {
  const cnt = Math.max(1, parseInt(document.getElementById('quoteCountInput').value || '1', 10));
  const res = await fetch(`/api/schedule/quote?bottles=${cnt}`);
  if (!res.ok) return;
  LAST_QUOTE = await res.json();
  renderQuoteCards(LAST_QUOTE);
}

function renderQuoteCards(q) {
  if (!q) return;
  document.getElementById('quoteResultArea').innerHTML = `
    <div class="quote-card std">
      <div style="font-size:0.74rem;font-weight:900;color:#166534;">🟢 STANDARD TURNAROUND</div>
      <div style="font-size:1.18rem;font-weight:900;margin:3px 0;">Ready: ${escHtml(q.standard_promise_label)}</div>
      <div style="font-size:0.76rem;color:var(--muted);">Clears ${q.total_backlog_bottles} cyl backlog + dry/stamp</div>
      <button class="secondary" style="width:100%;margin-top:7px;" onclick="prefillScheduleFromQuote(false, '${q.standard_promise_date}', ${q.requested_bottles})">➕ Book Standard</button>
    </div>
    <div class="quote-card rush">
      <div style="font-size:0.74rem;font-weight:900;color:#991b1b;">🔥 PAID RUSH SERVICE</div>
      <div style="font-size:1.18rem;font-weight:900;margin:3px 0;">Ready: ${escHtml(q.rush_promise_label)}</div>
      <div style="font-size:0.76rem;color:var(--muted);">Ahead of standard (${q.rush_backlog_bottles} rush ahead)</div>
      <button class="secondary" style="width:100%;margin-top:7px;background:#fee2e2;color:#991b1b;border-color:#fca5a5;" onclick="prefillScheduleFromQuote(true, '${q.rush_promise_date}', ${q.requested_bottles})">➕ Book Paid Rush</button>
    </div>`;
}

function prefillScheduleFromQuote(isRush, promiseDate, count) {
  document.getElementById('sjDate').value = STATE.today || STATE.selected_date;
  document.getElementById('sjDueDate').value = promiseDate;
  document.getElementById('sjCount').value = count;
  document.getElementById('sjPriority').value = isRush ? 'RUSH' : 'STANDARD';
  document.getElementById('sjCustomer').focus();
}

async function submitScheduleJob() {
  const cust = document.getElementById('sjCustomer').value.trim();
  if (!cust) { alert('Enter Customer / Fire Dept name first.'); return; }
  const pri = document.getElementById('sjPriority').value;
  const res = await fetch('/api/schedule/jobs', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      job_type: document.getElementById('sjType').value,
      scheduled_date: document.getElementById('sjDate').value || STATE.selected_date,
      due_date: document.getElementById('sjDueDate').value || '',
      customer_name: cust,
      customer_phone: document.getElementById('sjPhone').value,
      bottle_count: parseInt(document.getElementById('sjCount').value || '1', 10),
      priority: pri,
      rush_fee: pri === 'RUSH',
      holding_zone: document.getElementById('sjZone').value,
      tank_summary: document.getElementById('sjSummary').value,
      location_notes: document.getElementById('sjLocation').value,
      notes: document.getElementById('sjNotes').value,
    }),
  });
  if (!res.ok) { alert((await res.json()).detail || 'Could not save schedule entry.'); return; }
  ['sjCustomer', 'sjPhone', 'sjSummary', 'sjLocation', 'sjNotes'].forEach(id => { document.getElementById(id).value = ''; });
  await loadState(false);
  fetchLiveTurnaroundQuote();
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
  document.getElementById('activeBatchBanner').hidden = false;
  document.getElementById('activeBatchText').textContent =
    `📌 Scheduled Batch: ${j.customer_name} (${j.bottle_count} cyl${j.rush_fee ? ' • 🔥 RUSH' : ''})`;
  jumpToTab('intake');
}

async function markScheduleJobPrepped(jobId) {
  await fetch(`/api/schedule/jobs/${jobId}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prep_status: 'DEVALVED_TAGGED', holding_zone: 'ZONE_C_READY_RACK', status: 'PREPPED' }),
  });
  loadState(false);
}

async function toggleJobRush(jobId, curRush) {
  const nextRush = !curRush;
  await fetch(`/api/schedule/jobs/${jobId}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rush_fee: nextRush, priority: nextRush ? 'RUSH' : 'STANDARD' }),
  });
  loadState(false);
}

async function completeScheduleJob(jobId) {
  await fetch(`/api/schedule/jobs/${jobId}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: 'COMPLETED' }),
  });
  loadState(false);
}

async function deleteScheduleJob(jobId) {
  if (!confirm('Remove this entry from the shop schedule?')) return;
  await fetch(`/api/schedule/jobs/${jobId}`, { method: 'DELETE' });
  loadState(false);
}

// --- BACKROOM 24/7 DROP-OFF INBOX ---
function renderBackroomInbox() {
  const active = (STATE.dropoffs || []).filter(d => d.status !== 'ARCHIVED');
  const card = document.getElementById('backroomInboxCard');
  card.hidden = !active.length;
  document.getElementById('backroomCountBadge').textContent = `${active.length} Active`;
  document.getElementById('backroomInboxList').innerHTML = active.map(d => {
    const isPickup = d.entry_type === 'PICKUP_NOTICE' || d.status === 'PICKUP_LOGGED';
    return `
      <div class="dropoff-card ${isPickup ? 'pickup' : ''}">
        <div>
          <div style="font-weight:800;font-size:0.91rem;display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
            <span>${isPickup ? '🚚 PICKUP LOGGED:' : '📥 DROP-OFF:'} ${escHtml(d.customer_name)}</span>
            <span class="tag-chip">${d.bottle_count} Cyl</span>
            ${d.rush_requested ? '<span class="rush-badge">🔥 RUSH</span>' : ''}
            ${dwellBadgeHtml(d.dwell_days)}
            ${d.imported_count ? `<span class="stage-chip ready">${d.imported_count}/${d.bottle_count} Scanned</span>` : ''}
          </div>
          <div style="font-size:0.79rem;color:var(--muted);margin-top:2px;">
            ${escHtml(d.service_requested)}${d.promised_date ? ` • Target: <strong>${escHtml(d.promised_date)}</strong>` : ''}${d.tank_summary ? ' • ' + escHtml(d.tank_summary) : ''}${d.customer_phone ? ' • 📞 ' + escHtml(d.customer_phone) : ''}
          </div>
          ${d.notes ? `<div style="font-size:0.77rem;color:#92400e;margin-top:2px;">Note: ${escHtml(d.notes)}</div>` : ''}
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;">
          ${!isPickup ? `<button class="primary" style="width:auto;margin:0;padding:6px 11px;font-size:0.79rem;" onclick="importDropoffToIntake(${d.id})">↳ Import Batch</button>` : ''}
          ${!isPickup ? `<button class="secondary" title="Toggle Paid Rush" onclick="toggleDropoffRush(${d.id}, ${Boolean(d.rush_requested)})">🔥</button>` : ''}
          <button class="secondary" onclick="archiveDropoff(${d.id})">✓ Done</button>
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
  renderBackroomInbox();
}

function clearActiveDropoffBatch() {
  window.ACTIVE_DROPOFF_ID = null;
  window.ACTIVE_SCHEDULE_JOB_ID = null;
  document.getElementById('activeBatchBanner').hidden = true;
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

// --- CALIBRATION LADDER ---
function buildCalibrationLadder() {
  const jacket = document.getElementById('calJacket').value;
  const maxSel = document.getElementById('calMaxPsi');
  if (jacket === 'Short Jacket' && !LADDER.length && maxSel.querySelector('option[value="3000"]')) {
    maxSel.value = '3000';
  }
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

// --- INTAKE, PRIORITY BUMP & 15-YEAR COMPOSITE GUARD ---
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

function clearIntakeForm() {
  ['fSerial', 'fValveTag', 'fMfg', 'fMfgDate', 'fSpec', 'fSvcPsi', 'fTestPsi', 'fDia', 'fLen', 'fRee'].forEach(id => {
    document.getElementById(id).value = '';
  });
  document.getElementById('fGas').value = 'AIR';
  document.getElementById('cgaPreset').value = '';
  document.getElementById('compositeAlertBox').hidden = true;
  document.getElementById('intakeFieldsGrid').classList.remove('ai-filled');
  document.getElementById('sizeSourceBadge').className = 'sub-badge';
  document.getElementById('sizeSourceBadge').textContent = 'Ready';
}

async function submitIntake(allowDup = false) {
  const cust = document.getElementById('fCustomer').value.trim() || document.getElementById('inCustomer').value.trim();
  const ph = document.getElementById('fPhone').value.trim() || document.getElementById('inPhone').value.trim();
  const pri = document.getElementById('fPriority').value || 'STANDARD';
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
      valve_tag: document.getElementById('fValveTag').value,
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
  clearIntakeForm();
  if (typeof clearReferenceGallery === 'function') clearReferenceGallery();
  document.getElementById('fCustomer').value = cust;
  document.getElementById('fPhone').value = ph;
  document.getElementById('scanFeedback').hidden = true;
  await loadState(false);
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
  document.getElementById(tagInputId).value = '';
  loadState(false);
}

async function saveInlineValveTag(recordId, inputEl) {
  const res = await fetch('/api/valve-tag', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ record_id: recordId, valve_tag: inputEl.value, valve_reinstalled: false }),
  });
  if (!res.ok) { alert((await res.json()).detail); loadState(false); return; }
  inputEl.classList.add('saved-flash');
  setTimeout(() => { inputEl.classList.remove('saved-flash'); loadState(false); }, 400);
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

// --- TAB 4: HYDRO STAND & COMPLETED STAGING ---
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
  if (!rec) { area.innerHTML = '<p style="color:var(--muted);margin:0;">No queued bottles waiting—scan or prep a batch on Tab 3.</p>'; return; }

  BENCH_VIS = {};
  VIS_FIELDS.forEach(f => { BENCH_VIS[f.key] = rec[f.key] || 'OK'; });
  const isHydro = rec.service_mode === 'HYDRO_AND_VISUAL';
  const tallOk = (STATE.jacket_ceilings['Tall Jacket']?.max_calibrated_psi || 0) > 0;
  const shortOk = (STATE.jacket_ceilings['Short Jacket']?.max_calibrated_psi || 0) > 0;
  const defJacket = tallOk ? 'Tall Jacket' : (shortOk ? 'Short Jacket' : 'Tall Jacket');
  const effRee = (['3A', '3AA', '3AL'].includes((rec.dot_spec || '').toUpperCase()) && rec.ree_limit === 7.2) ? null : rec.ree_limit;

  area.innerHTML = `
    <div class="notice" id="benchPreview">
      ${priorityBadgeHtml(rec)} <strong>[${escHtml(rec.customer_name)}]</strong> • SN <strong>${escHtml(rec.serial_number)}</strong> • Target: <strong>${rec.test_psi} PSI</strong>${rec.valve_tag && !rec.valve_reinstalled ? ` • 🏷️ #<strong>${escHtml(rec.valve_tag)}</strong>` : ''}${effRee ? ` • REE ≤ <strong>${effRee} cc</strong>` : ''} • Sheet #<strong>${rec.sheet_no}</strong>
    </div>
    ${rec.composite_warning ? `<div class="notice bad">⛔ ${escHtml(rec.composite_warning)}</div>` : ''}
    <div class="grid">
      ${isHydro ? `
        <div><label>Water Jacket</label>
          <select id="bJacket">
            <option value="Short Jacket" ${defJacket === 'Short Jacket' ? 'selected' : ''}>Short Jacket</option>
            <option value="Tall Jacket" ${defJacket === 'Tall Jacket' ? 'selected' : ''}>Tall Jacket</option>
          </select>
        </div>
        <div><label>Total Exp (cc) *</label><input type="number" step="0.1" id="bTotal" oninput="previewBenchMath(${effRee ?? 'null'})" onkeydown="if(event.key==='Enter')document.getElementById('bPerm').focus()" /></div>
        <div><label>Perm Exp (cc) *</label><input type="number" step="0.1" id="bPerm" value="0" oninput="previewBenchMath(${effRee ?? 'null'})" onkeydown="if(event.key==='Enter')completeBottle(${rec.id}, ${isHydro})" /></div>
      ` : ''}
      <div><label>🏷️ Valve / Reg Tag #</label><input id="bValveTag" value="${rec.valve_tag && !rec.valve_reinstalled ? escHtml(rec.valve_tag) : ''}" placeholder="Tag #" /></div>
      <div><label>Coating</label><select id="bCoat"><option>Excellent</option><option selected>Good</option><option>Fair</option><option>Poor</option></select></div>
      <div><label>Relief Changed?</label><select id="bValve"><option>No</option><option>Yes</option></select></div>
      <div>
        <label>RIN Mark (If Done Now)</label>
        <select id="bRinType">
          <option value="">Mark After Hot-Air Dry</option>
          <option value="STAMPED">🔨 Neck Stamped B386</option>
          <option value="STICKERED">🏷️ B386 Sticker Applied</option>
        </select>
      </div>
      <div><label>Row Notes</label><input id="bNotes" value="${escHtml(rec.notes || '')}" placeholder="Optional DOT note..." /></div>
    </div>
    <label style="margin-top:11px;">7-Point Visual Inspection (Tap Any Pill to Toggle)</label>
    <div id="benchVisGrid" class="vis-pills-grid">${renderVisPillsHtml(BENCH_VIS, 'toggleBenchVis')}</div>
    <label class="inline-check" style="margin-top:11px;">
      <input type="checkbox" id="bValveReinst" />
      <span>Valve / HPA regulator already reinstalled (frees reusable Tag #)</span>
    </label>
    <button class="primary" id="btnCompleteBench" onclick="completeBottle(${rec.id}, ${isHydro})">Certify &amp; Send to Zone D Dryer / Finish (RP)</button>`;
}

function previewBenchMath(reeLimit) {
  const box = document.getElementById('benchPreview');
  const totEl = document.getElementById('bTotal');
  const visFailed = Object.values(BENCH_VIS).some(v => v !== 'OK');
  if (!totEl) return;
  const tot = parseFloat(totEl.value || '0'), perm = parseFloat(document.getElementById('bPerm').value || '0');
  if (tot <= 0) return;
  const elastic = (tot - perm).toFixed(2), pct = ((perm / tot) * 100).toFixed(1);
  const bad = visFailed || (reeLimit !== null && parseFloat(elastic) > reeLimit) || parseFloat(pct) > 10.0;
  box.className = bad ? 'notice bad' : 'notice';
  box.textContent = `Elastic: ${elastic} cc | % Perm: ${pct}%${visFailed ? ' | VISUAL FAIL' : ''} — ${bad ? 'CONDEMN (CODE D)' : 'PASS (Code A)'}`;
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
  BENCH_SUBMITTING = true;
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Saving & Syncing...'; }
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
        relief_valve_changed: document.getElementById('bValve').value,
        tested_by: getActiveCrew(),
        notes: document.getElementById('bNotes').value,
        ...BENCH_VIS,
      }),
    });
    if (!res.ok) { alert((await res.json()).detail); return; }
  } finally {
    BENCH_SUBMITTING = false;
    if (btn) { btn.disabled = false; btn.textContent = 'Certify & Send to Zone D Dryer / Finish (RP)'; }
  }
  await loadState(false);
}

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
          <button class="secondary" onclick="stageCustomerBatch('${safeC}', 'STAMPED_STICKERED', 'MARKED')">🔨 Mark Batch Stamped/Lbl</button>
          <button class="secondary" onclick="stageCustomerBatch('${safeC}', 'READY_BACKROOM', '')">📦 Revalve &amp; Shelf</button>
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
      </div>
      <div class="rec-actions">
        ${isPass && !r.rin_marked && r.shop_stage !== 'PICKED_UP'
          ? `<button class="secondary" style="background:#ede9fe;color:#5b21b6;border-color:#c4b5fd;" onclick="setRecordStage(${r.id}, 'STAMPED_STICKERED', '${defMark}')">${isComp ? '🏷️ Stickered' : '🔨 Stamped'}</button>`
          : ''}
        ${r.shop_stage !== 'READY_BACKROOM' && r.shop_stage !== 'PICKED_UP'
          ? `<button class="secondary" style="background:#dcfce7;color:#166534;border-color:#86efac;" onclick="setRecordStage(${r.id}, 'READY_BACKROOM', '${defMark}')">📦 Shelf</button>`
          : ''}
        ${r.shop_stage === 'READY_BACKROOM'
          ? `<button class="secondary" onclick="setRecordStage(${r.id}, 'PICKED_UP', '')">✓ Picked Up</button>`
          : ''}
        <button class="secondary" onclick="openEditModal(${r.id})">Edit</button>
      </div>
    </div>`;
}

async function setRecordStage(id, stage, markType = '', force = false) {
  const body = { shop_stage: stage, force };
  if (stage === 'STAMPED_STICKERED') {
    body.rin_marked = true;
    body.rin_mark_type = markType || 'STAMPED';
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
  const payload = {
    work_date: allDates ? null : STATE.selected_date,
    customer_name: cust,
    shop_stage: stage,
    rin_mark_type: markType || undefined,
    release_valves: stage === 'READY_BACKROOM' || stage === 'PICKED_UP',
  };
  const res = await fetch('/api/batch/stage', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  });
  if (res.status === 400 && stage === 'READY_BACKROOM') {
    const err = await res.json();
    if (confirm(err.detail + '\n\nMark entire batch Stamped/Stickered AND stage to Zone E Pickup Shelf now?')) {
      payload.rin_marked = true;
      payload.rin_mark_type = 'MARKED';
      await fetch('/api/batch/stage', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
    } else return;
  }
  loadState(false);
}

// --- TAB 5: FINISH BENCH, ACTIVE TAGS, PICKUP SHELF & LOOKUP ---
function sendPickupSms(custName, phone, count, invExtra) {
  let ph = (phone || '').trim();
  if (!ph) {
    ph = prompt(`Enter cell phone number for ${custName}:`, '') || '';
    if (!ph.trim()) return;
  }
  const cleanDigits = ph.replace(/\D/g, '');
  const msg = `Weber's Tank Testing (RIN B386): Hi ${custName}, your ${count} cylinder(s) are tested, stamped/labeled, and ready on our backroom pickup shelf at 3086 Legionville Rd${invExtra ? ' (' + invExtra + ')' : ''}. Questions? Call/text ${STATE.shop_phone || '724-266-8008'}.`;
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
          <div class="rec-sub">${escHtml(r.customer_name)} • ${escHtml(r.dot_spec)}-${r.service_psi} (${r.status})</div>
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
                ${c.needs_stamp_count ? `<span class="stage-chip drying">🔨 ${c.needs_stamp_count} Need Mark</span>` : ''}
                ${c.ready_backroom_count ? `<span class="stage-chip ready">📦 ${c.ready_backroom_count} Zone E</span>` : ''}
                ${c.shelf_dwell_days >= 2 ? dwellBadgeHtml(c.shelf_dwell_days, '📦') : ''}
              </div>
              <div class="rec-sub">${c.total_bottles} total • ${c.unbilled_count} unbilled • Last: ${escHtml(c.last_date)}</div>
            </div>
            <div class="rec-actions">
              ${c.ready_backroom_count ? `<button class="secondary" onclick="stageCustomerBatch('${safeC}', 'PICKED_UP', '', true)">✓ Picked Up</button>` : ''}
              <button class="secondary" onclick="sendPickupSms('${safeC}', '${escHtml(c.customer_phone || '')}', ${c.ready_backroom_count || c.total_bottles}, '')">📲 Text</button>
              <button class="secondary" onclick="filterCustomerHistory('${safeC}')">History</button>
            </div>
          </div>`;
      }).join('')
    : '<div style="color:var(--muted);font-size:0.86rem;">No customer records yet.</div>';
}

function jumpToDate(d) {
  document.getElementById('globalDate').value = d;
  document.getElementById('billDate').value = d;
  loadState(true);
  document.getElementById('searchBox').value = d;
  runSearch(false);
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
    ? rows.map(r => `
      <div class="rec-item">
        <div class="rec-main">
          <div class="rec-title">
            <span>${escHtml(r.serial_number)}</span>
            ${priorityBadgeHtml(r)}
            <span class="${r.hydro_disposition === 'A' ? 'pass' : 'fail'}">${escHtml(r.hydro_disposition)}</span>
            ${stageBadgeHtml(r)}
            ${r.valve_tag && !r.valve_reinstalled ? `<span class="tag-chip">#${escHtml(r.valve_tag)}</span>` : ''}
          </div>
          <div class="rec-sub">${escHtml(r.work_date)} • ${escHtml(r.customer_name)} • ${escHtml(r.dot_spec)}-${r.service_psi} • Sheet #${r.sheet_no} • Perm: ${fmtPctTenth(r.percent_permanent)}</div>
        </div>
        <button class="secondary" onclick="openEditModal(${r.id})">Edit</button>
      </div>`).join('')
    : '<div style="color:var(--muted);font-size:0.86rem;padding:8px 0;">No matching cylinder records found.</div>';
}

// --- EDIT RECORD MODAL ---
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
  document.getElementById('erValveChg').value = rec.relief_valve_changed || 'No';
  document.getElementById('erDispOvr').value = '';
  document.getElementById('erNotes').value = rec.notes || '';
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
      relief_valve_changed: document.getElementById('erValveChg').value,
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

// --- TAB 6: PRINT, MONTHLY STICKER PRE-PRINT & QR POSTER ---
function preprintFullSheet(compact) {
  const d = STATE.today || STATE.selected_date;
  const cnt = compact ? 60 : 30;
  window.open(`/api/pdf/stickers?work_date=${encodeURIComponent(d)}&count=${cnt}&skip=0&compact=${compact}`, '_blank');
}

function openPdf(kind) {
  if (kind === 'poster') {
    window.open('/api/pdf/poster?checkin_url=' + encodeURIComponent('https://weberstanktesting.com/#dropoff'), '_blank');
    return;
  }
  const d = STATE.selected_date;
  const cust = document.getElementById('pdfCustomerFilter').value;
  const sheetNo = document.getElementById('pdfSheetNo').value || '8095';
  const cnt = document.getElementById('stickerCount').value || '12';
  const skip = document.getElementById('stickerSkip').value || '0';
  const compact = document.getElementById('stickerCompact').checked;
  let url = `/api/pdf/${kind}?work_date=${encodeURIComponent(d)}`;
  if (cust) url += `&customer=${encodeURIComponent(cust)}`;
  if (kind === 'retest') url += `&sheet_no=${encodeURIComponent(sheetNo)}`;
  if (kind === 'stickers') url += `&count=${encodeURIComponent(cnt)}&skip=${encodeURIComponent(skip)}&compact=${compact}`;
  window.open(url, '_blank');
}

// --- TAB 7: BILLING, AUTO-RUSH FEES, PAYMENTS & RATES ---
function getRate(code) {
  const found = STATE.pricing_rates.find(r => r.code === code);
  return found ? Number(found.unit_price) : 0;
}

function isPaintballCylinder(r) {
  const dia = parseFloat(r.outside_dia || '0'), len = parseFloat(r.length_in || '0');
  const mfg = (r.mfg_id || '').toUpperCase();
  if (['NINJA', 'HK ARMY', 'EMPIRE', 'FIRST STRIKE', 'JT', 'VALKEN', 'IMMORTAL'].some(b => mfg.includes(b))) return true;
  return dia > 0 && dia <= 4.8 && len > 0 && len <= 12.0;
}

async function loadUnbilledItems() {
  const d = document.getElementById('billDate').value || STATE.selected_date;
  const allDates = document.getElementById('billAllDatesChk').checked;
  const cust = document.getElementById('billCustomer').value;
  const custMeta = STATE.customers_summary.find(c => c.customer_name === cust);
  document.getElementById('billPhone').value = custMeta?.customer_phone || '';
  if (!cust) { DRAFT_ITEMS = []; renderDraftInvoice(); return; }

  const res = await fetch(`/api/unbilled?customer_name=${encodeURIComponent(cust)}&work_date=${encodeURIComponent(d)}&all_dates=${allDates}`);
  const recs = await res.json();
  DRAFT_ITEMS = [];
  for (const r of recs) {
    const isPb = isPaintballCylinder(r);
    const isSp = (r.dot_spec || '').startsWith('SP') || (r.dot_spec || '').startsWith('E-');
    const pbRate = getRate('HYDRO_PAINTBALL');
    const code = r.service_mode === 'VISUAL_ONLY'
      ? 'VISUAL_ONLY'
      : (isPb && pbRate > 0 ? 'HYDRO_PAINTBALL' : (isSp ? 'HYDRO_SCBA' : 'HYDRO_STD'));
    DRAFT_ITEMS.push({
      record_id: r.id,
      description: `SN ${r.serial_number} (${r.dot_spec}-${r.service_psi} ${r.gas_service}) - ${r.service_mode === 'VISUAL_ONLY' ? 'Visual Insp' : (isPb ? 'HPA Hydro' : 'Hydro + Visual')} [${r.hydro_disposition}]`,
      qty: 1,
      unit_price: getRate(code),
    });
    if (r.rush_fee || r.priority === 'RUSH') {
      DRAFT_ITEMS.push({ record_id: null, description: `SN ${r.serial_number} - Priority / Rush Turnaround Service`, qty: 1, unit_price: getRate('RUSH_FEE') });
    }
    if (r.relief_valve_changed === 'Yes') {
      DRAFT_ITEMS.push({ record_id: null, description: `SN ${r.serial_number} - Relief / Burst Disc Replacement`, qty: 1, unit_price: getRate('VALVE_RELIEF') });
    }
  }
  renderDraftInvoice();
}

function addCustomInvoiceLine() {
  DRAFT_ITEMS.push({ record_id: null, description: 'Breathing Air / HPA Fill', qty: 1, unit_price: getRate('AIR_FILL') });
  renderDraftInvoice();
}

function removeDraftInvoiceLine(idx) {
  DRAFT_ITEMS.splice(idx, 1);
  renderDraftInvoice();
}

function renderDraftInvoice() {
  document.getElementById('draftInvoiceList').innerHTML = DRAFT_ITEMS.length
    ? DRAFT_ITEMS.map((it, i) => `
      <div class="inv-line-row">
        <div class="desc-col"><input value="${escHtml(it.description)}" oninput="DRAFT_ITEMS[${i}].description=this.value" placeholder="Line item description" /></div>
        <div><input type="number" step="1" value="${it.qty}" oninput="DRAFT_ITEMS[${i}].qty=parseFloat(this.value||0);recalcInvoiceDraft()" title="Qty" /></div>
        <div><input type="number" step="0.01" value="${it.unit_price}" oninput="DRAFT_ITEMS[${i}].unit_price=parseFloat(this.value||0);recalcInvoiceDraft()" title="Unit Price" /></div>
        <div id="lineTot_${i}"><strong>$${(it.qty * it.unit_price).toFixed(2)}</strong></div>
        <div><button class="secondary" onclick="removeDraftInvoiceLine(${i})">✕</button></div>
      </div>`).join('')
    : '<div style="color:var(--muted);font-size:0.86rem;padding:8px 0;">No unbilled completed bottles for this customer yet.</div>';
  recalcInvoiceDraft();
}

function recalcInvoiceDraft() {
  let sub = 0;
  DRAFT_ITEMS.forEach((it, i) => {
    const lt = (it.qty || 0) * (it.unit_price || 0);
    sub += lt;
    const cell = document.getElementById('lineTot_' + i);
    if (cell) cell.innerHTML = `<strong>$${lt.toFixed(2)}</strong>`;
  });
  const taxPct = parseFloat(document.getElementById('billTax').value || '0');
  const tot = sub + sub * (taxPct / 100);
  document.getElementById('draftTotalLabel').textContent = `Draft Total: $${tot.toFixed(2)}`;
  return tot;
}

async function submitInvoice() {
  const cust = document.getElementById('billCustomer').value;
  if (!cust || !DRAFT_ITEMS.length) { alert('Select a customer with completed bottles or add a custom line.'); return; }
  const res = await fetch('/api/invoices', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      work_date: document.getElementById('billDate').value || STATE.selected_date,
      customer_name: cust,
      customer_phone: document.getElementById('billPhone').value,
      tax_rate: parseFloat(document.getElementById('billTax').value || '0'),
      status: document.getElementById('billStatus').value,
      payment_method: document.getElementById('billPayMethod').value,
      created_by: getActiveCrew(),
      notes: document.getElementById('billNotes').value,
      items: DRAFT_ITEMS,
    }),
  });
  const data = await res.json();
  if (!res.ok) { alert(data.detail); return; }
  window.open(`/api/pdf/invoice/${data.id}`, '_blank');
  await loadState(false);
  loadUnbilledItems();
}

function sendCustomerPickupSmsFromBilling() {
  const cust = document.getElementById('billCustomer').value;
  if (!cust) { alert('Select a customer first.'); return; }
  const ph = document.getElementById('billPhone').value;
  const tot = recalcInvoiceDraft().toFixed(2);
  sendPickupSms(cust, ph, Math.max(1, DRAFT_ITEMS.filter(x => x.record_id).length || DRAFT_ITEMS.length), `Total: $${tot}`);
}

function renderInvoicesList() {
  document.getElementById('invoicesList').innerHTML = STATE.invoices.length
    ? STATE.invoices.map(inv => `
      <div class="rec-item">
        <div class="rec-main">
          <div class="rec-title">
            <span>${escHtml(inv.invoice_no)} — ${escHtml(inv.customer_name)}</span>
            <span class="${inv.status === 'PAID' ? 'pass' : ''}">$${Number(inv.total_amount).toFixed(2)} (${escHtml(inv.status)}${inv.payment_method ? ' • ' + escHtml(inv.payment_method) : ''})</span>
          </div>
          <div class="rec-sub">Service Date: ${escHtml(inv.work_date)}${inv.customer_phone ? ' • 📞 ' + escHtml(inv.customer_phone) : ''}</div>
        </div>
        <div class="rec-actions">
          <a class="secondary" href="/api/pdf/invoice/${inv.id}" target="_blank">📄 PDF</a>
          ${inv.customer_phone ? `<button class="secondary" onclick="sendPickupSms('${inv.customer_name.replace(/'/g, "\\'")}', '${escHtml(inv.customer_phone)}', ${inv.bottle_count || 1}, '${inv.invoice_no}: $${Number(inv.total_amount).toFixed(2)}')">📲 Text</button>` : ''}
          ${inv.status !== 'PAID' ? `<button class="secondary" style="background:#dcfce7;color:#166534;" onclick="markInvoicePaid(${inv.id})">✓ Mark Paid</button>` : ''}
        </div>
      </div>`).join('')
    : '<div style="color:var(--muted);font-size:0.86rem;">No invoices generated yet.</div>';
}

async function markInvoicePaid(id) {
  const method = prompt('Payment method (Cash, Check, Card / Venmo, Net-30):', 'Cash');
  if (method === null) return;
  await fetch(`/api/invoices/${id}/status`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status: 'PAID', payment_method: method }),
  });
  loadState(false);
}

function renderRateScheduleTable() {
  document.getElementById('pricingList').innerHTML = EDITABLE_RATES.map((p, i) => `
    <div class="rate-edit-row">
      <div>
        <input value="${escHtml(p.code)}" ${p.isNew ? '' : 'readonly style="background:#f1f5f9;font-weight:800;"'}
          placeholder="CODE_NAME" oninput="EDITABLE_RATES[${i}].code=this.value.toUpperCase()" />
      </div>
      <div class="rate-desc-col">
        <input value="${escHtml(p.description || '')}" placeholder="Service description" oninput="EDITABLE_RATES[${i}].description=this.value" />
      </div>
      <div>
        <input type="number" step="0.01" value="${p.unit_price}" oninput="EDITABLE_RATES[${i}].unit_price=parseFloat(this.value||0)" />
      </div>
      <div><button class="secondary" style="color:#b91c1c;" onclick="deleteRateRow(${i})">🗑</button></div>
    </div>`).join('');
}

function addRateScheduleRow() {
  EDITABLE_RATES.push({ code: '', description: '', unit_price: 0, sort_order: (EDITABLE_RATES.length + 1) * 10, isNew: true });
  renderRateScheduleTable();
}

async function deleteRateRow(idx) {
  const r = EDITABLE_RATES[idx];
  if (!r) return;
  if (!r.isNew && r.code) {
    if (!confirm(`Delete "${r.code}" from Master Rates?`)) return;
    await fetch(`/api/pricing/${encodeURIComponent(r.code)}`, { method: 'DELETE' });
  }
  EDITABLE_RATES.splice(idx, 1);
  renderRateScheduleTable();
  loadState(false);
}

async function savePricingRates() {
  const payload = EDITABLE_RATES.filter(r => r.code && r.code.trim()).map((r, idx) => ({
    code: r.code.trim().toUpperCase(),
    description: r.description || r.code,
    unit_price: parseFloat(r.unit_price || 0),
    sort_order: (idx + 1) * 10,
  }));
  await fetch('/api/pricing', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  alert('Master pricing rates saved!');
  loadState(false);
}

// --- SYNC, CREW, FEEDBACK & SETTINGS ---
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
    default_tax_rate: parseFloat(document.getElementById('editDefaultTax').value || '0'),
    gemini_api_key: document.getElementById('apiKeyInput').value || null,
    sheets_webhook_url: document.getElementById('webhookInput').value,
    feedback_webhook_url: document.getElementById('fbWebhookInput').value,
    shop_pin: document.getElementById('editShopPin').value || null,
    testing_mode: document.getElementById('testingModeChk').checked,
  };
  await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  document.getElementById('apiKeyInput').value = '';
  document.getElementById('editShopPin').value = '';
  document.getElementById('billTax').value = body.default_tax_rate;
  alert('Facility settings saved!');
  loadState(true);
}

checkAuthAndBoot();