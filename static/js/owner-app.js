let STATE = { db_version: 0, pricing_rates: [] };
let EDITABLE_RATES = [];

function escHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
}

async function loadOfficeState() { return loadOwnerState(); }
async function loadUnbilledItems() {}

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

// Override switchSetupPane to handle all 4 Owner/Admin panes + sidebar highlight
window.switchSetupPane = function (pane) {
  ['hours', 'rates', 'facility', 'sysadmin'].forEach(p => {
    const el = document.getElementById('setupPane-' + p);
    if (el) el.hidden = (p !== pane);
  });
  document.querySelectorAll('[data-spane]').forEach(b => {
    b.classList.toggle('active', b.getAttribute('data-spane') === pane);
  });
  window.scrollTo({ top: 0, behavior: 'smooth' });
};

async function submitLoginPin() {
  const pin = document.getElementById('loginPinInput').value;
  const res = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pin }) });
  if (!res.ok) { document.getElementById('loginError').hidden = false; return; }
  document.getElementById('loginError').hidden = true;
  bootOwnerPage();
}

async function loadOwnerState() {
  const res = await fetch('/api/bootstrap');
  if (res.status === 401) { bootOwnerPage(); return; }
  STATE = await res.json();
  EDITABLE_RATES = (STATE.pricing_rates || []).map(p => ({ ...p, isNew: false }));
  renderRateScheduleTable();
  await renderOperatingScheduleTab();
}

async function bootOwnerPage() {
  const params = new URLSearchParams(location.search);
  const pane = params.get('pane') || params.get('tab');
  const res = await fetch('/api/auth/status');
  const auth = await res.json();
  if (!auth.authenticated) {
    document.getElementById('loginScreen').hidden = false;
    document.getElementById('appShell').hidden = true;
    return;
  }
  if (auth.user && auth.user.role === 'CREW') {
    location.href = '/ops';
    return;
  }
  document.getElementById('loginScreen').hidden = true;
  document.getElementById('appShell').hidden = false;
  if (window.syncAuthAndRoleUI) await window.syncAuthAndRoleUI(auth.user, false);
  await loadOwnerState();
  if (pane && ['hours', 'rates', 'facility', 'sysadmin'].includes(pane)) window.switchSetupPane(pane);
}

bootOwnerPage();
