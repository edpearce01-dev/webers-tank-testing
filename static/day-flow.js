// Weber's Tank Testing — v10.0 Unified PIN Identity, Role Lockout, Quick-Switch PIN & Daily Shift Engine
(function () {
  window.CURRENT_USER = null;
  let DAY_DATA = null;
  let LAST_TOAST_UID = '';

  const esc = s => String(s ?? '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));

  function showAuthToast(msg) {
    let t = document.getElementById('webersAuthToast');
    if (!t) {
      t = document.createElement('div');
      t.id = 'webersAuthToast';
      t.className = 'auth-toast';
      document.body.appendChild(t);
    }
    t.innerHTML = msg;
    t.classList.add('show');
    clearTimeout(t._timer);
    t._timer = setTimeout(() => t.classList.remove('show'), 3400);
  }

  function roleBadgeLabel(u) {
    if (!u) return 'PIN ☰';
    if (u.role === 'OWNER') return `${u.initials} • Owner`;
    if (u.role === 'SYSTEM_ADMIN') return `${u.initials} • Admin`;
    return `${u.initials} • Crew`;
  }

  function roleFullTitle(u) {
    if (!u) return 'Not Signed In';
    if (u.role === 'OWNER') return 'Owner • Official DOT Sign-Off (RP)';
    if (u.role === 'SYSTEM_ADMIN') return 'System Admin • Diagnostic Mode (No DOT Sign-Off)';
    return 'Shop Crew • Prep, Stand & Finish Access';
  }

  function applyRoleLockouts(u) {
    if (!u) return;
    document.body.dataset.role = u.role;
    localStorage.setItem('webers_active_crew', u.initials || 'RP');

    // Sync hidden/legacy crew select on /ops if present
    const legacySel = document.getElementById('activeCrewSelect');
    if (legacySel) {
      legacySel.innerHTML = `<option value="${esc(u.initials)}" selected>${esc(u.initials)} — ${esc(u.name)}</option>`;
      legacySel.disabled = true;
    }

    // Install / update Unified Identity Pill in .top-controls across /ops, /schedule, and /office
    const topCtrls = document.querySelector('.top-controls');
    if (topCtrls) {
      // Hide old plain menu/lock buttons so there is ONE clean Identity & Menu button on the far right
      topCtrls.querySelectorAll('button[onclick*="toggleMenuDrawer"], button[onclick*="logoutCrew"]').forEach(b => {
        if (b.id !== 'unifiedIdentityBtn') b.hidden = true;
      });
      let idBtn = document.getElementById('unifiedIdentityBtn');
      if (!idBtn) {
        idBtn = document.createElement('button');
        idBtn.id = 'unifiedIdentityBtn';
        idBtn.type = 'button';
        idBtn.onclick = () => window.openAccountSwitchDrawer();
        topCtrls.appendChild(idBtn);
      }
      idBtn.className = `role-user-pill role-${u.role}`;
      idBtn.textContent = roleBadgeLabel(u);
      idBtn.title = `${u.name} (${roleFullTitle(u)}) — Click to Switch PIN or Open Menu`;
    }

    // Crew Calibration Lockout Card on /ops (#calFormCard)
    const calForm = document.getElementById('calFormCard');
    let crewLockCard = document.getElementById('crewCalLockNotice');
    if (calForm) {
      if (u.role === 'CREW') {
        calForm.style.display = 'none';
        if (!crewLockCard) {
          crewLockCard = document.createElement('div');
          crewLockCard.id = 'crewCalLockNotice';
          crewLockCard.className = 'crew-cal-lock-box';
          calForm.insertAdjacentElement('beforebegin', crewLockCard);
        }
        const anyOk = Boolean(DAY_DATA?.cal_status?.short_calibrated || DAY_DATA?.cal_status?.tall_calibrated);
        crewLockCard.hidden = anyOk;
        crewLockCard.innerHTML = `
          <div>
            <strong style="color:#92400e;font-size:0.94rem;">🔒 Owner Calibration Gate (Signed in as ${esc(u.name)})</strong>
            <div style="font-size:0.82rem;color:#78350f;margin-top:2px;">Water-jacket calibration must be certified under Ryan Pearce's Owner PIN. You can prep &amp; scan bottles or work Finish &amp; Shelf anytime.</div>
          </div>
          <div style="display:flex;gap:7px;flex-wrap:wrap;">
            <button type="button" class="secondary" style="background:#fff;border-color:#d97706;color:#92400e;font-weight:800;" onclick="window.openAccountSwitchDrawer(true)">🔄 Switch to Ryan PIN</button>
            <button type="button" class="primary" style="width:auto;margin:0;padding:8px 14px;" onclick="typeof jumpToTab==='function'&&jumpToTab('intake')">Go to 2. Prep &amp; Scan →</button>
          </div>`;
      } else {
        calForm.style.display = '';
        if (crewLockCard) crewLockCard.hidden = true;
        // Update Certify button wording for Admin vs Owner
        const certBtn = document.getElementById('btnCertCal');
        if (certBtn) {
          certBtn.textContent = u.role === 'SYSTEM_ADMIN'
            ? 'Certify Calibration (ADMIN Diagnostic — No RP Sign-Off)'
            : 'Certify Calibration Under Ryan Pearce (RP)';
        }
        const standBtn = document.getElementById('btnCompleteBench');
        if (standBtn && !standBtn.disabled) {
          standBtn.textContent = u.role === 'SYSTEM_ADMIN'
            ? 'Certify & Advance (ADMIN Diagnostic — Marks ADM*)'
            : 'Certify & Advance to Next Cylinder (RP)';
        }
      }
    }

    // If Crew was sitting on Service Rates tab in /office, bounce cleanly to Create Invoice
    if (u.role === 'CREW' && location.pathname === '/office') {
      const ratesSec = document.getElementById('tab-rates');
      if (ratesSec && !ratesSec.hidden && typeof window.switchOfficeTab === 'function') {
        window.switchOfficeTab('invoice');
      }
    }
  }

  function renderSidebarShiftSection() {
    const railNav = document.querySelector('.side-rail .rail-nav');
    if (!railNav || !DAY_DATA || !window.CURRENT_USER) return;
    const u = window.CURRENT_USER;
    let box = document.getElementById('shiftFlowRailBox');
    if (!box) {
      box = document.createElement('details');
      box.id = 'shiftFlowRailBox';
      box.className = 'rail-tools-drawer';
      railNav.appendChild(box);
    }
    const openTasks = (DAY_DATA.tasks || []).filter(t => t.status === 'OPEN').length;
    const isBoss = u.role === 'OWNER' || u.role === 'SYSTEM_ADMIN';
    const locked = Boolean(DAY_DATA.day_plan?.lineup_locked);

    box.innerHTML = `
      <summary>🛠️ Shift &amp; Crew Tools ${openTasks ? `(${openTasks})` : ''} ▾</summary>
      <div style="display:flex;flex-direction:column;gap:4px;margin-top:4px;">
        ${isBoss ? `
          <button type="button" class="rail-btn" onclick="window.openStartDayModal()">
            <span>☀️ Start Your Day</span>
            <span class="nav-badge ${locked ? 'green' : 'amber'}">${locked ? 'Locked' : 'Plan'}</span>
          </button>` : ''}
        <button type="button" class="rail-btn" onclick="window.openTasksModal()">
          <span>📋 Shop &amp; Property To-Do</span>
          <span class="nav-badge ${openTasks ? 'amber' : 'green'}">${openTasks}</span>
        </button>
        <button type="button" class="rail-btn" onclick="window.openShopMessagesDrawer && window.openShopMessagesDrawer()">
          <span>💬 Crew &amp; Web Chat</span>
          <span class="nav-badge" id="railMsgBadge">Chat</span>
        </button>
        <button type="button" class="rail-btn" onclick="window.openEndDayModal()">
          <span>🌙 ${isBoss ? 'End Your Day' : 'Wrap Up Shift'}</span>
          <span class="nav-badge">EOD</span>
        </button>
        <button type="button" class="rail-btn" onclick="location.href='/merch'">
          <span>👕 Crew Merch</span>
          <span class="nav-badge">Gear</span>
        </button>
        ${isBoss ? `
          <button type="button" class="rail-btn" onclick="location.href='/owner'">
            <span>⚙️ Shop Setup</span>
            <span class="nav-badge">Admin</span>
          </button>` : ''}
      </div>`;
  }

  function renderUncalibratedOpsBanner() {
    let bar = document.getElementById('opsUncalBanner');
    // Show prominent uncalibrated alert ONLY on /ops when viewing Today!
    if (location.pathname !== '/ops' && location.pathname !== '/') {
      if (bar) bar.remove();
      return;
    }
    const ws = document.querySelector('main.ops-workspace');
    if (!ws || !DAY_DATA) return;
    const selDate = document.getElementById('globalDate')?.value || DAY_DATA.today;
    const cs = DAY_DATA.cal_status || {};
    const anyCalibrated = Boolean(cs.short_calibrated || cs.tall_calibrated);
    if (selDate !== DAY_DATA.today || anyCalibrated) {
      if (bar) bar.remove();
      return;
    }
    if (!bar) {
      bar = document.createElement('div');
      bar.id = 'opsUncalBanner';
      bar.className = 'uncal-alert-bar';
      ws.prepend(bar);
    }
    const missing = (!cs.short_calibrated && !cs.tall_calibrated)
      ? 'Neither Short nor Tall Jacket is calibrated yet today.'
      : (!cs.short_calibrated ? 'Short Jacket still needs calibration today.' : 'Tall Jacket still needs calibration today.');
    const isCrew = window.CURRENT_USER?.role === 'CREW';
    bar.innerHTML = `
      <div>
        <strong style="font-size:0.92rem;">⚠️ Uncalibrated Today (${esc(DAY_DATA.today)}) — Stand Locked Until Verified</strong>
        <div style="font-size:0.8rem;opacity:0.92;">${missing} Prep &amp; Scan is open.</div>
      </div>
      <div>
        ${isCrew
          ? `<button type="button" class="secondary" style="background:#fff;color:#991b1b;border:none;font-weight:800;" onclick="window.openAccountSwitchDrawer(true)">🔄 Switch to Ryan PIN</button>`
          : `<button type="button" class="secondary" style="background:#fde68a;color:#111827;border:none;font-weight:800;" onclick="typeof jumpToTab==='function'&&(jumpToTab('cal'),setCalExpanded(true))">Calibrate Now →</button>`}
      </div>`;
  }

  // --- UNIFIED ACCOUNT & 2-SECOND SWITCH-PIN DRAWER (WORKS ON /OPS, /SCHEDULE & /OFFICE) ---
  window.openAccountSwitchDrawer = function (focusPin = false) {
    const u = window.CURRENT_USER || { name: 'Crew', initials: '—', role: 'CREW' };
    const isBoss = u.role === 'OWNER' || u.role === 'SYSTEM_ADMIN';
    let m = document.getElementById('unifiedAccountDrawer');
    if (!m) {
      m = document.createElement('div');
      m.id = 'unifiedAccountDrawer';
      m.className = 'modal-bg';
      m.onclick = e => { if (e.target === m) m.hidden = true; };
      document.body.appendChild(m);
    }
    m.hidden = false;
    m.innerHTML = `
      <div class="drawer-panel">
        <div style="display:flex;justify-content:space-between;align-items:center;">
          <h3 style="margin:0;font-family:var(--font-display);font-size:1.3rem;color:var(--forest);">Account &amp; Controls</h3>
          <button type="button" class="secondary" onclick="document.getElementById('unifiedAccountDrawer').hidden=true">✖</button>
        </div>

        <div class="day-sec-box" style="margin:0;background:#f0fdf4;border-color:#86efac;">
          <div style="font-size:0.7rem;font-weight:800;color:#166534;text-transform:uppercase;">Signed In Active User</div>
          <div style="font-size:1.05rem;font-weight:800;color:var(--forest);margin-top:2px;">${esc(u.name)} (${esc(u.initials)})</div>
          <div style="font-size:0.77rem;color:#15803d;font-weight:700;margin-top:2px;">${esc(roleFullTitle(u))}</div>
        </div>

        <div class="day-sec-box" style="margin:0;">
          <label style="margin-bottom:5px;">🔄 Quick-Switch User PIN</label>
          <div style="display:flex;gap:6px;">
            <input type="password" inputmode="numeric" id="quickSwitchPinInput" class="pin-mono-input" placeholder="Enter PIN"
              onkeydown="if(event.key==='Enter')window.submitQuickSwitchPin()" style="flex:1;padding:8px;" />
            <button type="button" class="primary" style="width:auto;margin:0;padding:8px 14px;" onclick="window.submitQuickSwitchPin()">Switch</button>
          </div>
          <div id="quickSwitchErr" style="color:#b91c1c;font-size:0.76rem;font-weight:700;margin-top:5px;" hidden>Incorrect PIN — try again.</div>
        </div>

        <div style="display:flex;flex-direction:column;gap:7px;">
          ${isBoss ? `<button type="button" class="secondary" style="padding:10px;justify-content:flex-start;" onclick="document.getElementById('unifiedAccountDrawer').hidden=true;window.openStartDayModal()">☀️ Start Your Day &amp; Lock Lineup</button>` : ''}
          <button type="button" class="secondary" style="padding:10px;justify-content:flex-start;" onclick="document.getElementById('unifiedAccountDrawer').hidden=true;window.openTasksModal()">📋 Shop &amp; Property To-Do List</button>
          <button type="button" class="secondary" style="padding:10px;justify-content:flex-start;" onclick="document.getElementById('unifiedAccountDrawer').hidden=true;window.openShopMessagesDrawer&&window.openShopMessagesDrawer()">💬 Crew &amp; Website Messages</button>
          <button type="button" class="secondary" style="padding:10px;justify-content:flex-start;" onclick="document.getElementById('unifiedAccountDrawer').hidden=true;window.openEndDayModal()">🌙 ${isBoss ? 'End Your Day Closeout' : 'Wrap Up My Shift'}</button>
          <button type="button" class="secondary" style="padding:10px;justify-content:flex-start;" onclick="document.getElementById('unifiedAccountDrawer').hidden=true;typeof toggleFeedbackModal==='function'?toggleFeedbackModal(true):(location.href='/ops')">💬 Crew Feedback &amp; Notes</button>
          <button type="button" class="secondary" style="padding:10px;justify-content:flex-start;background:#fffbeb;border-color:#f59e0b;color:#92400e;font-weight:800;" onclick="location.href='/merch'">👕 Crew Gear &amp; Merch Locker</button>
        </div>

        ${isBoss ? `
        <div style="border-top:1px solid var(--border-soft);padding-top:9px;display:flex;flex-direction:column;gap:7px;">
          <div class="rail-section-lbl" style="padding:0;">Owner &amp; Admin Settings</div>
          <button type="button" class="secondary" style="padding:10px;justify-content:flex-start;" onclick="document.getElementById('unifiedAccountDrawer').hidden=true;window.openPinManagerModal()">🔐 Manage Individual Crew PINs</button>
          <button type="button" class="secondary" style="padding:10px;justify-content:flex-start;" onclick="document.getElementById('unifiedAccountDrawer').hidden=true;window.openHolidayThemeModal&&window.openHolidayThemeModal()">🎨 Holiday Themes &amp; Shop Pups</button>
          <button type="button" class="secondary" style="padding:10px;justify-content:flex-start;" onclick="document.getElementById('unifiedAccountDrawer').hidden=true;location.href='/owner'">⚙ Facility Settings &amp; Cert Dates</button>
        </div>` : ''}

        <div style="border-top:1px solid var(--border-soft);padding-top:9px;margin-top:auto;display:flex;flex-direction:column;gap:7px;">
          <button type="button" class="secondary" style="padding:10px;justify-content:center;color:#b91c1c;border-color:#fca5a5;background:#fef2f2;" onclick="window.signOutCurrentDevice()">🔒 Sign Out This Device</button>
        </div>
      </div>`;

    if (focusPin) setTimeout(() => document.getElementById('quickSwitchPinInput')?.focus(), 50);
  };

  window.submitQuickSwitchPin = async function () {
    const inp = document.getElementById('quickSwitchPinInput');
    const err = document.getElementById('quickSwitchErr');
    const pin = (inp?.value || '').trim();
    if (!pin) return;
    const res = await fetch('/api/auth/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pin }),
    });
    if (!res.ok) { if (err) err.hidden = false; return; }
    const data = await res.json();
    document.getElementById('unifiedAccountDrawer').hidden = true;
    await window.syncAuthAndRoleUI(data.user, true);
    if (typeof loadState === 'function') await loadState(true);
    else if (typeof loadOfficeState === 'function') await loadOfficeState();
    else if (typeof loadScheduleState === 'function') await loadScheduleState();
  };

  window.signOutCurrentDevice = async function () {
    await fetch('/api/auth/logout', { method: 'POST' });
    window.CURRENT_USER = null;
    LAST_TOAST_UID = '';
    const d = document.getElementById('unifiedAccountDrawer');
    if (d) d.hidden = true;
    location.reload();
  };

  // --- INDIVIDUAL CREW PIN MANAGER MODAL (OWNER & ADMIN) ---
  window.openPinManagerModal = async function () {
    const res = await fetch('/api/crew/pins');
    if (!res.ok) return alert('Only Ryan (Owner) or Admin can manage PINs.');
    const data = await res.json();
    let m = document.getElementById('pinMgrModal');
    if (!m) {
      m = document.createElement('div'); m.id = 'pinMgrModal'; m.className = 'modal-bg';
      m.onclick = e => { if (e.target === m) m.hidden = true; };
      document.body.appendChild(m);
    }
    m.hidden = false;
    const canAdmin = data.viewer_role === 'SYSTEM_ADMIN';
    m.innerHTML = `
      <div class="modal" style="max-width:680px;">
        <div class="card-hdr">
          <div><h3>🔐 Individual Crew &amp; Owner PINs</h3><small style="color:var(--muted);">Every user signs in with their own PIN (resets daily at Midnight)</small></div>
          <button type="button" class="secondary" onclick="document.getElementById('pinMgrModal').hidden=true">✖ Close</button>
        </div>
        <div style="margin-bottom:12px;">
          ${data.users.map(u => `
            <div class="pin-mgr-row">
              <input id="pmName_${u.id}" value="${esc(u.name)}" placeholder="Full Name" />
              <input id="pmInit_${u.id}" value="${esc(u.initials)}" maxlength="4" class="mono" style="text-align:center;font-weight:800;" />
              <input id="pmPin_${u.id}" value="${esc(u.pin)}" maxlength="10" class="pin-mono-input" />
              <span class="sub-badge" style="text-align:center;">${esc(u.role)}</span>
              <div style="display:flex;gap:5px;">
                <button type="button" class="secondary" style="background:#dcfce7;color:#166534;border-color:#86efac;" onclick="window.savePinRow(${u.id}, '${u.role}')">Save</button>
                ${u.role === 'CREW' ? `<button type="button" class="secondary" style="color:#b91c1c;" onclick="window.deletePinRow(${u.id})">✕</button>` : ''}
              </div>
            </div>`).join('')}
        </div>
        <div class="day-sec-box">
          <label>➕ Add New Crew Member PIN</label>
          <div class="grid" style="align-items:end;">
            <div><label>Full Name</label><input id="pmNewName" placeholder="e.g. Alex Pearce" /></div>
            <div><label>Initials</label><input id="pmNewInit" maxlength="4" class="mono" placeholder="AP" /></div>
            <div><label>4+ Digit PIN</label><input id="pmNewPin" maxlength="10" class="pin-mono-input" placeholder="4444" /></div>
            <div><label>Role</label>
              <select id="pmNewRole">
                <option value="CREW">CREW</option>
                ${canAdmin ? '<option value="OWNER">OWNER</option><option value="SYSTEM_ADMIN">SYSTEM_ADMIN</option>' : ''}
              </select>
            </div>
          </div>
          <button type="button" class="primary" onclick="window.addNewPinUser()">+ Create Individual PIN</button>
        </div>
      </div>`;
  };

  window.savePinRow = async function (id, role) {
    const res = await fetch('/api/crew/pins', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        id, role,
        name: document.getElementById('pmName_' + id).value,
        initials: document.getElementById('pmInit_' + id).value,
        pin: document.getElementById('pmPin_' + id).value,
      }),
    });
    if (!res.ok) return alert((await res.json()).detail || 'Save failed.');
    showAuthToast('✓ Saved PIN changes');
    window.openPinManagerModal();
  };

  window.addNewPinUser = async function () {
    const res = await fetch('/api/crew/pins', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: document.getElementById('pmNewName').value,
        initials: document.getElementById('pmNewInit').value,
        pin: document.getElementById('pmNewPin').value,
        role: document.getElementById('pmNewRole').value,
      }),
    });
    if (!res.ok) return alert((await res.json()).detail || 'Could not create PIN.');
    showAuthToast('✓ Added new Crew PIN');
    window.openPinManagerModal();
  };

  window.deletePinRow = async function (id) {
    if (!confirm('Remove this Crew PIN?')) return;
    const res = await fetch('/api/crew/pins/' + id, { method: 'DELETE' });
    if (!res.ok) return alert((await res.json()).detail || 'Delete failed.');
    window.openPinManagerModal();
  };

  // --- START DAY, CREW TASKS & END DAY MODALS ---
  window.openStartDayModal = async function () {
    await refreshDayOverview();
    if (!DAY_DATA) return;
    const dp = DAY_DATA.day_plan || {};
    let m = document.getElementById('startDayModal');
    if (!m) {
      m = document.createElement('div'); m.id = 'startDayModal'; m.className = 'modal-bg';
      m.onclick = e => { if (e.target === m) m.hidden = true; };
      document.body.appendChild(m);
    }
    m.hidden = false;
    m.innerHTML = `
      <div class="modal" style="max-width:620px;">
        <div class="card-hdr">
          <h3>☀️ Start Your Day — Lock Lineup (${esc(DAY_DATA.today)})</h3>
          <button type="button" class="secondary" onclick="document.getElementById('startDayModal').hidden=true">✖ Close</button>
        </div>
        <div class="day-sec-box">
          <div class="grid">
            <div><label>Today's Operating Mode</label>
              <select id="sdMode">
                <option value="FULL_HYDRO" ${dp.shift_mode === 'FULL_HYDRO' ? 'selected' : ''}>Full Hydro &amp; Prep</option>
                <option value="ROAD_RUN" ${dp.shift_mode === 'ROAD_RUN' ? 'selected' : ''}>Truck Run &amp; Partial Hydro</option>
                <option value="PREP_ONLY" ${dp.shift_mode === 'PREP_ONLY' ? 'selected' : ''}>Prep &amp; Devalve Only</option>
                <option value="BACKROOM_ONLY" ${dp.shift_mode === 'BACKROOM_ONLY' ? 'selected' : ''}>24/7 Back Room Only</option>
              </select>
            </div>
            <div><label>Target Bottle Capacity</label><input type="number" id="sdCap" value="${dp.bottle_capacity ?? 24}" /></div>
            <div style="grid-column:1/-1;"><label>Morning Shift Note (Shows on Live TV)</label><input id="sdNotes" value="${esc(dp.day_notes || '')}" placeholder="e.g. Baden VFD priority batch first..." /></div>
          </div>
          <label class="inline-check" style="margin-top:10px;">
            <input type="checkbox" id="sdAutopace" ${dp.allow_extra_autopace !== 0 ? 'checked' : ''} />
            <span>Auto-pull upcoming batches into today if open capacity remains</span>
          </label>
        </div>
        <button type="button" class="primary" onclick="window.saveStartDayLock()">🔒 Lock Today's Lineup &amp; Auto-Pace</button>
      </div>`;
  };

  window.saveStartDayLock = async function () {
    const res = await fetch('/api/day/lock-lineup', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        shift_mode: document.getElementById('sdMode').value,
        bottle_capacity: parseInt(document.getElementById('sdCap').value || '24', 10),
        day_notes: document.getElementById('sdNotes').value,
        allow_extra_autopace: document.getElementById('sdAutopace').checked,
      }),
    });
    if (!res.ok) return alert((await res.json()).detail || 'Could not lock lineup.');
    document.getElementById('startDayModal').hidden = true;
    showAuthToast('✓ Today\'s lineup locked');
    await refreshDayOverview();
    if (typeof loadState === 'function') loadState(false);
  };

  window.openTasksModal = async function () {
    await refreshDayOverview();
    if (!DAY_DATA) return;
    let m = document.getElementById('crewTasksModal');
    if (!m) {
      m = document.createElement('div'); m.id = 'crewTasksModal'; m.className = 'modal-bg';
      m.onclick = e => { if (e.target === m) m.hidden = true; };
      document.body.appendChild(m);
    }
    m.hidden = false;
    const tasks = DAY_DATA.tasks || [];
    m.innerHTML = `
      <div class="modal" style="max-width:660px;">
        <div class="card-hdr">
          <div>
            <h3>📋 Shop &amp; Property To-Do List</h3>
            <small style="color:var(--muted);">Shared across Ryan &amp; all Crew — open items broadcast onto Live TV</small>
          </div>
          <button type="button" class="secondary" onclick="document.getElementById('crewTasksModal').hidden=true">✖ Close</button>
        </div>
        <div class="day-sec-box">
          <div class="grid">
            <div style="grid-column:1/-1;"><label>Task Description</label><input id="ctTitle" placeholder="e.g. Service Kubota/ATV for winter plowing, Firewood, Clean shop garage..." onkeydown="if(event.key==='Enter')window.addCrewTaskNow()" /></div>
            <div><label>Assign To</label>
              <select id="ctAssign">
                <option value="ALL">All Crew</option>
                ${(DAY_DATA.crew_list || []).map(c => `<option value="${esc(c.initials)}">${esc(c.name)} (${esc(c.initials)})</option>`).join('')}
              </select>
            </div>
            <div><label>Area</label>
              <select id="ctCat">
                <option value="PROPERTY">🚜 Property &amp; Grounds</option>
                <option value="SHOP" selected>🔧 Shop &amp; Garage</option>
                <option value="CYLINDERS">🧪 Cylinders &amp; Stand</option>
              </select>
            </div>
            <div><label>Priority</label>
              <select id="ctPrio">
                <option value="NORMAL" selected>Normal</option>
                <option value="HIGH">⚡ High Priority</option>
              </select>
            </div>
          </div>
          <button type="button" class="primary" onclick="window.addCrewTaskNow()">+ Add To-Do Item</button>
        </div>
        <div>
          ${tasks.length ? tasks.map(t => `
            <div class="rec-item">
              <div class="rec-main">
                <div class="rec-title">
                  <span style="${t.status === 'DONE' ? 'text-decoration:line-through;color:var(--muted);' : 'font-weight:800;'}">${t.category === 'PROPERTY' ? '🚜 ' : '🔧 '}${esc(t.title)}</span>
                  <span class="sub-badge">${esc(t.assigned_name || t.assigned_to)}</span>
                  ${t.priority === 'HIGH' && t.status !== 'DONE' ? '<span class="rush-badge">⚡ HIGH</span>' : ''}
                </div>
                <div class="rec-sub">${esc(t.category)} • ${t.status === 'DONE' ? `Done by ${esc(t.completed_by)} at ${esc(t.completed_at)}` : 'Open • Shows on TV'}</div>
              </div>
              <div class="rec-actions">
                <button type="button" class="secondary" onclick="window.toggleCrewTaskDone(${t.id}, '${t.status === 'DONE' ? 'OPEN' : 'DONE'}')">${t.status === 'DONE' ? 'Undo' : '✓ Done'}</button>
                <button type="button" class="secondary" onclick="window.promptEditCrewTask(${t.id}, '${esc(t.title).replace(/'/g, "\\'")}')">✎</button>
                <button type="button" class="secondary" style="color:#b91c1c;" onclick="window.removeCrewTask(${t.id})">✕</button>
              </div>
            </div>`).join('') : '<div style="color:var(--muted);font-size:0.86rem;padding:8px 0;">No open tasks right now.</div>'}
        </div>
      </div>`;
  };

  window.addCrewTaskNow = async function () {
    const title = document.getElementById('ctTitle').value.trim();
    if (!title) return;
    await fetch('/api/day/tasks', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title,
        assigned_to: document.getElementById('ctAssign').value,
        category: document.getElementById('ctCat').value,
        priority: document.getElementById('ctPrio')?.value || 'NORMAL',
      }),
    });
    if (typeof loadState === 'function') loadState(false);
    window.openTasksModal();
  };

  window.promptEditCrewTask = async function (id, curTitle) {
    const next = prompt('Edit To-Do item:', curTitle);
    if (next === null || !next.trim()) return;
    await fetch('/api/day/tasks/' + id, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: next.trim() }),
    });
    if (typeof loadState === 'function') loadState(false);
    window.openTasksModal();
  };

  window.toggleCrewTaskDone = async function (id, status) {
    await fetch('/api/day/tasks/' + id, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }) });
    window.openTasksModal();
  };

  window.removeCrewTask = async function (id) {
    await fetch('/api/day/tasks/' + id, { method: 'DELETE' });
    window.openTasksModal();
  };

  window.openEndDayModal = async function () {
    await refreshDayOverview();
    if (!DAY_DATA) return;
    const s = DAY_DATA.eod_summary || {};
    const isBoss = window.CURRENT_USER?.role === 'OWNER' || window.CURRENT_USER?.role === 'SYSTEM_ADMIN';
    let m = document.getElementById('endDayModal');
    if (!m) {
      m = document.createElement('div'); m.id = 'endDayModal'; m.className = 'modal-bg';
      m.onclick = e => { if (e.target === m) m.hidden = true; };
      document.body.appendChild(m);
    }
    m.hidden = false;
    m.innerHTML = `
      <div class="modal" style="max-width:560px;">
        <div class="card-hdr">
          <h3>🌙 ${isBoss ? 'End-of-Day Shop Closeout' : 'Wrap Up My Shift'}</h3>
          <button type="button" class="secondary" onclick="document.getElementById('endDayModal').hidden=true">✖ Close</button>
        </div>
        <div class="huddle-kpis">
          <div class="huddle-kpi"><small>Tested Today</small><strong>${s.tested_today || 0}</strong></div>
          <div class="huddle-kpi"><small>Passed (A)</small><strong>${s.passed_today || 0}</strong></div>
          <div class="huddle-kpi"><small>Still Queued</small><strong>${s.queued_remaining || 0}</strong></div>
          <div class="huddle-kpi"><small>Need B386 Mark</small><strong>${s.unmarked_pass || 0}</strong></div>
        </div>
        <div class="day-sec-box">
          <label>Shift Handoff Note for Ryan / Tomorrow</label>
          <input id="eodNote" placeholder="What got finished, valve/adapter notes, supplies low..." />
          ${isBoss ? `
          <label class="inline-check" style="margin-top:10px;">
            <input type="checkbox" id="eodRoll" checked />
            <span>Roll unfinished today shop batches cleanly to tomorrow</span>
          </label>` : ''}
        </div>
        <button type="button" class="primary" onclick="window.submitEndDayCloseout()">✓ Save Shift Wrap-Up</button>
      </div>`;
  };

  window.submitEndDayCloseout = async function () {
    await fetch('/api/day/end-day', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        shift_note: document.getElementById('eodNote').value,
        roll_unfinished: Boolean(document.getElementById('eodRoll')?.checked),
      }),
    });
    document.getElementById('endDayModal').hidden = true;
    showAuthToast('✓ Shift wrap-up saved');
    await refreshDayOverview();
  };

  async function refreshDayOverview() {
    try {
      const res = await fetch('/api/day/overview');
      if (!res.ok) return;
      DAY_DATA = await res.json();
      if (DAY_DATA.user) {
        window.CURRENT_USER = DAY_DATA.user;
        applyRoleLockouts(DAY_DATA.user);
      }
      renderSidebarShiftSection();
      renderUncalibratedOpsBanner();
    } catch (_) {}
  }

  window.syncAuthAndRoleUI = async function (userObj = null, forceToast = false) {
    let u = userObj;
    if (!u) {
      try {
        const res = await fetch('/api/auth/status');
        const d = await res.json();
        if (d.authenticated && d.user) u = d.user;
      } catch (_) {}
    }
    if (!u) return;
    window.CURRENT_USER = u;
    applyRoleLockouts(u);
    const sig = `${u.id}:${u.role}`;
    if (forceToast || (LAST_TOAST_UID && LAST_TOAST_UID !== sig)) {
      showAuthToast(`✓ Signed in as <strong>${esc(u.name)}</strong> (${esc(u.role.replace('_', ' '))})`);
    }
    LAST_TOAST_UID = sig;
    await refreshDayOverview();
    if (typeof window.refreshWebersTheme === 'function') window.refreshWebersTheme();

    // Support cross-page '?open=settings' deep link from /schedule or /office
    const params = new URLSearchParams(location.search);
    if (params.get('open') === 'settings' && typeof toggleGuide === 'function') {
      toggleGuide(true);
      history.replaceState(null, '', location.pathname);
    }
  };

  window.addEventListener('load', () => {
    const shell = document.getElementById('appShell');
    if (shell && !shell.hidden) window.syncAuthAndRoleUI(null, false);
  });
  setInterval(() => {
    const shell = document.getElementById('appShell');
    if (shell && !shell.hidden) refreshDayOverview();
  }, 30000);
})();

