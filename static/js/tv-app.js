let ACTIVE_VIEW = 'dash';
    let DASH_SECS = 75, CAL_SECS = 25, SECS_LEFT = 75;
    let IS_TRANSITIONING = false;
    let SCREENSAVER_ON = false;
    let IDLE_LIMIT_MINS = parseInt(localStorage.getItem('webers_tv_idle_mins') || '15', 10);
    let LAST_ACTIVITY_MS = Date.now();
    let LAST_DB_VERSION = null;
    let CHIME_ON = localStorage.getItem('webers_tv_chime') !== '0';
    let PREV_DOOR_CNT = null, PREV_COMPLETED = null;

    function esc(s) {
      return String(s ?? '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
    }

    function stripLeadEmoji(s) {
      return String(s || '').replace(/^[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE0F}\s]+/u, '').trim();
    }

    // --- SCREENSAVER & ACTIVITY WAKE ENGINE ---
    window.enterScreensaver = function enterScreensaver(manual = false) {
      if (SCREENSAVER_ON) return;
      SCREENSAVER_ON = true;
      document.body.classList.remove('tv-bg-reveal');
      document.body.classList.add('tv-screensaver');
      updateClocks();
      if (manual) {
        // Ignore the click that triggered Saver for 400ms so it doesn't immediately wake itself
        LAST_ACTIVITY_MS = Date.now() - (Math.max(1, IDLE_LIMIT_MINS) * 60000);
      }
    }

    function wakeFromScreensaver() {
      LAST_ACTIVITY_MS = Date.now();
      if (!SCREENSAVER_ON) return;
      SCREENSAVER_ON = false;
      
      document.body.classList.remove('tv-screensaver');
      SECS_LEFT = ACTIVE_VIEW === 'dash' ? DASH_SECS : CAL_SECS;
      updateFlipBtnLabel();
    }

    ['pointerdown', 'keydown', 'touchstart'].forEach(ev => {
      window.addEventListener(ev, e => {
        if (e.target && e.target.closest && e.target.closest('button, select, input')) return;
        wakeFromScreensaver();
      }, { passive: true });
    });
    window.addEventListener('mousemove', () => {
      if (SCREENSAVER_ON) wakeFromScreensaver();
      else LAST_ACTIVITY_MS = Date.now();
    }, { passive: true });

    function changeIdlePreset() {
      IDLE_LIMIT_MINS = parseInt(document.getElementById('idlePresetSel').value || '15', 10);
      localStorage.setItem('webers_tv_idle_mins', String(IDLE_LIMIT_MINS));
      LAST_ACTIVITY_MS = Date.now();
    }

    // --- SLOW BACKGROUND-REVEAL TRANSITION BETWEEN DASHBOARD & CALENDAR ---
    function swapViewDomInstant(v) {
      ACTIVE_VIEW = v;
      document.getElementById('viewDashboard').hidden = (v !== 'dash');
      document.getElementById('viewCalendar').hidden = (v !== 'cal');
      SECS_LEFT = v === 'dash' ? DASH_SECS : CAL_SECS;
      updateFlipBtnLabel();
    }

    function transitionToView(nextView) {
      if (SCREENSAVER_ON || IS_TRANSITIONING || nextView === ACTIVE_VIEW) {
        if (nextView === ACTIVE_VIEW) SECS_LEFT = nextView === 'dash' ? DASH_SECS : CAL_SECS;
        return;
      }
      IS_TRANSITIONING = true;
      // Slow 3.4s sinusoidal melt out -> immediate 0ms crossover -> slow 3.4s sinusoidal melt in
      document.body.classList.add('tv-bg-reveal');
      setTimeout(() => {
        swapViewDomInstant(nextView);
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            document.body.classList.remove('tv-bg-reveal');
            setTimeout(() => { IS_TRANSITIONING = false; }, 3450);
          });
        });
      }, 3400);
    }

    function changeRotationPreset() {
      const val = document.getElementById('rotPresetSel').value;
      localStorage.setItem('webers_tv_rot_v2', val);
      if (val === 'dash_only') { swapViewDomInstant('dash'); return; }
      if (val === 'cal_only') { swapViewDomInstant('cal'); return; }
      const [d, c] = val.split('_').map(Number);
      DASH_SECS = d; CAL_SECS = c;
      SECS_LEFT = ACTIVE_VIEW === 'dash' ? DASH_SECS : CAL_SECS;
      updateFlipBtnLabel();
    }

    function manualFlipView() {
      LAST_ACTIVITY_MS = Date.now();
      const mode = document.getElementById('rotPresetSel').value;
      if (mode === 'dash_only' || mode === 'cal_only') {
        document.getElementById('rotPresetSel').value = '75_25';
        DASH_SECS = 75; CAL_SECS = 25;
      }
      transitionToView(ACTIVE_VIEW === 'dash' ? 'cal' : 'dash');
    }

    function updateFlipBtnLabel() {
      const mode = document.getElementById('rotPresetSel').value;
      const btn = document.getElementById('btnFlipView');
      if (mode === 'dash_only' || mode === 'cal_only') {
        btn.textContent = mode === 'dash_only' ? 'Dash Pinned' : 'Cal Pinned';
        return;
      }
      const m = Math.floor(SECS_LEFT / 60), s = String(SECS_LEFT % 60).padStart(2, '0');
      btn.textContent = ACTIVE_VIEW === 'dash' ? `⇄ Cal in ${m}:${s}` : `⇄ Dash in ${m}:${s}`;
    }

    function updateClocks() {
      const now = new Date();
      document.getElementById('liveClock').textContent = now.toLocaleTimeString('en-US', {
        hour: 'numeric', minute: '2-digit',
      });
      document.getElementById('ssTime').textContent = now.toLocaleTimeString('en-US', {
        hour: 'numeric', minute: '2-digit',
      });
      document.getElementById('ssDate').textContent = now.toLocaleDateString('en-US', {
        weekday: 'long', month: 'long', day: 'numeric', year: 'numeric',
      });
    }

    setInterval(() => {
      updateClocks();

      // Auto-enter Screensaver when shop has been idle for IDLE_LIMIT_MINS
      if (!SCREENSAVER_ON && IDLE_LIMIT_MINS > 0) {
        if ((Date.now() - LAST_ACTIVITY_MS) >= IDLE_LIMIT_MINS * 60000) {
          enterScreensaver(false);
          return;
        }
      }

      if (SCREENSAVER_ON || IS_TRANSITIONING) return;
      const mode = document.getElementById('rotPresetSel').value;
      if (mode !== 'dash_only' && mode !== 'cal_only') {
        SECS_LEFT--;
        if (SECS_LEFT <= 0) transitionToView(ACTIVE_VIEW === 'dash' ? 'cal' : 'dash');
        else updateFlipBtnLabel();
      }
    }, 1000);

    // Syncs the TV's centered background Wreath Arch, Shop Dogs & Screensaver Holiday Badge
    async function syncTvBackgroundArtwork() {
      try {
        const res = await fetch('/api/public/theme');
        if (!res.ok) return;
        const t = await res.json();
        const archImg = document.getElementById('tvBgArchImg');
        const dogsImg = document.getElementById('tvBgDogsImg');
        const ssBadge = document.getElementById('ssHolidayBadge');

        window.ACTIVE_HOLIDAY_KEY = (t && t.active && t.key) ? t.key : 'ANY';
        if (t.active && t.enable_tv) {
          if (ssBadge && t.countdown_label) { ssBadge.hidden = false; ssBadge.textContent = t.countdown_label; }
          if (archImg) {
            const archMap = { THANKSGIVING: 'arch-thanksgiving', HALLOWEEN: 'arch-thanksgiving', CHRISTMAS: 'arch-christmas', NEW_YEAR: 'arch-newyear' };
            const stem = archMap[t.key] || 'arch-thanksgiving';
            const pA = new Image();
            pA.onload = () => { archImg.src = `/static/images/${stem}.png`; archImg.hidden = false; };
            pA.onerror = () => { archImg.hidden = true; };
            pA.src = `/static/images/${stem}.png`;
          }
          if (dogsImg && t.dog_image) {
            const dStem = t.dog_image.replace(/\.(jpg|jpeg|png)$/i, '');
            const pD = new Image();
            pD.onload = () => { dogsImg.classList.remove('photo-fallback'); dogsImg.src = `/static/images/${dStem}.png`; };
            pD.onerror = () => {
              dogsImg.src = `/static/images/${t.dog_image}`;
              dogsImg.classList.toggle('photo-fallback', t.dog_image === 'shop-dogs.jpg');
            };
            pD.src = `/static/images/${dStem}.png`;
          }
        } else {
          if (ssBadge) ssBadge.hidden = true;
          if (archImg) archImg.hidden = true;
          if (dogsImg) { dogsImg.src = '/static/images/dogs-classic.png?v=13.3'; dogsImg.classList.remove('photo-fallback'); }
        }
      } catch (_) {}
    }
    window.ACTIVE_HOLIDAY_KEY = 'THANKSGIVING';
