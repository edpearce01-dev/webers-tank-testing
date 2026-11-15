// Weber's Tank Testing — v9.0 Role Guard, Start/End Day, Uncalibrated Box & Visible PIN Manager
(function () {
  window.WEBERS_USER = null;
  window.WEBERS_DAY = null;
  let PREVIEW_AS_CREW = false;

  function esc(s) {
    const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' };
    return String(s ?? '').replace(/[&<>"]/g, m => map[m]);
  }

  function isOwnerOrAdmin() {
    return window.WEBERS_USER && (window.WEBERS_USER.role === 'OWNER' || window.WEBERS_USER.role === 'SYSTEM_ADMIN');
  }

  function ensureModalsAndBanners() {
    const ws = document.querySelector('main.ops-workspace');
    if (ws && !document.getElementById('uncalibratedAlertBox')) {
      const bar = document.createElement('div');
      bar.id = 'uncalibratedAlertBox';
      bar.className = 'uncal-alert-bar';
      bar.hidden = true;
      ws.prepend(bar);
    }

    // Inject Left-Rail Day & Task Controls once
    const railNav = document.querySelector('.side-rail .rail-nav');
    if (railNav && !document.getElementById('railDayControls')) {
      const wrap = document.createElement('div');
      wrap.id = 'railDayControls';
      wrap.style.marginTop = '8px';
      wrap.style.paddingTop = '8px';
      wrap.style.borderTop = '1px solid var(--border-soft)';
      railNav.appendChild(wrap);
    }

    // Inject PIN Manager Button inside Settings Modal on /ops if present
    const settingsModal = document.querySelector('#guideModal .modal');
    if (settingsModal && !document.getElementById('btnOpenPinMgrInSettings')) {
      const box = document.createElement('div');
      box.id = 'settingsDayAndPinBox';
      box.style.cssText = 'margin:12px 0;padding:12px;background:#ecfdf5;border:1.5px solid #6ee7b7;border-radius:10px;display:flex;gap:8px;flex-wrap:wrap;justify-content:space-between;align-items:center;';
      box.innerHTML = `
        <div>
          <strong style="color:#065f46;font-size:0.88rem;">☀️ Daily Launchpad &amp; Individual PINs</strong>
          <div style="font-size:0.76rem;color:#047857;">Re-open Start Your Day or view/edit Crew &amp; Admin PIN numbers</div>
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;">
          <button type="button" class="secondary" onclick="toggleGuide(false);window.openStartYourDayModal(true)">☀️ Start Your Day</button>
          <button type="button" class="primary" id="btnOpenPinMgrInSettings" style="width:auto;margin:0;padding:8px 12px;" onclick="toggleGuide(false);window.openPinManagerModal()">🔐 Crew &amp; Admin PINs</button>
        </div>`;
      const h3 = settingsModal.querySelector('.card-hdr');
      if (h3) h3.insertAdjacentElement('afterend', box);
    }

    if (!document.getElementById('startDayModal')) {
      const m = document.createElement('div');
      m.id = 'startDayModal';
      m.className = 'modal-bg';
      m.hidden = true;
      m.innerHTML = '<div class="modal" style="max-width:780px;" id="startDayModalInner"></div>';
      document.body.appendChild(m);
    }

    if (!document.getElementById('endDayModal')) {
      const m = document.createElement('div');
      m.id = 'endDayModal';
      m.className = 'modal-bg';
      m.hidden = true;
      m.onclick = e => { if (e.target === m) m.hidden = true; };
      m.innerHTML = '<div class="modal" style="max-width:660px;" id="endDayModalInner"></div>';
      document.body.appendChild(m);
    }

    if (!document.getElementById('pinMgrModal')) {
      const m = document.createElement('div');
      m.id = 'pinMgrModal';
      m.className = 'modal-bg';
      m.hidden = true;
      m.onclick = e => { if (e.target === m) m.hidden = true; };
      m.innerHTML = '<div class="modal" style="max-width:720px;" id="pinMgrModalInner"></div>';
      document.body.appendChild(m);
    }
  }

  function applyRoleRestrictionsAndTopBar() {
    const u = window.WEBERS_USER;
    const d = window.WEBERS_DAY;
    if (!u || !d) return;

    localStorage.setItem('webers_active_crew', u.initials);
    const badge = document.getElementById('menuCrewInitials');
    if (badge) badge.textContent = u.role === 'SYSTEM_ADMIN' ? `${u.initials} 🛡️` : u.initials;

    // Show Admin No-Legal-Sign-Off pill when logged in as Eric (EP)
    const topCtrls = document.querySelector('.top-controls');
    if (topCtrls) {
      let admPill = document.getElementById('adminNoSigBadge');
      if (u.role === 'SYSTEM_ADMIN') {
        if (!admPill) {
          admPill = document.createElement('span');
          admPill.id = 'adminNoSigBadge';
          admPill.className = 'admin-nosig-pill';
          admPill.title = 'Logged in with Eric Admin PIN — Full system access without placing Ryan legal signature on DOT sheets';
          admPill.textContent = '🛡️ ADMIN (No Legal Sign-Off)';
          topCtrls.prepend(admPill);
        }
      } else if (admPill) {
        admPill.remove();
      }
    }

    // Persistent Uncalibrated Alert Box until water jackets are calibrated today
    const uncalBox = document.getElementById('uncalibratedAlertBox');
    if (uncalBox) {
      const cs = d.cal_status;
      if (!cs.both_calibrated) {
        uncalBox.hidden = false;
        const detail = !cs.any_calibrated
          ? 'Neither Short nor Tall Water Jacket is calibrated yet today.'
          : (!cs.short_calibrated
              ? `Short Jacket is UNCALIBRATED (Tall Ready ≤${cs.tall_allowed_psi} PSI).`
              : `Tall Jacket is UNCALIBRATED (Short Ready ≤${cs.short_allowed_psi} PSI).`);
        const actionBtn = isOwnerOrAdmin()
          ? `<button type="button" class="secondary" style="background:#fde68a;color:#111827;border-color:#fff;font-weight:900;padding:7px 13px;" onclick="window.jumpToCalibrationNow()">💧 Calibrate Now</button>`
          : `<span style="font-size:0.78rem;background:rgba(0,0,0,0.28);padding:5px 10px;border-radius:7px;font-weight:800;">Prep &amp; Scan OK • Ryan (RP) Must Calibrate</span>`;
        uncalBox.innerHTML = `
          <div>
            <div style="font-weight:900;font-size:0.92rem;">🚨 UNCALIBRATED FOR TODAY (${esc(d.today)}) — Hydro Testing Locked Until Calibrated</div>
            <div style="font-size:0.8rem;opacity:0.95;">${detail}</div>
          </div>
          ${actionBtn}`;
      } else {
        uncalBox.hidden = true;
      }
    }

    // Lock out Calibration Ladder & Facility Settings for CREW role
    const calArea = document.getElementById('calFormCard');
    if (calArea) {
      let crewLockMsg = document.getElementById('crewCalLockNotice');
      if (u.role === 'CREW') {
        calArea.style.display = 'none';
        if (!crewLockMsg) {
          crewLockMsg = document.createElement('div');
          crewLockMsg.id = 'crewCalLockNotice';
          crewLockMsg.className = 'card';
          crewLockMsg.style.borderLeft = '4px solid var(--amber)';
          crewLockMsg.innerHTML = '<strong>🔒 Water Jacket Calibration (Ryan Pearce Only)</strong><div style="font-size:0.84rem;color:var(--muted);margin-top:4px;">Crew accounts can prep, devalve, tag, and scan cylinders — Ryan (RP) certifies morning water-jacket calibration.</div>';
          calArea.insertAdjacentElement('afterend', crewLockMsg);
        }
      } else {
        calArea.style.display = '';
        if (crewLockMsg) crewLockMsg.remove();
      }
    }

    // Update Left-Rail Day Controls (Role-Separated!)
    const railCtrl = document.getElementById('railDayControls');
    if (railCtrl) {
      const openTasks = (d.tasks || []).filter(t => t.status === 'OPEN').length;
      if (isOwnerOrAdmin()) {
        railCtrl.innerHTML = `
          <div class="rail-section-lbl">${u.role === 'SYSTEM_ADMIN' ? '🛡️ Admin &amp; Day Flow' : '👑 Owner Day Flow'}</div>
          <button type="button" class="rail-btn" onclick="window.openStartYourDayModal(true)">
            <span>☀️ Start Your Day</span><span class="nav-badge ${d.day_plan?.lineup_locked ? 'green' : 'amber'}">${d.day_plan?.lineup_locked ? '🔒' : 'Set'}</span>
          </button>
          <button type="button" class="rail-btn" onclick="window.openStartYourDayModal(true, 'tasks')">
            <span>📋 Crew Tasks</span><span class="nav-badge">${openTasks}</span>
          </button>
          <button type="button" class="rail-btn" onclick="window.openPinManagerModal()">
            <span>🔐 Individual PINs</span><span class="nav-badge">PIN</span>
          </button>
          <button type="button" class="rail-btn" onclick="window.openHolidayThemeModal && window.openHolidayThemeModal()">
            <span>🎨 Holiday Themes</span><span class="nav-badge green">🎄</span>
          </button>
          <button type="button" class="rail-btn" onclick="window.openEndYourDayModal()">
            <span>🌙 End Your Day</span><span class="nav-badge">Out</span>
          </button>`;
      } else {
        railCtrl.innerHTML = `
          <div class="rail-section-lbl">🔧 ${esc(u.name)}</div>
          <button type="button" class="rail-btn" onclick="window.openStartYourDayModal(true)">
            <span>📋 My Assigned Tasks</span><span class="nav-badge ${openTasks ? 'amber' : 'green'}">${openTasks}</span>
          </button>
          <button type="button" class="rail-btn" onclick="window.openEndYourDayModal()">
            <span>🌙 Wrap Up My Shift</span><span class="nav-badge">Out</span>
          </button>`;
      }
    }
  }

  window.jumpToCalibrationNow = function () {
    document.getElementById('startDayModal').hidden = true;
    if (location.pathname !== '/ops') {
      location.href = '/ops?tab=cal';
      return;
    }
    if (typeof jumpToTab === 'function') jumpToTab('cal');
    if (typeof setCalExpanded === 'function') setCalExpanded(true);
    setTimeout(() => (document.getElementById('stepIn_0') || document.getElementById('calMaxPsi'))?.focus(), 100);
  };

  window.refreshDayFlowState = async function (checkAutoOpen = false) {
    const shell = document.getElementById('appShell');
    if (!shell || shell.hidden) return;
    ensureModalsAndBanners();
    try {
      const res = await fetch('/api/day/overview');
      if (res.status === 401) {
        location.reload();
        return;
      }
      if (!res.ok) return;
      const data = await res.json();
      window.WEBERS_USER = data.user;
      window.WEBERS_DAY = data;
      applyRoleRestrictionsAndTopBar();

      // Auto-open Start Your Day ONCE after morning PIN unlock on /ops (never nags again!)
      const seenKey = `webers_day_started_${data.today}_${data.user.initials}`;
      if (checkAutoOpen && location.pathname === '/ops' && !localStorage.getItem(seenKey)) {
        window.openStartYourDayModal(false);
      }
    } catch (_) {}
  };

  // --- START YOUR DAY MODAL (OWNER/ADMIN VS CREW) ---
  window.openStartYourDayModal = async function (manualOpen = true, scrollFocus = '') {
    await window.refreshDayFlowState(false);
    const d = window.WEBERS_DAY;
    const u = window.WEBERS_USER;
    if (!d || !u) return;

    const modal = document.getElementById('startDayModal');
    const inner = document.getElementById('startDayModalInner');
    modal.hidden = false;

    const showCrewView = (u.role === 'CREW') || (u.role === 'SYSTEM_ADMIN' && PREVIEW_AS_CREW);

    if (showCrewView) {
      const openTasks = d.tasks || [];
      inner.innerHTML = `
        <div class="card-hdr">
          <div>
            <h3 style="font-size:1.12rem;">☀️ Morning Briefing — ${esc(u.name)}</h3>
            <small style="color:var(--muted);">Date: ${esc(d.today)} • Assigned Shop &amp; Property Tasks</small>
          </div>
          ${u.role === 'SYSTEM_ADMIN' ? `<button type="button" class="secondary" onclick="window.toggleAdminCrewPreview(false)">👑 Back to Ryan/Admin View</button>` : ''}
        </div>

        <div class="day-sec-box">
          <div style="font-weight:900;font-size:0.85rem;color:var(--green);margin-bottom:8px;">📋 YOUR ASSIGNED TASKS FROM RYAN (${openTasks.filter(t => t.status === 'OPEN').length} Open)</div>
          ${openTasks.length ? openTasks.map(t => `
            <div class="rec-item" style="background:#fff;padding:9px 11px;border-radius:8px;margin-bottom:6px;border:1px solid var(--border-soft);">
              <label class="inline-check" style="flex:1;font-size:0.9rem;">
                <input type="checkbox" ${t.status === 'DONE' ? 'checked' : ''} onchange="window.toggleCrewTaskDone(${t.id}, this.checked)" />
                <span style="${t.status === 'DONE' ? 'text-decoration:line-through;color:var(--muted);' : 'font-weight:800;'}">
                  ${t.category === 'PROPERTY' ? '🌿 [Property]' : '🔧 [Shop]'} ${esc(t.title)}
                </span>
              </label>
              <span class="sub-badge">${esc(t.assigned_name)}</span>
            </div>
          `).join('') : '<div style="color:var(--muted);font-size:0.86rem;">No special tasks assigned yet — check Zone A Backroom Door &amp; Zone B Prep queue!</div>'}
        </div>

        <div class="day-sec-box" style="display:flex;gap:12px;flex-wrap:wrap;justify-content:space-between;align-items:center;">
          <div><strong>🚪 Backroom Drop-Offs Waiting:</strong> ${d.waiting_dropoffs.length}</div>
          <div><strong>📌 Today's Scheduled Batches:</strong> ${d.todays_jobs.length}</div>
          <div><strong>💧 Stand Status:</strong> ${d.cal_status.both_calibrated ? '✅ Calibrated' : '⚠️ Waiting on Ryan Calibration'}</div>
        </div>

        <button type="button" class="primary" onclick="window.acknowledgeCrewStartDay()">👍 Got It — Open Shop Workspace</button>
      `;
      return;
    }

    // RYAN (OWNER) & ERIC (SYSTEM_ADMIN) START-YOUR-DAY VIEW
    const dp = d.day_plan || {};
    const adminBanner = u.role === 'SYSTEM_ADMIN' ? `
      <div class="notice warn" style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:10px;">
        <span>🛡️ <strong>Admin Mode (${esc(u.name)}):</strong> Full access unlocked. Actions under your PIN <strong>never</strong> place Ryan's legal DOT sign-off on official forms.</span>
        <button type="button" class="secondary" onclick="window.toggleAdminCrewPreview(true)">👁️ Preview Crew Popup</button>
      </div>` : '';

    const todayRows = (d.todays_jobs || []).map(j => `
      <div class="rec-item" style="background:#fff;padding:8px 10px;border-radius:8px;margin-bottom:6px;border:1px solid var(--border-soft);">
        <div class="rec-main">
          <div class="rec-title">
            <span>${j.job_type.startsWith('ROAD_') ? '🚚' : '📌'} ${esc(j.customer_name)}</span>
            ${j.locked_for_day ? '<span class="stage-chip ready">🔒 Locked</span>' : ''}
            ${j.auto_paced ? '<span class="zone-badge">⚡ Auto-Pace Bonus</span>' : ''}
            ${(j.rush_fee || j.priority === 'RUSH') ? '<span class="rush-badge">🔥 RUSH</span>' : ''}
          </div>
          <div class="rec-sub">${esc(j.tank_summary || j.job_type)}${j.due_date ? ` • Due: ${esc(j.due_date)}` : ''}</div>
        </div>
        <div class="rec-actions">
          <input type="number" min="1" max="250" value="${j.bottle_count}" style="width:68px;padding:5px 7px;font-weight:800;" onchange="window.dayJobAction(${j.id}, 'SET_COUNT', this.value)" title="Edit Bottle Count" />
          <button type="button" class="secondary" onclick="window.dayJobAction(${j.id}, 'BUMP_TOMORROW')">↪ Tomorrow</button>
        </div>
      </div>`).join('') || '<div style="color:var(--muted);font-size:0.84rem;">No scheduled batches on today yet — pull one in below or keep open for walk-ins/backroom drops.</div>';

    const pullCandidates = [...(d.carryover_jobs || []), ...(d.upcoming_jobs || [])].slice(0, 6);
    const pullHtml = pullCandidates.length ? `
      <div style="margin-top:8px;padding-top:8px;border-top:1px dashed var(--border);">
        <div style="font-size:0.74rem;font-weight:900;color:var(--muted);margin-bottom:5px;">CARRYOVERS &amp; UPCOMING BATCHES (1-TAP PULL INTO TODAY):</div>
        ${pullCandidates.map(j => `
          <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;padding:4px 0;font-size:0.82rem;">
            <span><strong>${esc(j.customer_name)}</strong> (${j.bottle_count} cyl) • Sched: ${esc(j.scheduled_date)}${j.due_date ? ` • Due: ${esc(j.due_date)}` : ''}</span>
            <button type="button" class="secondary" style="padding:3px 9px;" onclick="window.dayJobAction(${j.id}, 'PULL_TODAY')">➕ Add to Today</button>
          </div>`).join('')}
      </div>` : '';

    const crewOpts = `<option value="ALL">All Crew</option>` +
      (d.crew_list || []).filter(c => c.initials !== 'RP').map(c => `<option value="${esc(c.initials)}">${esc(c.name)} (${esc(c.initials)})</option>`).join('');

    const taskRows = (d.tasks || []).map(t => `
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;padding:5px 8px;background:#fff;border-radius:7px;border:1px solid var(--border-soft);margin-top:5px;font-size:0.83rem;">
        <label class="inline-check" style="margin:0;flex:1;">
          <input type="checkbox" ${t.status === 'DONE' ? 'checked' : ''} onchange="window.toggleCrewTaskDone(${t.id}, this.checked)" />
          <span style="${t.status === 'DONE' ? 'text-decoration:line-through;color:var(--muted);' : 'font-weight:700;'}">
            ${t.category === 'PROPERTY' ? '🌿' : '🔧'} <strong>[${esc(t.assigned_name)}]</strong> ${esc(t.title)}
          </span>
        </label>
        <button type="button" class="secondary" style="padding:2px 7px;color:#b91c1c;" onclick="window.deleteCrewTaskItem(${t.id})">✕</button>
      </div>`).join('');

    inner.innerHTML = `
      <div class="card-hdr">
        <div>
          <h3 style="font-size:1.15rem;">☀️ Start Your Day — ${esc(d.today)}</h3>
          <small style="color:var(--muted);">Lock today's cylinder lineup, assign Kenny/Twins tasks &amp; choose calibration</small>
        </div>
        <button type="button" class="secondary" onclick="window.dismissOwnerStartModal()">✖ Later</button>
      </div>
      ${adminBanner}

      <!-- 1. TODAY'S LINEUP & AUTO-PACE LOCK -->
      <div class="day-sec-box">
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:8px;">
          <strong style="color:var(--green);font-size:0.9rem;">1. Today's Cylinder Lineup (${d.waiting_dropoffs.length} Waiting in Backroom Door)</strong>
          <span class="sub-badge">${dp.lineup_locked ? '🔒 Locked for Today' : 'Unlocked Draft'}</span>
        </div>
        <div class="grid" style="margin-bottom:9px;">
          <div>
            <label>Today's Operating Window</label>
            <select id="sdShiftMode">
              <option value="FULL_HYDRO" ${dp.shift_mode === 'FULL_HYDRO' ? 'selected' : ''}>🟢 Full Hydro &amp; Prep Window</option>
              <option value="PREP_ONLY" ${dp.shift_mode === 'PREP_ONLY' ? 'selected' : ''}>🔧 Prep / Devalve Only (0 Hydro)</option>
              <option value="ROAD_RUN" ${dp.shift_mode === 'ROAD_RUN' ? 'selected' : ''}>🚚 Truck Run &amp; Partial Hydro</option>
              <option value="BACKROOM_ONLY" ${dp.shift_mode === 'BACKROOM_ONLY' ? 'selected' : ''}>🚪 24/7 Backroom Self-Drop Only</option>
            </select>
          </div>
          <div>
            <label>Max Bottle Target Today</label>
            <input type="number" id="sdBottleCap" min="0" max="150" value="${dp.bottle_capacity ?? 24}" />
          </div>
          <div>
            <label>Day Note (Shows on Wall &amp; TV)</label>
            <input id="sdDayNotes" value="${esc(dp.day_notes || '')}" placeholder="Optional note..." />
          </div>
        </div>

        ${todayRows}
        ${pullHtml}

        <label class="inline-check" style="margin-top:10px;padding:8px 10px;background:#ecfdf5;border:1px solid #6ee7b7;border-radius:8px;color:#065f46;">
          <input type="checkbox" id="sdAllowAutopace" ${(dp.allow_extra_autopace ?? 1) ? 'checked' : ''} />
          <span>🚀 <strong>Allow Auto-Pace Extra Work:</strong> Keep my locked lineup untouched, but slot extra waiting batches underneath if open capacity allows so we stay caught up / ahead.</span>
        </label>
      </div>

      <!-- 2. ASSIGN CREW TASKS (SHOP OR PROPERTY) -->
      <div class="day-sec-box" id="sdTasksBox">
        <strong style="color:var(--green);font-size:0.9rem;display:block;margin-bottom:6px;">2. Assign Tasks for Kenny &amp; Twins (Shop or Property)</strong>
        <div style="display:grid;grid-template-columns:140px 115px 1fr auto;gap:6px;align-items:center;">
          <select id="sdTaskWho">${crewOpts}</select>
          <select id="sdTaskCat">
            <option value="SHOP">🔧 Testing/Shop</option>
            <option value="PROPERTY">🌿 Property/Yard</option>
          </select>
          <input id="sdTaskTitle" placeholder="e.g. Devalve 14 scuba tanks, or trim brush by backroom..." onkeydown="if(event.key==='Enter')window.addCrewTaskFromModal()" />
          <button type="button" class="secondary" style="padding:9px 12px;background:#ecfdf5;color:#065f46;border-color:#6ee7b7;" onclick="window.addCrewTaskFromModal()">+ Assign</button>
        </div>
        <div id="sdTasksList">${taskRows}</div>
      </div>

      <!-- 3. CALIBRATION PROMPT -->
      <div style="display:grid;grid-template-columns:1.2fr 1fr;gap:10px;margin-top:10px;">
        <button type="button" class="primary" style="margin:0;background:#15803d;" onclick="window.submitOwnerStartDay(true)">
          💧 Lock Today's Plan &amp; Calibrate Now
        </button>
        <button type="button" class="secondary" style="padding:12px;font-weight:900;background:#fffbeb;color:#92400e;border-color:#f59e0b;" onclick="window.submitOwnerStartDay(false)">
          ⏭️ Lock Plan &amp; Skip Calibration Until Later
        </button>
      </div>
    `;

    if (scrollFocus === 'tasks') {
      setTimeout(() => document.getElementById('sdTaskTitle')?.focus(), 60);
    }
  };

  window.toggleAdminCrewPreview = function (asCrew) {
    PREVIEW_AS_CREW = asCrew;
    window.openStartYourDayModal(true);
  };

  window.acknowledgeCrewStartDay = function () {
    if (window.WEBERS_DAY && window.WEBERS_USER) {
      localStorage.setItem(`webers_day_started_${window.WEBERS_DAY.today}_${window.WEBERS_USER.initials}`, '1');
    }
    document.getElementById('startDayModal').hidden = true;
  };

  window.dismissOwnerStartModal = function () {
    if (window.WEBERS_DAY && window.WEBERS_USER) {
      localStorage.setItem(`webers_day_started_${window.WEBERS_DAY.today}_${window.WEBERS_USER.initials}`, '1');
    }
    document.getElementById('startDayModal').hidden = true;
  };

  window.dayJobAction = async function (jobId, action, val) {
    await fetch('/api/day/job-move', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_id: jobId, action, bottle_count: val ? parseInt(val, 10) : null }),
    });
    if (typeof loadState === 'function') loadState(false);
    window.openStartYourDayModal(true);
  };

  window.addCrewTaskFromModal = async function () {
    const inp = document.getElementById('sdTaskTitle');
    const title = (inp?.value || '').trim();
    if (!title) return;
    await fetch('/api/day/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        assigned_to: document.getElementById('sdTaskWho').value,
        category: document.getElementById('sdTaskCat').value,
        title,
      }),
    });
    inp.value = '';
    window.openStartYourDayModal(true, 'tasks');
  };

  window.toggleCrewTaskDone = async function (taskId, isDone) {
    await fetch(`/api/day/tasks/${taskId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: isDone ? 'DONE' : 'OPEN' }),
    });
    await window.refreshDayFlowState(false);
  };

  window.deleteCrewTaskItem = async function (taskId) {
    await fetch(`/api/day/tasks/${taskId}`, { method: 'DELETE' });
    window.openStartYourDayModal(true, 'tasks');
  };

  window.submitOwnerStartDay = async function (calibrateNow) {
    const mode = document.getElementById('sdShiftMode').value;
    const cap = parseInt(document.getElementById('sdBottleCap').value || '24', 10);
    const notes = document.getElementById('sdDayNotes').value;
    const allowExtra = document.getElementById('sdAllowAutopace').checked;

    const res = await fetch('/api/day/lock-lineup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        shift_mode: mode,
        bottle_capacity: cap,
        day_notes: notes,
        allow_extra_autopace: allowExtra,
      }),
    });
    if (!res.ok) { alert((await res.json()).detail || 'Could not lock lineup.'); return; }

    window.dismissOwnerStartModal();
    if (typeof loadState === 'function') await loadState(false);
    await window.refreshDayFlowState(false);

    if (calibrateNow) {
      window.jumpToCalibrationNow();
    }
  };

  // --- MANUAL END-YOUR-DAY MODAL (ROLE SEPARATED, ZERO CLOCK NAGGING) ---
  window.openEndYourDayModal = async function () {
    await window.refreshDayFlowState(false);
    const d = window.WEBERS_DAY;
    const u = window.WEBERS_USER;
    if (!d || !u) return;

    const modal = document.getElementById('endDayModal');
    const inner = document.getElementById('endDayModalInner');
    modal.hidden = false;

    if (!isOwnerOrAdmin()) {
      const myTasks = d.tasks || [];
      inner.innerHTML = `
        <div class="card-hdr">
          <h3>🌙 Wrap Up My Shift — ${esc(u.name)}</h3>
          <button type="button" class="secondary" onclick="document.getElementById('endDayModal').hidden=true">✖ Close</button>
        </div>
        <div class="day-sec-box">
          <strong style="font-size:0.86rem;color:var(--green);display:block;margin-bottom:6px;">Check Off Completed Tasks:</strong>
          ${myTasks.length ? myTasks.map(t => `
            <label class="inline-check" style="padding:6px 0;border-bottom:1px solid var(--border-soft);">
              <input type="checkbox" ${t.status === 'DONE' ? 'checked' : ''} onchange="window.toggleCrewTaskDone(${t.id}, this.checked)" />
              <span>${t.category === 'PROPERTY' ? '🌿' : '🔧'} ${esc(t.title)}</span>
            </label>`).join('') : '<div style="color:var(--muted);font-size:0.84rem;">No assigned checklist items today.</div>'}
        </div>
        <label>Leave Handoff Note for Ryan (Optional)</label>
        <input id="eodShiftNote" placeholder="e.g. Devalved 10 scuba tanks, low on Viton O-rings..." />
        <div style="display:flex;gap:8px;margin-top:12px;">
          <button type="button" class="primary" style="margin:0;flex:2;" onclick="window.submitEndOfDay(true)">🌙 Save Handoff &amp; Sign Out</button>
          <button type="button" class="secondary" style="flex:1;" onclick="window.submitEndOfDay(false)">Save &amp; Stay In</button>
        </div>`;
      return;
    }

    const s = d.eod_summary;
    inner.innerHTML = `
      <div class="card-hdr">
        <h3>🌙 Owner End-of-Day Closeout (${esc(d.today)})</h3>
        <button type="button" class="secondary" onclick="document.getElementById('endDayModal').hidden=true">✖ Close</button>
      </div>
      <div class="stat-strip" style="margin-bottom:12px;">
        <div class="stat-tile"><small>Tested Today</small><strong>${s.tested_today} (${s.passed_today}✓ / ${s.condemned_today}✕)</strong></div>
        <div class="stat-tile"><small>Needs B386 Mark</small><strong style="color:${s.unmarked_pass ? '#b91c1c' : '#15803d'}">${s.unmarked_pass} Cyl</strong></div>
        <div class="stat-tile"><small>Unbilled on Zone E</small><strong style="color:${s.unbilled_shelf_customers ? '#b45309' : '#15803d'}">${s.unbilled_shelf_customers} Cust</strong></div>
        <div class="stat-tile"><small>Queued Carryover</small><strong>${s.queued_remaining} Cyl</strong></div>
      </div>

      ${d.day_plan?.shift_notes ? `<div class="notice" style="margin-bottom:10px;"><strong>📝 Crew Shift Notes Today:</strong> ${esc(d.day_plan.shift_notes)}</div>` : ''}

      <div class="day-sec-box" style="display:flex;gap:8px;flex-wrap:wrap;">
        <a class="secondary" href="/api/pdf/retest?work_date=${encodeURIComponent(d.today)}" target="_blank">🖨️ Print Today's Retest Sheet</a>
        <a class="secondary" href="/api/pdf/verification?work_date=${encodeURIComponent(d.today)}" target="_blank">📊 Print Daily Verification</a>
        ${s.unbilled_shelf_customers ? `<a class="secondary" style="background:#fef3c7;color:#92400e;" href="/office?tab=invoice">💲 Bill Zone E Customers (${s.unbilled_shelf_customers})</a>` : ''}
      </div>

      <label class="inline-check" style="margin-bottom:10px;">
        <input type="checkbox" id="eodRollChk" checked />
        <span>Automatically roll unfinished shop batches onto tomorrow's Start-Your-Day board</span>
      </label>
      <label>End-of-Day Note (Optional)</label>
      <input id="eodShiftNote" placeholder="Optional wrap-up note..." />

      <div style="display:flex;gap:8px;margin-top:12px;">
        <button type="button" class="primary" style="margin:0;flex:2;" onclick="window.submitEndOfDay(true)">🌙 Sync Sheets, Roll Carryovers &amp; Sign Out</button>
        <button type="button" class="secondary" style="flex:1;" onclick="window.submitEndOfDay(false)">Save Without Signing Out</button>
      </div>`;
  };

  window.submitEndOfDay = async function (signOutAfter) {
    const note = document.getElementById('eodShiftNote')?.value || '';
    const roll = document.getElementById('eodRollChk') ? document.getElementById('eodRollChk').checked : false;
    await fetch('/api/day/end-day', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ shift_note: note, roll_unfinished: roll }),
    });
    document.getElementById('endDayModal').hidden = true;
    if (signOutAfter) {
      await fetch('/api/auth/logout', { method: 'POST' });
      location.reload();
    } else {
      await window.refreshDayFlowState(false);
    }
  };

  // --- VISIBLE INDIVIDUAL PIN & CREW ROSTER MANAGER (OWNER & ADMIN ONLY) ---
  window.openPinManagerModal = async function () {
    const res = await fetch('/api/crew/pins');
    if (!res.ok) { alert((await res.json()).detail || 'Only Ryan or Admin can manage PINs.'); return; }
    const data = await res.json();
    const isSysAdmin = data.viewer_role === 'SYSTEM_ADMIN';

    const modal = document.getElementById('pinMgrModal');
    const inner = document.getElementById('pinMgrModalInner');
    modal.hidden = false;

    const rowsHtml = (data.users || []).map(u => {
      const roleOpts = isSysAdmin
        ? `<option value="SYSTEM_ADMIN" ${u.role === 'SYSTEM_ADMIN' ? 'selected' : ''}>🛡️ Admin (No DOT Sign)</option>
           <option value="OWNER" ${u.role === 'OWNER' ? 'selected' : ''}>👑 Owner (DOT Sign-Off)</option>
           <option value="CREW" ${u.role === 'CREW' ? 'selected' : ''}>🔧 Crew</option>`
        : `<option value="OWNER" ${u.role === 'OWNER' ? 'selected' : ''}>👑 Owner (DOT Sign-Off)</option>
           <option value="CREW" ${u.role === 'CREW' ? 'selected' : ''}>🔧 Crew</option>`;
      return `
        <div class="pin-mgr-row">
          <input id="pmName_${u.id}" value="${esc(u.name)}" placeholder="Full Name" />
          <input id="pmInit_${u.id}" value="${esc(u.initials)}" maxlength="4" style="text-transform:uppercase;text-align:center;font-weight:800;" />
          <select id="pmRole_${u.id}">${roleOpts}</select>
          <input id="pmPin_${u.id}" value="${esc(u.pin)}" class="pin-mono-input" maxlength="10" title="Visible Individual PIN" />
          <div style="display:flex;gap:4px;">
            <button type="button" class="secondary" style="background:#dcfce7;color:#166534;border-color:#86efac;" onclick="window.savePinUserRow(${u.id})">💾 Save</button>
            ${u.role === 'CREW' ? `<button type="button" class="secondary" style="color:#b91c1c;" onclick="window.deletePinUserRow(${u.id}, '${esc(u.name)}')">🗑</button>` : ''}
          </div>
        </div>`;
    }).join('');

    inner.innerHTML = `
      <div class="card-hdr">
        <div>
          <h3>🔐 Individual PINs &amp; Role Access Manager</h3>
          <small style="color:var(--muted);">All sessions reset at 12:00 AM midnight • PINs are stored visibly below for easy reference</small>
        </div>
        <button type="button" class="secondary" onclick="document.getElementById('pinMgrModal').hidden=true">✖ Close</button>
      </div>

      <div class="day-sec-box">
        <div style="display:grid;grid-template-columns:1.4fr 74px 1.1fr 110px auto;gap:7px;font-size:0.7rem;font-weight:900;color:var(--muted);padding-bottom:4px;">
          <span>NAME</span><span>INIT</span><span>ACCESS ROLE</span><span>VISIBLE PIN</span><span>ACTION</span>
        </div>
        ${rowsHtml}
      </div>

      <div class="day-sec-box" style="background:#f0fdf4;border-color:#86efac;">
        <strong style="font-size:0.85rem;color:#065f46;display:block;margin-bottom:6px;">➕ Add New Crew Member PIN</strong>
        <div class="pin-mgr-row" style="border:none;padding:0;">
          <input id="pmNewName" placeholder="e.g. Alex Pearce" />
          <input id="pmNewInit" placeholder="AP" maxlength="4" style="text-transform:uppercase;text-align:center;font-weight:800;" />
          <select id="pmNewRole">
            <option value="CREW">🔧 Crew (Tasks &amp; Ops)</option>
            ${isSysAdmin ? '<option value="SYSTEM_ADMIN">🛡️ Admin (No DOT Sign)</option>' : ''}
          </select>
          <input id="pmNewPin" placeholder="4444" class="pin-mono-input" maxlength="10" />
          <button type="button" class="primary" style="width:auto;margin:0;padding:8px 14px;" onclick="window.addNewPinUserRow()">+ Add</button>
        </div>
      </div>
    `;
  };

  window.savePinUserRow = async function (id) {
    const res = await fetch('/api/crew/pins', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        id,
        name: document.getElementById('pmName_' + id).value,
        initials: document.getElementById('pmInit_' + id).value,
        role: document.getElementById('pmRole_' + id).value,
        pin: document.getElementById('pmPin_' + id).value,
      }),
    });
    const d = await res.json();
    if (!res.ok) { alert(d.detail || 'Save failed.'); return; }
    window.openPinManagerModal();
  };

  window.addNewPinUserRow = async function () {
    const res = await fetch('/api/crew/pins', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: document.getElementById('pmNewName').value,
        initials: document.getElementById('pmNewInit').value,
        role: document.getElementById('pmNewRole').value,
        pin: document.getElementById('pmNewPin').value,
      }),
    });
    const d = await res.json();
    if (!res.ok) { alert(d.detail || 'Could not add crew PIN.'); return; }
    window.openPinManagerModal();
  };

  window.deletePinUserRow = async function (id, name) {
    if (!confirm(`Remove PIN access for ${name}?`)) return;
    const res = await fetch(`/api/crew/pins/${id}`, { method: 'DELETE' });
    if (!res.ok) { alert((await res.json()).detail || 'Delete failed.'); return; }
    window.openPinManagerModal();
  };

  // Boot watcher + Midnight Auto-Lock check every 20 seconds
  let BOOTED_ONCE = false;
  setInterval(async () => {
    const shell = document.getElementById('appShell');
    if (shell && !shell.hidden) {
      if (!BOOTED_ONCE) {
        BOOTED_ONCE = true;
        await window.refreshDayFlowState(true);
      } else {
        await window.refreshDayFlowState(false);
      }
    } else {
      BOOTED_ONCE = false;
    }
  }, 12000);

  // Also trigger immediately right after page load / login unlock
  window.addEventListener('load', () => setTimeout(() => window.refreshDayFlowState(true), 350));
})();
