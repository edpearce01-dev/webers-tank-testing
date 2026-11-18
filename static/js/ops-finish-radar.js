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
          ${r.star_10yr && isPass ? '<span class="tag-chip" style="background:#fef3c7;border-color:#f59e0b;color:#92400e;">★ Stamp 10-Yr Star</span>' : ''}${r.valve_tag && !r.valve_reinstalled ? `<span class="tag-chip">#${escHtml(r.valve_tag)}</span>` : ''}
        </div>
        <div class="rec-sub">${escHtml(r.customer_name)} • ${escHtml(r.dot_spec)}-${r.service_psi} • ${r.service_mode === 'VISUAL_ONLY' ? 'Visual Log' : (r.service_mode === 'VISUAL_ONLY' ? 'Visual Log' : 'Sheet #' + r.sheet_no)} • Perm: ${fmtPctTenth(r.percent_permanent)}</div>
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
  [STATE.records, STATE.in_shop_post_test].forEach(arr => {
    const item = (arr || []).find(x => x.id === recordId);
    if (item) {
      item[field] = nextVal ? 1 : 0;
      if (field === 'addon_burst_disc') item.relief_valve_changed = nextVal ? 'Yes' : 'No';
    }
  });
  renderCompletedSection(STATE.records.filter(r => r.status === 'COMPLETED'));
  renderFinishValvesAndCustomers();
  fetch(`/api/records/${recordId}/addons`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ [field]: nextVal }),
  }).then(() => loadState(false));
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
  const msg = `Weber's Tank Testing: Hi ${custName}, your ${count} cylinder(s) are certified and staged for pickup at our 24/7 Loading Dock at 3086 Legionville Rd${invExtra ? ' (' + invExtra + ')' : ''}. Questions? Call/text ${STATE.shop_phone || '724-266-8008'}.`;
  const sep = /iPad|iPhone|iPod/i.test(navigator.userAgent) ? '&' : '?';
  window.location.href = `sms:${cleanDigits}${sep}body=${encodeURIComponent(msg)}`;
}

window.FINISH_BATCH_OPEN = window.FINISH_BATCH_OPEN || {};

function toggleFinishBatchGroup(custKey) {
  window.FINISH_BATCH_OPEN[custKey] = !window.FINISH_BATCH_OPEN[custKey];
  renderFinishValvesAndCustomers();
}