// =========================================================================
// v13.0 KENNY'S WORKER TIME CLOCK WIDGET (SIDEBAR + TOPBAR + MY TIMECARD)
// =========================================================================
if (!window._WEBER_TIMECLOCK_V13) {
  window._WEBER_TIMECLOCK_V13 = true;
  window.TC_STATE = { clocked_in: false, open_punch: null, today_total_minutes: 0, active_workers: [] };

  function fmtHrsMins(mins) {
    const m = Math.max(0, parseInt(mins || 0, 10));
    return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
  }

  window.refreshWorkerTimeClock = async function () {
    const shell = document.getElementById('appShell');
    if (!shell || shell.hidden) return;
    try {
      const res = await fetch('/api/timeclock/status');
      if (!res.ok) return;
      window.TC_STATE = await res.json();
      renderTimeClockUI();
    } catch (_) {}
  };

  function renderTimeClockUI() {
    const st = window.TC_STATE || {};
    const rail = document.querySelector('.side-rail .rail-nav');
    if (rail && !document.getElementById('railTimeClockBox')) {
      const box = document.createElement('div');
      box.id = 'railTimeClockBox';
      box.style.cssText = 'margin-top:8px;padding:9px 10px;border-radius:10px;border:1.5px solid #cbd5e1;background:#f8fafc;';
      rail.appendChild(box);
    }
    const box = document.getElementById('railTimeClockBox');
    if (box) {
      box.style.borderColor = st.clocked_in ? '#10b981' : '#cbd5e1';
      box.style.background = st.clocked_in ? '#ecfdf5' : '#f8fafc';
      box.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px;">
          <strong style="font-size:0.76rem;color:#0c261b;">⏱️ TIME CLOCK</strong>
          <span style="font-size:0.7rem;font-weight:800;color:${st.clocked_in ? '#047857' : '#64748b'};">
            ${st.clocked_in ? '🟢 IN ' + (st.open_punch?.clock_in_at || '') : '⚪ OUT'}
          </span>
        </div>
        <div style="font-size:0.73rem;color:#334155;margin-bottom:6px;">
          Today: <strong>${fmtHrsMins(st.today_total_minutes)}</strong>
        </div>
        <div style="display:flex;gap:5px;">
          <button type="button" onclick="toggleWorkerPunch()" style="flex:1;padding:7px 8px;border-radius:7px;border:none;font-weight:800;font-size:0.76rem;cursor:pointer;background:${st.clocked_in ? '#dc2626' : '#059669'};color:#fff;">
            ${st.clocked_in ? '🔴 Punch Out' : '🟢 Punch In'}
          </button>
          <button type="button" class="secondary" style="padding:7px 8px;font-size:0.73rem;" onclick="openMyTimecardModal()" title="View Timecard">🕒</button>
        </div>`;
    }

    // Also add compact mobile-accessible pill in .top-controls
    const topCtrl = document.querySelector('.top-controls');
    if (topCtrl && !document.getElementById('topTimeClockBtn')) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.id = 'topTimeClockBtn';
      btn.className = 'menu-pill';
      btn.onclick = () => window.openMyTimecardModal();
      topCtrl.insertBefore(btn, topCtrl.firstChild);
    }
    const topBtn = document.getElementById('topTimeClockBtn');
    if (topBtn) {
      topBtn.style.background = st.clocked_in ? '#dcfce7' : '';
      topBtn.style.color = st.clocked_in ? '#166534' : '';
      topBtn.style.borderColor = st.clocked_in ? '#4ade80' : '';
      topBtn.textContent = st.clocked_in ? `🟢 ${fmtHrsMins(st.today_total_minutes)}` : '⏱️ Clock In';
    }
  }

  window.toggleWorkerPunch = async function () {
    const st = window.TC_STATE || {};
    let note = '';
    if (st.clocked_in) {
      const ask = prompt('Optional shift wrap-up note (or leave blank to Punch Out now):', '');
      if (ask === null) return;
      note = ask;
    }
    const res = await fetch('/api/timeclock/punch', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: st.clocked_in ? 'OUT' : 'IN', notes: note }),
    });
    if (!res.ok) { alert((await res.json()).detail || 'Punch failed.'); return; }
    await window.refreshWorkerTimeClock();
    if (document.getElementById('myTimecardModal') && !document.getElementById('myTimecardModal').hidden) {
      window.openMyTimecardModal();
    }
  };

  window.openMyTimecardModal = async function () {
    let m = document.getElementById('myTimecardModal');
    if (!m) {
      m = document.createElement('div');
      m.id = 'myTimecardModal';
      m.className = 'modal-bg';
      m.onclick = e => { if (e.target === m) m.hidden = true; };
      document.body.appendChild(m);
    }
    m.hidden = false;
    await window.refreshWorkerTimeClock();
    const repRes = await fetch('/api/timeclock/report?days=14');
    const rep = repRes.ok ? await repRes.json() : { punches: [] };
    const st = window.TC_STATE || {};
    const myInit = window.CURRENT_USER?.initials || '';
    const myRows = (rep.punches || []).filter(p => !myInit || p.crew_initials === myInit).slice(0, 14);

    m.innerHTML = `
      <div class="modal" style="max-width:560px;border-top:5px solid ${st.clocked_in ? '#10b981' : '#0c261b'};">
        <div class="card-hdr">
          <div>
            <h3 style="margin:0;">⏱️ Worker Time Clock</h3>
            <small style="color:var(--muted);font-weight:700;">${st.clocked_in ? '🟢 Currently Clocked In since ' + (st.open_punch?.clock_in_at || '') : '⚪ Currently Clocked Out'} • Today: ${fmtHrsMins(st.today_total_minutes)}</small>
          </div>
          <button type="button" class="secondary" onclick="document.getElementById('myTimecardModal').hidden=true">✖</button>
        </div>
        <button type="button" class="primary" style="margin-top:0;margin-bottom:14px;background:${st.clocked_in ? '#dc2626' : '#059669'};" onclick="toggleWorkerPunch()">
          ${st.clocked_in ? '🔴 Punch Out Now' : '🟢 Punch In Now'}
        </button>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
          <strong style="font-size:0.84rem;">Recent Shifts (Last 14 Days)</strong>
          ${rep.is_boss ? '<a class="secondary" style="font-size:0.75rem;padding:4px 9px;" href="/office?tab=timeclock">All Crew Timecards &amp; CSV →</a>' : ''}
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Date</th><th>In</th><th>Out</th><th>Hours</th><th>Note</th></tr></thead>
            <tbody>
              ${myRows.length ? myRows.map(r => `<tr>
                <td><strong>${r.work_date}</strong></td>
                <td>${r.clock_in_at}</td>
                <td>${r.status === 'OPEN' ? '<span class="pass">ACTIVE</span>' : r.clock_out_at}</td>
                <td>${r.status === 'OPEN' ? 'Running' : fmtHrsMins(r.duration_minutes)}</td>
                <td><small>${r.notes || ''}</small></td>
              </tr>`).join('') : '<tr><td colspan="5">No shifts punched yet.</td></tr>'}
            </tbody>
          </table>
        </div>
      </div>`;
  };

  setTimeout(() => window.refreshWorkerTimeClock(), 600);
  setInterval(() => window.refreshWorkerTimeClock(), 30000);
}

// =========================================================================
// v16.0 UNIFIED SHOP MESSAGING (CREW ↔ CREW + WEBSITE CUSTOMER INQUIRIES)
// =========================================================================
if (!window._WEBER_MESSAGES_V16) {
  window._WEBER_MESSAGES_V16 = true;
  let MSG_STATE = { crew_messages: [], web_inquiries: [], crew_directory: [], latest_msg_id: 0, open_inquiries_count: 0 };
  let MSG_TAB = 'CREW';

  const escM = s => String(s ?? '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));

  function seenMsgStorageKey() {
    const init = window.CURRENT_USER?.initials || localStorage.getItem('webers_active_crew') || 'RP';
    return `webers_seen_msg_${init}`;
  }

  function installTopMessagesButton() {
    const topCtrl = document.querySelector('.top-controls');
    if (!topCtrl || document.getElementById('topShopMsgBtn')) return;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.id = 'topShopMsgBtn';
    btn.className = 'menu-pill';
    btn.title = 'Crew Chat & Website Customer Messages';
    btn.onclick = () => window.openShopMessagesDrawer();
    const anchor = document.getElementById('topHelpAiBtn') || topCtrl.firstChild;
    topCtrl.insertBefore(btn, anchor);
    updateMessagesBadges();
  }

  function updateMessagesBadges() {
    const btn = document.getElementById('topShopMsgBtn');
    const seenId = parseInt(localStorage.getItem(seenMsgStorageKey()) || '0', 10);
    const hasNewCrew = (MSG_STATE.latest_msg_id || 0) > seenId;
    const openInq = MSG_STATE.open_inquiries_count || 0;
    const badgeNum = openInq + (hasNewCrew ? 1 : 0);

    if (btn) {
      btn.innerHTML = badgeNum > 0
        ? `💬 <span class="full-lbl">Messages</span> <span class="top-msg-badge">${openInq || '•'}</span>`
        : `💬 <span class="full-lbl">Messages</span>`;
    }
    const railB = document.getElementById('railMsgBadge');
    if (railB) {
      railB.textContent = openInq ? `${openInq} New` : (hasNewCrew ? 'New' : 'Chat');
      railB.className = 'nav-badge ' + ((openInq || hasNewCrew) ? 'amber' : '');
    }
  }

  window.refreshMessagesBadgeSilent = async function () {
    const shell = document.getElementById('appShell');
    if (!shell || shell.hidden) return;
    installTopMessagesButton();
    try {
      const res = await fetch('/api/ping');
      if (!res.ok) return;
      const p = await res.json();
      MSG_STATE.latest_msg_id = p.latest_msg_id || 0;
      MSG_STATE.open_inquiries_count = p.open_inquiries_count || 0;
      updateMessagesBadges();
      const drawer = document.getElementById('shopMessagesDrawer');
      if (drawer && !drawer.hidden) {
        await loadFullMessagesState(false);
      }
    } catch (_) {}
  };

  async function loadFullMessagesState(markSeen = true) {
    const res = await fetch('/api/messages');
    if (!res.ok) return;
    MSG_STATE = await res.json();
    if (markSeen && MSG_STATE.latest_msg_id) {
      localStorage.setItem(seenMsgStorageKey(), String(MSG_STATE.latest_msg_id));
    }
    updateMessagesBadges();
    renderShopMessagesDrawerBody();
  }

  window.openShopMessagesDrawer = async function (forceTab = null) {
    if (forceTab) MSG_TAB = forceTab;
    let d = document.getElementById('shopMessagesDrawer');
    if (!d) {
      d = document.createElement('div');
      d.id = 'shopMessagesDrawer';
      d.className = 'modal-bg';
      d.onclick = e => { if (e.target === d) d.hidden = true; };
      document.body.appendChild(d);
    }
    d.hidden = false;
    d.innerHTML = `
      <div class="drawer-panel msg-drawer-panel">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
          <div>
            <h3 style="margin:0;font-size:1.12rem;">💬 Shop &amp; Customer Messages</h3>
            <small style="color:var(--ui-muted);font-weight:600;">Crew-to-Crew Chat + Website Inquiries</small>
          </div>
          <button type="button" class="secondary" onclick="document.getElementById('shopMessagesDrawer').hidden=true">✖</button>
        </div>

        <div class="cal-view-seg" style="width:100%;display:grid;grid-template-columns:1fr 1fr;" id="msgTabBar">
          <button type="button" class="cal-vseg-btn" id="msgTabBtnCrew" onclick="window.switchMsgDrawerTab('CREW')">👷 Crew Chat</button>
          <button type="button" class="cal-vseg-btn" id="msgTabBtnWeb" onclick="window.switchMsgDrawerTab('WEB')">🌐 Website Inquiries <span id="msgWebCntSpan"></span></button>
        </div>

        <div id="msgDrawerContent" style="display:flex;flex-direction:column;flex:1;min-height:0;gap:10px;">
          <div style="color:var(--ui-muted);font-size:0.85rem;">Loading messages...</div>
        </div>
      </div>`;
    await loadFullMessagesState(true);
    if (!forceTab && MSG_STATE.open_inquiries_count > 0 && !(MSG_STATE.crew_messages || []).length) {
      MSG_TAB = 'WEB';
      renderShopMessagesDrawerBody();
    }
  };

  window.switchMsgDrawerTab = function (tab) {
    MSG_TAB = tab;
    renderShopMessagesDrawerBody();
  };

  function renderShopMessagesDrawerBody() {
    const host = document.getElementById('msgDrawerContent');
    if (!host) return;
    document.getElementById('msgTabBtnCrew')?.classList.toggle('active', MSG_TAB === 'CREW');
    document.getElementById('msgTabBtnWeb')?.classList.toggle('active', MSG_TAB === 'WEB');
    const cntSpan = document.getElementById('msgWebCntSpan');
    if (cntSpan) cntSpan.textContent = MSG_STATE.open_inquiries_count ? `(${MSG_STATE.open_inquiries_count})` : '';

    const myInit = (window.CURRENT_USER?.initials || 'RP').toUpperCase();
    const isBoss = ['OWNER', 'SYSTEM_ADMIN'].includes(window.CURRENT_USER?.role || '');

    if (MSG_TAB === 'CREW') {
      const prevRecip = document.getElementById('msgRecipSel')?.value || 'ALL';
      const prevDraft = document.getElementById('msgBodyInput')?.value || '';
      const msgs = MSG_STATE.crew_messages || [];
      const dirOpts = (MSG_STATE.crew_directory || [])
        .filter(c => c.initials !== myInit)
        .map(c => `<option value="${escM(c.initials)}" ${c.initials === prevRecip ? 'selected' : ''}>Direct → ${escM(c.name)} (${escM(c.initials)})</option>`)
        .join('');

      host.innerHTML = `
        <div class="msg-chat-stream" id="msgChatStream">
          ${msgs.length ? msgs.map(m => {
            const mine = (m.sender_initials || '').toUpperCase() === myInit;
            const recipTag = m.recipient && m.recipient !== 'ALL' ? ` → @${escM(m.recipient)}` : ' → All Crew';
            return `
              <div class="msg-bubble ${mine ? 'mine' : 'theirs'}">
                <div class="msg-bubble-meta">
                  <strong>${escM(m.sender_name)} (${escM(m.sender_initials)})</strong><span>${recipTag} • ${escM(m.created_at)}</span>
                  ${(mine || isBoss) ? `<button type="button" class="msg-del-x" onclick="window.deleteShopMsg(${m.id})" title="Delete">×</button>` : ''}
                </div>
                <div class="msg-bubble-txt">${escM(m.body)}</div>
              </div>`;
          }).join('') : `<div style="margin:auto;text-align:center;color:var(--ui-muted);font-size:0.84rem;padding:24px 12px;">
            <div style="font-size:1.8rem;margin-bottom:6px;">💬</div>
            <strong>Crew Chat Ready</strong>
            <p style="margin:4px 0 0;">Leave notes between Ryan and the crew (e.g. valve parts, truck pickup updates, or shop handoffs).</p>
          </div>`}
        </div>

        <div class="msg-composer-box">
          <div style="display:flex;gap:6px;margin-bottom:6px;">
            <select id="msgRecipSel" style="padding:6px 10px;font-size:0.78rem;font-weight:700;">
              <option value="ALL" ${prevRecip === 'ALL' ? 'selected' : ''}>📣 All Shop Crew</option>
              ${dirOpts}
            </select>
          </div>
          <div style="display:flex;gap:6px;">
            <input id="msgBodyInput" value="${escM(prevDraft)}" placeholder="Message crew or Ryan..." onkeydown="if(event.key==='Enter'){event.preventDefault();window.sendCrewChatMsg();}" style="flex:1;" />
            <button type="button" class="primary" style="width:auto;margin:0;padding:9px 16px;" onclick="window.sendCrewChatMsg()">Send</button>
          </div>
        </div>`;

      const stream = document.getElementById('msgChatStream');
      if (stream) stream.scrollTop = stream.scrollHeight;
      return;
    }

    // WEBSITE CUSTOMER INQUIRIES TAB
    const inqs = MSG_STATE.web_inquiries || [];
    host.innerHTML = `
      <div class="msg-chat-stream">
        ${inqs.length ? inqs.map(q => {
          const isOpen = q.status === 'OPEN';
          const digits = String(q.sender_phone || '').replace(/\D/g, '');
          const smsHref = digits ? `sms:${digits}?body=${encodeURIComponent(`Hi ${q.sender_name}, this is Ryan at Weber's Tank Testing regarding your message: `)}` : '';
          const mailHref = q.sender_email ? `mailto:${encodeURIComponent(q.sender_email)}?subject=${encodeURIComponent("Re: Your Question for Weber's Tank Testing")}` : '';
          return `
            <div class="msg-inq-card ${isOpen ? 'open' : 'resolved'}">
              <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;">
                <div>
                  <strong style="font-size:0.93rem;">${escM(q.sender_name)}</strong>
                  <span class="${isOpen ? 'dwell-badge' : 'pass'}" style="margin-left:6px;">${isOpen ? 'OPEN' : '✓ Resolved'}</span>
                  <div style="font-size:0.74rem;color:var(--ui-muted);margin-top:2px;">${escM(q.created_at)}${q.sender_phone ? ' • 📞 ' + escM(q.sender_phone) : ''}${q.sender_email ? ' • ✉️ ' + escM(q.sender_email) : ''}</div>
                </div>
                ${isBoss ? `<button type="button" class="msg-del-x" onclick="window.deleteShopMsg(${q.id})" title="Delete">×</button>` : ''}
              </div>
              <div style="margin:8px 0;font-size:0.86rem;line-height:1.48;white-space:pre-line;">${escM(q.body)}</div>
              ${q.replied_at ? `<div style="font-size:0.74rem;color:var(--ui-accent);font-weight:700;margin-bottom:6px;">✓ Marked resolved by ${escM(q.replied_by)} (${escM(q.replied_at)})${q.reply_note ? ': ' + escM(q.reply_note) : ''}</div>` : ''}
              <div style="display:flex;gap:6px;flex-wrap:wrap;">
                ${smsHref ? `<a class="secondary" href="${smsHref}">📲 Text Back</a>` : ''}
                ${digits ? `<a class="secondary" href="tel:${digits}">📞 Call</a>` : ''}
                ${mailHref ? `<a class="secondary" href="${mailHref}">✉️ Email</a>` : ''}
                <button type="button" class="secondary" onclick="window.toggleInquiryResolved(${q.id}, '${isOpen ? 'RESOLVED' : 'OPEN'}')">${isOpen ? '✓ Mark Resolved' : 'Reopen'}</button>
              </div>
            </div>`;
        }).join('') : `<div style="margin:auto;text-align:center;color:var(--ui-muted);font-size:0.84rem;padding:24px 12px;">
          <div style="font-size:1.8rem;margin-bottom:6px;">🌐</div>
          <strong>No Website Questions Waiting</strong>
          <p style="margin:4px 0 0;">Questions submitted via the "Message Ryan" button on weberstanktesting.com appear right here.</p>
        </div>`}
      </div>`;
  }

  window.sendCrewChatMsg = async function () {
    const inp = document.getElementById('msgBodyInput');
    const body = (inp?.value || '').trim();
    if (!body) return;
    const recipient = document.getElementById('msgRecipSel')?.value || 'ALL';
    inp.value = '';
    const res = await fetch('/api/messages', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ recipient, body }),
    });
    if (!res.ok) { alert((await res.json()).detail || 'Could not send message.'); return; }
    await loadFullMessagesState(true);
    setTimeout(() => document.getElementById('msgBodyInput')?.focus(), 30);
  };

  window.toggleInquiryResolved = async function (id, status) {
    await fetch(`/api/messages/${id}/resolve`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    });
    await loadFullMessagesState(true);
  };

  window.deleteShopMsg = async function (id) {
    if (!confirm('Delete this message?')) return;
    await fetch(`/api/messages/${id}`, { method: 'DELETE' });
    await loadFullMessagesState(true);
  };

  setTimeout(() => window.refreshMessagesBadgeSilent(), 800);
  setInterval(() => window.refreshMessagesBadgeSilent(), 12000);
}
