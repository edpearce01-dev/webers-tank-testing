let STATE = {
      db_version: 0, today: '', selected_date: '', default_tax_rate: 6.0,
      pricing_rates: [], customers_summary: [], customers_rolodex: [],
      recalls: [], shop_supplies: [], invoices: [],
    };
    let CURRENT_TAB = 'invoice';
    let DRAFT_ITEMS = [];
    let EDITABLE_RATES = [];
    let INV_FILTER = 'ALL';
    let RECALL_FILTER = 'DUE';
    let SUPPLY_CAT = 'ALL';
    let INITIAL_BOOT = true;

    function escHtml(s) {
      return String(s ?? '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
    }

    function formatPhoneLive(el) {
      const d = el.value.replace(/\D/g, '').slice(0, 10);
      if (d.length >= 7) el.value = `${d.slice(0,3)}-${d.slice(3,6)}-${d.slice(6)}`;
      else if (d.length >= 4) el.value = `${d.slice(0,3)}-${d.slice(3)}`;
    }

    function switchOfficeTab(tab) {
      CURRENT_TAB = tab;
      document.querySelectorAll('main.ops-workspace > section').forEach(s => { s.hidden = true; });
      const target = document.getElementById('tab-' + tab);
      if (target) target.hidden = false;
      document.querySelectorAll('.rail-btn, .bnav-btn').forEach(b => {
        b.classList.toggle('active', b.getAttribute('data-tab') === tab);
      });
      if (tab === 'quick') setTimeout(() => document.getElementById('qsCustomer')?.focus(), 40);
      if (tab === 'dispatch') setTimeout(() => document.getElementById('tdCustomer')?.focus(), 40);
      if (tab === 'timeclock') loadTimeclockOfficeTab(14);
      if (tab === 'sms') loadSmsOutboxTab();
      if (CURRENT_TAB === 'cabinet') loadFilingCabinet();
      if (tab === 'setup') renderOperatingScheduleTab();
      if (tab === 'cabinet') loadFilingCabinet();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    function stepOfficeDate(delta) {
      const cur = document.getElementById('globalDate').value || STATE.selected_date || STATE.today;
      if (!cur) return;
      const [y, m, d] = cur.split('-').map(Number);
      const dt = new Date(y, m - 1, d + delta);
      const iso = `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
      document.getElementById('globalDate').value = iso;
      changeOfficeDate();
    }

    function jumpOfficeToday() {
      sessionStorage.removeItem('webers_working_date');
      document.getElementById('globalDate').value = STATE.today || '';
      changeOfficeDate();
    }

    async function bootOffice() {
      const params = new URLSearchParams(location.search);
      const initD = params.get('date') || sessionStorage.getItem('webers_working_date') || '';
      if (initD) document.getElementById('globalDate').value = initD;
      const tabParam = params.get('tab') || params.get('sec');
      if (tabParam) {
        if (tabParam === 'quick' || tabParam === 'dispatch') { location.replace('/schedule?tab=' + tabParam); return; }
        if (tabParam === 'print' || tabParam === 'cabinet') { location.replace('/print?tab=' + tabParam); return; }
        if (['rates', 'hours', 'setup', 'settings'].includes(tabParam)) {
          const pMap = { rates: 'rates', hours: 'hours', setup: 'hours', settings: 'facility' };
          location.replace('/owner?pane=' + pMap[tabParam]);
          return;
        }
        const cleanTab = tabParam === 'billing' ? 'invoice' : tabParam;
        if (['invoice', 'saved', 'rolodex', 'supplies', 'timeclock', 'sms'].includes(cleanTab)) {
          switchOfficeTab(cleanTab);
        }
      }

      const res = await fetch('/api/auth/status');
      const auth = await res.json();
      if (!auth.authenticated) {
        document.getElementById('loginScreen').hidden = false;
        document.getElementById('appShell').hidden = true;
        document.getElementById('loginPinInput').focus();
        return;
      }
      document.getElementById('loginScreen').hidden = true;
      document.getElementById('appShell').hidden = false;
      if (window.syncAuthAndRoleUI) await window.syncAuthAndRoleUI(auth.user, Boolean(window._justLoggedInToast));
      window._justLoggedInToast = false;
      await loadOfficeState();
      await fetchQsQuote();
      await fetchTdQuote();
      switchOfficeTab(CURRENT_TAB);
    }

    async function submitLoginPin() {
      const pin = document.getElementById('loginPinInput').value;
      const res = await fetch('/api/auth/login', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pin }),
      });
      if (!res.ok) { document.getElementById('loginError').hidden = false; return; }
      document.getElementById('loginError').hidden = true;
      window._justLoggedInToast = true;
      bootOffice();
    }

    async function logoutCrew() {
      await fetch('/api/auth/logout', { method: 'POST' });
      bootOffice();
    }

    function changeOfficeDate() {
      const d = document.getElementById('globalDate').value;
      if (d) sessionStorage.setItem('webers_working_date', d);
      document.getElementById('billDate').value = d;
      loadOfficeState();
    }

    function jumpToDate(d) {
      document.getElementById('globalDate').value = d;
      document.getElementById('billDate').value = d;
      loadOfficeState();
    }

    async function loadOfficeState() {
      const wDate = document.getElementById('globalDate').value || '';
      const res = await fetch('/api/bootstrap' + (wDate ? `?work_date=${encodeURIComponent(wDate)}` : ''));
      if (res.status === 401) { bootOffice(); return; }
      const prevVer = STATE.db_version;
      STATE = await res.json();

      document.getElementById('globalDate').value = STATE.selected_date;
      if (!document.getElementById('billDate').value) document.getElementById('billDate').value = STATE.selected_date;

      document.getElementById('pdfDateLabel').textContent = STATE.selected_date;
      document.getElementById('pdfSheetNo').value = STATE.binder_state?.active_sheet_no || STATE.starting_sheet_no;
      const syncTxt = STATE.has_sheets_webhook
        ? (STATE.unsynced_count ? `↻ Sync (${STATE.unsynced_count})` : '☁ Synced')
        : `Local (${STATE.unsynced_count})`;
      document.getElementById('syncBtnTop').textContent = syncTxt;

      const queued = (STATE.records || []).filter(r => r.status === 'QUEUED');
      const doneToday = (STATE.records || []).filter(r => r.status === 'COMPLETED');
      const openSched = (STATE.schedule_jobs || []).filter(j => j.status !== 'COMPLETED');
      const totalUnbilled = (STATE.customers_summary || []).reduce((s, c) => s + (c.unbilled_count || 0), 0);
      const lowCnt = STATE.low_stock_count || 0;

      document.getElementById('wsOpsBadge').textContent = queued.length ? `(${queued.length})` : '';
      document.getElementById('wsSchedBadge').textContent = openSched.length ? `(${openSched.length})` : '';
      document.getElementById('wsBillBadge').textContent = totalUnbilled ? `(${totalUnbilled})` : '';

      document.getElementById('navBadgeUnbilled').textContent = String(totalUnbilled);
      document.getElementById('navBadgeSaved').textContent = String(STATE.invoices.length);
      document.getElementById('navBadgeRolo').textContent = String(STATE.customers_rolodex.length);
      document.getElementById('navBadgeSupplies').textContent = lowCnt ? `⚠️ ${lowCnt}` : '✓';
      document.getElementById('navBadgeSupplies').className = 'nav-badge ' + (lowCnt ? 'amber' : 'green');

      document.getElementById('mobBadgeUnbilled').hidden = !totalUnbilled;
      document.getElementById('mobBadgeUnbilled').textContent = totalUnbilled;
      document.getElementById('mobBadgeSupplies').hidden = !lowCnt;
      document.getElementById('mobBadgeSupplies').textContent = lowCnt;

      document.getElementById('stickerCount').value = Math.max(1, doneToday.filter(r => r.hydro_disposition === 'A').length);

      const allDayCusts = [...new Set((STATE.records || []).map(r => r.customer_name))];
      document.getElementById('pdfCustomerFilter').innerHTML = '<option value="">-- All Customers on Selected Date --</option>' +
        allDayCusts.map(c => `<option value="${escHtml(c)}">${escHtml(c)}</option>`).join('');

      document.getElementById('claimSlipCustomerSel').innerHTML = STATE.customers_summary.length
        ? STATE.customers_summary.map(c => `<option value="${escHtml(c.customer_name)}">${escHtml(c.customer_name)} (${c.ready_backroom_count || c.total_bottles} cyl)</option>`).join('')
        : '<option value="">No customer batches yet</option>';

      document.getElementById('datesListTable').innerHTML = (STATE.dates_summary || []).length
        ? STATE.dates_summary.map(d => `
          <tr>
            <td><strong>${escHtml(d.work_date)}</strong></td><td>${d.customer_count}</td>
            <td>${d.completed_bottles} / ${d.total_bottles}</td>
            <td><button class="secondary" onclick="jumpToDate('${d.work_date}')">Load Date</button></td>
          </tr>`).join('')
        : '<tr><td colspan="4">No test dates recorded yet.</td></tr>';

      const params = new URLSearchParams(location.search);
      const urlCust = params.get('customer');
      const billSel = document.getElementById('billCustomer');
      const firstUnbilled = (STATE.customers_summary.find(c => c.unbilled_count > 0) || {}).customer_name || '';
      const targetCust = INITIAL_BOOT ? (urlCust || firstUnbilled || (STATE.customers_summary[0]?.customer_name || '')) : billSel.value;

      billSel.innerHTML = STATE.customers_summary.length
        ? STATE.customers_summary.map(c =>
            `<option value="${escHtml(c.customer_name)}" ${c.customer_name === targetCust ? 'selected' : ''}>${escHtml(c.customer_name)} (${c.unbilled_count} unbilled)${c.tax_exempt ? ' • Tax-Exempt' : ''}</option>`
          ).join('')
        : '<option value="">No customers yet</option>';

      const unbilledCusts = STATE.customers_summary.filter(c => c.unbilled_count > 0);
      document.getElementById('unbilledPillsWrap').innerHTML = unbilledCusts.length
        ? `<small style="display:block;font-weight:800;color:var(--muted);margin-bottom:5px;">⚡ TAP UNBILLED CUSTOMER:</small><div class="pill-selector-bar" style="margin-bottom:4px;">` +
          unbilledCusts.map(c => `<button type="button" class="tap-pill ${c.customer_name === billSel.value ? 'active' : ''}" onclick="selectBillingCustomer('${c.customer_name.replace(/'/g, "\\'")}')">${escHtml(c.customer_name)} (${c.unbilled_count})</button>`).join('') +
          `</div>`
        : '';

      if (prevVer !== STATE.db_version) {
        EDITABLE_RATES = STATE.pricing_rates.map(p => ({ ...p, isNew: false }));
        renderRateScheduleTable();
      }
      const allZero = (STATE.pricing_rates || []).length > 0 && STATE.pricing_rates.every(r => !(Number(r.unit_price) > 0));
      document.getElementById('zeroRatesHint').hidden = !allZero;
      renderInvoiceRateChips();
      renderInvoicesList();
      renderRolodexDirectory();
      renderRecallsList();
      renderSuppliesGrid();
      renderOperatingScheduleTab();
      loadSmsOutboxTab();
      if (CURRENT_TAB === 'timeclock') loadTimeclockOfficeTab(14);
      renderQuickScheduleJobs();
      renderTruckDispatchList();

      if (INITIAL_BOOT) {
        INITIAL_BOOT = false;
        await loadUnbilledItems();
      }
    }


    // --- TAB 0: QUICK PHONE SCHEDULE (45-SECOND CALL COCKPIT) ---
    let QS_ACCT = 'WALK_IN';
    let QS_ARRIVE = 'BACKROOM';
    let QS_TANKS = {};
    let QS_LAST_QUOTE = null;

    function setQsAcct(acct) {
      QS_ACCT = acct;
      document.querySelectorAll('#qsAcctBar .tap-pill').forEach(b => {
        b.classList.toggle('active', b.getAttribute('data-acct') === acct);
      });
      const isOrg = acct !== 'WALK_IN';
      document.getElementById('qsContactWrap').hidden = !isOrg;
      document.getElementById('qsPoWrap').hidden = !isOrg;
      document.getElementById('qsNameLbl').textContent = acct === 'FIRE_EMS' ? 'Fire Dept / EMS Agency *' : (acct === 'COMMERCIAL' ? 'Company / Fleet Name *' : 'Customer Name *');
      updateQsScript();
    }

    function setQsArrive(mode) {
      QS_ARRIVE = mode;
      document.querySelectorAll('#qsArriveBar .tap-pill').forEach(b => {
        b.classList.toggle('active', b.getAttribute('data-mode') === mode);
      });
      document.getElementById('qsAddrWrap').hidden = (mode !== 'ROAD_PICKUP');
      document.getElementById('qsDateLbl').textContent = mode === 'ROAD_PICKUP' ? 'Scheduled Pickup Date' : 'Expected Drop Date';
      updateQsScript();
    }

    function setQsQuickDate(offsetDays) {
      const base = STATE.today || new Date().toISOString().slice(0, 10);
      const [y, m, d] = base.split('-').map(Number);
      const dt = new Date(y, m - 1, d + offsetDays);
      document.getElementById('qsDate').value = `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
      updateQsScript();
    }

    function setQsNextMonday() {
      const base = STATE.today || new Date().toISOString().slice(0, 10);
      const [y, m, d] = base.split('-').map(Number);
      const dt = new Date(y, m - 1, d);
      const add = (8 - dt.getDay()) % 7 || 7;
      dt.setDate(dt.getDate() + add);
      document.getElementById('qsDate').value = `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
      updateQsScript();
    }

    function onQsCustomerInput() {
      const val = (document.getElementById('qsCustomer').value || '').trim();
      const badge = document.getElementById('qsKnownBadge');
      if (!val) { badge.hidden = true; updateQsScript(); return; }
      const match = (STATE.customers_rolodex || []).find(c => c.customer_name.toUpperCase() === val.toUpperCase());
      if (match) {
        badge.hidden = false;
        if (match.phone) document.getElementById('qsPhone').value = match.phone;
        if (match.contact_person) document.getElementById('qsContact').value = match.contact_person;
        if (match.account_type) setQsAcct(match.account_type);
        if (match.street_address) {
          const fullA = [match.street_address, match.city, `${match.state || 'PA'} ${match.zip_code || ''}`.trim()].filter(Boolean).join(', ');
          document.getElementById('qsAddress').value = fullA;
          document.getElementById('qsMailAddr').value = fullA;
        }
      } else {
        badge.hidden = true;
        const up = val.toUpperCase();
        if (['VFD', ' FD', 'FIRE', 'EMS', 'RESCUE', 'AMBULANCE'].some(k => up.includes(k)) && QS_ACCT === 'WALK_IN') {
          setQsAcct('FIRE_EMS');
        }
      }
      updateQsScript();
    }

    function bumpQsTank(label, qty) {
      QS_TANKS[label] = (QS_TANKS[label] || 0) + qty;
      const tot = Object.values(QS_TANKS).reduce((a, b) => a + b, 0);
      document.getElementById('qsCount').value = tot;
      document.getElementById('qsSummary').value = Object.entries(QS_TANKS).map(([k, v]) => `${v}x ${k}`).join(', ');
      fetchQsQuote();
    }

    function clearQsTanks() {
      QS_TANKS = {};
      document.getElementById('qsCount').value = 1;
      document.getElementById('qsSummary').value = '';
      fetchQsQuote();
    }

    async function fetchQsQuote() {
      const cnt = Math.max(1, parseInt(document.getElementById('qsCount').value || '1', 10));
      try {
        const res = await fetch('/api/schedule/quote?bottles=' + cnt);
        if (!res.ok) return;
        QS_LAST_QUOTE = await res.json();
        document.getElementById('qsBacklogBadge').textContent = `Shop Backlog: ${QS_LAST_QUOTE.total_backlog_bottles} Cyl`;
        document.getElementById('qsStdDateHint').textContent = `🟢 Std Ready: ~${QS_LAST_QUOTE.standard_promise_label}`;
        document.getElementById('qsRushDateHint').textContent = `(Ready ~${QS_LAST_QUOTE.rush_promise_label})`;
        applyQsQuoteDates();
      } catch (_) {}
    }

    function applyQsQuoteDates() {
      if (QS_LAST_QUOTE) {
        const isRush = document.getElementById('qsRush').checked;
        document.getElementById('qsDueDate').value = isRush ? QS_LAST_QUOTE.rush_promise_date : QS_LAST_QUOTE.standard_promise_date;
      }
      updateQsScript();
    }

    function updateQsScript() {
      const cust = (document.getElementById('qsCustomer')?.value || '').trim() || '[Caller]';
      const cnt = parseInt(document.getElementById('qsCount')?.value || '1', 10);
      const sum = (document.getElementById('qsSummary')?.value || '').trim();
      const ph = (document.getElementById('qsPhone')?.value || '').trim() || 'your cell';
      const isRush = document.getElementById('qsRush')?.checked;
      const readyLbl = QS_LAST_QUOTE
        ? (isRush ? QS_LAST_QUOTE.rush_promise_label : QS_LAST_QUOTE.standard_promise_label)
        : (document.getElementById('qsDueDate')?.value || 'soonest window');
      const dVal = document.getElementById('qsDate')?.value || 'today';
      const addr = (document.getElementById('qsAddress')?.value || '').trim() || 'your station/shop';
      const tankPhrase = sum ? `${cnt} cylinder${cnt > 1 ? 's' : ''} (${sum})` : `${cnt} cylinder${cnt > 1 ? 's' : ''}`;

      let msg = '';
      if (QS_ARRIVE === 'BACKROOM') {
        msg = `Got you on the board for ${escHtml(tankPhrase)}, ${escHtml(cust)}. Drop them at our 24/7 Loading Dock at 3086 Legionville Rd anytime — just scan the dock QR sign and tap Loading Dock Drop-Off. Estimated${isRush ? ' RUSH' : ''} ready date is ~${escHtml(readyLbl)} (testing only — returned clean, dry & empty). We will text ${escHtml(ph)} the second they hit the pickup shelf!`;
      } else if (QS_ARRIVE === 'ROAD_PICKUP') {
        msg = `Got you scheduled for a pickup of ${escHtml(tankPhrase)} on ${escHtml(dVal)} at ${escHtml(addr)}. Estimated${isRush ? ' RUSH' : ''} ready date is ~${escHtml(readyLbl)} (testing only, returned dry & empty). We will text ${escHtml(ph)} when complete!`;
      } else {
        msg = `Got you down to drop off ${escHtml(tankPhrase)} on ${escHtml(dVal)} at 3086 Legionville Rd (Mon/Fri 9–4, Tue–Thu 9–2, or 24/7 Loading Dock anytime). Estimated${isRush ? ' RUSH' : ''} ready date is ~${escHtml(readyLbl)} (testing only, no refills). We will text ${escHtml(ph)} as soon as they are ready!`;
      }
      const box = document.getElementById('qsScriptBox');
      if (box) box.innerHTML = msg;
    }

    function renderQuickScheduleJobs() {
      const suggest = document.getElementById('qsRoloSuggest');
      if (suggest) {
        const names = [...new Set([
          ...(STATE.customers_rolodex || []).map(c => c.customer_name),
          ...(STATE.customers_summary || []).map(c => c.customer_name),
        ])].sort((a, b) => a.localeCompare(b));
        suggest.innerHTML = names.map(n => `<option value="${escHtml(n)}">`).join('');
      }
      if (!document.getElementById('qsDate').value) {
        document.getElementById('qsDate').value = STATE.today || STATE.selected_date;
      }
      const openSched = (STATE.schedule_jobs || []).filter(j => j.status !== 'COMPLETED');
      const el = document.getElementById('qsOpenJobsList');
      if (!el) return;
      el.innerHTML = openSched.length
        ? openSched.slice(0, 14).map(j => {
            const ico = j.job_type === 'ROAD_PICKUP' ? '🚚 Pickup' : '📌 Drop';
            return `
              <div class="rec-item">
                <div class="rec-main">
                  <div class="rec-title">
                    <span>${ico}: ${escHtml(j.customer_name)} (${j.bottle_count})</span>
                    ${(j.rush_fee || j.priority === 'RUSH') ? '<span class="rush-badge">🔥 RUSH</span>' : ''}
                  </div>
                  <div class="rec-sub">
                    Sched: <strong>${escHtml(j.scheduled_date)}</strong>${j.due_date ? ` • Est Ready: <strong>${escHtml(j.due_date)}</strong>` : ''}${j.contact_person ? ` • 👤 ${escHtml(j.contact_person)}` : ''}${j.customer_phone ? ` • 📞 ${escHtml(j.customer_phone)}` : ''}${j.tank_summary ? ` • ${escHtml(j.tank_summary)}` : ''}
                  </div>
                </div>
                <div class="rec-actions">
                  <a class="secondary" style="background:#ecfdf5;color:#065f46;border-color:#6ee7b7;" href="/ops?job=${j.id}">↳ Prep</a>
                  <button class="secondary" onclick="completeQuickJob(${j.id})">✓</button>
                  <button class="secondary" style="color:#b91c1c;" onclick="deleteQuickJob(${j.id})">✕</button>
                </div>
              </div>`;
          }).join('')
        : '<div style="color:var(--muted);font-size:0.86rem;padding:8px 0;">No open phone bookings right now — ready for the next call!</div>';
    }

    async function completeQuickJob(id) {
      await fetch(`/api/schedule/jobs/${id}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: 'COMPLETED' }),
      });
      loadOfficeState();
    }

    async function deleteQuickJob(id) {
      if (!confirm('Remove this scheduled call booking?')) return;
      await fetch(`/api/schedule/jobs/${id}`, { method: 'DELETE' });
      loadOfficeState();
    }

    async function submitQuickSchedule(sendSmsAfter = false) {
      const cust = document.getElementById('qsCustomer').value.trim();
      const phone = document.getElementById('qsPhone').value.trim();
      const contact = document.getElementById('qsContact').value.trim();
      const cnt = Math.max(1, parseInt(document.getElementById('qsCount').value || '1', 10));
      const isRush = document.getElementById('qsRush').checked;
      const schedDate = document.getElementById('qsDate').value || STATE.today || STATE.selected_date;
      const dueDate = document.getElementById('qsDueDate').value || schedDate;
      const summary = document.getElementById('qsSummary').value.trim();
      const addr = document.getElementById('qsAddress').value.trim();

      if (isRush && cnt > 12 && !confirm('Paid Rush has a 12-cylinder limit (' + cnt + ' entered). Override limit for this call?')) return;
      if (!cust || phone.replace(/\D/g, '').length < 10) {
        alert('Enter Caller/Dept Name and a 10-digit Phone Number so Ready texts work.');
        return;
      }
      if (QS_ACCT !== 'WALK_IN' && contact.length < 2) {
        alert('Enter Caller Name for Fire/EMS or Commercial bookings.');
        document.getElementById('qsContact').focus();
        return;
      }
      if (QS_ARRIVE === 'ROAD_PICKUP' && !addr) {
        alert('Enter the pickup street or station address.');
        document.getElementById('qsAddress').focus();
        return;
      }

      const rawNote = document.getElementById('qsNotes').value.trim();
      const modeNote = QS_ARRIVE === 'BACKROOM' ? '24/7 Backroom Self-Drop' : '';
      const fullNotes = [modeNote, rawNote].filter(Boolean).join(' | ');

      const btn = document.getElementById('btnQsSave');
      btn.disabled = true;
      try {
        const res = await fetch('/api/schedule/jobs', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            job_type: QS_ARRIVE === 'ROAD_PICKUP' ? 'ROAD_PICKUP' : 'SHOP_BATCH',
            account_type: QS_ACCT,
            po_station: document.getElementById('qsPo').value.trim(),
            contact_person: contact,
            scheduled_date: schedDate,
            due_date: dueDate,
            customer_name: cust,
            customer_phone: phone,
            bottle_count: cnt,
            tank_summary: summary,
            valves_attached: true,
            needs_air_fill: false,
            priority: isRush ? 'RUSH' : 'STANDARD',
            rush_fee: isRush,
            holding_zone: QS_ARRIVE === 'ROAD_PICKUP' ? 'OUTBOUND_TRUCK' : 'ZONE_A_BACKROOM',
            location_notes: addr,
            street_address: document.getElementById('qsMailAddr').value.trim() || addr,
            notes: fullNotes,
          }),
        });
        const data = await res.json();
        if (!res.ok) { alert(data.detail || 'Could not save phone booking.'); return; }

        const banner = document.getElementById('qsSavedBanner');
        banner.hidden = false;
        banner.textContent = `✅ Logged ${cust} (${cnt} cyl • Est Ready ${dueDate}) — Screen cleared for next caller!`;
        setTimeout(() => { banner.hidden = true; }, 5000);

        if (sendSmsAfter) {
          const digits = phone.replace(/\D/g, '');
          const smsTxt = `Weber's Tank Testing: Got you down for ${cnt} cylinder(s)${summary ? ' (' + summary + ')' : ''}. ${QS_ARRIVE === 'ROAD_PICKUP' ? 'Scheduled pickup: ' + schedDate + '.' : 'Drop off anytime at our 24/7 Loading Dock at 3086 Legionville Rd, Baden PA 15005.'} Est ready: ~${dueDate} (testing only - returned clean, dry & empty). Questions? 724-266-8008.`;
          const sep = /iPad|iPhone|iPod/i.test(navigator.userAgent) ? '&' : '?';
          window.location.href = `sms:${digits}${sep}body=${encodeURIComponent(smsTxt)}`;
        }

        ['qsCustomer', 'qsPhone', 'qsContact', 'qsPo', 'qsMailAddr', 'qsSummary', 'qsAddress', 'qsNotes'].forEach(id => {
          document.getElementById(id).value = '';
        });
        document.getElementById('qsRush').checked = false;
        document.getElementById('qsKnownBadge').hidden = true;
        QS_TANKS = {};
        document.getElementById('qsCount').value = 1;
        setQsAcct('WALK_IN');
        setQsArrive('BACKROOM');
        await loadOfficeState();
        await fetchQsQuote();
        document.getElementById('qsCustomer').focus();
      } finally {
        btn.disabled = false;
      }
    }


    // --- TAB 0B: DEDICATED TRUCK PICKUP & DELIVERY DISPATCH ---
    let TD_MODE = 'ROAD_ROUNDTRIP';
    let TD_TANKS = {};
    let TD_LAST_QUOTE = null;

    function setTdMode(mode) {
      TD_MODE = mode;
      document.querySelectorAll('#tdModeBar .tap-pill').forEach(b => {
        b.classList.toggle('active', b.getAttribute('data-tmode') === mode);
      });
      updateTdScript();
    }

    function setTdQuickDate(offsetDays) {
      const base = STATE.today || new Date().toISOString().slice(0, 10);
      const [y, m, d] = base.split('-').map(Number);
      const dt = new Date(y, m - 1, d + offsetDays);
      document.getElementById('tdDate').value = `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
      updateTdScript();
    }

    function onTdCustomerInput() {
      const val = (document.getElementById('tdCustomer').value || '').trim();
      const badge = document.getElementById('tdKnownBadge');
      if (!val) { badge.hidden = true; updateTdScript(); return; }
      const match = (STATE.customers_rolodex || []).find(c => c.customer_name.toUpperCase() === val.toUpperCase());
      if (match) {
        badge.hidden = false;
        if (match.phone) document.getElementById('tdPhone').value = match.phone;
        if (match.contact_person) document.getElementById('tdContact').value = match.contact_person;
        if (match.street_address) {
          document.getElementById('tdAddress').value = [match.street_address, match.city, match.state, match.zip_code].filter(Boolean).join(', ');
        }
      } else {
        badge.hidden = true;
      }
      updateTdScript();
    }

    function bumpTdTank(label, qty) {
      TD_TANKS[label] = (TD_TANKS[label] || 0) + qty;
      const tot = Object.values(TD_TANKS).reduce((a, b) => a + b, 0);
      document.getElementById('tdCount').value = tot;
      document.getElementById('tdSummary').value = Object.entries(TD_TANKS).map(([k, v]) => `${v}x ${k}`).join(', ');
      fetchTdQuote();
    }

    function clearTdTanks() {
      TD_TANKS = {};
      document.getElementById('tdCount').value = 6;
      document.getElementById('tdSummary').value = '';
      fetchTdQuote();
    }

    async function fetchTdQuote() {
      const cnt = Math.max(1, parseInt(document.getElementById('tdCount')?.value || '6', 10));
      try {
        const res = await fetch('/api/schedule/quote?bottles=' + cnt);
        if (!res.ok) return;
        TD_LAST_QUOTE = await res.json();
        document.getElementById('tdStdHint').textContent = `🟢 Std Return: ~${TD_LAST_QUOTE.standard_promise_label}`;
        document.getElementById('tdRushHint').textContent = `(Return ~${TD_LAST_QUOTE.rush_promise_label})`;
        applyTdQuoteDates();
      } catch (_) {}
    }

    function applyTdQuoteDates() {
      if (TD_LAST_QUOTE) {
        const isRush = document.getElementById('tdRush').checked;
        document.getElementById('tdDueDate').value = isRush ? TD_LAST_QUOTE.rush_promise_date : TD_LAST_QUOTE.standard_promise_date;
      }
      updateTdScript();
    }

    function updateTdScript() {
      const cust = (document.getElementById('tdCustomer')?.value || '').trim() || '[Dept/Company]';
      const cnt = parseInt(document.getElementById('tdCount')?.value || '6', 10);
      const sum = (document.getElementById('tdSummary')?.value || '').trim();
      const addr = (document.getElementById('tdAddress')?.value || '').trim() || '[Station/Shop Address]';
      const ph = (document.getElementById('tdPhone')?.value || '').trim() || 'your phone';
      const runDate = document.getElementById('tdDate')?.value || 'scheduled date';
      const isRush = document.getElementById('tdRush')?.checked;
      const readyLbl = TD_LAST_QUOTE
        ? (isRush ? TD_LAST_QUOTE.rush_promise_label : TD_LAST_QUOTE.standard_promise_label)
        : (document.getElementById('tdDueDate')?.value || 'soonest window');
      const phrase = sum ? `${cnt} cylinders (${sum})` : `${cnt} cylinders`;

      let actionTxt = `pick up ${escHtml(phrase)} on ${escHtml(runDate)} at ${escHtml(addr)} and deliver them back around ~${escHtml(readyLbl)}`;
      if (TD_MODE === 'ROAD_PICKUP') actionTxt = `pick up ${escHtml(phrase)} on ${escHtml(runDate)} at ${escHtml(addr)} (ready for shop pickup ~${escHtml(readyLbl)})`;
      if (TD_MODE === 'ROAD_DELIVERY') actionTxt = `deliver ${escHtml(phrase)} back to ${escHtml(addr)} on ${escHtml(runDate)}`;

      const box = document.getElementById('tdScriptBox');
      if (box) {
        box.innerHTML = `"Got ${escHtml(cust)} on the truck dispatch board to ${actionTxt}. Reminder: testing facility only — cylinders return clean, dry & depressurized. We will text/call ${escHtml(ph)} before heading out!"`;
      }
    }

    function renderTruckDispatchList() {
      if (!document.getElementById('tdDate')?.value) {
        const dEl = document.getElementById('tdDate');
        if (dEl) dEl.value = STATE.today || STATE.selected_date;
      }
      const truckRuns = (STATE.schedule_jobs || []).filter(j => j.status !== 'COMPLETED' && j.job_type.startsWith('ROAD_'));
      const navB = document.getElementById('navBadgeDispatch');
      if (navB) navB.textContent = truckRuns.length ? String(truckRuns.length) : 'Truck';

      const el = document.getElementById('tdOpenRunsList');
      if (!el) return;
      el.innerHTML = truckRuns.length
        ? truckRuns.map(j => {
            const lbl = j.job_type === 'ROAD_ROUNDTRIP' ? '🔄 Pickup + Return' : (j.job_type === 'ROAD_PICKUP' ? '🚚 Pickup Only' : '📦 Delivery Only');
            const mapUrl = j.location_notes ? `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(j.location_notes)}` : '';
            return `
              <div class="rec-item">
                <div class="rec-main">
                  <div class="rec-title">
                    <span>${lbl}: ${escHtml(j.customer_name)} (${j.bottle_count})</span>
                    ${(j.rush_fee || j.priority === 'RUSH') ? '<span class="rush-badge">🔥 RUSH</span>' : ''}
                  </div>
                  <div class="rec-sub">
                    Run Date: <strong>${escHtml(j.scheduled_date)}</strong>${j.due_date ? ` • Return/Ready: <strong>${escHtml(j.due_date)}</strong>` : ''}${j.location_notes ? ` • 📍 ${escHtml(j.location_notes)}` : ''}${j.contact_person ? ` • 👤 ${escHtml(j.contact_person)}` : ''}${j.customer_phone ? ` • 📞 ${escHtml(j.customer_phone)}` : ''}
                  </div>
                </div>
                <div class="rec-actions">
                  ${mapUrl ? `<a class="secondary" href="${mapUrl}" target="_blank">📍 Map</a>` : ''}
                  <a class="secondary" style="background:#ecfdf5;color:#065f46;border-color:#6ee7b7;" href="/ops?job=${j.id}">↳ Scan Batch</a>
                  <button class="secondary" onclick="completeQuickJob(${j.id})">✓</button>
                  <button class="secondary" style="color:#b91c1c;" onclick="deleteQuickJob(${j.id})">✕</button>
                </div>
              </div>`;
          }).join('')
        : '<div style="color:var(--muted);font-size:0.86rem;padding:8px 0;">No open truck pickup/delivery runs scheduled right now.</div>';
    }

    async function submitTruckDispatch(sendSmsAfter = false) {
      const cust = document.getElementById('tdCustomer').value.trim();
      const contact = document.getElementById('tdContact').value.trim();
      const phone = document.getElementById('tdPhone').value.trim();
      const addr = document.getElementById('tdAddress').value.trim();
      const cnt = Math.max(1, parseInt(document.getElementById('tdCount').value || '1', 10));
      const isRush = document.getElementById('tdRush').checked;
      const schedDate = document.getElementById('tdDate').value || STATE.today || STATE.selected_date;
      const dueDate = document.getElementById('tdDueDate').value || schedDate;
      const summary = document.getElementById('tdSummary').value.trim();

      if (isRush && cnt > 12 && !confirm('Paid Rush has a 12-cylinder limit (' + cnt + ' entered). Override limit for this truck run?')) return;
      if (!cust || !contact || phone.replace(/\D/g, '').length < 10 || addr.length < 5) {
        alert('Enter Dept/Company Name, Contact Name, 10-Digit Phone, and Street Address.');
        return;
      }

      const up = cust.toUpperCase();
      const acct = ['VFD', ' FD', 'FIRE', 'EMS', 'RESCUE', 'AMBULANCE'].some(k => up.includes(k)) ? 'FIRE_EMS' : 'COMMERCIAL';
      const btn = document.getElementById('btnTdSave');
      btn.disabled = true;
      try {
        const res = await fetch('/api/schedule/jobs', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            job_type: TD_MODE,
            account_type: acct,
            po_station: document.getElementById('tdPo').value.trim(),
            contact_person: contact,
            scheduled_date: schedDate,
            due_date: dueDate,
            pickup_time: document.getElementById('tdPickupTime')?.value || '',
            dropoff_time: document.getElementById('tdDropoffTime')?.value || '',
            customer_name: cust,
            customer_phone: phone,
            bottle_count: cnt,
            tank_summary: summary,
            valves_attached: true,
            needs_air_fill: false,
            priority: isRush ? 'RUSH' : 'STANDARD',
            rush_fee: isRush,
            holding_zone: 'OUTBOUND_TRUCK',
            location_notes: addr,
            notes: TD_MODE === 'ROAD_ROUNDTRIP' ? 'Round-Trip Truck Pickup & Return Delivery' : '',
          }),
        });
        const data = await res.json();
        if (!res.ok) { alert(data.detail || 'Could not save truck dispatch.'); return; }

        const banner = document.getElementById('tdSavedBanner');
        banner.hidden = false;
        banner.textContent = `✅ Truck Run Logged: ${cust} (${cnt} cyl on ${schedDate}) — Cleared for next call!`;
        setTimeout(() => { banner.hidden = true; }, 5000);

        if (sendSmsAfter) {
          const digits = phone.replace(/\D/g, '');
          const smsTxt = `Weber's Tank Testing: Confirmed truck ${TD_MODE === 'ROAD_DELIVERY' ? 'delivery' : 'pickup'} for ${cust} (${cnt} cyl) at ${addr} on ${schedDate}. Est return/ready: ~${dueDate} (testing only - returned clean, dry & empty). Questions? 724-266-8008.`;
          const sep = /iPad|iPhone|iPod/i.test(navigator.userAgent) ? '&' : '?';
          window.location.href = `sms:${digits}${sep}body=${encodeURIComponent(smsTxt)}`;
        }

        ['tdCustomer', 'tdContact', 'tdPhone', 'tdPo', 'tdAddress', 'tdSummary'].forEach(id => {
          document.getElementById(id).value = '';
        });
        document.getElementById('tdRush').checked = false;
        document.getElementById('tdKnownBadge').hidden = true;
        TD_TANKS = {};
        document.getElementById('tdCount').value = 6;
        await loadOfficeState();
        await fetchTdQuote();
        document.getElementById('tdCustomer').focus();
      } finally {
        btn.disabled = false;
      }
    }
