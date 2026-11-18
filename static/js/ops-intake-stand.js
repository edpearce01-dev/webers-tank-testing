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
            }${d.contact_person ? ' • 👤 By: <strong>' + escHtml(d.contact_person) + '</strong>' : ''}${d.customer_phone ? ' • 📞 ' + escHtml(d.customer_phone) : ''}
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
    star10: Boolean(document.getElementById('fStar10Yr')?.checked),
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
  const svcLow = ((d.service_requested || '') + ' ' + (d.tank_summary || '')).toLowerCase();
  if (svcLow.includes('visual') || svcLow.includes('propane')) {
    document.getElementById('inMode').value = 'VISUAL_ONLY';
    if (svcLow.includes('propane')) document.getElementById('fGas').value = 'LPG';
    updateAdvSpecsSummary();
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
    if (serverLife.expired && typeof playShopTone === 'function') playShopTone('condemn');
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
      const wasHidden = box.hidden;
      box.className = 'notice bad';
      box.textContent = `⛔ EXPIRED COMPOSITE (>15 YRS): Born ${String(mo).padStart(2, '0')}-${yr} retired in ${String(mo).padStart(2, '0')}-${yr + 15}. Do NOT hydro unless covered by a valid 30-yr MAE permit.`;
      box.hidden = false;
      if (wasHidden && typeof playShopTone === 'function') playShopTone('condemn');
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


let LOOKUP_FILTER = 'ALL';

function updateAdvSpecsSummary() {
  const el = document.getElementById('advSpecsSummary');
  if (!el) return;
  const gas = (document.getElementById('fGas')?.value || 'AIR').trim();
  const dia = (document.getElementById('fDia')?.value || '').trim();
  const len = (document.getElementById('fLen')?.value || '').trim();
  const mode = document.getElementById('inMode')?.value === 'VISUAL_ONLY' ? 'Visual Only' : 'Hydro + Visual';
  const dim = (dia || len) ? `${dia || '?'}"×${len || '?'}"` : '';
  el.textContent = [gas, dim, mode].filter(Boolean).join(' • ');
}

async function setLookupFilter(mode) {
  LOOKUP_FILTER = mode;
  document.querySelectorAll('#lookupQuickBar .tap-pill').forEach(b => {
    b.classList.toggle('active', b.getAttribute('data-qmode') === mode);
  });
  if (mode === 'TAGS') { renderSearchRows(STATE.active_valves || []); return; }
  if (mode === 'QUEUED') { renderSearchRows((STATE.records || []).filter(r => r.status === 'QUEUED')); return; }
  if (mode === 'POST') { renderSearchRows(STATE.in_shop_post_test || []); return; }
  const q = document.getElementById('searchBox')?.value || '';
  const res = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
  let rows = await res.json();
  if (mode === 'CONDEMN') rows = rows.filter(r => r.hydro_disposition && r.hydro_disposition !== 'A');
  renderSearchRows(rows);
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
  if (s.category === 'Propane') {
    document.getElementById('inMode').value = 'VISUAL_ONLY';
    document.getElementById('fGas').value = 'LPG';
  }
  if (BATCH_LOCKED) captureBatchSnapshot();
  const spPreset = (s.dot_spec || '').toUpperCase();
  const starChk = document.getElementById('fStar10Yr');
  if (starChk && spPreset && !['3A', '3AA'].includes(spPreset)) starChk.checked = false;
  const reeEl = document.getElementById('fRee');
  if (reeEl) reeEl.placeholder = spPreset.startsWith('SP') ? 'Check carbon label REE!' : 'Blank on Steel/Alum';
  updateAdvSpecsSummary();
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
  updateAdvSpecsSummary();
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
    if (document.getElementById('fStar10Yr')) document.getElementById('fStar10Yr').checked = false;
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
    if (document.getElementById('fStar10Yr')) document.getElementById('fStar10Yr').checked = Boolean(LOCKED_SNAPSHOT.star10);
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
  let cust = document.getElementById('fCustomer').value.trim() || document.getElementById('inCustomer').value.trim();
  if (!cust) {
    if (!confirm('No customer name entered.\n\nContinue without a customer name and log under "Walk-In"?')) {
      document.getElementById('fCustomer').focus();
      return;
    }
    cust = 'Walk-In';
    document.getElementById('fCustomer').value = cust;
    document.getElementById('inCustomer').value = cust;
  }
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
      star_10yr: Boolean(document.getElementById('fStar10Yr')?.checked),
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
  const nextRush = nextPri === 'RUSH' ? 1 : 0;
  [STATE.records, STATE.in_shop_post_test].forEach(arr => {
    const item = (arr || []).find(x => x.id === id);
    if (item) { item.priority = nextPri; item.rush_fee = nextRush; }
  });
  renderIntakeAndQueue(STATE.records.filter(r => r.status === 'QUEUED'));
  fetch(`/api/records/${id}/priority`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ priority: nextPri, rush_fee: Boolean(nextRush) }),
  }).then(() => loadState(false));
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
  STATE.active_valves = (STATE.active_valves || []).filter(v => v.id !== recordId);
  [STATE.records, STATE.in_shop_post_test].forEach(arr => {
    const item = (arr || []).find(x => x.id === recordId);
    if (item) item.valve_reinstalled = 1;
  });
  renderFinishValvesAndCustomers();
  fetch('/api/valve-tag', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ record_id: recordId, valve_reinstalled: true }),
  }).then(() => loadState(false));
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
      <span>${f.label}</span> <span>${isFail ? '✗ FAIL' : '✓ OK'}</span>
    </button>`;
  }).join('');
}

function toggleBenchVis(key) {
  BENCH_VIS[key] = BENCH_VIS[key] === 'OK' ? 'FAIL' : 'OK';
  document.getElementById('benchVisGrid').innerHTML = renderVisPillsHtml(BENCH_VIS, 'toggleBenchVis');
  const rec = STATE.records.find(r => r.id === parseInt(document.getElementById('benchSelect').value || '0', 10));
  if (rec) {
    const effRee = (['3A', '3AA', '3AL'].includes((rec.dot_spec || '').toUpperCase()) && rec.ree_limit === 7.2) ? null : (rec.ree_limit ?? null);
    previewBenchMath(effRee);
  }
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
  const isAdminDiag = window.CURRENT_USER && window.CURRENT_USER.role === 'SYSTEM_ADMIN';
  const certBtnLbl = isAdminDiag
    ? 'Certify &amp; Advance (ADMIN Diagnostic — Marks ADM*)'
    : 'Certify &amp; Advance to Next Cylinder (RP)';

  area.innerHTML = `
    <div class="notice" id="benchContextBanner">
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
        <div><label>Total Exp (cc) *</label><input type="number" step="0.1" id="bTotal" placeholder="Numpad + Enter" oninput="previewBenchMath(${effRee ?? 'null'})" onkeydown="if(event.key==='Enter'){event.preventDefault();const p=document.getElementById('bPerm');if(p){p.focus();p.select();}}" /></div>
        <div><label>Perm Exp (cc) *</label><input type="number" step="0.1" id="bPerm" value="0" oninput="previewBenchMath(${effRee ?? 'null'})" onkeydown="if(event.key==='Enter'){event.preventDefault();completeBottle(${rec.id}, ${isHydro}, true);}" /></div>
      ` : ''}
      <div><label>🏷️ Valve / Reg Tag #</label><input id="bValveTag" value="${rec.valve_tag && !rec.valve_reinstalled ? escHtml(rec.valve_tag) : ''}" placeholder="Tag #" /></div>
      <div><label>Coating</label><select id="bCoat"><option>Excellent</option><option selected>Good</option><option>Fair</option><option>Poor</option></select></div>
      <div>
        <label>RIN Mark (If Done Now)</label>
        <select id="bRinType">
          <option value="">Mark After Drying</option>
          <option value="STAMPED">🔨 Neck Stamped B386</option>
          <option value="STICKERED">🏷️ B386 Sticker Applied</option>
        </select>
      </div>
      <div style="grid-column:1/-1;"><label>Row Notes</label><input id="bNotes" value="${escHtml(rec.notes || '')}" placeholder="Optional DOT note..." /></div>
    </div>

    ${isHydro ? `
      <div id="benchLiveMath" class="bench-telemetry-grid">
        <div class="bench-tele-tile" id="teleElasticTile">
          <small>Elastic Exp (Total − Perm)</small>
          <strong id="teleElasticVal">— cc${effRee ? ` (≤ ${effRee})` : ''}</strong>
        </div>
        <div class="bench-tele-tile" id="telePctTile">
          <small>% Permanent (≤ 10.0% Max)</small>
          <strong id="telePctVal">— %</strong>
        </div>
        <div class="bench-tele-tile" id="teleDispTile">
          <small>Live DOT Disposition</small>
          <strong id="teleDispVal">Enter Total cc ↵</strong>
        </div>
      </div>
    ` : ''}

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

    <div style="display:flex;gap:14px;flex-wrap:wrap;margin-top:11px;padding:9px 11px;background:var(--ui-surface-2);border-radius:10px;border:1px solid var(--border-soft);">
      ${["3A","3AA"].includes((rec.dot_spec||"").toUpperCase()) ? `<label class="inline-check" style="color:#92400e;"><input type="checkbox" id="bStar10Yr" ${rec.star_10yr ? "checked" : ""} /><span><strong>★ 10-Yr Star</strong></span></label>` : ""}
      <label class="inline-check"><input type="checkbox" id="bOring" ${rec.addon_oring ? 'checked' : ''} /><span>+ Neck O-Ring</span></label>
      <label class="inline-check"><input type="checkbox" id="bBurstDisc" ${(rec.addon_burst_disc || rec.relief_valve_changed === 'Yes') ? 'checked' : ''} /><span>+ Burst Disc / Relief</span></label>
      <label class="inline-check"><input type="checkbox" id="bClean" ${rec.addon_clean ? 'checked' : ''} /><span>+ Internal Clean</span></label>
      <label class="inline-check"><input type="checkbox" id="bValveReinst" /><span>Valve Reinstalled</span></label>
    </div>

    <button class="primary" id="btnCompleteBench" onclick="completeBottle(${rec.id}, ${isHydro}, false)">${certBtnLbl}</button>
    ${isHydro ? `<div style="text-align:center;font-size:0.75rem;color:var(--muted);margin-top:6px;">⌨️ Hands-Fast Numpad: Type <strong>Total cc</strong> → <strong>Enter</strong> → <strong>Perm cc</strong> → <strong>Enter</strong> to certify &amp; load next bottle</div>` : ''}`;
  if (typeof renderLiveDotForm393Sheets === 'function') renderLiveDotForm393Sheets();
}

