// --- TAB 1: BILLING + AUTO REVALVE ADD-ONS + TAX-EXEMPT ---

    const SHORT_RATE_LABELS = {
      HYDRO_STD: 'Std Steel/Alum Hydro',
      HYDRO_SCBA: 'Carbon SCBA Hydro',
      HYDRO_PAINTBALL: 'Paintball HPA Hydro',
      VISUAL_ONLY: 'Visual Insp Only',
      PROPANE_VISUAL: 'Propane Visual',
      RUSH_FEE: 'Rush Service Fee',
      NECK_ORING: 'Neck O-Ring',
      VALVE_RELIEF: 'Burst Disc / Relief',
      INTERNAL_CLEAN: 'Internal Tumble/Clean',
    };

    function renderInvoiceRateChips() {
      const bar = document.getElementById('invoiceRateChipsBar');
      if (!bar) return;
      const rates = STATE.pricing_rates || [];
      bar.innerHTML = rates.map(r => {
        const shortLbl = SHORT_RATE_LABELS[r.code] || r.description || r.code;
        const safeDesc = (r.description || shortLbl).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
        const priceStr = `$${Number(r.unit_price || 0).toFixed(2)}`;
        return `<button type="button" class="inv-rate-chip" onclick="addCustomInvoiceLine('${escHtml(r.code)}', '${safeDesc}')">
          <span>+ ${escHtml(shortLbl)}</span><strong>${priceStr}</strong>
        </button>`;
      }).join('');
    }

    function appendManualInvoiceItem() {
      const descEl = document.getElementById('customLineDesc');
      const qtyEl = document.getElementById('customLineQty');
      const prEl = document.getElementById('customLinePrice');
      const desc = (descEl?.value || '').trim();
      if (!desc) { alert('Enter a description for the custom line item first.'); descEl?.focus(); return; }
      const qty = parseFloat(qtyEl?.value || '1') || 1;
      const price = parseFloat(prEl?.value || '0') || 0;
      DRAFT_ITEMS.push({ record_id: null, description: desc, qty, unit_price: price });
      descEl.value = '';
      qtyEl.value = '1';
      prEl.value = '';
      renderDraftInvoice();
      descEl.focus();
    }

    function openQuickRatesModal() {
      let m = document.getElementById('quickRatesModal');
      if (!m) {
        m = document.createElement('div'); m.id = 'quickRatesModal'; m.className = 'modal-bg';
        m.onclick = e => { if (e.target === m) m.hidden = true; };
        document.body.appendChild(m);
      }
      m.hidden = false;
      const rates = STATE.pricing_rates || [];
      m.innerHTML = `
        <div class="modal" style="max-width:580px;">
          <div class="card-hdr">
            <div><h3>⚙ Quick-Set Default Service Prices</h3><small style="color:var(--muted);">Saves to Master Rates &amp; refreshes current draft invoice automatically</small></div>
            <button type="button" class="secondary" onclick="document.getElementById('quickRatesModal').hidden=true">✖ Close</button>
          </div>
          <div style="display:flex;flex-direction:column;gap:8px;margin-bottom:12px;">
            ${rates.map((r, idx) => `
              <div style="display:grid;grid-template-columns:1fr 120px;gap:10px;align-items:center;">
                <div style="font-size:0.84rem;font-weight:700;">${escHtml(r.description || r.code)}</div>
                <input type="number" step="0.01" id="qrPrice_${idx}" class="mono" value="${Number(r.unit_price || 0)}" />
              </div>`).join('')}
          </div>
          <button type="button" class="primary" onclick="saveQuickRatesAndRefreshDraft()">💾 Save Prices &amp; Refresh Current Invoice</button>
        </div>`;
    }

    async function saveQuickRatesAndRefreshDraft() {
      const payload = (STATE.pricing_rates || []).map((r, idx) => ({
        code: r.code,
        description: r.description,
        unit_price: parseFloat(document.getElementById('qrPrice_' + idx)?.value || '0') || 0,
        sort_order: r.sort_order || (idx + 1) * 10,
      }));
      const res = await fetch('/api/pricing', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      if (!res.ok) { alert((await res.json()).detail || 'Could not save rates.'); return; }
      document.getElementById('quickRatesModal').hidden = true;
      await loadOfficeState();
      await loadUnbilledItems();
    }

    function selectBillingCustomer(cust) {
      document.getElementById('billCustomer').value = cust;
      loadUnbilledItems();
      loadOfficeState();
    }

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
      const rolo = (STATE.customers_rolodex || []).find(c => c.customer_name.toUpperCase() === (cust || '').toUpperCase());
      const custMeta = STATE.customers_summary.find(c => c.customer_name === cust);

      document.getElementById('billPhone').value = rolo?.phone || custMeta?.customer_phone || '';
      const isTaxExempt = Boolean(rolo?.tax_exempt || custMeta?.tax_exempt);
      document.getElementById('billTaxExemptBadge').hidden = !isTaxExempt;
      document.getElementById('billTax').value = isTaxExempt ? 0 : (STATE.default_tax_rate ?? 6.0);

      if (rolo?.payment_terms === 'NET_30' || custMeta?.payment_terms === 'NET_30') {
        document.getElementById('billPayMethod').value = 'Net-30 Municipal';
        document.getElementById('billStatus').value = 'SENT';
      }

      if (!cust) { DRAFT_ITEMS = []; renderDraftInvoice(); return; }

      const res = await fetch(`/api/unbilled?customer_name=${encodeURIComponent(cust)}&work_date=${encodeURIComponent(d)}&all_dates=${allDates}`);
      const recs = await res.json();
      DRAFT_ITEMS = [];
      for (const r of recs) {
        const isPb = isPaintballCylinder(r);
        const isSp = (r.dot_spec || '').startsWith('SP') || (r.dot_spec || '').startsWith('E-');
        const pbRate = getRate('HYDRO_PAINTBALL');
        const isProp = r.gas_service === 'LPG' || (r.dot_spec || '').startsWith('4B');
        const code = r.service_mode === 'VISUAL_ONLY' ? (isProp && getRate('PROPANE_VISUAL') > 0 ? 'PROPANE_VISUAL' : 'VISUAL_ONLY') : (isPb && pbRate > 0 ? 'HYDRO_PAINTBALL' : (isSp ? 'HYDRO_SCBA' : 'HYDRO_STD'));
        DRAFT_ITEMS.push({
          record_id: r.id,
          description: `SN ${r.serial_number} (${r.dot_spec}-${r.service_psi} ${r.gas_service}) - ${r.service_mode === 'VISUAL_ONLY' ? 'Visual Insp' : (isPb ? 'HPA Hydro' : 'Hydro + Visual')} [${r.hydro_disposition}]`,
          qty: 1,
          unit_price: getRate(code),
        });
        if (r.rush_fee || r.priority === 'RUSH') {
          DRAFT_ITEMS.push({ record_id: null, description: `SN ${r.serial_number} - Priority / Rush Turnaround Service`, qty: 1, unit_price: getRate('RUSH_FEE') });
        }
        if (r.addon_oring) {
          DRAFT_ITEMS.push({ record_id: null, description: `SN ${r.serial_number} - Neck / Valve O-Ring Replacement`, qty: 1, unit_price: getRate('NECK_ORING') });
        }
        if (r.addon_burst_disc || r.relief_valve_changed === 'Yes') {
          DRAFT_ITEMS.push({ record_id: null, description: `SN ${r.serial_number} - Relief / Burst Disc Replacement`, qty: 1, unit_price: getRate('VALVE_RELIEF') });
        }
        if (r.addon_clean) {
          DRAFT_ITEMS.push({ record_id: null, description: `SN ${r.serial_number} - Internal Cylinder Tumble / Cleaning`, qty: 1, unit_price: getRate('INTERNAL_CLEAN') });
        }
      }
      renderDraftInvoice();
    }

    function addCustomInvoiceLine(code, desc) {
      DRAFT_ITEMS.push({ record_id: null, description: desc, qty: 1, unit_price: getRate(code) });
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
            <div class="desc-col"><input value="${escHtml(it.description)}" oninput="DRAFT_ITEMS[${i}].description=this.value" /></div>
            <div><input type="number" step="1" value="${it.qty}" oninput="DRAFT_ITEMS[${i}].qty=parseFloat(this.value||0);recalcInvoiceDraft()" /></div>
            <div><input type="number" step="0.01" value="${it.unit_price}" oninput="DRAFT_ITEMS[${i}].unit_price=parseFloat(this.value||0);recalcInvoiceDraft()" /></div>
            <div id="lineTot_${i}"><strong>$${(it.qty * it.unit_price).toFixed(2)}</strong></div>
            <div><button class="secondary" onclick="removeDraftInvoiceLine(${i})">✕</button></div>
          </div>`).join('')
        : '<div style="color:var(--muted);font-size:0.86rem;padding:8px 0;">No unbilled completed bottles for this customer — pick an unbilled customer pill above or add a custom line.</div>';
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
      const taxAmt = sub * (taxPct / 100);
      const tot = sub + taxAmt;
      document.getElementById('draftTotalLabel').textContent = `Draft Total: $${tot.toFixed(2)}`;
      if (document.getElementById('invSubTxt')) {
        document.getElementById('invSubTxt').textContent = `$${sub.toFixed(2)}`;
        document.getElementById('invTaxTxt').textContent = `$${taxAmt.toFixed(2)}`;
        document.getElementById('invGrandTxt').textContent = `$${tot.toFixed(2)}`;
      }
      return tot;
    }

    async function submitInvoice() {
      const cust = document.getElementById('billCustomer').value;
      if (!cust || !DRAFT_ITEMS.length) { alert('Select a customer with completed bottles or add a custom line.'); return; }
      const zeroLines = DRAFT_ITEMS.filter(it => !(Number(it.unit_price) > 0)).length;
      if (zeroLines > 0 && !confirm(`${zeroLines} line item(s) currently have a $0.00 unit price.\n\nSave this invoice anyway, or press Cancel to enter prices / update Service Rates?`)) return;

      const res = await fetch('/api/invoices', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          work_date: document.getElementById('billDate').value || STATE.selected_date,
          customer_name: cust,
          customer_phone: document.getElementById('billPhone').value,
          tax_rate: parseFloat(document.getElementById('billTax').value || '0'),
          status: document.getElementById('billStatus').value,
          payment_method: document.getElementById('billPayMethod').value,
          created_by: (window.CURRENT_USER && window.CURRENT_USER.initials) || localStorage.getItem('webers_active_crew') || 'RP',
          notes: document.getElementById('billNotes').value,
          items: DRAFT_ITEMS,
        }),
      });
      const data = await res.json();
      if (!res.ok) { alert(data.detail); return; }
      window.open(`/api/pdf/invoice/${data.id}`, '_blank');
      await loadOfficeState();
      loadUnbilledItems();
    }

    function openSelectedClaimSlip() {
      const cust = document.getElementById('billCustomer').value;
      if (!cust) { alert('Select a customer first.'); return; }
      window.open(`/api/pdf/claim-slip?customer=${encodeURIComponent(cust)}&work_date=${encodeURIComponent(STATE.selected_date)}`, '_blank');
    }

    function sendPickupSms(custName, phone, count, invExtra) {
      let ph = (phone || '').trim() || prompt(`Enter cell phone number for ${custName}:`, '') || '';
      if (!ph.trim()) return;
      const cleanDigits = ph.replace(/\D/g, '');
      const msg = `Weber's Tank Testing: Hi ${custName}, your ${count} cylinder(s) are tested, stamped/labeled, and staged for pickup at our 24/7 Loading Dock at 3086 Legionville Rd${invExtra ? ' (' + invExtra + ')' : ''}. Questions? Call/text ${STATE.shop_phone || '724-266-8008'}.`;
      const sep = /iPad|iPhone|iPod/i.test(navigator.userAgent) ? '&' : '?';
      window.location.href = `sms:${cleanDigits}${sep}body=${encodeURIComponent(msg)}`;
    }

    function sendCustomerPickupSmsFromBilling() {
      const cust = document.getElementById('billCustomer').value;
      if (!cust) { alert('Select a customer first.'); return; }
      const tot = recalcInvoiceDraft().toFixed(2);
      sendPickupSms(cust, document.getElementById('billPhone').value, Math.max(1, DRAFT_ITEMS.filter(x => x.record_id).length || DRAFT_ITEMS.length), `Total: $${tot}`);
    }

    // --- TAB 2: SAVED INVOICES ---
    function setInvFilter(f) { INV_FILTER = f; renderInvoicesList(); }

    function renderInvoicesList() {
      let rows = STATE.invoices || [];
      if (INV_FILTER === 'OPEN') rows = rows.filter(i => i.status !== 'PAID');
      if (INV_FILTER === 'PAID') rows = rows.filter(i => i.status === 'PAID');
      document.getElementById('invoicesList').innerHTML = rows.length
        ? rows.map(inv => `
          <div class="rec-item">
            <div class="rec-main">
              <div class="rec-title">
                <span>${escHtml(inv.invoice_no)} — ${escHtml(inv.customer_name)}</span>
                <span class="${inv.status === 'PAID' ? 'pass' : 'dwell-badge'}">$${Number(inv.total_amount).toFixed(2)} (${escHtml(inv.status)}${inv.payment_method ? ' • ' + escHtml(inv.payment_method) : ''})</span>
              </div>
              <div class="rec-sub">Service Date: ${escHtml(inv.work_date)}${inv.customer_phone ? ' • 📞 ' + escHtml(inv.customer_phone) : ''}${inv.notes ? ' • ' + escHtml(inv.notes) : ''}</div>
            </div>
            <div class="rec-actions">
              <a class="secondary" href="/api/pdf/invoice/${inv.id}" target="_blank">📄 PDF</a>
              <button class="secondary" onclick="sendPickupSms('${inv.customer_name.replace(/'/g, "\\'")}', '${escHtml(inv.customer_phone || '')}', 1, '${inv.invoice_no}: $${Number(inv.total_amount).toFixed(2)}')">📲 Text</button>
              ${inv.status !== 'PAID' ? `<button class="secondary" style="background:#dcfce7;color:#166534;" onclick="markInvoicePaid(${inv.id})">✓ Mark Paid</button><button class="secondary" data-owner-only="1" style="color:#b91c1c;" onclick="voidInvoice(${inv.id}, '${inv.invoice_no}')">🗑 Void &amp; Re-Bill</button>` : ''}
            </div>
          </div>`).join('')
        : '<div style="color:var(--muted);font-size:0.86rem;">No matching invoices found.</div>';
    }

    async function voidInvoice(id, invNo) {
      if (!confirm(`Void ${invNo} and return its cylinders back to Create Invoice for re-billing?`)) return;
      const res = await fetch(`/api/invoices/${id}`, { method: 'DELETE' });
      if (!res.ok) { alert((await res.json()).detail || 'Could not void invoice.'); return; }
      await loadOfficeState();
      loadUnbilledItems();
    }

    async function markInvoicePaid(id) {
      const method = prompt('Payment method (Cash, Check, Card / Venmo, Net-30):', 'Cash');
      if (method === null) return;
      await fetch(`/api/invoices/${id}/status`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: 'PAID', payment_method: method }),
      });
      loadOfficeState();
    }

    // --- TAB 3: CUSTOMER ROLODEX & 5-YR / 1-YR RECALL ENGINE ---
    function toggleRolodexSub(which) {
      document.getElementById('roloDirPane').hidden = (which !== 'dir');
      document.getElementById('roloRecallPane').hidden = (which !== 'recall');
      document.getElementById('subBtnDir').classList.toggle('active', which === 'dir');
      document.getElementById('subBtnRecall').classList.toggle('active', which === 'recall');
    }

    function onRoloTypeChange() {
      const t = document.getElementById('rcType').value;
      if (t === 'FIRE_EMS') {
        document.getElementById('rcTaxExempt').checked = true;
        document.getElementById('rcTerms').value = 'NET_30';
      }
    }

    function resetRolodexForm() {
      document.getElementById('roloFormTitle').textContent = '➕ Add / Update Customer Profile';
      ['rcId', 'rcName', 'rcContact', 'rcPhone', 'rcEmail', 'rcStreet', 'rcCity', 'rcZip', 'rcNotes'].forEach(id => { document.getElementById(id).value = ''; });
      document.getElementById('rcState').value = 'PA';
      document.getElementById('rcType').value = 'WALK_IN';
      document.getElementById('rcTerms').value = 'DUE_AT_PICKUP';
      document.getElementById('rcTaxExempt').checked = false;
    }

    function editRolodexCustomer(id) {
      const c = (STATE.customers_rolodex || []).find(x => x.id === id);
      if (!c) return;
      document.getElementById('roloFormTitle').textContent = `✎ Editing: ${c.customer_name}`;
      document.getElementById('rcId').value = c.id;
      document.getElementById('rcName').value = c.customer_name || '';
      document.getElementById('rcType').value = c.account_type || 'WALK_IN';
      document.getElementById('rcContact').value = c.contact_person || '';
      document.getElementById('rcPhone').value = c.phone || '';
      document.getElementById('rcEmail').value = c.email || '';
      document.getElementById('rcStreet').value = c.street_address || '';
      document.getElementById('rcCity').value = c.city || '';
      document.getElementById('rcState').value = c.state || 'PA';
      document.getElementById('rcZip').value = c.zip_code || '';
      document.getElementById('rcTerms').value = c.payment_terms || 'DUE_AT_PICKUP';
      document.getElementById('rcTaxExempt').checked = Boolean(c.tax_exempt);
      document.getElementById('rcNotes').value = c.notes || '';
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    async function saveCustomerProfile() {
      const name = document.getElementById('rcName').value.trim();
      if (!name) { alert('Enter Customer or Department name first.'); return; }
      const idVal = document.getElementById('rcId').value;
      const res = await fetch('/api/customers', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: idVal ? parseInt(idVal, 10) : null,
          customer_name: name,
          account_type: document.getElementById('rcType').value,
          contact_person: document.getElementById('rcContact').value,
          phone: document.getElementById('rcPhone').value,
          email: document.getElementById('rcEmail').value,
          street_address: document.getElementById('rcStreet').value,
          city: document.getElementById('rcCity').value,
          state: document.getElementById('rcState').value,
          zip_code: document.getElementById('rcZip').value,
          payment_terms: document.getElementById('rcTerms').value,
          tax_exempt: document.getElementById('rcTaxExempt').checked,
          notes: document.getElementById('rcNotes').value,
        }),
      });
      if (!res.ok) { alert((await res.json()).detail || 'Save failed.'); return; }
      resetRolodexForm();
      loadOfficeState();
    }

    async function deleteRolodexCustomer(id, name) {
      if (!confirm(`Remove "${name}" from the Rolodex directory? (Existing test logs are unaffected.)`)) return;
      await fetch(`/api/customers/${id}`, { method: 'DELETE' });
      loadOfficeState();
    }

    function renderRolodexDirectory() {
      const q = (document.getElementById('roloSearch')?.value || '').trim().toLowerCase();
      const rows = (STATE.customers_rolodex || []).filter(c => {
        if (!q) return true;
        return [c.customer_name, c.contact_person, c.phone, c.email, c.city, c.street_address].some(v => (v || '').toLowerCase().includes(q));
      });
      document.getElementById('roloCountBadge').textContent = `${rows.length} Profiles`;
      document.getElementById('rolodexList').innerHTML = rows.length
        ? rows.map(c => {
            const addr = [c.street_address, c.city, c.state, c.zip_code].filter(Boolean).join(', ');
            const typeLbl = c.account_type === 'FIRE_EMS' ? '🚒 Fire/EMS' : (c.account_type === 'COMMERCIAL' ? '🏢 Commercial' : '👤 Walk-In');
            return `
              <div class="rec-item">
                <div class="rec-main">
                  <div class="rec-title">
                    <span>${escHtml(c.customer_name)}</span>
                    <span class="acct-tag ${escHtml(c.account_type)}">${typeLbl}</span>
                    ${c.tax_exempt ? '<span class="tax-exempt-pill">Tax-Exempt</span>' : ''}
                    ${c.payment_terms === 'NET_30' ? '<span class="zone-badge">Net-30</span>' : ''}
                  </div>
                  <div class="rec-sub">
                    ${c.contact_person ? `👤 ${escHtml(c.contact_person)} • ` : ''}${c.phone ? `📞 ${escHtml(c.phone)} • ` : ''}${c.email ? `✉️ ${escHtml(c.email)} • ` : ''}${addr ? `📍 ${escHtml(addr)}` : 'No mailing address yet'}
                  </div>
                  ${c.notes ? `<div style="font-size:0.74rem;color:#92400e;margin-top:2px;">Note: ${escHtml(c.notes)}</div>` : ''}
                </div>
                <div class="rec-actions">
                  <button class="secondary" onclick="editRolodexCustomer(${c.id})">✎ Edit</button>
                  <button class="secondary" style="color:#b91c1c;" onclick="deleteRolodexCustomer(${c.id}, '${c.customer_name.replace(/'/g, "\\'")}')">✕</button>
                </div>
              </div>`;
          }).join('')
        : '<div style="color:var(--muted);font-size:0.86rem;">No matching Rolodex profiles yet.</div>';
    }

    function setRecallFilter(f) { RECALL_FILTER = f; renderRecallsList(); }

    function sendRecallSms(custName, phone, sn, spec, dueLbl, cycleLbl) {
      let ph = (phone || '').trim() || prompt(`Enter cell phone for ${custName}:`, '') || '';
      if (!ph.trim()) return;
      const digits = ph.replace(/\D/g, '');
      const msg = `Weber's Tank Testing: Friendly reminder for ${custName} — cylinder SN ${sn} (${spec}) is due for its ${cycleLbl} around ${dueLbl}. Drop off 24/7 at 3086 Legionville Rd, Baden or call/text ${STATE.shop_phone || '724-266-8008'}.`;
      const sep = /iPad|iPhone|iPod/i.test(navigator.userAgent) ? '&' : '?';
      window.location.href = `sms:${digits}${sep}body=${encodeURIComponent(msg)}`;
    }

    function renderRecallsList() {
      let rows = STATE.recalls || [];
      if (RECALL_FILTER === 'DUE') rows = rows.filter(r => r.status_bucket !== 'FUTURE');
      document.getElementById('recallsList').innerHTML = rows.length
        ? rows.map(r => `
          <div class="rec-item">
            <div class="rec-main">
              <div class="rec-title">
                <span>${escHtml(r.customer_name)} — SN ${escHtml(r.serial_number)}</span>
                <span class="recall-pill ${r.status_bucket}">${r.status_bucket === 'OVERDUE' ? '⛔ OVERDUE' : (r.status_bucket === 'DUE_SOON' ? '⚠️ DUE SOON' : '✓ ' + escHtml(r.due_month_label))}</span>
              </div>
              <div class="rec-sub">${escHtml(r.spec_label)} • ${escHtml(r.cycle_label)} • Last Tested: ${escHtml(r.last_test_date)} • Due: <strong>${escHtml(r.due_date)}</strong>${r.phone ? ' • 📞 ' + escHtml(r.phone) : ''}</div>
            </div>
            <div class="rec-actions">
              <button class="secondary" onclick="sendRecallSms('${r.customer_name.replace(/'/g, "\\'")}', '${escHtml(r.phone)}', '${escHtml(r.serial_number)}', '${escHtml(r.spec_label)}', '${escHtml(r.due_month_label)}', '${escHtml(r.cycle_label)}')">📲 Text Reminder</button>
            </div>
          </div>`).join('')
        : '<div style="color:var(--muted);font-size:0.86rem;">No cylinders due within 120 days right now — click <strong>All Tracked Cylinders</strong> to see upcoming 1-yr &amp; 5-yr dates.</div>';
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

    // --- TAB 5: RYAN'S SUPPLY THRESHOLD LIMITS & LOW-STOCK NOTIFICATIONS ---
    function setSupplyCat(cat) {
      SUPPLY_CAT = cat;
      document.querySelectorAll('#supplyCatBar .tap-pill').forEach(b => {
        b.classList.toggle('active', b.getAttribute('data-cat') === cat);
      });
      renderSuppliesGrid();
    }

    function renderSuppliesGrid() {
      let items = STATE.shop_supplies || [];
      if (SUPPLY_CAT === 'LOW') items = items.filter(s => s.low_stock);
      else if (SUPPLY_CAT !== 'ALL') items = items.filter(s => s.category === SUPPLY_CAT);

      document.getElementById('suppliesGrid').innerHTML = items.length
        ? items.map(s => {
            const qty = s.on_hand_qty ?? 5;
            const lim = s.reorder_threshold ?? 2;
            const unit = s.unit_label || 'units';
            return `
              <div class="supply-card ${s.low_stock ? 'is-low' : ''}">
                <div>
                  <div style="display:flex;justify-content:space-between;align-items:center;gap:6px;margin-bottom:4px;">
                    <span class="sub-badge" style="font-size:0.67rem;">${escHtml(s.category)}</span>
                    ${s.low_stock ? '<span class="rush-badge">⚠️ NEEDS REPLACEMENT</span>' : '<span class="sub-badge" style="background:#ecfdf5;color:#065f46;">✓ Stock OK</span>'}
                  </div>
                  <strong style="font-size:0.94rem;display:block;margin-bottom:3px;">${escHtml(s.item_name)}</strong>
                  <div style="font-size:0.77rem;color:var(--muted);margin-bottom:8px;">${escHtml(s.spec_note || '')}</div>

                  <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;background:#f8fafc;border:1px solid #e2e8f0;padding:7px 9px;border-radius:8px;">
                    <div>
                      <small style="display:block;font-size:0.66rem;font-weight:800;color:var(--muted);text-transform:uppercase;">On Hand (${escHtml(unit)})</small>
                      <div style="display:inline-flex;align-items:center;gap:6px;margin-top:2px;">
                        <button type="button" class="secondary" style="padding:2px 9px;font-weight:900;" onclick="stepSupplyQty(${s.id}, -1)">−</button>
                        <strong class="mono" style="font-size:1.05rem;min-width:24px;text-align:center;color:${s.low_stock ? '#b91c1c' : '#0f172a'};">${qty}</strong>
                        <button type="button" class="secondary" style="padding:2px 9px;font-weight:900;" onclick="stepSupplyQty(${s.id}, 1)">+</button>
                      </div>
                    </div>
                    <div style="text-align:right;">
                      <small style="display:block;font-size:0.66rem;font-weight:800;color:#92400e;text-transform:uppercase;">Alert Me At ≤</small>
                      <input type="number" min="0" value="${lim}" style="width:64px;padding:4px 6px;font-weight:800;text-align:center;margin-top:2px;"
                        onchange="setSupplyThreshold(${s.id}, this.value)" title="Email &amp; banner alert fires when On-Hand hits this number" />
                    </div>
                  </div>
                </div>
                <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:8px;">
                  ${s.low_stock
                    ? `<button class="secondary" style="flex:1;background:#dcfce7;color:#166534;border-color:#86efac;font-weight:800;" onclick="toggleSupplyStock(${s.id}, false, true)">✓ Restocked</button>`
                    : `<button class="secondary" style="flex:1;" onclick="toggleSupplyStock(${s.id}, true, false)">🔔 Trigger Low Alert</button>`}
                  <button class="secondary" onclick="openSupplyModal(${s.id})" title="Edit Item &amp; Limit">✎ Edit</button>
                </div>
              </div>`;
          }).join('')
        : '<div style="color:var(--muted);font-size:0.86rem;">No supply items in this filter.</div>';
    }

    async function stepSupplyQty(id, delta) {
      await fetch(`/api/supplies/${id}/qty`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ delta }),
      });
      loadOfficeState();
    }

    async function setSupplyThreshold(id, val) {
      await fetch(`/api/supplies/${id}/qty`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reorder_threshold: Math.max(0, parseInt(val || '0', 10)) }),
      });
      loadOfficeState();
    }

    async function toggleSupplyStock(id, lowStock, markOrderedToday) {
      await fetch(`/api/supplies/${id}/stock`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ low_stock: lowStock, mark_ordered_today: markOrderedToday }),
      });
      loadOfficeState();
    }

    function openSupplyModal(id) {
      const s = id ? (STATE.shop_supplies || []).find(x => x.id === id) : null;
      document.getElementById('supplyModalTitle').textContent = s ? '✎ Edit Supply & Alert Limit' : '➕ Add Tracked Shop Supply';
      document.getElementById('supId').value = s ? s.id : '';
      document.getElementById('supCat').value = s ? s.category : 'Marking & Labels';
      document.getElementById('supVendor').value = s ? (s.vendor_name || 'Shop') : 'Shop';
      document.getElementById('supName').value = s ? s.item_name : '';
      document.getElementById('supNote').value = s ? (s.spec_note || '') : '';
      document.getElementById('supUrl').value = s ? (s.reorder_url || '') : '';
      if (document.getElementById('supQty')) document.getElementById('supQty').value = s ? (s.on_hand_qty ?? 5) : 5;
      if (document.getElementById('supThresh')) document.getElementById('supThresh').value = s ? (s.reorder_threshold ?? 2) : 2;
      if (document.getElementById('supUnit')) document.getElementById('supUnit').value = s ? (s.unit_label || 'units') : 'units';
      document.getElementById('supLow').checked = Boolean(s && s.low_stock);
      document.getElementById('btnDelSupply').hidden = !s;
      document.getElementById('supplyModal').hidden = false;
    }

    function closeSupplyModal() { document.getElementById('supplyModal').hidden = true; }

    async function saveSupplyItem() {
      const name = document.getElementById('supName').value.trim();
      if (!name) { alert('Enter item name first.'); return; }
      const idVal = document.getElementById('supId').value;
      const qty = parseInt(document.getElementById('supQty')?.value || '5', 10);
      const thresh = parseInt(document.getElementById('supThresh')?.value || '2', 10);
      const res = await fetch('/api/supplies', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: idVal ? parseInt(idVal, 10) : null,
          category: document.getElementById('supCat').value,
          vendor_name: document.getElementById('supVendor').value,
          item_name: name,
          spec_note: document.getElementById('supNote').value,
          reorder_url: document.getElementById('supUrl').value,
          on_hand_qty: qty,
          reorder_threshold: thresh,
          unit_label: document.getElementById('supUnit')?.value || 'units',
          low_stock: document.getElementById('supLow').checked || qty <= thresh,
        }),
      });
      if (!res.ok) { alert((await res.json()).detail || 'Save failed.'); return; }
      closeSupplyModal();
      loadOfficeState();
    }

    async function deleteSupplyItem() {
      const idVal = document.getElementById('supId').value;
      if (!idVal || !confirm('Remove this supply item?')) return;
      await fetch(`/api/supplies/${idVal}`, { method: 'DELETE' });
      closeSupplyModal();
      loadOfficeState();
    }