function playChime() {
      if (!CHIME_ON) return;
      try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        [587.33, 880].forEach((freq, idx) => {
          const o = ctx.createOscillator(), g = ctx.createGain();
          o.type = 'sine'; o.frequency.value = freq;
          g.gain.setValueAtTime(0.18, ctx.currentTime + idx * 0.18);
          g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + idx * 0.18 + 0.45);
          o.connect(g); g.connect(ctx.destination);
          o.start(ctx.currentTime + idx * 0.18); o.stop(ctx.currentTime + idx * 0.18 + 0.46);
        });
      } catch (_) {}
    }

    function toggleChime() {
      CHIME_ON = !CHIME_ON;
      localStorage.setItem('webers_tv_chime', CHIME_ON ? '1' : '0');
      document.getElementById('btnChime').classList.toggle('on', CHIME_ON);
      if (CHIME_ON) playChime();
    }

    function toggleFs() {
      if (!document.fullscreenElement) document.documentElement.requestFullscreen().catch(() => {});
      else document.exitFullscreen();
    }

    async function unlockTv() {
      const pin = document.getElementById('tvPinInput').value;
      const res = await fetch('/api/auth/login', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pin }),
      });
      if (res.ok) { document.getElementById('tvPinGate').hidden = true; wakeFromScreensaver(); pollTv(); }
      else alert('Incorrect Shop PIN.');
    }

    function recommendJacketLabel(r, ceilings) {
      const psi = Number(r.test_psi || 0);
      const sMax = ceilings['Short Jacket']?.max_allowed_test_psi || 0;
      const tMax = ceilings['Tall Jacket']?.max_allowed_test_psi || 0;
      if (sMax >= psi && tMax < psi) return 'SHORT JACKET';
      if (tMax >= psi && sMax < psi) return 'TALL JACKET';
      if (sMax >= psi && tMax >= psi) return (parseFloat(r.length_in || '0') > 0 && parseFloat(r.length_in) <= 16.5) ? 'SHORT JACKET' : 'TALL JACKET';
      return 'CALIBRATE JACKET';
    }

    async function pollTv() {
      try {
        const res = await fetch('/api/tv');
        if (res.status === 401) { document.getElementById('tvPinGate').hidden = false; return; }
        if (!res.ok) return;
        document.getElementById('tvPinGate').hidden = true;
        const d = await res.json();
        const sb = d.scoreboard || {};

        // Wake immediately from Screensaver whenever ANY shop data changes!
        if (LAST_DB_VERSION !== null && d.db_version !== LAST_DB_VERSION) {
          wakeFromScreensaver();
        }
        LAST_DB_VERSION = d.db_version;

        if (PREV_DOOR_CNT !== null && sb.backroom_waiting_count > PREV_DOOR_CNT) {
          wakeFromScreensaver();
          playChime();
          if (ACTIVE_VIEW === 'cal') swapViewDomInstant('dash');
        }
        if (PREV_COMPLETED !== null && sb.completed_today > PREV_COMPLETED && ACTIVE_VIEW === 'cal') {
          wakeFromScreensaver();
          swapViewDomInstant('dash');
        }
        PREV_DOOR_CNT = sb.backroom_waiting_count;
        PREV_COMPLETED = sb.completed_today;

        document.getElementById('hdrShiftTxt').textContent = stripLeadEmoji(d.today_plan?.shift_label) || 'DOT RIN B386';
        const todayNoteTxt = (d.today_plan?.day_notes || '').trim();
        const dnEl = document.getElementById('tvDayNoteBanner');
        if (dnEl) {
          if (todayNoteTxt) {
            dnEl.style.display = 'flex';
            dnEl.innerHTML = `<span>📌 <strong>TODAY'S SHOP NOTE:</strong> ${esc(todayNoteTxt)}</span><span class="mono" style="font-size:0.75rem;color:#92400e;">${esc(d.work_date)}</span>`;
          } else {
            dnEl.style.display = 'none';
          }
        }
        const sJ = d.jacket_ceilings['Short Jacket'] || {}, tJ = d.jacket_ceilings['Tall Jacket'] || {};
        const pS = document.getElementById('tvShortPill'), pT = document.getElementById('tvTallPill');
        pS.className = 'pill ' + (sJ.max_allowed_test_psi ? 'ok' : 'wait');
        pS.textContent = sJ.max_allowed_test_psi ? `SHORT ≤ ${sJ.max_allowed_test_psi}# ✓` : 'SHORT: UNCAL';
        pT.className = 'pill ' + (tJ.max_allowed_test_psi ? 'ok' : 'wait');
        pT.textContent = tJ.max_allowed_test_psi ? `TALL ≤ ${tJ.max_allowed_test_psi}# ✓` : 'TALL: UNCAL';

        const b = d.binder_state || {};
        document.getElementById('tvBinderPill').textContent = `SHEET #${b.active_sheet_no || 8095} • ROW #${b.next_row_number || 1}/12`;

        document.getElementById('scDoor').textContent = sb.backroom_waiting_count || 0;
        document.getElementById('tileDoor').classList.toggle('alert-door', (sb.backroom_waiting_count || 0) > 0);
        document.getElementById('scQueue').textContent = sb.queued_count || 0;
        document.getElementById('scPassed').textContent = sb.passed_today || 0;
        document.getElementById('scStamp').textContent = sb.needs_stamp_count || 0;
        document.getElementById('scTags').textContent = sb.active_tags_count || 0;
        document.getElementById('scShelf').textContent = sb.ready_pickup_count || 0;

        const st = d.on_stand;
        const hero = document.getElementById('standCard');
        if (st) {
          const isRush = Boolean(st.rush_fee || st.priority === 'RUSH');
          hero.className = 'stand-hero' + (isRush ? ' rush' : '');
          hero.innerHTML = `
            <div style="display:flex;justify-content:space-between;align-items:center;">
              <span class="mono" style="font-size:0.72rem;font-weight:800;color:var(--emerald);letter-spacing:0.04em;">NEXT ON STAND → ${recommendJacketLabel(st, d.jacket_ceilings)}</span>
              ${isRush ? '<span class="b-rush">PAID RUSH</span>' : ''}
            </div>
            <div class="stand-sn">SN ${esc(st.serial_number)}</div>
            <div style="font-size:0.9rem;color:var(--ink-soft);font-weight:700;">${esc(st.customer_name)}${st.promised_date ? ' • Due ' + esc(st.promised_date) : ''}</div>
            <div class="stand-meta">
              <div class="meta-box"><small>Spec &amp; Service</small><strong>${esc(st.dot_spec)}-${st.service_psi}</strong></div>
              <div class="meta-box"><small>Target Test PSI</small><strong style="color:var(--gold);">${st.test_psi} PSI</strong></div>
              <div class="meta-box"><small>Valve Tag / REE</small><strong>${st.valve_tag && !st.valve_reinstalled ? 'TAG #' + esc(st.valve_tag) : (st.ree_limit ? 'REE ' + st.ree_limit : 'Ready')}</strong></div>
            </div>`;
        } else {
          hero.className = 'stand-hero';
          hero.innerHTML = `<div class="mono" style="font-size:0.74rem;color:var(--emerald);font-weight:800;">HYDRO TEST STAND</div><div style="font-family:var(--font-display);font-size:1.55rem;font-weight:800;color:var(--forest);margin-top:6px;">Stand Queue Clear — Ready for Next Batch</div>`;
        }

        document.getElementById('upNextCnt').textContent = `${(d.up_next || []).length} Waiting`;
        document.getElementById('upNextList').innerHTML = (d.up_next || []).length
          ? d.up_next.map((r, i) => `
            <div class="row-it">
              <div><strong class="mono" style="color:var(--forest);">#${i + 2} ${esc(r.serial_number)}</strong> ${(r.rush_fee || r.priority === 'RUSH') ? '<span class="b-rush">RUSH</span>' : ''}<div style="font-size:0.75rem;color:var(--muted);font-weight:600;">${esc(r.customer_name)} • <span class="mono">${esc(r.dot_spec)}-${r.service_psi}</span></div></div>
              <div style="text-align:right;"><strong class="mono" style="color:var(--gold);">${r.test_psi}#</strong>${r.valve_tag && !r.valve_reinstalled ? `<div><span class="b-tag">#${esc(r.valve_tag)}</span></div>` : ''}</div>
            </div>`).join('')
          : '<div style="color:var(--muted);font-size:0.82rem;padding:8px 0;">No additional cylinders waiting behind stand.</div>';

        const batches = d.batch_leaderboard || [];
        document.getElementById('batchCnt').textContent = `${batches.length} Active`;
        document.getElementById('batchList').innerHTML = batches.length
          ? batches.map(bItem => {
              const pct = bItem.total > 0 ? Math.min(100, Math.round((bItem.completed / bItem.total) * 100)) : 0;
              return `
                <div style="padding:7px 2px;border-bottom:1px solid rgba(15,46,32,0.08);">
                  <div style="display:flex;justify-content:space-between;font-size:0.84rem;font-weight:800;color:var(--forest);">
                    <span>${esc(bItem.customer_name)} ${bItem.is_rush ? '<span class="b-rush">RUSH</span>' : ''}</span>
                    <span class="mono" style="font-size:0.76rem;color:var(--emerald);">${bItem.completed}/${bItem.total} Done${bItem.ready_backroom ? ` • ${bItem.ready_backroom} Ready` : ''}</span>
                  </div>
                  <div class="prog-track"><div class="prog-fill" style="width:${pct}%"></div></div>
                </div>`;
            }).join('')
          : '<div style="color:var(--muted);font-size:0.82rem;">No active batches in shop.</div>';

        const sched = d.upcoming_schedule || [];
        const noteDays = (d.calendar_days || []).filter(cd => (cd.day_notes || '').trim());
        const noteRowsHtml = noteDays.map(cd => `<div class="row-it" style="background:rgba(254,243,199,0.65);border-radius:6px;padding:5px 8px;"><strong style="color:#92400e;">📝 ${esc(cd.weekday)} ${esc(cd.month_day)} Note:</strong> <span style="font-weight:700;color:#78350f;">${esc(cd.day_notes)}</span></div>`).join('');
        document.getElementById('schedCnt').textContent = `${sched.length} Open` + (noteDays.length ? ` • ${noteDays.length} Note` : '');
        document.getElementById('schedList').innerHTML = noteRowsHtml + (sched.length
          ? sched.map(j => {
              const kind = j.job_type === 'ROAD_PICKUP' ? '📥 Pickup:' : (j.job_type === 'ROAD_DELIVERY' ? '📤 Delivery:' : (j.job_type === 'ROAD_ROUNDTRIP' ? '🔄 Round-Trip:' : '📌 Batch:')); const tm = j.pickup_time ? ' @ ' + j.pickup_time : ''; const eta = j.drive_eta_mins ? ` • 🚗 ~${j.drive_eta_mins}m` : '';
              return `
                <div class="row-it">
                  <div><strong style="color:var(--forest);">${kind} ${esc(j.customer_name)} (${j.bottle_count})</strong> ${j.rush_fee ? '<span class="b-rush">RUSH</span>' : ''}<div style="font-size:0.74rem;color:var(--muted);"><span class="mono">${esc(j.scheduled_date)}${esc(tm)}</span>${j.job_type === 'ROAD_ROUNDTRIP' && j.due_date ? ` → Return ${esc(j.due_date)}` : ''}${eta}${j.tank_summary ? ' • ' + esc(j.tank_summary) : ''}</div></div>
                  <span class="pill ${j.prep_status === 'DEVALVED_TAGGED' ? 'ok' : ''}" style="font-size:0.66rem;padding:3px 7px;">${j.prep_status === 'DEVALVED_TAGGED' ? 'Prepped' : 'Scheduled'}</span>
                </div>`;
            }).join('')
          : (noteRowsHtml ? '' : '<div style="color:var(--muted);font-size:0.82rem;">No scheduled runs queued.</div>'));

        const drops = d.backroom_dropoffs || [];
        document.getElementById('doorPanel').hidden = !drops.length;
        document.getElementById('doorCnt').textContent = drops.length;
        document.getElementById('doorList').innerHTML = drops.map(dr => `
          <div class="row-it">
            <div><strong style="color:#92400e;">${esc(dr.customer_name)}</strong> <span class="mono" style="font-size:0.78rem;">(${dr.bottle_count - (dr.imported_count || 0)} waiting)</span> ${dr.rush_requested ? '<span class="b-rush">RUSH</span>' : ''}<div style="font-size:0.73rem;color:var(--muted);">${esc(dr.service_requested)}</div></div>
          </div>`).join('');

        const valves = d.active_valves || [];
        document.getElementById('tagCnt').textContent = `${valves.length} Out`;
        document.getElementById('tagsGrid').innerHTML = valves.length
          ? valves.slice(0, 14).map(v => `<div class="tag-tile"><strong class="mono" style="color:#92400e;">#${esc(v.valve_tag)}</strong> → <span class="mono" style="font-weight:700;">${esc(v.serial_number)}</span><div style="font-size:0.69rem;color:var(--muted);overflow:hidden;text-overflow:ellipsis;">${esc(v.customer_name)}</div></div>`).join('')
          : '<div style="color:var(--muted);font-size:0.8rem;grid-column:1/-1;">All reusable valve tags clear.</div>';

        const done = d.recent_completed || [];
        document.getElementById('doneCnt').textContent = `${sb.completed_today || 0} Today`;
        document.getElementById('doneList').innerHTML = done.length
          ? done.map(r => `<div class="row-it"><span><strong class="mono" style="color:var(--forest);">${esc(r.serial_number)}</strong> • <small style="color:var(--muted);font-weight:600;">${esc(r.customer_name)}</small></span><span class="mono" style="color:${r.hydro_disposition === 'A' ? 'var(--emerald)' : 'var(--red)'};font-weight:800;">${r.service_mode === 'VISUAL_ONLY' ? 'VIS' : '#' + r.sheet_no} [${esc(r.hydro_disposition)}]</span></div>`).join('')
          : '<div style="color:var(--muted);font-size:0.8rem;">No tests completed today yet.</div>';

        document.getElementById('tvCalGrid').innerHTML = (d.calendar_days || []).map(day => {
          const isToday = day.work_date === d.work_date;
          const shortMode = day.shift_mode === 'FULL_HYDRO' ? `Hydro (${day.bottle_capacity})`
            : (day.shift_mode === 'PREP_ONLY' ? 'Prep & Drop-Off'
            : (day.shift_mode === 'ROAD_RUN' ? `Truck Run (${day.bottle_capacity})` : '24/7 Back Room'));
          const pct = day.bottle_capacity > 0 ? Math.min(100, Math.round(((day.booked_bottles || 0) / day.bottle_capacity) * 100)) : 0;
          return `
            <div class="cal-cell mode-${day.shift_mode} ${isToday ? 'today' : ''}">
              <div>
                <div style="display:flex;justify-content:space-between;align-items:center;font-weight:800;font-size:0.86rem;color:var(--forest);">
                  <span>${esc(day.weekday)} <span class="mono">${esc(day.month_day)}</span></span>
                  ${isToday ? '<span class="mono" style="background:var(--gold-bright);color:#0f172a;font-size:0.6rem;padding:2px 5px;border-radius:4px;font-weight:800;">TODAY</span>' : ''}
                </div>
                <div class="mono" style="font-size:0.7rem;color:var(--emerald);font-weight:700;margin-top:2px;">${shortMode}</div>
                ${day.bottle_capacity > 0 ? `<div class="prog-track"><div class="prog-fill" style="width:${pct}%;background:${pct >= 90 ? 'var(--red)' : 'var(--emerald)'};"></div></div>` : ''}
              </div>
              <div class="cal-jobs">
                ${(day.jobs || []).map(j => { const lg = j.cal_leg === 'RETURN' ? '📤 ' : (j.cal_leg === 'PICKUP' ? '📥 ' : '📌 '); const tm = j.cal_time ? j.cal_time + ' ' : ''; return `<div class="cal-job-chip ${(j.rush_fee || j.priority === 'RUSH') ? 'rush' : ''}">${lg}${esc(tm)}${esc(j.customer_name)} <span class="mono">(${j.bottle_count})</span></div>`; }).join('')}
                ${day.day_notes ? `<div style="font-size:0.7rem;color:#92400e;font-weight:700;">Note: ${esc(day.day_notes)}</div>` : ''}
              </div>
              <div class="mono" style="font-size:0.66rem;color:var(--muted);">${day.bottle_capacity > 0 ? `${day.booked_bottles || 0}/${day.bottle_capacity} booked` : 'Self-Drop Active'}</div>
            </div>`;
        }).join('');
      } catch (_) {}
    }

    const savedRot = localStorage.getItem('webers_tv_rot_v2');
    if (savedRot && document.querySelector(`#rotPresetSel option[value="${savedRot}"]`)) {
      document.getElementById('rotPresetSel').value = savedRot;
    }
    if (document.querySelector(`#idlePresetSel option[value="${IDLE_LIMIT_MINS}"]`)) {
      document.getElementById('idlePresetSel').value = String(IDLE_LIMIT_MINS);
    }
    document.getElementById('btnChime').classList.toggle('on', CHIME_ON);
    updateClocks();
    changeRotationPreset();
    syncTvBackgroundArtwork();
    pollTv();
    setInterval(pollTv, 3500);
    setInterval(syncTvBackgroundArtwork, 20000);
