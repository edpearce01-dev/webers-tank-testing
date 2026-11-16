let STATE = { db_version: 0, today: '', selected_date: '', customers_summary: [], dates_summary: [], records: [] };
let CURRENT_TAB = 'cabinet';

function escHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
}

function switchPrintTab(tab) {
  CURRENT_TAB = tab;
  document.querySelectorAll('main.ops-workspace > section').forEach(s => { s.hidden = true; });
  const el = document.getElementById('tab-' + tab);
  if (el) el.hidden = false;
  document.querySelectorAll('.rail-btn, .bnav-btn').forEach(b => {
    b.classList.toggle('active', b.getAttribute('data-tab') === tab);
  });
  if (tab === 'cabinet') loadFilingCabinet();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function stepPrintDate(delta) {
  const cur = document.getElementById('globalDate').value || STATE.selected_date || STATE.today;
  if (!cur) return;
  const [y, m, d] = cur.split('-').map(Number);
  const dt = new Date(y, m - 1, d + delta);
  document.getElementById('globalDate').value = `${dt.getFullYear()}-${String(dt.getMonth()+1).padStart(2,'0')}-${String(dt.getDate()).padStart(2,'0')}`;
  changePrintDate();
}
function jumpPrintToday() { sessionStorage.removeItem('webers_working_date'); document.getElementById('globalDate').value = STATE.today || ''; changePrintDate(); }
function changePrintDate() { const d = document.getElementById('globalDate').value; if (d) sessionStorage.setItem('webers_working_date', d); loadPrintState(); }
function jumpToDate(d) { document.getElementById('globalDate').value = d; sessionStorage.setItem('webers_working_date', d); loadPrintState(); }

async function submitLoginPin() {
  const pin = document.getElementById('loginPinInput').value;
  const res = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pin }) });
  if (!res.ok) { document.getElementById('loginError').hidden = false; return; }
  document.getElementById('loginError').hidden = true;
  bootPrintVault();
}

async function triggerManualSync() {
  document.getElementById('syncBtnTop').textContent = 'Syncing...';
  const res = await fetch('/api/sync-now', { method: 'POST' });
  const d = await res.json();
  alert(res.ok ? (d.synced ? `Synced ${d.pushed} item(s) to Google Sheets!` : d.reason) : d.detail);
  loadPrintState();
}

async function loadPrintState() {
  const wDate = document.getElementById('globalDate').value || '';
  const res = await fetch('/api/bootstrap' + (wDate ? `?work_date=${encodeURIComponent(wDate)}` : ''));
  if (res.status === 401) { bootPrintVault(); return; }
  STATE = await res.json();
  document.getElementById('globalDate').value = STATE.selected_date;
  document.getElementById('pdfDateLabel').textContent = STATE.selected_date;
  document.getElementById('pdfSheetNo').value = STATE.binder_state?.active_sheet_no || STATE.starting_sheet_no || 8095;
  document.getElementById('syncBtnTop').textContent = STATE.has_sheets_webhook ? (STATE.unsynced_count ? `Sync (${STATE.unsynced_count})` : 'Synced') : 'Local';
  if (STATE.master_sheet_url && document.getElementById('railMasterSheetBtn')) document.getElementById('railMasterSheetBtn').href = STATE.master_sheet_url;

  const doneToday = (STATE.records || []).filter(r => r.status === 'COMPLETED' && r.hydro_disposition === 'A');
  document.getElementById('stickerCount').value = Math.max(1, doneToday.length);
  const allDayCusts = [...new Set((STATE.records || []).map(r => r.customer_name))];
  document.getElementById('pdfCustomerFilter').innerHTML = '<option value="">-- All Customers on Selected Date --</option>' + allDayCusts.map(c => `<option value="${escHtml(c)}">${escHtml(c)}</option>`).join('');
  document.getElementById('claimSlipCustomerSel').innerHTML = (STATE.customers_summary || []).length
    ? STATE.customers_summary.map(c => `<option value="${escHtml(c.customer_name)}">${escHtml(c.customer_name)} (${c.ready_backroom_count || c.total_bottles} cyl)</option>`).join('')
    : '<option value="">No customer batches yet</option>';
  document.getElementById('datesListTable').innerHTML = (STATE.dates_summary || []).length
    ? STATE.dates_summary.map(d => `<tr><td><strong>${escHtml(d.work_date)}</strong></td><td>${d.customer_count}</td><td>${d.completed_bottles} / ${d.total_bottles}</td><td><button class="secondary" onclick="jumpToDate('${d.work_date}')">Load</button></td></tr>`).join('')
    : '<tr><td colspan="4">No test dates recorded yet.</td></tr>';
  await loadFilingCabinet();
}

