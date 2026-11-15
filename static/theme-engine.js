// Weber's Tank Testing — v9.1 Holiday Theme Engine (Light Ops Garland + All-Out TV + Admin Switcher)
(function () {
  const MUTE_KEY = 'webers_mute_ops_theme';
  let THEME = null;
  let LAST_KEY = '';

  function esc(s) {
    const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' };
    return String(s ?? '').replace(/[&<>"]/g, m => map[m]);
  }

  const BULB_PALETTES = {
    christmas_lights: ['#ef4444', '#22c55e', '#f59e0b', '#3b82f6', '#f43f5e'],
    autumn_lights: ['#d97706', '#ea580c', '#b45309', '#f59e0b', '#dc2626'],
    patriotic_lights: ['#ef4444', '#ffffff', '#3b82f6', '#ef4444', '#ffffff'],
    emerald_lights: ['#10b981', '#34d399', '#fbbf24', '#059669', '#6ee7b7'],
    spooky_lights: ['#f97316', '#a855f7', '#f59e0b', '#9333ea', '#ea580c'],
    warm_pink_lights: ['#f43f5e', '#fb7185', '#fda4af', '#e11d48', '#fef08a'],
    pastel_lights: ['#a78bfa', '#34d399', '#f472b6', '#fbbf24', '#60a5fa'],
    gold_lights: ['#fbbf24', '#fef08a', '#f59e0b', '#ffffff', '#d97706'],
  };

  const PARTICLE_EMOJIS = {
    snow: ['❄️', '❅', '❆', '✨'],
    autumn_leaves: ['🍂', '🍁', '🍃', '🌾'],
    patriotic: ['✨', '⭐', '🎆', '🇺🇸'],
    shamrocks: ['🍀', '☘️', '✨'],
    halloween: ['🎃', '🦇', '🍂', '✨'],
    hearts: ['❤️', '💖', '✨'],
    spring: ['🌸', '🌷', '✨', '🐣'],
    fireworks: ['🎆', '✨', '🥂', '⭐'],
  };

  function injectBaseThemeStyles() {
    if (document.getElementById('webersThemeCss')) return;
    const st = document.createElement('style');
    st.id = 'webersThemeCss';
    st.textContent = `
      .ops-light-strand {
        position: fixed; top: 50px; left: 0; right: 0; height: 18px; z-index: 49;
        pointer-events: none; overflow: hidden; display: flex; justify-content: space-around; align-items: flex-start;
      }
      .ops-bulb {
        width: 8px; height: 12px; border-radius: 50% 50% 55% 55%;
        margin-top: 2px; animation: webersTwinkle 2.2s infinite alternate ease-in-out;
        box-shadow: 0 0 7px currentColor;
      }
      .ops-bulb:nth-child(odd) { animation-duration: 1.7s; }
      body.webers-ops-themed .card { border-top: 2px dashed rgba(217, 119, 6, 0.38); }
      @keyframes webersTwinkle {
        0% { opacity: 0.55; transform: scale(0.92); }
        100% { opacity: 1; transform: scale(1.08); }
      }

      .tv-perimeter-lights {
        position: fixed; inset: 0; pointer-events: none; z-index: 90;
        border: 3px solid rgba(251, 191, 36, 0.35);
      }
      .tv-edge-row { position: absolute; left: 0; right: 0; display: flex; justify-content: space-around; }
      .tv-edge-row.top { top: 2px; }
      .tv-edge-row.bottom { bottom: 2px; }
      .tv-edge-col { position: absolute; top: 24px; bottom: 24px; display: flex; flex-direction: column; justify-content: space-around; }
      .tv-edge-col.left { left: 3px; }
      .tv-edge-col.right { right: 3px; }
      .tv-bulb {
        width: 13px; height: 17px; border-radius: 50%;
        animation: webersTwinkle 1.4s infinite alternate ease-in-out;
        box-shadow: 0 0 14px currentColor, 0 0 26px currentColor;
      }
      .tv-bulb:nth-child(2n) { animation-delay: 0.5s; }
      .tv-bulb:nth-child(3n) { animation-delay: 0.9s; }

      .tv-particles-wrap { position: fixed; inset: 0; pointer-events: none; z-index: 88; overflow: hidden; }
      .tv-particle { position: absolute; top: -40px; font-size: 1.35rem; opacity: 0.82; animation: tvFall linear infinite; }
      @keyframes tvFall {
        0% { transform: translateY(-30px) rotate(0deg); opacity: 0.9; }
        100% { transform: translateY(108vh) rotate(320deg); opacity: 0.25; }
      }

      .tv-countdown-banner {
        position: fixed; top: 10px; left: 50%; transform: translateX(-50%); z-index: 95;
        background: linear-gradient(90deg, #991b1b, #15803d, #b45309);
        color: #fff; border: 2px solid #fde68a; border-radius: 999px;
        padding: 6px 20px; font-weight: 900; font-size: 0.95rem; letter-spacing: 0.6px;
        box-shadow: 0 8px 24px rgba(0,0,0,0.45); pointer-events: auto; cursor: pointer;
      }
      .tv-pups-card {
        position: fixed; right: 18px; bottom: 18px; z-index: 92; width: 320px;
        background: rgba(6, 21, 16, 0.92); border: 2px solid #fbbf24; border-radius: 16px;
        padding: 10px 12px; color: #fff; display: flex; gap: 10px; align-items: center;
        box-shadow: 0 14px 34px rgba(0,0,0,0.55); backdrop-filter: blur(8px);
      }
      .tv-pups-card img {
        width: 78px; height: 78px; border-radius: 12px; object-fit: cover;
        border: 2px solid #fde68a; background: #fdf8ee; flex-shrink: 0;
      }
    `;
    document.head.appendChild(st);
  }

  function clearThemeDom() {
    document.body.classList.remove('webers-ops-themed');
    ['webersOpsStrand', 'webersTvLights', 'webersTvParticles', 'webersTvCountdown', 'webersTvPups'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.remove();
    });
  }

  function renderOpsTheme() {
    const topCtrls = document.querySelector('.top-controls');
    let muteBtn = document.getElementById('opsThemeToggleBtn');
    const isMuted = localStorage.getItem(MUTE_KEY) === '1';

    if (!THEME || !THEME.active || !THEME.enable_ops) {
      if (muteBtn) muteBtn.remove();
      clearThemeDom();
      return;
    }

    if (topCtrls && !muteBtn) {
      muteBtn = document.createElement('button');
      muteBtn.id = 'opsThemeToggleBtn';
      muteBtn.type = 'button';
      muteBtn.className = 'menu-pill';
      muteBtn.onclick = () => {
        const cur = localStorage.getItem(MUTE_KEY) === '1';
        localStorage.setItem(MUTE_KEY, cur ? '0' : '1');
        renderOpsTheme();
      };
      topCtrls.prepend(muteBtn);
    }

    if (muteBtn) {
      muteBtn.textContent = isMuted ? `${THEME.icon} Off` : `${THEME.icon} ON`;
      muteBtn.title = 'Click to toggle holiday border lights on your screen';
    }

    if (isMuted) { clearThemeDom(); return; }

    document.body.classList.add('webers-ops-themed');
    if (!document.getElementById('webersOpsStrand')) {
      const palette = BULB_PALETTES[THEME.garland] || BULB_PALETTES.gold_lights;
      const strand = document.createElement('div');
      strand.id = 'webersOpsStrand';
      strand.className = 'ops-light-strand';
      let bulbs = '';
      for (let i = 0; i < 34; i++) {
        const c = palette[i % palette.length];
        bulbs += `<span class="ops-bulb" style="background:${c};color:${c};"></span>`;
      }
      strand.innerHTML = bulbs;
      document.body.appendChild(strand);
    }
  }

  function renderTvAllOutTheme() {
    const isMuted = localStorage.getItem('webers_mute_tv_theme') === '1';
    if (!THEME || !THEME.active || !THEME.enable_tv) { clearThemeDom(); return; }

    let cd = document.getElementById('webersTvCountdown');
    if (!cd) {
      cd = document.createElement('div');
      cd.id = 'webersTvCountdown';
      cd.className = 'tv-countdown-banner';
      cd.title = 'Click to mute/unmute TV holiday effects';
      cd.onclick = () => {
        const cur = localStorage.getItem('webers_mute_tv_theme') === '1';
        localStorage.setItem('webers_mute_tv_theme', cur ? '0' : '1');
        clearThemeDom();
        renderTvAllOutTheme();
      };
      document.body.appendChild(cd);
    }
    cd.textContent = isMuted ? `${THEME.icon} Theme Paused (Click to Enable)` : THEME.countdown_label;
    if (isMuted) return;

    const palette = BULB_PALETTES[THEME.garland] || BULB_PALETTES.christmas_lights;
    if (!document.getElementById('webersTvLights')) {
      const box = document.createElement('div');
      box.id = 'webersTvLights';
      box.className = 'tv-perimeter-lights';
      const mkRow = n => Array.from({ length: n }, (_, i) => {
        const c = palette[i % palette.length];
        return `<span class="tv-bulb" style="background:${c};color:${c};"></span>`;
      }).join('');
      box.innerHTML = `
        <div class="tv-edge-row top">${mkRow(36)}</div>
        <div class="tv-edge-row bottom">${mkRow(36)}</div>
        <div class="tv-edge-col left">${mkRow(18)}</div>
        <div class="tv-edge-col right">${mkRow(18)}</div>`;
      document.body.appendChild(box);
    }

    if (!document.getElementById('webersTvParticles')) {
      const emojis = PARTICLE_EMOJIS[THEME.effect] || ['✨'];
      const pWrap = document.createElement('div');
      pWrap.id = 'webersTvParticles';
      pWrap.className = 'tv-particles-wrap';
      let pHtml = '';
      for (let i = 0; i < 24; i++) {
        const em = emojis[i % emojis.length];
        const left = Math.round((i / 24) * 98);
        const dur = (9 + (i % 7) * 1.8).toFixed(1);
        const delay = ((i * 0.7) % 10).toFixed(1);
        pHtml += `<span class="tv-particle" style="left:${left}%;animation-duration:${dur}s;animation-delay:-${delay}s;">${em}</span>`;
      }
      pWrap.innerHTML = pHtml;
      document.body.appendChild(pWrap);
    }

    if (!document.getElementById('webersTvPups')) {
      const card = document.createElement('div');
      card.id = 'webersTvPups';
      card.className = 'tv-pups-card';
      card.innerHTML = `
        <img src="/static/images/${esc(THEME.dog_image)}" onerror="this.onerror=null;this.src='/static/images/shop-dogs.jpg'" alt="Pongo, Zeke & Pierce" />
        <div style="font-size:0.76rem;line-height:1.35;">
          <div style="font-weight:900;color:#fde68a;margin-bottom:2px;">${esc(THEME.icon)} Pongo, Zeke &amp; Pierce</div>
          <div>${esc(THEME.dog_message)}</div>
        </div>`;
      document.body.appendChild(card);
    }
  }

  async function syncTheme() {
    injectBaseThemeStyles();
    try {
      const res = await fetch('/api/public/theme');
      if (!res.ok) return;
      const data = await res.json();
      const sig = `${data.key}|${data.enable_ops}|${data.enable_tv}|${data.dog_message}`;
      if (sig !== LAST_KEY) {
        LAST_KEY = sig;
        clearThemeDom();
      }
      THEME = data;
      if (location.pathname === '/tv') renderTvAllOutTheme();
      else {
        const shell = document.getElementById('appShell');
        if (shell && !shell.hidden) renderOpsTheme();
      }
    } catch (_) {}
  }

  window.openHolidayThemeModal = async function () {
    await syncTheme();
    if (!THEME) return;
    let m = document.getElementById('holidayThemeModal');
    if (!m) {
      m = document.createElement('div');
      m.id = 'holidayThemeModal';
      m.className = 'modal-bg';
      m.onclick = e => { if (e.target === m) m.hidden = true; };
      m.innerHTML = '<div class="modal" style="max-width:620px;" id="holidayThemeModalInner"></div>';
      document.body.appendChild(m);
    }
    m.hidden = false;

    const opts = [
      `<option value="AUTO" ${THEME.mode === 'AUTO' ? 'selected' : ''}>🤖 Auto-Schedule (15 Days Before → 2 Days After All Holidays)</option>`,
      `<option value="OFF" ${THEME.mode === 'OFF' ? 'selected' : ''}>🌲 Off (Neutral Cream &amp; Green Year-Round)</option>`,
      ...(THEME.available_holidays || []).map(h =>
        `<option value="${esc(h.key)}" ${THEME.mode === h.key ? 'selected' : ''}>${esc(h.icon)} Force Preview / Activate: ${esc(h.name)}</option>`
      ),
    ].join('');

    document.getElementById('holidayThemeModalInner').innerHTML = `
      <div class="card-hdr">
        <div>
          <h3>🎨 Holiday Themes &amp; Shop Pups Manager</h3>
          <small style="color:var(--muted);">Currently Showing: <strong>${esc(THEME.icon)} ${esc(THEME.name)}</strong> ${THEME.countdown_label ? `(${esc(THEME.countdown_label)})` : ''}</small>
        </div>
        <button type="button" class="secondary" onclick="document.getElementById('holidayThemeModal').hidden=true">✖ Close</button>
      </div>

      <div class="day-sec-box">
        <label>Active Holiday Mode</label>
        <select id="tmModeSel" style="margin-bottom:10px;">${opts}</select>

        <div style="display:flex;gap:14px;flex-wrap:wrap;padding:8px 10px;background:#fff;border:1px solid var(--border-soft);border-radius:8px;margin-bottom:10px;">
          <label class="inline-check"><input type="checkbox" id="tmPubChk" ${THEME.enable_public ? 'checked' : ''} /><span>🌐 Main Website &amp; Dogs</span></label>
          <label class="inline-check"><input type="checkbox" id="tmTvChk" ${THEME.enable_tv ? 'checked' : ''} /><span>📺 Live TV (All-Out Morale)</span></label>
          <label class="inline-check"><input type="checkbox" id="tmOpsChk" ${THEME.enable_ops ? 'checked' : ''} /><span>🔧 Ops/Office (Light Garland)</span></label>
        </div>

        <label>Custom Message from Pongo, Zeke &amp; Pierce (Leave Blank for Auto Holiday Greeting)</label>
        <input id="tmDogMsg" value="${esc(THEME.custom_dog_message || '')}" placeholder="${esc(THEME.dog_message || '')}" />
        <small style="display:block;color:var(--muted);margin-top:5px;">
          💡 Holiday dog image filename for <strong>${esc(THEME.name)}</strong>: <code>static/images/${esc(THEME.dog_image)}</code> (auto-falls back to <code>shop-dogs.jpg</code> if not saved yet).
        </small>
      </div>

      <div style="display:flex;gap:8px;">
        <button type="button" class="primary" style="margin:0;flex:2;" onclick="window.saveHolidayThemeModal()">💾 Apply Theme Across Site, Ops &amp; TV</button>
        <a class="secondary" href="/tv" target="_blank" style="flex:1;justify-content:center;">📺 Preview Live TV ↗</a>
      </div>
    `;
  };

  window.saveHolidayThemeModal = async function () {
    const res = await fetch('/api/theme/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        theme_mode: document.getElementById('tmModeSel').value,
        enable_public: document.getElementById('tmPubChk').checked,
        enable_tv: document.getElementById('tmTvChk').checked,
        enable_ops: document.getElementById('tmOpsChk').checked,
        custom_dog_message: document.getElementById('tmDogMsg').value,
      }),
    });
    if (!res.ok) { alert((await res.json()).detail || 'Could not save theme.'); return; }
    LAST_KEY = '';
    await syncTheme();
    document.getElementById('holidayThemeModal').hidden = true;
  };

  window.addEventListener('load', () => setTimeout(syncTheme, 250));
  setInterval(syncTheme, 20000);
})();
