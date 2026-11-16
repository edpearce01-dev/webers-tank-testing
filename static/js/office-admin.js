// --- UNIFIED SHOP SETUP HUB (HOURS & HOLIDAYS + PRICING + FACILITY) ---
    let OS_DATA = null;

    function switchSetupPane(pane) {
      ['hours', 'rates', 'facility'].forEach(p => {
        const el = document.getElementById('setupPane-' + p);
        if (el) el.hidden = (p !== pane);
      });
      document.querySelectorAll('#setupSubtabBar .setup-subtab').forEach(b => {
        b.classList.toggle('active', b.getAttribute('data-spane') === pane);
      });
    }

    function fmt12hJs(hhmm) {
      const m = String(hhmm || '').match(/^(\d{1,2}):(\d{2})$/);
      if (!m) return hhmm || '';
      const h = parseInt(m[1], 10), mn = parseInt(m[2], 10);
      const ap = h >= 12 ? 'PM' : 'AM';
      const h12 = h % 12 || 12;
      return `${h12}:${String(mn).padStart(2, '0')} ${ap}`;
    }

    function setClosureHoursMode(isSpecialOpen) {
      document.getElementById('clStaffed').value = isSpecialOpen ? '1' : '0';
      document.getElementById('clModeClosedBtn')?.classList.toggle('active', !isSpecialOpen);
      document.getElementById('clModeSpecialBtn')?.classList.toggle('active', isSpecialOpen);
      const wrap = document.getElementById('clSpecHrsWrap');
      if (wrap) wrap.hidden = !isSpecialOpen;
      if (isSpecialOpen) syncClosureTimePreview();
    }

    function syncClosureTimePreview() {
      const o = document.getElementById('clOpenTime')?.value || '09:00';
      const c = document.getElementById('clCloseTime')?.value || '12:00';
      const inp = document.getElementById('clSpecHrs');
      if (inp) inp.value = `${fmt12hJs(o)} – ${fmt12hJs(c)}`;
    }

    function resetClosureForm() {
      document.getElementById('clEditId').value = '';
      document.getElementById('closureFormHdr').textContent = '🏖️ Holiday, Vacation & Special Hours';
      document.getElementById('btnSaveClosure').textContent = '➕ Save Holiday / Special Hours';
      document.getElementById('btnCancelClosureEdit').hidden = true;
      ['clStart', 'clEnd', 'clTitle', 'clSpecHrs', 'clNote'].forEach(id => {
        const el = document.getElementById(id); if (el) el.value = '';
      });
      document.getElementById('clDockOpen').checked = true;
      setClosureHoursMode(false);
    }

    function editClosureRow(id) {
      const c = (OS_DATA?.upcoming_closures || []).find(x => x.id === id);
      if (!c) return;
      document.getElementById('clEditId').value = c.id;
      document.getElementById('closureFormHdr').textContent = `✎ Editing: ${c.title}`;
      document.getElementById('btnSaveClosure').textContent = '💾 Update Holiday / Special Hours';
      document.getElementById('btnCancelClosureEdit').hidden = false;
      document.getElementById('clStart').value = c.start_date || '';
      document.getElementById('clEnd').value = c.end_date || '';
      document.getElementById('clTitle').value = c.title || '';
      document.getElementById('clDockOpen').checked = Boolean(c.dock_open);
      document.getElementById('clNote').value = c.public_note || '';
      setClosureHoursMode(Boolean(c.staffed_open));
      if (c.special_hours) document.getElementById('clSpecHrs').value = c.special_hours;
      document.getElementById('clTitle').focus();
    }

    async function renderOperatingScheduleTab() {
      let d = STATE.operating_schedule || OS_DATA;
      if (!d || !Array.isArray(d.weekly_hours) || !d.weekly_hours.length) {
        try {
          const r = await fetch('/api/office/operating-schedule');
          if (r.ok) { d = await r.json(); STATE.operating_schedule = d; }
        } catch (_) {}
      }
      if (!d) return;
      OS_DATA = d;

      const msBtn = document.getElementById('railMasterSheetBtn');
      if (msBtn && (STATE.master_sheet_url || d.master_sheet_url)) {
        msBtn.href = STATE.master_sheet_url || d.master_sheet_url;
      }

      const ed = document.getElementById('weeklyHoursEditor');
      if (ed && !ed.contains(document.activeElement)) {
        ed.innerHTML = (d.weekly_hours || []).map((h, i) => `
          <div class="hours-day-row ${h.is_open ? '' : 'closed-day'}" id="ohRow_${i}">
            <strong>${escHtml(h.day_name)}</strong>
            <label class="inline-check" style="margin:0;font-size:0.8rem;">
              <input type="checkbox" id="ohOpen_${i}" ${h.is_open ? 'checked' : ''}
                onchange="document.getElementById('ohStart_${i}').disabled=!this.checked;document.getElementById('ohEnd_${i}').disabled=!this.checked;document.getElementById('ohRow_${i}').classList.toggle('closed-day',!this.checked)" />
              <span>${h.is_open ? 'Open' : 'Closed'}</span>
            </label>
            <input type="time" id="ohStart_${i}" value="${escHtml(h.open_time || '09:00')}" ${h.is_open ? '' : 'disabled'} />
            <input type="time" id="ohEnd_${i}" value="${escHtml(h.close_time || '16:00')}" ${h.is_open ? '' : 'disabled'} />
          </div>`).join('');
      }
      if (document.getElementById('osOpenGbpBtn')) {
        document.getElementById('osOpenGbpBtn').href = d.gbp_edit_url || 'https://business.google.com/';
      }
      if (document.getElementById('osGbpPreview')) document.getElementById('osGbpPreview').textContent = d.gbp_copy_text || '';

      // Populate Facility & Alerts Pane fields
      const setValIfIdle = (id, v) => {
        const el = document.getElementById(id);
        if (el && document.activeElement !== el) el.value = v ?? '';
      };
      setValIfIdle('ofAlertEmail', STATE.alert_email || '');
      setValIfIdle('ofResendFrom', STATE.resend_from_email || 'alerts@weberstanktesting.com');
      setValIfIdle('ofMasterSheetUrl', STATE.master_sheet_url || d.master_sheet_url || '');
      setValIfIdle('ofWebhookUrl', STATE.sheets_webhook_url || '');
      setValIfIdle('ofGbpUrl', d.gbp_edit_url || 'https://business.google.com/');
      setValIfIdle('ofCalSerial', STATE.calibrated_serial || '1SF11904');
      setValIfIdle('ofSheetNo', STATE.starting_sheet_no || 8095);
      setValIfIdle('ofDailyCap', STATE.daily_hydro_capacity || 24);
      setValIfIdle('ofTaxRate', STATE.default_tax_rate ?? 6.0);
      setValIfIdle('ofRinExp', STATE.rin_expires_date || '');
      setValIfIdle('ofGaugeExp', STATE.gauge_cert_expires_date || '');
      if (document.getElementById('ofTestingMode')) document.getElementById('ofTestingMode').checked = Boolean(STATE.testing_mode);
      if (document.getElementById('ofResendStatus')) document.getElementById('ofResendStatus').textContent = STATE.has_resend_key ? 'Configured ✓' : 'Not Set';
      if (document.getElementById('ofGeminiStatus')) document.getElementById('ofGeminiStatus').textContent = STATE.has_gemini_key ? 'Configured ✓' : 'Not Set';

      const clEl = document.getElementById('closuresListArea');
      if (clEl) {
        const cls = d.upcoming_closures || [];
        clEl.innerHTML = cls.length ? cls.map(c => `
          <div class="rec-item">
            <div class="rec-main">
              <div class="rec-title">
                <span>🏖️ ${escHtml(c.title)}</span>
                <span class="sub-badge">${escHtml(c.start_date)}${c.end_date !== c.start_date ? ' → ' + escHtml(c.end_date) : ''}</span>
              </div>
              <div class="rec-sub">
                <strong>${c.staffed_open ? '🕒 Special Hours: ' + escHtml(c.special_hours || 'Open') : '🚪 Staffed Counter Closed'}</strong>
                • ${c.dock_open ? '✅ 24/7 Dock Open' : '⛔ Dock Closed'}${c.public_note ? ' • ' + escHtml(c.public_note) : ''}
              </div>
            </div>
            <div class="rec-actions">
              <button class="secondary" onclick="editClosureRow(${c.id})">✎ Edit Hours</button>
              <button class="secondary" style="color:#b91c1c;" onclick="removeClosureWindow(${c.id})">✕</button>
            </div>
          </div>`).join('') : '<div style="color:var(--muted);font-size:0.84rem;">No upcoming holiday or special-hour dates set yet.</div>';
      }
    }

    async function saveOperatingSchedule() {
      const hrs = (OS_DATA?.weekly_hours || []).map((h, i) => ({
        day_of_week: h.day_of_week,
        day_name: h.day_name,
        is_open: Boolean(document.getElementById('ohOpen_' + i)?.checked),
        open_time: document.getElementById('ohStart_' + i)?.value || '09:00',
        close_time: document.getElementById('ohEnd_' + i)?.value || '16:00',
        dock_24_7: true,
        note: h.note || '',
      }));
      const res = await fetch('/api/office/operating-hours', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          weekly_hours: hrs,
          gbp_edit_url: document.getElementById('ofGbpUrl')?.value || undefined,
          master_sheet_url: document.getElementById('ofMasterSheetUrl')?.value || undefined,
        }),
      });
      if (!res.ok) { alert((await res.json()).detail || 'Save failed.'); return; }
      OS_DATA = await res.json();
      STATE.operating_schedule = OS_DATA;
      renderOperatingScheduleTab();
      alert('Weekly operating days & hours saved and live on weberstanktesting.com!');
    }

    function copyGbpHoursText() {
      const txt = document.getElementById('osGbpPreview')?.textContent || '';
      if (navigator.clipboard) navigator.clipboard.writeText(txt);
      alert('Formatted hours copied! Paste into your Google Business Profile.');
    }

    async function addClosureWindow() {
      const s = document.getElementById('clStart').value;
      const t = document.getElementById('clTitle').value.trim();
      if (!s || !t) { alert('Enter Start Date and Holiday / Event Title.'); return; }
      const editId = parseInt(document.getElementById('clEditId').value || '0', 10) || null;
      const staffedOpen = document.getElementById('clStaffed').value === '1';
      let specTxt = (document.getElementById('clSpecHrs')?.value || '').trim();
      if (staffedOpen && !specTxt) {
        syncClosureTimePreview();
        specTxt = (document.getElementById('clSpecHrs')?.value || '').trim();
      }
      const res = await fetch('/api/office/closures', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: editId,
          start_date: s,
          end_date: document.getElementById('clEnd').value || s,
          title: t,
          staffed_open: staffedOpen,
          special_hours: staffedOpen ? specTxt : '',
          dock_open: document.getElementById('clDockOpen').checked,
          public_note: document.getElementById('clNote').value,
        }),
      });
      if (!res.ok) { alert((await res.json()).detail || 'Could not save holiday/special hours.'); return; }
      OS_DATA = await res.json();
      STATE.operating_schedule = OS_DATA;
      resetClosureForm();
      renderOperatingScheduleTab();
    }

    async function removeClosureWindow(id) {
      if (!confirm('Remove this holiday / special hours notice?')) return;
      await fetch(`/api/office/closures/${id}`, { method: 'DELETE' });
      loadOfficeState();
    }

    async function saveFacilitySetupFromOffice(silent = false) {
      const body = {
        calibrated_serial: document.getElementById('ofCalSerial')?.value || '1SF11904',
        starting_sheet_no: parseInt(document.getElementById('ofSheetNo')?.value || '8095', 10),
        daily_hydro_capacity: parseInt(document.getElementById('ofDailyCap')?.value || '24', 10),
        default_tax_rate: parseFloat(document.getElementById('ofTaxRate')?.value || '6.0'),
        rin_expires_date: document.getElementById('ofRinExp')?.value || '',
        gauge_cert_expires_date: document.getElementById('ofGaugeExp')?.value || '',
        alert_email: document.getElementById('ofAlertEmail')?.value || '',
        resend_api_key: document.getElementById('ofResendKey')?.value || null,
        resend_from_email: document.getElementById('ofResendFrom')?.value || 'alerts@weberstanktesting.com',
        master_sheet_url: document.getElementById('ofMasterSheetUrl')?.value || '',
        sheets_webhook_url: document.getElementById('ofWebhookUrl')?.value || '',
        gbp_edit_url: document.getElementById('ofGbpUrl')?.value || '',
        gemini_api_key: document.getElementById('ofGeminiKey')?.value || null,
        testing_mode: Boolean(document.getElementById('ofTestingMode')?.checked),
      };
      const res = await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      if (!res.ok) { alert((await res.json()).detail || 'Could not save settings.'); return; }
      if (document.getElementById('ofResendKey')) document.getElementById('ofResendKey').value = '';
      if (document.getElementById('ofGeminiKey')) document.getElementById('ofGeminiKey').value = '';
      await loadOfficeState();
      if (!silent) alert('Shop setup & alert settings saved!');
    }

    async function sendOfficeTestEmail() {
      await saveFacilitySetupFromOffice(true);
      const res = await fetch('/api/settings/test-email', { method: 'POST' });
      const d = await res.json();
      if (!res.ok) { alert(d.detail || 'Could not send test email.'); return; }
      alert('Test alert email sent!');
    }

    async function wipePracticeDataFromOffice() {
      if (!confirm('ERASE ALL PRACTICE / TEST DATA?\n\nWipes practice calibrations, cylinders, drop-offs, schedule jobs, time punches, and invoices while keeping API keys, supplies, crew, hours, and pricing intact.')) return;
      const wipeCusts = confirm('Also clear current Customer Rolodex profiles?\n\nClick OK to clear Rolodex too, or Cancel to KEEP saved Customer Rolodex profiles.');
      const res = await fetch('/api/admin/wipe-test-data?wipe_customers=' + wipeCusts, { method: 'POST' });
      if (!res.ok) { alert((await res.json()).detail || 'Wipe failed.'); return; }
      alert('Practice data wiped clean!');
      loadOfficeState();
    }
    // --- TAB 8: CREW TIMECARDS & PAYROLL ---
    async function loadTimeclockOfficeTab(days = 14) {
      const res = await fetch(`/api/timeclock/report?days=${days}`);
      if (!res.ok) return;
      const d = await res.json();
      document.getElementById('tcSummaryCards').innerHTML = (d.summary || []).length
        ? d.summary.map(s => `<div class="huddle-kpi"><small>${escHtml(s.name)} (${escHtml(s.initials)})</small><strong>${s.hours} hrs (${s.shifts} shifts)</strong></div>`).join('')
        : '<div style="color:var(--muted);font-size:0.84rem;">No worker shifts logged in this window yet.</div>';

      document.getElementById('tcReportTable').innerHTML = (d.punches || []).length
        ? d.punches.map(p => {
            const hrs = (Number(p.duration_minutes || 0) / 60).toFixed(2);
            return `<tr>
              <td><strong>${escHtml(p.work_date)}</strong></td>
              <td>${escHtml(p.crew_name)} (${escHtml(p.crew_initials)})</td>
              <td>${escHtml(p.clock_in_at)}</td>
              <td>${p.status === 'OPEN' ? '<span class="pass">🟢 CLOCKED IN</span>' : escHtml(p.clock_out_at)}</td>
              <td><strong>${p.status === 'OPEN' ? 'Active' : hrs + ' hr'}</strong></td>
              <td><small>${escHtml(p.notes || '')}</small></td>
              <td>${d.is_boss ? `<button class="secondary" onclick="bossAdjustPunch(${p.id}, ${p.duration_minutes || 0})">✎ Mins</button> <button class="secondary" style="color:#b91c1c;" onclick="bossDeletePunch(${p.id})">✕</button>` : ''}</td>
            </tr>`;
          }).join('')
        : '<tr><td colspan="7">No punches recorded yet.</td></tr>';
    }

    async function bossAdjustPunch(id, curMins) {
      const v = prompt('Enter corrected total shift duration in minutes (e.g. 420 = 7.0 hrs):', String(curMins || 60));
      if (v === null) return;
      await fetch(`/api/timeclock/${id}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ duration_minutes: Math.max(0, parseInt(v || '0', 10)) }),
      });
      loadTimeclockOfficeTab(14);
    }

    async function bossDeletePunch(id) {
      if (!confirm('Delete this time punch?')) return;
      await fetch(`/api/timeclock/${id}`, { method: 'DELETE' });
      loadTimeclockOfficeTab(14);
    }

    // --- TAB 9: DEDICATED SHOP IPHONE CUSTOMER SMS QUEUE ---
    async function loadSmsOutboxTab() {
      const bridgeInput = document.getElementById('iphoneBridgeUrlInput');
      if (bridgeInput && STATE.sms_shortcut_key) {
        bridgeInput.value = `${location.origin}/api/public/iphone-sms-bridge?key=${STATE.sms_shortcut_key}`;
      }
      const res = await fetch('/api/sms-outbox');
      if (!res.ok) return;
      const rows = await res.json();
      const badge = document.getElementById('navBadgeSms');
      if (badge) badge.textContent = String(rows.length);
      const sep = /iPad|iPhone|iPod/i.test(navigator.userAgent) ? '&' : '?';
      document.getElementById('smsOutboxList').innerHTML = rows.length
        ? rows.map(r => {
            const digits = String(r.phone || '').replace(/\D/g, '');
            return `<div class="rec-item">
              <div class="rec-main">
                <div class="rec-title"><span>📲 ${escHtml(r.customer_name)} — ${escHtml(r.phone)}</span><span class="sub-badge">${escHtml(r.created_at)}</span></div>
                <div class="rec-sub">${escHtml(r.message)}</div>
              </div>
              <div class="rec-actions">
                <a class="primary" style="width:auto;margin:0;padding:7px 12px;" href="sms:${digits}${sep}body=${encodeURIComponent(r.message)}" onclick="ackSmsSent(${r.id})">📲 Send on iPhone</a>
                <button class="secondary" onclick="ackSmsSent(${r.id})">✓ Clear</button>
              </div>
            </div>`;
          }).join('')
        : '<div style="color:var(--muted);font-size:0.85rem;">No unsent customer text notifications in queue.</div>';
    }

    async function ackSmsSent(id) {
      await fetch(`/api/sms-outbox/${id}/ack`, { method: 'POST' });
      setTimeout(loadSmsOutboxTab, 350);
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

    // --- TAB 6: MASTER SERVICE RATES ---
    function renderRateScheduleTable() {
      document.getElementById('pricingList').innerHTML = EDITABLE_RATES.map((p, i) => `
        <div class="rate-edit-row">
          <div><input value="${escHtml(p.code)}" ${p.isNew ? '' : 'readonly style="background:#f1f5f9;font-weight:800;"'} placeholder="CODE_NAME" oninput="EDITABLE_RATES[${i}].code=this.value.toUpperCase()" /></div>
          <div class="rate-desc-col"><input value="${escHtml(p.description || '')}" placeholder="Service description" oninput="EDITABLE_RATES[${i}].description=this.value" /></div>
          <div><input type="number" step="0.01" value="${p.unit_price}" oninput="EDITABLE_RATES[${i}].unit_price=parseFloat(this.value||0)" /></div>
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
      loadOfficeState();
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
      await loadOfficeState();
      loadUnbilledItems();
    }

    async function triggerManualSync() {
      document.getElementById('syncBtnTop').textContent = '↻...';
      const res = await fetch('/api/sync-now', { method: 'POST' });
      const data = await res.json();
      alert(res.ok ? (data.synced ? `Synced ${data.pushed} item(s) to Google Sheets!` : data.reason) : data.detail);
      loadOfficeState();
    }

    setInterval(async () => {
      if (document.getElementById('appShell').hidden) return;
      try {
        const res = await fetch('/api/ping');
        if (res.status === 401) { bootOffice(); return; }
        if (res.ok) {
          const p = await res.json();
          if (p.db_version !== STATE.db_version) loadOfficeState();
        }
      } catch (_) {}
    }, 4000);

    bootOffice();