async function bootPrintVault() {
  const params = new URLSearchParams(location.search);
  const initD = params.get('date') || sessionStorage.getItem('webers_working_date') || '';
  if (initD) document.getElementById('globalDate').value = initD;
  if (params.get('tab') === 'print' || params.get('tab') === 'stickers') CURRENT_TAB = 'print';
  const res = await fetch('/api/auth/status');
  const auth = await res.json();
  if (!auth.authenticated) {
    document.getElementById('loginScreen').hidden = false;
    document.getElementById('appShell').hidden = true;
    return;
  }
  document.getElementById('loginScreen').hidden = true;
  document.getElementById('appShell').hidden = false;
  if (window.syncAuthAndRoleUI) await window.syncAuthAndRoleUI(auth.user, false);
  await loadPrintState();
  switchPrintTab(CURRENT_TAB);
}

// --- 🗄️ 5-YEAR DOT AUDIT FILING CABINET ---
    let CAB_DATA = null;
    let CAB_DRAWER = 'sheets';

    function setCabinetDrawer(d) {
      CAB_DRAWER = d;
      document.querySelectorAll('#cabDrawerTabs .cab-tab').forEach(b => {
        b.classList.toggle('active', b.getAttribute('data-cdrawer') === d);
      });
      renderFilingCabinetActiveDrawer();
    }

    function downloadAuditZipPacket() {
      const yr = document.getElementById('cabYearSel')?.value || '';
      window.location.href = '/api/cabinet/audit-pack.zip' + (yr ? `?year=${encodeURIComponent(yr)}` : '');
    }

    async function loadFilingCabinet() {
      const yr = document.getElementById('cabYearSel')?.value || '';
      const res = await fetch('/api/cabinet/index' + (yr ? `?year=${encodeURIComponent(yr)}` : ''));
      if (!res.ok) return;
      CAB_DATA = await res.json();

      const sel = document.getElementById('cabYearSel');
      if (sel) {
        const cur = yr || '';
        sel.innerHTML = '<option value="">All Years (5-Yr Vault)</option>' +
          (CAB_DATA.available_years || []).map(y => `<option value="${escHtml(y)}" ${y === cur ? 'selected' : ''}>${escHtml(y)} Records</option>`).join('');
      }
      const st = CAB_DATA.stats || {};
      const nb = document.getElementById('navBadgeCabinet');
      if (nb) nb.textContent = st.total_sheets ? `${st.total_sheets} Sh` : 'Vault';
      document.getElementById('cabCntSheets').textContent = String((CAB_DATA.retest_sheets || []).length);
      document.getElementById('cabCntCals').textContent = String((CAB_DATA.calibration_days || []).length);
      document.getElementById('cabCntVis').textContent = String((CAB_DATA.visual_days || []).length);
      document.getElementById('cabCntBill').textContent = String((CAB_DATA.invoices || []).length + (CAB_DATA.tickets || []).length);
      document.getElementById('cabCntTc').textContent = String((CAB_DATA.timecard_months || []).length);

      renderFilingCabinetActiveDrawer();
    }

    function renderFilingCabinetActiveDrawer() {
      const host = document.getElementById('cabinetDrawerBody');
      if (!host || !CAB_DATA) return;
      const q = (document.getElementById('cabSearchInput')?.value || '').trim().toLowerCase();
      const matchQ = (...vals) => !q || vals.some(v => String(v || '').toLowerCase().includes(q));

      if (CAB_DRAWER === 'sheets') {
        const rows = (CAB_DATA.retest_sheets || []).filter(s => matchQ('sheet #' + s.sheet_no, s.sheet_no, s.customers, s.first_date, s.last_date, s.first_sn, s.last_sn));
        host.innerHTML = rows.length ? rows.map(s => {
          const span = s.first_date === s.last_date ? s.last_date : `${s.first_date} → ${s.last_date}`;
          return `<div class="rec-item">
            <div class="rec-main">
              <div class="rec-title">
                <span style="font-size:0.98rem;color:var(--forest);">📖 Form 393-001 • <strong>Sheet #${s.sheet_no}</strong></span>
                <span class="${s.sealed ? 'pass' : 'sub-badge'}">${s.sealed ? '✅ Complete 12/12 Binder Sheet' : `⏳ In Progress (${s.row_count}/12 Rows)`}</span>
                ${s.condemn_count ? `<span class="rush-badge">⛔ ${s.condemn_count} Condemned</span>` : ''}
              </div>
              <div class="rec-sub">
                Dates: <strong>${escHtml(span)}</strong> • Passed: <strong>${s.pass_count}</strong> • Customers: ${escHtml(s.customers || '—')} • Serials: ${escHtml(s.first_sn)}..${escHtml(s.last_sn)}
              </div>
            </div>
            <div class="rec-actions">
              <a class="primary" style="width:auto;margin:0;padding:8px 14px;" href="/api/pdf/retest?sheet_no=${s.sheet_no}&by_sheet=true" target="_blank">📄 Open Sheet #${s.sheet_no} PDF</a>
            </div>
          </div>`;
        }).join('') : '<div style="color:var(--muted);font-size:0.86rem;padding:10px 2px;">No Form 393-001 hydro retest sheets match yet — sheets file here automatically as cylinders complete on Station 3.</div>';
        return;
      }

      if (CAB_DRAWER === 'cals') {
        const rows = (CAB_DATA.calibration_days || []).filter(c => matchQ(c.test_date, c.calibrated_serial, c.short_cal_psi, c.tall_cal_psi));
        host.innerHTML = rows.length ? rows.map(c => `
          <div class="rec-item">
            <div class="rec-main">
              <div class="rec-title">
                <span>📊 Daily Verification — <strong>${escHtml(c.test_date)}</strong></span>
                <span class="${c.all_passed ? 'pass' : 'fail'}">${c.all_passed ? '✓ CERTIFIED (±1.0%)' : '⚠ CHECK LOG'}</span>
              </div>
              <div class="rec-sub">
                Master Cyl: <strong>${escHtml(c.calibrated_serial)}</strong> • Short Jacket: <strong>${c.short_cal_psi ? c.short_cal_psi + ' PSI' : 'Not Run'}</strong> • Tall Jacket: <strong>${c.tall_cal_psi ? c.tall_cal_psi + ' PSI' : 'Not Run'}</strong> (${c.step_count} ladder steps • Last ${escHtml(c.last_time)})
              </div>
            </div>
            <div class="rec-actions">
              <a class="primary" style="width:auto;margin:0;padding:8px 14px;" href="/api/pdf/verification?work_date=${encodeURIComponent(c.test_date)}" target="_blank">📄 Open Calibration PDF</a>
            </div>
          </div>`).join('') : '<div style="color:var(--muted);font-size:0.86rem;padding:10px 2px;">No daily calibration sheets filed in this filter yet.</div>';
        return;
      }

      if (CAB_DRAWER === 'visual') {
        const vRows = (CAB_DATA.visual_days || []).filter(v => matchQ(v.work_date, v.customers));
        const cRows = (CAB_DATA.condemned_records || []).filter(c => matchQ(c.work_date, c.serial_number, c.customer_name, c.dot_spec, c.condemn_action));
        host.innerHTML = `
          <h4 style="margin:4px 0 8px;color:var(--forest);">🔍 Daily 7-Point &amp; Propane Visual Inspection Logs</h4>
          ${vRows.length ? vRows.map(v => `
            <div class="rec-item">
              <div class="rec-main">
                <div class="rec-title">
                  <span>🔍 Visual Inspection Log — <strong>${escHtml(v.work_date)}</strong></span>
                  <span class="sub-badge">${v.total_count} Inspected</span>
                  ${v.propane_count ? `<span class="sub-badge" style="background:#fef3c7;color:#92400e;">🔥 ${v.propane_count} Propane</span>` : ''}
                  ${v.fail_count ? `<span class="rush-badge">⛔ ${v.fail_count} Condemned</span>` : ''}
                </div>
                <div class="rec-sub">Customers: ${escHtml(v.customers || '—')}</div>
              </div>
              <div class="rec-actions">
                <a class="secondary" style="font-weight:800;" href="/api/pdf/visual?work_date=${encodeURIComponent(v.work_date)}" target="_blank">📄 Open Visual Log PDF</a>
              </div>
            </div>`).join('') : '<div style="color:var(--muted);font-size:0.85rem;margin-bottom:12px;">No visual logs in this filter yet.</div>'}

          <h4 style="margin:16px 0 8px;color:#991b1b;">⛔ Condemned / Rejected Cylinders Register (49 CFR §180.205(i))</h4>
          ${cRows.length ? cRows.map(c => `
            <div class="rec-item" style="border-left:4px solid #dc2626;">
              <div class="rec-main">
                <div class="rec-title"><span>SN <strong>${escHtml(c.serial_number)}</strong> — ${escHtml(c.customer_name)}</span><span class="fail">Code ${escHtml(c.hydro_disposition)}</span></div>
                <div class="rec-sub">${escHtml(c.work_date)}${c.sheet_no ? ' • Sheet #' + c.sheet_no : ''} • ${escHtml(c.dot_spec)}-${c.service_psi} • Action: <strong>${escHtml(c.condemn_action || 'Condemned')}</strong>${c.notes ? ' • ' + escHtml(c.notes) : ''}</div>
              </div>
            </div>`).join('') : '<div style="color:var(--muted);font-size:0.85rem;">Zero condemned cylinders recorded in this period.</div>'}`;
        return;
      }

      if (CAB_DRAWER === 'billing') {
        const iRows = (CAB_DATA.invoices || []).filter(i => matchQ(i.invoice_no, i.customer_name, i.invoice_date, i.work_date, i.status));
        const tRows = (CAB_DATA.tickets || []).filter(t => matchQ(t.ticket_no, t.customer_name, t.work_date, t.service_requested));
        host.innerHTML = `
          <h4 style="margin:4px 0 8px;color:var(--forest);">💲 Saved Customer Invoices (${iRows.length})</h4>
          ${iRows.length ? iRows.map(iv => `
            <div class="rec-item">
              <div class="rec-main">
                <div class="rec-title"><span><strong>${escHtml(iv.invoice_no)}</strong> — ${escHtml(iv.customer_name)}</span><span class="${iv.status === 'PAID' ? 'pass' : 'dwell-badge'}">$${Number(iv.total_amount || 0).toFixed(2)} (${escHtml(iv.status)})</span></div>
                <div class="rec-sub">Date: ${escHtml(iv.invoice_date)} • ${iv.line_count || 1} line(s)${iv.payment_method ? ' • ' + escHtml(iv.payment_method) : ''}</div>
              </div>
              <div class="rec-actions">
                <a class="secondary" style="font-weight:800;" href="/api/pdf/invoice/${iv.id}" target="_blank">📄 Invoice PDF</a>
                <a class="secondary" href="/api/pdf/claim-slip?customer=${encodeURIComponent(iv.customer_name)}&work_date=${encodeURIComponent(iv.work_date)}" target="_blank">🏷️ Claim Slip</a>
              </div>
            </div>`).join('') : '<div style="color:var(--muted);font-size:0.85rem;margin-bottom:12px;">No invoices in this filter yet.</div>'}

          <h4 style="margin:16px 0 8px;color:var(--forest);">📥 24/7 Loading Dock &amp; Truck Tickets (${tRows.length})</h4>
          ${tRows.length ? tRows.slice(0, 60).map(t => `
            <div class="rec-item">
              <div class="rec-main">
                <div class="rec-title"><span><strong>${escHtml(t.ticket_no)}</strong> — ${escHtml(t.customer_name)} (${t.bottle_count} Cyl)</span><span class="sub-badge">${escHtml(t.entry_type)}</span></div>
                <div class="rec-sub">${escHtml(t.created_at)} • ${escHtml(t.service_requested || '')}${t.tank_summary ? ' • ' + escHtml(t.tank_summary) : ''}</div>
              </div>
              <div class="rec-actions">
                <a class="secondary" href="/api/public/receipt/${encodeURIComponent(t.ticket_no)}.pdf?p=${encodeURIComponent(t.phone_last4)}" target="_blank">📄 Ticket PDF</a>
              </div>
            </div>`).join('') : '<div style="color:var(--muted);font-size:0.85rem;">No Loading Dock tickets logged in this filter yet.</div>'}`;
        return;
      }

      if (CAB_DRAWER === 'timecards') {
        const mRows = CAB_DATA.timecard_months || [];
        const pRows = (CAB_DATA.recent_punches || []).filter(p => matchQ(p.work_date, p.crew_name, p.crew_initials, p.notes));
        host.innerHTML = `
          <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:10px;">
            <h4 style="margin:0;color:var(--forest);">⏱️ Monthly Payroll Summaries</h4>
            <a class="secondary" data-owner-only="1" style="background:#ecfdf5;color:#065f46;border-color:#6ee7b7;font-weight:800;" href="/api/timeclock/export.csv?days=365">📥 Download Full-Year Payroll CSV</a>
          </div>
          <div class="huddle-kpis" style="margin-bottom:14px;">
            ${mRows.length ? mRows.map(m => `<div class="huddle-kpi"><small>${escHtml(m.ym)} (${m.worker_count} Crew)</small><strong>${m.total_hours} hrs (${m.shift_count} shifts)</strong></div>`).join('') : '<div style="color:var(--muted);font-size:0.84rem;">No completed shifts recorded yet.</div>'}
          </div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>Date</th><th>Worker</th><th>In</th><th>Out</th><th>Hours</th><th>Note</th></tr></thead>
              <tbody>
                ${pRows.length ? pRows.map(p => `<tr><td><strong>${escHtml(p.work_date)}</strong></td><td>${escHtml(p.crew_name)}</td><td>${escHtml(p.clock_in_at)}</td><td>${p.status === 'OPEN' ? '<span class="pass">ACTIVE</span>' : escHtml(p.clock_out_at)}</td><td><strong>${p.status === 'OPEN' ? 'Running' : (Number(p.duration_minutes || 0) / 60).toFixed(2) + ' hr'}</strong></td><td><small>${escHtml(p.notes || '')}</small></td></tr>`).join('') : '<tr><td colspan="6">No punches in filter.</td></tr>'}
              </tbody>
            </table>
          </div>`;
        return;
      }

      const st = CAB_DATA.stats || {};
      host.innerHTML = `
        <div class="split-2">
          <div class="card" style="margin:0;border-left:4px solid #10b981;">
            <h4 style="margin:0 0 6px;">📦 1-Click Inspector / Accountant Audit Packet</h4>
            <p style="font-size:0.84rem;color:var(--muted);margin:0 0 12px;">Downloads one organized <code>.ZIP</code> file containing every 12-row Form 393-001 PDF, Daily Calibration PDF, Visual Log PDF, Condemned Register CSV, Invoice PDF, Timecard CSV, and full SQLite database backup.</p>
            <button type="button" class="primary" style="margin:0;" onclick="downloadAuditZipPacket()">📦 Download Audit Packet (.ZIP)</button>
          </div>
          <div class="card" style="margin:0;border-left:4px solid #2563eb;">
            <h4 style="margin:0 0 6px;">☁️ Live Redundant Storage Status</h4>
            <p style="font-size:0.84rem;color:var(--muted);margin:0 0 12px;">
              <strong>Render Persistent Disk:</strong> Active (${st.total_sheets || 0} Retest Sheets, ${st.cal_days_count || 0} Cal Days)<br/>
              <strong>Google Sheets Cloud Sync:</strong> ${st.has_sheets_webhook ? (st.unsynced_count ? `${st.unsynced_count} row(s) queued` : '✅ 100% Synced') : 'Webhook not pasted in Shop Setup yet'}
            </p>
            <div style="display:flex;gap:8px;flex-wrap:wrap;">
              <a class="secondary" style="font-weight:800;" href="/api/admin/backup-db">💾 Download Raw .db Backup</a>
              ${st.master_sheet_url ? `<a class="secondary" style="background:#ecfdf5;color:#065f46;border-color:#6ee7b7;font-weight:800;" href="${escHtml(st.master_sheet_url)}" target="_blank">📊 Open Master Google Sheet ↗</a>` : ''}
            </div>
          </div>
        </div>`;
    }

// --- TAB 4: DOT PDFS, STICKERS & ZONE E CLAIM SLIPS ---
    function preprintFullSheet(compact) {
      const d = STATE.today || STATE.selected_date;
      window.open(`/api/pdf/stickers?work_date=${encodeURIComponent(d)}&count=${compact ? 60 : 30}&skip=0&compact=${compact}`, '_blank');
    }

    function printClaimSlipFromTab() {
      const c = document.getElementById('claimSlipCustomerSel').value;
      if (!c) { alert('No customer batch available yet.'); return; }
      window.open(`/api/pdf/claim-slip?customer=${encodeURIComponent(c)}&work_date=${encodeURIComponent(STATE.selected_date)}`, '_blank');
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

bootPrintVault();
