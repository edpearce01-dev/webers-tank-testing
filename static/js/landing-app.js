let PUB_ACCT = 'WALK_IN';
    let PUB_TANK_COUNTS = {};
    let LAST_TICKET_DATA = null;

    function esc(s) {
      const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' };
      return String(s ?? '').replace(/[&<>"]/g, m => map[m]);
    }

    function updateHeroScrollEffect() {
      const y = Math.max(0, window.scrollY || window.pageYOffset || 0);
      const stage = document.getElementById('fixedBrandStage');
      const arch = document.getElementById('heroHolidayArchImg');
      const greet = document.getElementById('greetingFadeBox');
      const nav = document.getElementById('stickySiteNav');
      const topBar = document.getElementById('topChrome');

      if (nav) nav.classList.toggle('scrolled', y > 24);

      if (greet) {
        const gProg = Math.min(1, y / 180);
        greet.style.opacity = (1 - gProg).toFixed(3);
        greet.style.transform = `translate3d(0, ${(-gProg * 24).toFixed(1)}px, 0)`;
        greet.style.visibility = gProg >= 0.99 ? 'hidden' : 'visible';
      }

      if (stage) {
        const vh = window.innerHeight || 820;
        const vw = window.innerWidth || 1280;
        const isMob = vw <= 680;
        const navH = nav?.offsetHeight || 60;
        const utilH = topBar?.offsetHeight || 34;
        const hdrBottom = navH + utilH;
        const greetH = (greet?.offsetHeight || 160) + (isMob ? 18 : 26);

        // Fit Wreath + Circular Logo + Dogs Out in Front cleanly between bottom of navbar & top of speech bubble!
        const availH = Math.max(230, vh - hdrBottom - greetH);
        const hasWreath = arch && !arch.hidden;
        const totalStackRatio = (hasWreath ? 0.18 : 0.02) + 1.0 + 0.39; // top wreath + circle + dogs net height below circle
        const D = Math.round(Math.min(isMob ? 245 : 375, vw * (isMob ? 0.58 : 0.36), availH / totalStackRatio));

        const topWreathPad = Math.round(D * (hasWreath ? 0.18 : 0.02));
        const usedH = Math.round(D * totalStackRatio);
        const extraSlack = Math.max(0, availH - usedH);
        const crestTop = Math.round(hdrBottom + 10 + topWreathPad + (extraSlack * 0.42));

        stage.style.setProperty('--crest-d', `${D}px`);
        stage.style.setProperty('--crest-top', `${crestTop}px`);
        stage.style.setProperty('--dogs-w', `${Math.round(D * 1.62)}px`);
        stage.style.setProperty('--dogs-h', `${Math.round(D * 0.63)}px`);
        stage.style.setProperty('--dogs-overlap', `${-Math.round(D * 0.24)}px`);

        const sProg = Math.min(1, y / 340);
        const stageOpacity = 1 - (sProg * 0.74);
        const stageScale = 1 - (sProg * 0.06);
        const liftY = -Math.min(utilH, y) * 0.5;
        stage.style.opacity = stageOpacity.toFixed(3);
        stage.style.transform = `translate3d(0, ${liftY.toFixed(1)}px, 0) scale(${stageScale.toFixed(3)})`;
      }
    }
    window.addEventListener('resize', () => requestAnimationFrame(updateHeroScrollEffect), { passive: true });
    window.addEventListener('scroll', () => requestAnimationFrame(updateHeroScrollEffect), { passive: true });

    function formatPubPhone(el) {
      const d = el.value.replace(/\D/g, '').slice(0, 10);
      if (d.length >= 7) el.value = `${d.slice(0,3)}-${d.slice(3,6)}-${d.slice(6)}`;
      else if (d.length >= 4) el.value = `${d.slice(0,3)}-${d.slice(3)}`;
    }

    async function loadPublicOperatingSchedule() {
      try {
        const res = await fetch('/api/public/hours');
        if (!res.ok) return;
        const d = await res.json();
        const topEl = document.getElementById('pubTopHoursTxt');
        if (topEl && d.summary_line) {
          topEl.innerHTML = esc(d.summary_line).replace('24/7 Dock', '<strong>24/7 Dock</strong>');
        }
        const facEl = document.getElementById('pubFacilityHoursSpan');
        if (facEl && d.summary_line) {
          const cleanStaffed = d.summary_line.replace(/\s*\|\s*24\/7 Dock/i, '');
          facEl.innerHTML = `Staffed Hours: <strong>${esc(cleanStaffed)}</strong> — plus <strong>24/7 Loading Dock Drop-Off &amp; Pickup</strong> anytime.`;
        }
        const cWrap = document.getElementById('pubClosureBannerWrap');
        if (cWrap && d.active_banner) {
          const b = d.active_banner;
          const spanTxt = b.start_date === b.end_date ? b.start_date : `${b.start_date} through ${b.end_date}`;
          const dockMsg = b.dock_open ? '✅ Our <strong>24/7 Loading Dock</strong> remains OPEN for self-serve cylinder drop-off &amp; pickup!' : '⚠️ Both staffed shop and Loading Dock are closed during this window.';
          const hrsMsg = (b.staffed_open && b.special_hours) ? `Special Staffed Hours: <strong>${esc(b.special_hours)}</strong>` : 'Staffed Test Counter Closed';
          cWrap.hidden = false;
          cWrap.innerHTML = `
            <div class="glass-card" style="padding:14px 18px;border-left:5px solid #f59e0b;background:rgba(255,251,235,0.94);">
              <div style="font-weight:800;color:#78350f;font-size:0.95rem;">
                📅 ${b.banner_state === 'ACTIVE' ? 'Holiday / Schedule Notice' : 'Upcoming Holiday / Schedule Notice'}: ${esc(b.title)} (${esc(spanTxt)})
              </div>
              <div style="font-size:0.86rem;color:#92400e;margin-top:3px;">
                ${hrsMsg} • ${dockMsg}${b.public_note ? ' • ' + esc(b.public_note) : ''}
              </div>
            </div>`;
        }
      } catch (_) {}
    }

    async function loadHolidayStorefrontTheme() {
      try {
        const res = await fetch('/api/public/theme');
        if (!res.ok) return;
        const t = await res.json();
        const bubble = document.getElementById('heroDogsBubble');
        const img = document.getElementById('heroDogsImg');
        const pill = document.getElementById('heroHolidayPill');
        const bar = document.getElementById('pubHolidayBanner');
        const archImg = document.getElementById('heroHolidayArchImg');

        if (bubble && t.dog_message) bubble.textContent = t.dog_message;

        if (t.active && t.enable_public) {
          if (archImg) {
            const archMap = { THANKSGIVING: 'arch-thanksgiving', HALLOWEEN: 'arch-thanksgiving', CHRISTMAS: 'arch-christmas', NEW_YEAR: 'arch-newyear' };
            const archStem = archMap[t.key] || 'arch-thanksgiving';
            const tryPng = `/static/images/${archStem}.png`;
            const tryJpg = `/static/images/${archStem}.jpg`;
            const pA = new Image();
            pA.onload = () => { archImg.src = tryPng; archImg.style.mixBlendMode = 'normal'; archImg.hidden = false; updateHeroScrollEffect(); };
            pA.onerror = () => {
              const pB = new Image();
              pB.onload = () => { archImg.src = tryJpg; archImg.style.mixBlendMode = 'multiply'; archImg.hidden = false; updateHeroScrollEffect(); };
              pB.onerror = () => { archImg.hidden = true; };
              pB.src = tryJpg;
            };
            pA.src = tryPng;
          }
          if (pill && t.countdown_label) { pill.hidden = false; pill.textContent = t.countdown_label; }
          if (bar && t.countdown_label) { bar.hidden = false; bar.textContent = t.countdown_label; }
          if (img && t.dog_image) {
            const stem = t.dog_image.replace(/\.(jpg|jpeg|png)$/i, '');
            const pngUrl = `/static/images/${stem}.png`;
            const jpgUrl = `/static/images/${t.dog_image}`;
            const probePng = new Image();
            probePng.onload = () => { img.classList.remove('photo-fallback'); img.src = pngUrl; };
            probePng.onerror = () => {
              const probeJpg = new Image();
              probeJpg.onload = () => {
                img.src = jpgUrl;
                img.classList.toggle('photo-fallback', t.dog_image === 'shop-dogs.jpg');
              };
              probeJpg.onerror = () => { img.src = '/static/images/dogs-classic.png?v=13.3'; img.classList.remove('photo-fallback'); };
              probeJpg.src = jpgUrl;
            };
            probePng.src = pngUrl;
          }
        } else if (img) {
          if (archImg) archImg.hidden = true;
          img.src = '/static/images/dogs-classic.png?v=13.3';
          img.classList.remove('photo-fallback');
        }
      } catch (_) {}
    }

    function setCheckinMode(mode) {
      const tabDrop = document.getElementById('tabDrop');
      const tabPick = document.getElementById('tabPick');
      const tabTruck = document.getElementById('tabTruck');
      document.getElementById('checkinModeTabs').hidden = false;

      tabDrop.className = 'mode-tab' + (mode === 'DROPOFF' ? ' active-drop' : '');
      tabPick.className = 'mode-tab' + (mode === 'PICKUP_NOTICE' ? ' active-pick' : '');
      tabTruck.className = 'mode-tab' + (mode === 'TRUCK_REQUEST' ? ' active-truck' : '');

      document.getElementById('formDropoff').hidden = mode !== 'DROPOFF';
      document.getElementById('formPickup').hidden = mode !== 'PICKUP_NOTICE';
      document.getElementById('formTruck').hidden = mode !== 'TRUCK_REQUEST';
      document.getElementById('ciConfirm').hidden = true;
    }

    function resetForAnotherEntry(mode) { setCheckinMode(mode || 'DROPOFF'); }

    function setPubAcct(acct) {
      PUB_ACCT = acct;
      document.querySelectorAll('#pubAcctBar .acct-pill').forEach(b => {
        b.classList.toggle('active', b.getAttribute('data-acct') === acct);
      });
      const isOrg = acct !== 'WALK_IN';
      document.getElementById('dropContactWrap').hidden = !isOrg;
      document.getElementById('dropPoWrap').hidden = !isOrg;
      const lbl = document.getElementById('lblDropName');
      const inp = document.getElementById('dropName');
      const stLbl = document.getElementById('lblDropStreet');
      const ciLbl = document.getElementById('lblDropCity');
      const zpLbl = document.getElementById('lblDropZip');
      if (stLbl && ciLbl && zpLbl) {
        if (isOrg) {
          stLbl.innerHTML = 'Department / Billing Street Address * <span class="opt">(For Net-30 invoices &amp; 5-yr recall notices)</span>';
          ciLbl.textContent = 'City *';
          zpLbl.textContent = 'ZIP Code *';
        } else {
          stLbl.innerHTML = 'Mailing Street Address <span class="opt">(Optional — for 5-yr hydro reminder postcards, promos &amp; PDF receipts)</span>';
          ciLbl.innerHTML = 'City <span class="opt">(Optional)</span>';
          zpLbl.innerHTML = 'ZIP Code <span class="opt">(Optional)</span>';
        }
      }
      if (acct === 'FIRE_EMS') { lbl.textContent = 'Fire Dept / EMS Agency *'; inp.placeholder = 'e.g. Baden VFD'; }
      else if (acct === 'COMMERCIAL') { lbl.textContent = 'Company / Fleet Name *'; inp.placeholder = 'e.g. Beaver Valley Welding'; }
      else { lbl.textContent = 'Your Full Name *'; inp.placeholder = 'e.g. John Smith'; }
    }

    function addPubTankChip(label) {
      PUB_TANK_COUNTS[label] = (PUB_TANK_COUNTS[label] || 0) + 1;
      if (label === 'Propane' && Object.keys(PUB_TANK_COUNTS).length === 1) { const sm = document.getElementById('dropServiceMode'); if (sm) sm.value = 'Propane Visual Inspection'; }
      const total = Object.values(PUB_TANK_COUNTS).reduce((a, b) => a + b, 0);
      document.getElementById('dropCount').value = total;
      document.getElementById('dropSummary').value = Object.entries(PUB_TANK_COUNTS).map(([k, v]) => `${v}x ${k}`).join(', ');
      refreshPublicSla(total);
    }

    function resetPubTankChips() {
      PUB_TANK_COUNTS = {};
      document.getElementById('dropSummary').value = '';
      document.getElementById('dropCount').value = 1;
        syncPublicRushCaps();
      refreshPublicSla(1);
    }

    let LAST_RUSH_LABEL = 'soonest window';

    function syncPublicRushCaps() {
      const dCnt = Math.max(1, parseInt(document.getElementById('dropCount')?.value || '1', 10));
      const dChk = document.getElementById('dropRush');
      const dNote = document.getElementById('dropRushCapNote');
      if (dChk) {
        if (dCnt > 12) {
          dChk.checked = false;
          dChk.disabled = true;
          if (dNote) dNote.innerHTML = '(12-cylinder max for Paid Rush — standard turnaround applies to 13+ cylinders)';
        } else {
          dChk.disabled = false;
          if (dNote) dNote.innerHTML = `(Max 12 cyl • Est. Ready: <strong id="formRushSla" class="mono">${esc(LAST_RUSH_LABEL)}</strong>)`;
        }
      }

      const tCnt = Math.max(1, parseInt(document.getElementById('trkCount')?.value || '1', 10));
      const tChk = document.getElementById('trkRush');
      const tNote = document.getElementById('trkRushCapNote');
      if (tChk) {
        if (tCnt > 12) {
          tChk.checked = false;
          tChk.disabled = true;
          if (tNote) tNote.textContent = '(12-cylinder max for Paid Rush — standard turnaround applies to 13+ cylinders)';
        } else {
          tChk.disabled = false;
          if (tNote) tNote.textContent = '(Max 12 cylinders)';
        }
      }
    }

    async function refreshPublicSla(bottles = 4) {
      syncPublicRushCaps();
      try {
        const res = await fetch('/api/public/turnaround?bottles=' + Math.max(1, parseInt(bottles || '4', 10)));
        if (!res.ok) return;
        const d = await res.json();
        LAST_RUSH_LABEL = '~' + d.rush_ready_label;
        document.getElementById('slaStdTxt').textContent = '~' + d.standard_ready_label;
        document.getElementById('slaRushTxt').textContent = d.rush_eligible === false ? 'Max 12 Cyl' : LAST_RUSH_LABEL;
        syncPublicRushCaps();
      } catch (_) {}
    }

    async function lookupTicket() {
      const t = document.getElementById('trkTicket').value.trim();
      const l4 = document.getElementById('trkLast4').value.trim();
      const box = document.getElementById('trkResult');
      if (!t) { alert('Enter your Ticket # (for example BR-0001).'); return; }
      const res = await fetch(`/api/public/track?ticket=${encodeURIComponent(t)}&phone_last4=${encodeURIComponent(l4)}`);
      const data = await res.json();
      box.hidden = false;
      if (!res.ok) {
        box.innerHTML = `<div class="alert-box alert-bad" style="margin:0;">${esc(data.detail || 'Ticket not found.')}</div>`;
        return;
      }
      localStorage.setItem('webers_last_ticket', data.ticket_no);
      if (l4) localStorage.setItem('webers_last_phone4', l4);
      let dots = '';
      for (let i = 1; i <= 5; i++) dots += `<div class="step-dot ${i <= data.step_num ? 'done' : ''}"></div>`;
      box.innerHTML = `
        <div style="background:rgba(236,253,245,0.95);border:1px solid rgba(16,185,129,0.45);padding:13px;border-radius:10px;">
          <div style="display:flex;justify-content:space-between;font-family:var(--font-mono);font-size:0.76rem;color:#065f46;font-weight:700;">
            <span>TICKET ${esc(data.ticket_no)} (${data.bottle_count} CYL)</span>
            <span>${data.estimated_ready ? 'EST: ' + esc(data.estimated_ready) : ''}</span>
          </div>
          <div style="font-size:0.95rem;font-weight:800;margin-top:4px;color:var(--forest);">${esc(data.stage_label)}</div>
          <div class="step-dots">${dots}</div>
          <div style="margin-top:9px;text-align:right;">
            <a href="/api/public/receipt/${encodeURIComponent(data.ticket_no)}.pdf?p=${encodeURIComponent(l4)}" style="color:#065f46;font-size:0.78rem;font-weight:800;text-decoration:none;">Download Ticket PDF →</a>
          </div>
        </div>`;
    }

    function copyTicketCode(code, btnEl) {
      if (navigator.clipboard) {
        navigator.clipboard.writeText(code);
        const orig = btnEl.textContent;
        btnEl.textContent = 'Copied!';
        setTimeout(() => { btnEl.textContent = orig; }, 2000);
      }
    }

    async function shareTicketReceipt() {
      if (!LAST_TICKET_DATA) return;
      const d = LAST_TICKET_DATA;
      const url = `https://weberstanktesting.com/?ticket=${encodeURIComponent(d.ticket_no)}&p=${encodeURIComponent(d.phone_last4)}#tracker`;
      const text = `Weber's Tank Testing Ticket ${d.ticket_no} (${d.bottle_count} cyl) • Est Ready: ${d.estimated_ready}. Track live: ${url}`;
      if (navigator.share) {
        try { await navigator.share({ title: `Weber's Ticket ${d.ticket_no}`, text, url }); return; } catch (_) {}
      }
      window.location.href = `sms:?body=${encodeURIComponent(text)}`;
    }

    function showDigitalTicketCard(data, meta) {
      LAST_TICKET_DATA = { ...data, ...meta };
      localStorage.setItem('webers_last_ticket', data.ticket_no);
      localStorage.setItem('webers_last_phone4', data.phone_last4 || '');
      document.getElementById('trkTicket').value = data.ticket_no;
      document.getElementById('trkLast4').value = data.phone_last4 || '';

      document.getElementById('checkinModeTabs').hidden = true;
      document.getElementById('formDropoff').hidden = true;
      document.getElementById('formPickup').hidden = true;
      document.getElementById('formTruck').hidden = true;

      const box = document.getElementById('ciConfirm');
      box.hidden = false;
      const isPick = meta.mode === 'PICKUP_NOTICE';
      const isTruck = meta.mode === 'TRUCK_REQUEST';
      const hdrTxt = isTruck
        ? 'Truck Dispatch Request Logged'
        : (isPick ? 'Loading Dock Pick-Up Confirmed' : 'Loading Dock Drop-Off Checked In — Save Ticket #');

      box.innerHTML = `
        <div style="background:linear-gradient(155deg,#0c261b 0%,#163a2a 100%);color:#fff;border-radius:12px;padding:24px;text-align:center;border:1.5px solid #f59e0b;box-shadow:0 16px 34px -8px rgba(12,38,27,0.35);">
          <div style="font-family:var(--font-mono);font-size:0.72rem;font-weight:700;letter-spacing:0.08em;color:#fde68a;text-transform:uppercase;">${hdrTxt}</div>
          <div style="font-family:var(--font-mono);font-size:2.7rem;font-weight:700;letter-spacing:0.04em;margin:6px 0;color:#ffffff;">${esc(data.ticket_no)}</div>
          <div style="font-size:0.92rem;font-weight:700;color:#a7f3d0;margin-bottom:14px;">
            ${isPick ? `Signed Out By: ${esc(meta.personName || meta.name)}` : `Estimated Ready / Return: ~${esc(data.estimated_ready)}`}
          </div>
          <div style="background:rgba(255,255,255,0.08);border:1px solid rgba(255,255,255,0.14);border-radius:8px;padding:11px 14px;text-align:left;font-size:0.84rem;line-height:1.6;margin-bottom:14px;">
            <div><strong>Customer / Dept:</strong> ${esc(meta.name)} (${meta.bottle_count} Cylinder${meta.bottle_count > 1 ? 's' : ''})</div>
            ${meta.address ? `<div><strong>Address:</strong> ${esc(meta.address)}</div>` : ''}
            <div><strong>Contact Phone:</strong> <span class="mono">${esc(meta.phone)}</span></div>
            ${data.email_queued ? `<div style="color:#fde68a;"><strong>Email Receipt:</strong> Queued to ${esc(meta.email)}</div>` : ''}
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:8px;">
            <a href="${esc(data.receipt_pdf_url)}" class="btn btn-gold" style="padding:11px 10px;">Save Ticket PDF</a>
            <button type="button" onclick="shareTicketReceipt()" class="btn btn-forest" style="background:#334155;border-color:#1e293b;padding:11px 10px;">Text / Save Link</button>
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
            <button type="button" onclick="copyTicketCode('${esc(data.ticket_no)}', this)" class="btn btn-forest" style="background:rgba(255,255,255,0.12);border-color:rgba(255,255,255,0.2);padding:9px;">Copy ${esc(data.ticket_no)}</button>
            <button type="button" onclick="resetForAnotherEntry('${meta.mode}')" class="btn btn-forest" style="background:rgba(255,255,255,0.12);border-color:rgba(255,255,255,0.2);padding:9px;">+ Log Another</button>
          </div>
        </div>`;
      document.getElementById('checkinCardTop')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    async function submitDropoffForm() {
      const name = document.getElementById('dropName').value.trim();
      const contact = document.getElementById('dropContact').value.trim();
      const phone = document.getElementById('dropPhone').value.trim();
      const email = document.getElementById('dropEmail').value.trim();
      const street = document.getElementById('dropStreet').value.trim();
      const city = document.getElementById('dropCity').value.trim();
      const state = (document.getElementById('dropState').value.trim() || 'PA').toUpperCase();
      const zip = document.getElementById('dropZip').value.trim();
      const summary = document.getElementById('dropSummary').value.trim();
      const cnt = Math.max(1, parseInt(document.getElementById('dropCount').value || '1', 10));

      if (!name || phone.replace(/\D/g, '').length < 10) { alert('Please enter your Name/Department and a 10-digit Phone Number.'); return; }
      if (PUB_ACCT !== 'WALK_IN' && contact.length < 2) { alert('Please enter your individual name in Dropped Off By.'); document.getElementById('dropContact').focus(); return; }
      if (PUB_ACCT !== 'WALK_IN' && (street.length < 3 || city.length < 2 || zip.length < 5)) { alert('Please enter your Department / Company Billing Street Address, City, and ZIP Code.'); document.getElementById('dropStreet').focus(); return; }

      const sumUp = summary.toUpperCase();
      const pickedSvc = document.getElementById('dropServiceMode')?.value || 'Hydro + Visual';
      const svc = (pickedSvc === 'Propane Visual Inspection' || sumUp.includes('PROPANE')) ? 'Propane Visual Inspection'
        : ((pickedSvc === 'Visual Inspection Only' || sumUp.includes('VISUAL ONLY')) ? 'Visual Inspection Only'
        : ((sumUp.includes('PAINTBALL') || sumUp.includes('HPA')) ? 'Paintball HPA Hydro' : 'Hydro + Visual'));

      const btn = document.getElementById('btnSubmitDrop');
      btn.disabled = true;
      try {
        const res = await fetch('/api/public/dropoff', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            entry_type: 'DROPOFF', account_type: PUB_ACCT, po_station: document.getElementById('dropPo').value.trim(),
            contact_person: contact, customer_name: name, customer_phone: phone, customer_email: email,
            street_address: street, city: city, state: state, zip_code: zip, bottle_count: cnt,
            service_requested: svc, tank_summary: summary, valves_attached: true, needs_air_fill: false,
            rush_requested: document.getElementById('dropRush').checked, website_hp: document.getElementById('hpField').value,
          }),
        });
        const data = await res.json();
        if (!res.ok) { alert(data.detail || 'Drop-off check-in failed.'); return; }
        ['dropName', 'dropContact', 'dropPo', 'dropStreet', 'dropCity', 'dropZip', 'dropSummary'].forEach(id => { document.getElementById(id).value = ''; });
        document.getElementById('dropRush').checked = false;
        PUB_TANK_COUNTS = {};
        document.getElementById('dropCount').value = 1;
        syncPublicRushCaps();
        showDigitalTicketCard(data, { mode: 'DROPOFF', name, phone, email, address: (street || city) ? [street, city, `${state} ${zip}`.trim()].filter(Boolean).join(', ') : '', bottle_count: cnt });
      } finally { btn.disabled = false; }
    }

    async function submitPickupForm() {
      const custName = document.getElementById('pickCustName').value.trim();
      const personName = document.getElementById('pickPersonName').value.trim();
      const phone = document.getElementById('pickPhone').value.trim();
      const ticket = document.getElementById('pickTicket').value.trim();
      const cnt = Math.max(1, parseInt(document.getElementById('pickCount').value || '1', 10));

      if (!custName || !personName) { alert('Please enter the Customer/Dept Name and Your Full Name.'); return; }
      const btn = document.getElementById('btnSubmitPick');
      btn.disabled = true;
      try {
        const res = await fetch('/api/public/dropoff', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            entry_type: 'PICKUP_NOTICE', account_type: 'WALK_IN', customer_name: custName,
            customer_phone: phone, bottle_count: cnt, picked_up_by: personName,
            pickup_ticket_ref: ticket, website_hp: document.getElementById('hpField').value,
          }),
        });
        const data = await res.json();
        if (!res.ok) { alert(data.detail || 'Pick-up sign-out failed.'); return; }
        ['pickCustName', 'pickPersonName', 'pickTicket'].forEach(id => { document.getElementById(id).value = ''; });
        document.getElementById('pickCount').value = 1;
        showDigitalTicketCard(data, { mode: 'PICKUP_NOTICE', name: custName, personName, phone, email: '', bottle_count: cnt });
      } finally { btn.disabled = false; }
    }

    async function submitTruckForm() {
      const trkMode = document.getElementById('trkMode').value;
      const name = document.getElementById('trkCustName').value.trim();
      const contact = document.getElementById('trkContact').value.trim();
      const phone = document.getElementById('trkPhone').value.trim();
      const email = document.getElementById('trkEmail').value.trim();
      const street = document.getElementById('trkAddress').value.trim();
      const city = document.getElementById('trkCity').value.trim();
      const state = (document.getElementById('trkState').value.trim() || 'PA').toUpperCase();
      const zip = document.getElementById('trkZip').value.trim();
      const address = [street, city, `${state} ${zip}`.trim()].filter(Boolean).join(', ');
      const prefDate = document.getElementById('trkPrefDate').value;
      const summary = document.getElementById('trkSummary').value.trim();
      const cnt = Math.max(1, parseInt(document.getElementById('trkCount').value || '1', 10));

      if (!name || !contact || phone.replace(/\D/g, '').length < 10 || street.length < 3 || city.length < 2) {
        alert('Please enter Dept/Business Name, Contact Name, 10-Digit Phone, Street Address, and City.'); return;
      }
      const modeLabel = trkMode === 'ROAD_ROUNDTRIP' ? 'Truck Pickup & Return Delivery'
        : (trkMode === 'ROAD_PICKUP' ? 'Truck Pickup Only' : 'Return Truck Delivery Only');
      const up = name.toUpperCase();
      const guessAcct = ['VFD', ' FD', 'FIRE', 'EMS', 'RESCUE', 'AMBULANCE'].some(k => up.includes(k)) ? 'FIRE_EMS' : 'COMMERCIAL';

      const btn = document.getElementById('btnSubmitTruck');
      btn.disabled = true;
      try {
        const res = await fetch('/api/public/dropoff', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            entry_type: 'TRUCK_REQUEST', account_type: guessAcct, contact_person: contact,
            customer_name: name, customer_phone: phone, customer_email: email, pickup_address: address,
            street_address: street, city: city, state: state, zip_code: zip, preferred_date: prefDate,
            preferred_time: document.getElementById('trkPrefTime')?.value || '',
            return_date: document.getElementById('trkRetDate')?.value || '',
            truck_mode: trkMode, bottle_count: cnt, service_requested: modeLabel, tank_summary: summary,
            rush_requested: document.getElementById('trkRush').checked, website_hp: document.getElementById('hpField').value,
          }),
        });
        const data = await res.json();
        if (!res.ok) { alert(data.detail || 'Truck request failed.'); return; }
        ['trkCustName', 'trkContact', 'trkAddress', 'trkCity', 'trkZip', 'trkSummary'].forEach(id => { document.getElementById(id).value = ''; });
        document.getElementById('trkRush').checked = false;
        syncPublicRushCaps();
        showDigitalTicketCard(data, { mode: 'TRUCK_REQUEST', name, phone, email, address, bottle_count: cnt });
      } finally { btn.disabled = false; }
    }

    function runDotDateCheck() {
      const rawType = document.getElementById('chkType').value;
      const isPropaneVis = (rawType === '12' || rawType === '5_propane');
      const interval = rawType === '5_propane' ? 5 : parseInt(rawType, 10);
      document.getElementById('origMfgWrap').hidden = (interval !== 15);
      const mo = parseInt(document.getElementById('chkMonth').value || '0', 10);
      let yr = parseInt(document.getElementById('chkYear').value || '0', 10);
      const out = document.getElementById('chkOutput');
      if (!(mo >= 1 && mo <= 12) || !yr) return;
      const now = new Date(), curY = now.getFullYear(), curM = now.getMonth() + 1;
      if (yr < 100) yr = yr <= ((curY % 100) + 1) ? 2000 + yr : 1900 + yr;

      if (interval === 15) {
        let bornY = parseInt(document.getElementById('chkOrigYear').value || String(yr), 10);
        if (bornY < 100) bornY = bornY <= ((curY % 100) + 1) ? 2000 + bornY : 1900 + bornY;
        if ((bornY + 15) < curY || ((bornY + 15) === curY && mo < curM)) {
          out.className = 'alert-box alert-bad';
          out.innerHTML = `<strong>RETIRED (15-Year Composite Limit Reached in ${String(mo).padStart(2,'0')}-${bornY + 15}):</strong> Standard DOT-SP carbon/fiberglass cylinders cannot be hydrotested past 15 years from original manufacture.`;
          return;
        }
      }
      const cycle = interval === 12 ? 12 : (interval === 10 ? 10 : 5);
      const dueY = yr + cycle;
      const expired = (dueY < curY) || (dueY === curY && mo < curM);
      out.className = 'alert-box ' + (expired ? 'alert-warn' : 'alert-ok');
      out.innerHTML = expired
        ? `<strong>DUE FOR ${isPropaneVis ? 'PROPANE VISUAL INSPECTION' : 'HYDRO TEST'} NOW:</strong> Requalification expired at the end of <strong class="mono">${String(mo).padStart(2,'0')}-${dueY}</strong>. Pull up to our 24/7 Loading Dock for drop-off.`
        : `<strong>IN-DATE &amp; VALID THROUGH <span class="mono">${String(mo).padStart(2,'0')}-${dueY}</span>:</strong> Next ${cycle}-year DOT ${isPropaneVis ? 'propane visual inspection' : 'hydro test'} is due by <span class="mono">${String(mo).padStart(2,'0')}-${dueY}</span>.`;
    }

    (function initStorefront() {
      document.getElementById('trkPrefDate').value = new Date().toISOString().slice(0, 10);
      updateHeroScrollEffect();
      loadHolidayStorefrontTheme();
      loadPublicOperatingSchedule();
      const params = new URLSearchParams(location.search);
      const savedT = localStorage.getItem('webers_last_ticket') || '';
      const savedP = localStorage.getItem('webers_last_phone4') || '';
      if (params.get('ticket')) {
        document.getElementById('trkTicket').value = params.get('ticket');
        if (params.get('p')) document.getElementById('trkLast4').value = params.get('p');
        setTimeout(() => {
          document.getElementById('tracker')?.scrollIntoView({ behavior: 'smooth' });
          lookupTicket();
        }, 200);
      } else if (savedT) {
        document.getElementById('trkTicket').value = savedT;
        document.getElementById('trkLast4').value = savedP;
      }
      if (params.get('mode') === 'pickup') {
        setCheckinMode('PICKUP_NOTICE');
        if (params.get('customer')) document.getElementById('pickCustName').value = params.get('customer');
        setTimeout(() => { document.getElementById('dropoff')?.scrollIntoView({ behavior: 'smooth' }); document.getElementById('pickPersonName')?.focus(); }, 150);
      } else if (params.get('mode') === 'truck') {
        setCheckinMode('TRUCK_REQUEST');
        setTimeout(() => document.getElementById('dropoff')?.scrollIntoView({ behavior: 'smooth' }), 150);
      }
    })();
    refreshPublicSla(4);