function renderFinishValvesAndCustomers() {
  const postRows = (STATE.in_shop_post_test || []).filter(r => r.shop_stage !== 'PICKED_UP');
  const needsWork = postRows.filter(r => r.shop_stage !== 'READY_BACKROOM');
  document.getElementById('finishCountBadge').textContent = `${needsWork.length} Need Finish • ${postRows.length - needsWork.length} Dock Ready`;

  if (!postRows.length) {
    document.getElementById('finishBenchList').innerHTML =
      '<div style="color:var(--muted);font-size:0.86rem;">All tested cylinders have been stamped/stickered, revalved, and picked up.</div>';
  } else {
    const groupsMap = new Map();
    postRows.forEach(r => {
      const k = r.customer_name || 'Walk-In';
      if (!groupsMap.has(k)) groupsMap.set(k, []);
      groupsMap.get(k).push(r);
    });

    const sortedGroups = [...groupsMap.entries()].sort((a, b) => {
      const aNeed = a[1].some(r => r.shop_stage !== 'READY_BACKROOM') ? 0 : 1;
      const bNeed = b[1].some(r => r.shop_stage !== 'READY_BACKROOM') ? 0 : 1;
      return aNeed - bNeed || a[0].localeCompare(b[0]);
    });

    document.getElementById('finishBenchList').innerHTML = sortedGroups.map(([cust, rows]) => {
      if (!(cust in window.FINISH_BATCH_OPEN)) {
        window.FINISH_BATCH_OPEN[cust] = rows.length <= 3;
      }
      const isOpen = Boolean(window.FINISH_BATCH_OPEN[cust]);
      const safeC = cust.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
      const unmarked = rows.filter(r => r.hydro_disposition === 'A' && !r.rin_marked).length;
      const notReady = rows.filter(r => r.shop_stage !== 'READY_BACKROOM').length;
      const onShelf = rows.filter(r => r.shop_stage === 'READY_BACKROOM').length;
      const hasRush = rows.some(r => r.rush_fee || r.priority === 'RUSH');
      const ph = (rows.find(r => r.customer_phone) || {}).customer_phone || '';

      return `
        <div class="batch-finish-card">
          <div class="batch-finish-hdr" onclick="toggleFinishBatchGroup('${safeC}')">
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
              <span class="mono" style="font-size:0.82rem;color:var(--muted);width:14px;">${isOpen ? '▾' : '▸'}</span>
              <strong style="font-size:0.95rem;color:var(--forest);">${escHtml(cust)}</strong>
              <span class="sub-badge">${rows.length} Cyl</span>
              ${hasRush ? '<span class="rush-badge">RUSH</span>' : ''}
              ${unmarked ? `<span class="stage-chip drying">${unmarked} Need B386 Mark</span>` : ''}
              ${onShelf ? `<span class="stage-chip ready">${onShelf} Dock Ready</span>` : ''}
            </div>
            <div class="rec-actions" onclick="event.stopPropagation()">
              ${unmarked ? `<button class="secondary" style="background:#ede9fe;color:#5b21b6;border-color:#c4b5fd;" onclick="stageCustomerBatch('${safeC}', 'STAMPED_STICKERED', 'MARKED', true)">Mark Batch B386</button>` : ''}
              ${notReady ? `<button class="secondary" style="background:#dcfce7;color:#166534;border-color:#86efac;" onclick="stageCustomerBatch('${safeC}', 'READY_BACKROOM', '', true)">Revalve &amp; Move Batch</button>` : ''}
              <button class="secondary" onclick="openClaimSlipPdf('${safeC}')">Claim Slip</button>
              <button class="secondary" onclick="sendPickupSms('${safeC}', '${escHtml(ph)}', ${rows.length}, '')">Text</button>
              ${onShelf === rows.length ? `<button class="secondary" onclick="stageCustomerBatch('${safeC}', 'PICKED_UP', '', true)">✓ Picked Up</button>` : ''}
            </div>
          </div>
          ${isOpen ? `<div class="batch-finish-body">${rows.map(r => recordFinishRowHtml(r)).join('')}</div>` : ''}
        </div>`;
    }).join('');
  }

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
    : '<div style="color:var(--muted);font-size:0.86rem;">All reusable valve tags are clear.</div>';

  const activeCusts = STATE.customers_summary.filter(c => c.ready_backroom_count || c.needs_stamp_count || c.unbilled_count);
  const shownCusts = SHOW_ALL_SHELF_CUSTS ? STATE.customers_summary : activeCusts;
  const toggleBtnHtml = STATE.customers_summary.length > activeCusts.length
    ? `<div style="margin-bottom:8px;"><button class="secondary" onclick="SHOW_ALL_SHELF_CUSTS=!SHOW_ALL_SHELF_CUSTS;renderFinishValvesAndCustomers()">${SHOW_ALL_SHELF_CUSTS ? 'Show Active Shelf/Unbilled Only' : `Show All Past Customers (${STATE.customers_summary.length})`}</button></div>`
    : '';
  document.getElementById('customersListArea').innerHTML = toggleBtnHtml + (shownCusts.length
    ? shownCusts.map(c => {
        const safeC = c.customer_name.replace(/'/g, "\\'");
        return `
          <div class="rec-item">
            <div class="rec-main">
              <div class="rec-title">
                <span>${escHtml(c.customer_name)}</span>
                ${c.tax_exempt ? '<span class="tax-exempt-pill">Tax-Exempt</span>' : ''}
                ${c.needs_stamp_count ? `<span class="stage-chip drying">${c.needs_stamp_count} Need B386</span>` : ''}
                ${c.ready_backroom_count ? `<span class="stage-chip ready">${c.ready_backroom_count} Dock Ready</span>` : ''}
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
    : '<div style="color:var(--muted);font-size:0.86rem;">No active shelf or unbilled batches right now.</div>');
}

async function filterCustomerHistory(cust) {
  jumpToTab('search');
  const sb = document.getElementById('searchBox');
  if (sb) sb.value = cust;
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
  const lb = document.getElementById('lookupResultBadge');
  if (lb) lb.textContent = `${rows.length} Found`;
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
              <div class="rec-sub">${escHtml(r.work_date)} • ${escHtml(r.customer_name)} • ${escHtml(r.dot_spec)}-${r.service_psi} • ${isQueued ? 'Awaiting Stand' : (r.service_mode === 'VISUAL_ONLY' ? 'Visual Log' : 'Sheet #' + r.sheet_no)} • Perm: ${fmtPctTenth(r.percent_permanent)}</div>
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
    || (STATE.active_sheet_records || []).find(r => r.id === recordId)
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
  if (document.getElementById('erStar10Yr')) document.getElementById('erStar10Yr').checked = Boolean(rec.star_10yr);
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
  let custVal = (document.getElementById('erCustomer').value || '').trim();
  if (!custVal) {
    if (!confirm('No customer name entered.\n\nContinue without a customer name and save under "Walk-In"?')) {
      document.getElementById('erCustomer').focus();
      return;
    }
    custVal = 'Walk-In';
    document.getElementById('erCustomer').value = custVal;
  }
  const pri = document.getElementById('erPriority').value;
  const rinType = document.getElementById('erRinType').value;
  const burstChecked = document.getElementById('erBurstDisc').checked;
  const res = await fetch(`/api/records/${id}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      customer_name: custVal,
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
      star_10yr: Boolean(document.getElementById('erStar10Yr')?.checked),
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
  if (CURRENT_TAB === 'search') runSearch(false);
}

async function deleteRecordFromModal() {
  const id = document.getElementById('erId').value;
  closeEditModal();
  await removeRecord(id);
  if (CURRENT_TAB === 'search') runSearch(false);
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
    resend_api_key: document.getElementById('editResendKey')?.value || null,
    resend_from_email: document.getElementById('editResendFrom')?.value || 'alerts@weberstanktesting.com',
    master_sheet_url: document.getElementById('editMasterSheetUrl')?.value || '',
    alert_email: document.getElementById('editAlertEmail')?.value || '',
    smtp_sender_email: document.getElementById('editSmtpSender')?.value || '',
    smtp_app_password: document.getElementById('editSmtpPass')?.value || null,
    shop_pin: document.getElementById('editShopPin').value || null,
    testing_mode: document.getElementById('testingModeChk').checked,
  };
  await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  document.getElementById('apiKeyInput').value = '';
  if (document.getElementById('editResendKey')) document.getElementById('editResendKey').value = '';
  if (document.getElementById('editSmtpPass')) document.getElementById('editSmtpPass').value = '';
  document.getElementById('editShopPin').value = '';
  alert('Facility settings & compliance dates saved!');
  loadState(true);
}

function toggleShopMapCollapse() {
  const curHidden = localStorage.getItem('webers_hide_floor_map') !== '0';
  localStorage.setItem('webers_hide_floor_map', curHidden ? '0' : '1');
  renderLiveFloorPlanMap();
}

function renderLiveFloorPlanMap() {
  const host = document.getElementById('liveShopFloorMap');
  if (!host || !window.STATE) return;

  const isHidden = localStorage.getItem('webers_hide_floor_map') !== '0';
  const recs = STATE.records || [];
  const postRecs = (STATE.in_shop_post_test || []).filter(r => r.shop_stage !== 'PICKED_UP');
  const drops = (STATE.dropoffs || []).filter(d => (d.status === 'IN_BACKROOM' || d.status === 'CHECKED_IN') && (d.imported_count || 0) < d.bottle_count);

  const dropBottles = drops.reduce((s, d) => s + Math.max(0, d.bottle_count - (d.imported_count || 0)), 0);
  const atPickup = postRecs.filter(r => r.shop_stage === 'READY_BACKROOM').length;
  const atJackets = recs.filter(r => r.status === 'QUEUED').length;
  const viceTags = (STATE.active_valves || []).length;
  const dryingUnmarked = postRecs.filter(r => r.hydro_disposition === 'A' && !r.rin_marked).length;
  const markedNeedRevalve = postRecs.filter(r => r.shop_stage !== 'READY_BACKROOM' && (r.rin_marked || r.hydro_disposition !== 'A')).length;

  host.style.padding = isHidden ? '10px 14px' : '13px 15px';
  host.innerHTML = `
    <div class="card-hdr" style="margin-bottom:${isHidden ? '0' : '9px'};padding-bottom:${isHidden ? '0' : '7px'};border-bottom:${isHidden ? 'none' : '1px solid var(--border-soft)'};">
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
        <h3>Baden Shop Floor Status</h3>
        <span class="sub-badge">${dropBottles + atJackets + dryingUnmarked + markedNeedRevalve} In Progress • ${atPickup} Ready</span>
      </div>
      <button type="button" class="secondary" style="padding:3px 9px;font-size:0.73rem;" onclick="toggleShopMapCollapse()">
        ${isHidden ? 'Show Stations ▾' : 'Collapse ▴'}
      </button>
    </div>
    ${isHidden ? '' : `
    <div class="shop-flow-grid">
      <div class="flow-zone">
        <div class="flow-zone-hdr"><span>01 • 24/7 Loading Dock</span><span>Dock Door</span></div>
        <div class="flow-spot ${dropBottles ? 'warn-load' : ''}" onclick="jumpToTab('intake')">
          <span>Incoming Drop-Offs</span>
          <span class="flow-cnt">${dropBottles} Waiting</span>
        </div>
        <div class="flow-spot ${atPickup ? 'active-load' : ''}" onclick="jumpToTab('logs')">
          <span>Pickup Shelf Ready</span>
          <span class="flow-cnt">${atPickup} Ready</span>
        </div>
      </div>

      <div class="flow-zone">
        <div class="flow-zone-hdr"><span>02 • Hydro Test Room</span><span>Stand &amp; Bench</span></div>
        <div class="flow-spot ${atJackets ? 'active-load' : ''}" onclick="jumpToTab('bench')">
          <span>Short &amp; Tall Jackets</span>
          <span class="flow-cnt">${atJackets} Queued</span>
        </div>
        <div class="flow-spot ${viceTags ? 'warn-load' : ''}" onclick="jumpToTab('logs')">
          <span>Active Valve Tags</span>
          <span class="flow-cnt">${viceTags} Active</span>
        </div>
      </div>

      <div class="flow-zone">
        <div class="flow-zone-hdr"><span>03 • Main Bay Finish</span><span>Dry &amp; Mark</span></div>
        <div class="flow-spot ${dryingUnmarked ? 'warn-load' : ''}" onclick="jumpToTab('logs')">
          <span>Drying • Needs B386</span>
          <span class="flow-cnt">${dryingUnmarked} Cyl</span>
        </div>
        <div class="flow-spot ${markedNeedRevalve ? 'active-load' : ''}" onclick="jumpToTab('logs')">
          <span>Marked • Needs Revalve</span>
          <span class="flow-cnt">${markedNeedRevalve} Cyl</span>
        </div>
      </div>
    </div>`}`;
}


let RADAR_JOBS_CACHE = [];

window.openOpsJobModal = function (jobId) {
  const j = RADAR_JOBS_CACHE.find(x => x.id === jobId) || (STATE.schedule_jobs || []).find(x => x.id === jobId);
  if (!j) return;
  const m = document.getElementById('opsJobDetailModal');
  if (!m) return;
  m.hidden = false;
  const isRound = j.job_type === 'ROAD_ROUNDTRIP';
  const addr = (j.location_notes || '').trim();
  const retAddr = (j.return_address || addr).trim();
  const mapsUrl = j.maps_route_url || (addr ? `https://www.google.com/maps/dir/?api=1&origin=${encodeURIComponent('3086 Legionville Rd, Baden, PA 15005')}&destination=${encodeURIComponent(addr)}&travelmode=driving` : '');
  const digits = String(j.customer_phone || '').replace(/\D/g, '');
  const sep = /iPad|iPhone|iPod/i.test(navigator.userAgent) ? '&' : '?';
  const smsTxt = `Weber's Tank Testing: Hi ${j.contact_person || j.customer_name}, confirming our truck run for ${j.bottle_count} cylinder(s)${j.pickup_time ? ' around ' + j.pickup_time : ''}. Questions? 724-266-8008.`;

  document.getElementById('ojdTitle').textContent = `${j.customer_name} (${j.bottle_count} Cylinders)`;
  document.getElementById('ojdSub').textContent = isRound ? 'Round-Trip: Pickup & Return Delivery' : j.job_type.replace('_', ' ');
  document.getElementById('ojdActions').innerHTML = `
    <button class="primary" style="width:auto;margin:0;padding:8px 13px;" onclick="document.getElementById('opsJobDetailModal').hidden=true;importScheduleJobToIntake(${j.id})">📥 Scan Now (Prep &amp; Scan)</button>
    ${(j.job_type === 'ROAD_PICKUP' || j.job_type === 'ROAD_ROUNDTRIP') && !j.pickup_leg_done ? `<button class="secondary" style="padding:8px 12px;background:#fef3c7;color:#92400e;border-color:#f59e0b;font-weight:800;" onclick="stageJobToBackroomFromOps(${j.id}, true)">🚚 Picked Up → Hold in Backroom</button>` : ''}
    ${mapsUrl ? `<a class="secondary" style="padding:8px 12px;background:#eff6ff;color:#1d4ed8;border-color:#93c5fd;font-weight:800;" href="${mapsUrl}" target="_blank" rel="noopener">🗺️ Route from Shop${j.drive_eta_mins ? ` (~${j.drive_eta_mins}m)` : ''}</a>` : ''}
    ${digits ? `<a class="secondary" style="padding:8px 12px;font-weight:800;" href="tel:${digits}">📞 ${escHtml(j.customer_phone)}</a>` : ''}
    ${digits ? `<a class="secondary" style="padding:8px 12px;background:#fef3c7;color:#92400e;border-color:#f59e0b;font-weight:800;" href="sms:${digits}${sep}body=${encodeURIComponent(smsTxt)}">📲 Text (iPhone)</a>` : ''}
    <a class="secondary" style="padding:8px 12px;" href="/schedule">📅 Edit in Schedule →</a>
  `;
  const rb = document.getElementById('ojdRouteBox');
  if (addr) {
    rb.hidden = false;
    rb.innerHTML = j.drive_eta_mins
      ? `🛣️ <strong>Pre-Planned Route from 3086 Legionville Rd:</strong> ~<strong>${j.drive_eta_mins} min</strong> one-way (${j.drive_miles} mi) • ~${j.drive_eta_mins * 2} min round-trip.`
      : `🛣️ <strong>Destination:</strong> ${escHtml(addr)}`;
  } else rb.hidden = true;

  document.getElementById('ojdGrid').innerHTML = `
    <div><small>Customer / Dept</small><strong>${escHtml(j.customer_name)}</strong></div>
    <div><small>Contact &amp; Phone</small><strong>${escHtml(j.contact_person || '—')}</strong> • ${escHtml(j.customer_phone || 'No phone')}</div>
    <div><small>Cylinder Types</small><strong>${j.bottle_count} Cyl • ${escHtml(j.tank_summary || 'Standard Hydro/Visual')}</strong></div>
    <div><small>Valves</small><strong>${j.valves_attached === 0 ? 'Bare / No Valves' : 'Valves Attached'}</strong></div>
    <div><small>📥 Pickup Date &amp; Time</small><strong>${escHtml(j.scheduled_date || '—')}</strong> ${j.pickup_time ? `• ${escHtml(j.pickup_time)}` : ''}</div>
    <div><small>📤 Return / Ready Date &amp; Time</small><strong>${escHtml(j.due_date || '—')}</strong> ${j.dropoff_time ? `• ${escHtml(j.dropoff_time)}` : ''}</div>
    <div style="grid-column:1/-1;"><small>Pickup Address</small><strong>${escHtml(addr || 'Shop Drop-Off at 3086 Legionville Rd')}</strong></div>
    ${isRound ? `<div style="grid-column:1/-1;"><small>Return Delivery Address</small><strong>${escHtml(retAddr || 'Same as Pickup')}</strong></div>` : ''}
    ${(j.po_station || j.notes) ? `<div style="grid-column:1/-1;"><small>PO / Station &amp; Notes</small><strong>${escHtml([j.po_station, j.notes].filter(Boolean).join(' • '))}</strong></div>` : ''}
  `;
};

let RADAR_EXPANDED = false;

function formatTruckBadge(modeStr) {
  const m = String(modeStr || '').toUpperCase();
  if (m.includes('ROUND')) return { cls: 'roundtrip', lbl: '🚚 PICKUP & DELIVER BACK' };
  if (m.includes('DELIV')) return { cls: 'delivery', lbl: '📤 DELIVER BACK TO CUSTOMER' };
  if (m.includes('PICKUP') || m.includes('ROAD') || m.includes('TRUCK')) return { cls: 'pickup', lbl: '📥 TRUCK PICKUP AT CUSTOMER' };
  return { cls: 'walkin', lbl: '🚪 SCHEDULED DROP-OFF' };
}

async function refreshOpsDispatchRadar() {
  const box = document.getElementById('opsDispatchRadar');
  if (!box) return;
  try {
    const res = await fetch('/api/ops/dispatch-radar');
    if (!res.ok) return;
    const data = await res.json();
    const today = data.today || '';
    const allJobs = data.jobs || [];
    RADAR_JOBS_CACHE = allJobs;

    const isTruckJob = j => {
      const dm = String(j.delivery_mode || j.truck_mode || j.job_type || '').toUpperCase();
      return dm && dm !== 'SHOP_BATCH' && dm !== 'CUSTOMER_DROPOFF' && dm !== 'WALK_IN' && dm !== 'DROP_OFF' && dm !== 'NONE';
    };

    const truckJobs = allJobs.filter(isTruckJob);
    const dueTodayOrPast = truckJobs.filter(j => (j.scheduled_date || '') <= today);
    const upcomingTruck = truckJobs.filter(j => (j.scheduled_date || '') > today);

    // Update top Schedule nav badge if open truck runs exist
    const schedLink = document.querySelector('.ws-switcher a[href="/schedule"]');
    if (schedLink && !schedLink.querySelector('.radar-cnt')) {
      const sp = document.createElement('span');
      sp.className = 'radar-cnt';
      sp.style.cssText = 'margin-left:4px;padding:1px 6px;border-radius:999px;font-size:0.68rem;font-weight:800;background:#fbbf24;color:#111827;';
      schedLink.appendChild(sp);
    }
    const cntEl = schedLink?.querySelector('.radar-cnt');
    if (cntEl) {
      const badgeCnt = dueTodayOrPast.length || truckJobs.length;
      cntEl.hidden = badgeCnt === 0;
      cntEl.textContent = String(badgeCnt);
    }

    if (truckJobs.length === 0) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    if (dueTodayOrPast.length === 0 && !RADAR_EXPANDED) {
      box.className = 'ops-dispatch-radar calm';
      box.innerHTML = `
        <div class="radar-hdr">
          <div style="font-size:0.82rem;font-weight:700;color:#334155;">
            🚚 <strong>Truck Pickups &amp; Deliveries Today:</strong> None due today
            ${upcomingTruck.length ? ` • <strong style="color:#b45309;">${upcomingTruck.length} upcoming scheduled run(s)</strong>` : ''}
            ${allJobs.length - truckJobs.length > 0 ? ` • ${allJobs.length - truckJobs.length} scheduled drop-off(s)` : ''}
          </div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;">
            ${truckJobs.length ? `<button type="button" class="secondary" style="padding:4px 10px;font-size:0.74rem;" onclick="RADAR_EXPANDED=true;refreshOpsDispatchRadar()">Show ${truckJobs.length} Upcoming Run(s) ▾</button>` : ''}
            <a class="secondary" style="padding:4px 10px;font-size:0.74rem;" href="/schedule">📅 Schedule / Book Run →</a>
          </div>
        </div>`;
      return;
    }

    box.className = 'ops-dispatch-radar';
    const shown = RADAR_EXPANDED ? truckJobs : dueTodayOrPast;
    box.innerHTML = `
      <div class="radar-hdr">
        <div>
          <strong style="font-size:0.92rem;color:#0c261b;">🚚 Scheduled Truck Pickups &amp; Deliveries (${dueTodayOrPast.length} Due Today${upcomingTruck.length ? ` • ${upcomingTruck.length} Upcoming` : ''})</strong>
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;">
          ${upcomingTruck.length ? `<button type="button" class="secondary" style="padding:4px 10px;font-size:0.74rem;" onclick="RADAR_EXPANDED=!RADAR_EXPANDED;refreshOpsDispatchRadar()">${RADAR_EXPANDED ? 'Today Only ▴' : `Show All (${truckJobs.length}) ▾`}</button>` : ''}
          <a class="secondary" style="padding:4px 10px;font-size:0.74rem;" href="/schedule">📅 Full Calendar &amp; Booking →</a>
        </div>
      </div>
      <div class="radar-list">
        ${shown.map(j => {
          const b = formatTruckBadge(j.delivery_mode || j.truck_mode || j.job_type);
          const addr = (j.location_notes || j.address || j.street_address || '').trim();
          const ph = (j.customer_phone || '').trim();
          const mapsUrl = j.maps_route_url || (addr ? `https://www.google.com/maps/dir/?api=1&origin=${encodeURIComponent('3086 Legionville Rd, Baden, PA 15005')}&destination=${encodeURIComponent(addr)}&travelmode=driving` : '');
          return `
            <div class="radar-card" style="cursor:pointer;" onclick="openOpsJobModal(${j.id})">
              <div style="display:flex;justify-content:space-between;align-items:center;gap:6px;flex-wrap:wrap;">
                <span class="radar-badge ${b.cls}">${b.lbl}</span>
                ${j.pickup_time ? `<span class="time-window-pill">🕒 ${escHtml(j.pickup_time)}</span>` : ""}${j.leave_by_time ? `<span class="leave-by-pill">🚦 Leave by ${escHtml(j.leave_by_time)}</span>` : ""}${j.drive_eta_mins ? `<span class="eta-route-pill">🚗 ~${j.drive_eta_mins}m (${j.drive_miles} mi)</span>` : ""}<span class="mono" style="font-size:0.74rem;font-weight:800;color:${(j.scheduled_date||'') < today ? '#b91c1c' : '#0c261b'};">
                  ${(j.scheduled_date||'') < today ? '⚠️ OVERDUE ' : ''}${escHtml(j.scheduled_date || today)}
                </span>
              </div>
              <div style="font-weight:800;font-size:0.94rem;color:#0f172a;">
                ${escHtml(j.customer_name)} <span style="color:#b45309;">• ${j.bottle_count || 1} cyl</span>
                ${j.priority === 'RUSH' ? '<span class="sub-badge" style="background:#fee2e2;color:#991b1b;">RUSH</span>' : ''}
              </div>
              <div style="font-size:0.78rem;color:#475569;">
                ${addr ? `📍 ${escHtml(addr)}` : '📍 Address pending'}${j.contact_person ? ` • 👤 ${escHtml(j.contact_person)}` : ''}${j.tank_summary ? ` • ${escHtml(j.tank_summary)}` : ''}
                ${j.notes ? ` • <em>${escHtml(j.notes)}</em>` : ''}
              </div>
              <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:2px;" onclick="event.stopPropagation()">
                <button type="button" class="secondary" style="padding:5px 9px;font-size:0.74rem;font-weight:800;" onclick="openOpsJobModal(${j.id})">🔍 Details</button>
                <button type="button" class="secondary" style="padding:5px 9px;font-size:0.74rem;background:#ecfdf5;color:#065f46;border-color:#6ee7b7;font-weight:800;" onclick="importScheduleJobToIntake(${j.id});jumpToTab('intake')">📥 Load to Prep</button>
                ${mapsUrl ? `<a class="secondary" style="padding:5px 9px;font-size:0.74rem;" href="${mapsUrl}" target="_blank" rel="noopener">🗺️ Maps</a>` : ''}
                ${ph ? `<a class="secondary" style="padding:5px 9px;font-size:0.74rem;" href="tel:${escHtml(ph)}">📞 ${escHtml(ph)}</a>` : ''}
                <button type="button" class="secondary" style="padding:5px 9px;font-size:0.74rem;" onclick="completeTruckRunFromOps(${j.id})">✓ Done</button>
              </div>
            </div>`;
        }).join('')}
      </div>`;
  } catch (_) {}
}

async function stageJobToBackroomFromOps(jobId, closeModal = false) {
  const res = await fetch(`/api/schedule/jobs/${jobId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ move_to_backroom: true, pickup_leg_done: true }),
  });
  if (!res.ok) { alert((await res.json()).detail || 'Could not stage batch to backroom.'); return; }
  if (closeModal) document.getElementById('opsJobDetailModal').hidden = true;
  if (typeof playShopTone === 'function') playShopTone('pass');
  await loadState(false);
  refreshOpsDispatchRadar();
  alert('✓ Truck batch moved into Backroom Holding (2. Prep & Scan → 24/7 Loading Dock Check-Ins)!');
}

async function completeTruckRunFromOps(jobId) {
  if (!confirm('Mark this scheduled truck pickup/delivery complete?')) return;
  await fetch(`/api/schedule/jobs/${jobId}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: 'COMPLETED' }),
  });
  refreshOpsDispatchRadar();
  loadState(false);
}


async function sendTestEmailAlert() {
  const body = {
    sheets_webhook_url: document.getElementById('webhookInput')?.value || '',
    alert_email: document.getElementById('editAlertEmail')?.value || '',
    smtp_sender_email: document.getElementById('editSmtpSender')?.value || '',
    smtp_app_password: document.getElementById('editSmtpPass')?.value || null,
  };
  await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (document.getElementById('editSmtpPass')) document.getElementById('editSmtpPass').value = '';
  const res = await fetch('/api/settings/test-email', { method: 'POST' });
  const data = await res.json();
  if (!res.ok) { alert(data.detail || 'Test email failed.'); return; }
  const via = data.sent_smtp ? 'Direct Gmail SMTP' : 'Google Sheets Webhook';
  alert('Test alert email sent via ' + via + '!');
  loadState(false);
}

checkAuthAndBoot();