function previewBenchMath(reeLimit) {
  const totEl = document.getElementById('bTotal');
  const condemnWrap = document.getElementById('bCondemnWrap');
  const visFailed = Object.values(BENCH_VIS).some(v => v !== 'OK');
  if (!totEl) {
    if (condemnWrap) condemnWrap.hidden = !visFailed;
    return;
  }
  const elTile = document.getElementById('teleElasticTile');
  const pctTile = document.getElementById('telePctTile');
  const dispTile = document.getElementById('teleDispTile');
  const elVal = document.getElementById('teleElasticVal');
  const pctVal = document.getElementById('telePctVal');
  const dispVal = document.getElementById('teleDispVal');

  const rawTot = totEl.value;
  const tot = parseFloat(rawTot || '0');
  const perm = parseFloat(document.getElementById('bPerm')?.value || '0');

  if (!rawTot || !(tot > 0)) {
    if (elTile) elTile.className = 'bench-tele-tile';
    if (pctTile) pctTile.className = 'bench-tele-tile';
    if (dispTile) dispTile.className = 'bench-tele-tile' + (visFailed ? ' state-fail' : '');
    if (elVal) elVal.textContent = `— cc${reeLimit !== null ? ` (≤ ${reeLimit})` : ''}`;
    if (pctVal) pctVal.textContent = '— %';
    if (dispVal) dispVal.textContent = visFailed ? '⛔ VISUAL FAIL (Code D)' : 'Enter Total cc ↵';
    if (condemnWrap) condemnWrap.hidden = !visFailed;
    if (typeof renderLiveDotForm393Sheets === 'function') renderLiveDotForm393Sheets();
    return;
  }

  const elasticNum = tot - perm;
  const pctNum = (perm / tot) * 100;
  const reeFailed = reeLimit !== null && elasticNum > reeLimit;
  const permFailed = pctNum > 10.0 || perm < 0 || perm > tot;
  const bad = visFailed || reeFailed || permFailed;

  if (elTile) elTile.className = 'bench-tele-tile ' + (reeFailed ? 'state-fail' : 'state-pass');
  if (pctTile) pctTile.className = 'bench-tele-tile ' + (permFailed ? 'state-fail' : 'state-pass');
  if (dispTile) dispTile.className = 'bench-tele-tile ' + (bad ? 'state-fail' : 'state-pass');

  if (elVal) elVal.textContent = `${elasticNum.toFixed(2)} cc${reeLimit !== null ? ` / ≤${reeLimit}` : ''}`;
  if (pctVal) pctVal.textContent = `${pctNum.toFixed(1)}%`;
  if (dispVal) {
    const reason = visFailed ? 'Visual' : (reeFailed ? '>REE' : '>10%');
    dispVal.textContent = bad ? `⛔ CONDEMN (${reason})` : '✓ PASS • Code A';
  }
  if (condemnWrap) condemnWrap.hidden = !bad;
  if (typeof renderLiveDotForm393Sheets === 'function') renderLiveDotForm393Sheets();
}

async function completeBottle(id, isHydro, fromEnterKey = false) {
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

  if (!condemnHidden && fromEnterKey) {
    if (typeof playShopTone === 'function') playShopTone('condemn');
    const rec = STATE.records.find(x => x.id === id);
    if (!confirm(`⚠️ CONDEMN (CODE D) DETECTED for SN ${rec ? rec.serial_number : id}.\n\nConfirm condemning this cylinder under 49 CFR §180.205(i)? (Cancel to double-check burette reading or select Legal Action.)`)) {
      document.getElementById('bCondemnAct')?.focus();
      return;
    }
  }

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
        star_10yr: document.getElementById('bStar10Yr') ? document.getElementById('bStar10Yr').checked : Boolean(STATE.records.find(x => x.id === id)?.star_10yr),
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
    const out = await res.json();
    if (!res.ok) {
      if (typeof playShopTone === 'function') playShopTone('condemn');
      alert(out.detail || 'Could not complete cylinder test.');
      return;
    }
    if (typeof playShopTone === 'function') {
      playShopTone(out.hydro_disposition === 'A' ? 'pass' : 'condemn');
    }
  } finally {
    BENCH_SUBMITTING = false;
    if (btn) {
      btn.disabled = false;
      btn.textContent = (window.CURRENT_USER && window.CURRENT_USER.role === 'SYSTEM_ADMIN')
        ? 'Certify & Advance (ADMIN Diagnostic — Marks ADM*)'
        : 'Certify & Advance to Next Cylinder (RP)';
    }
  }
  await loadState(false);
  const nextTot = document.getElementById('bTotal');
  if (nextTot) nextTot.focus();
}
