// Weber's Tank Testing — v9.4 Clarity-First Holiday Theme Engine (.app-topbar + Unsquished .tv-stage)
(function () {
  const MUTE_KEY = 'webers_mute_ops_theme';
  let THEME = null;
  let LAST_SIG = '';

  const esc = s => String(s ?? '').replace(/[&<>"]/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[m]));

  const PALETTES = {
    christmas_lights: ['#ef4444', '#22c55e', '#f59e0b', '#3b82f6', '#f43f5e'],
    autumn_lights: ['#d97706', '#ea580c', '#fbbf24', '#b45309', '#dc2626'],
    patriotic_lights: ['#ef4444', '#ffffff', '#3b82f6', '#ef4444', '#ffffff'],
    emerald_lights: ['#10b981', '#34d399', '#fbbf24', '#059669', '#6ee7b7'],
    spooky_lights: ['#f97316', '#a855f7', '#f59e0b', '#9333ea', '#ea580c'],
    warm_pink_lights: ['#f43f5e', '#fb7185', '#fda4af', '#e11d48', '#fef08a'],
    pastel_lights: ['#a78bfa', '#34d399', '#f472b6', '#fbbf24', '#60a5fa'],
    gold_lights: ['#fbbf24', '#fef08a', '#f59e0b', '#ffffff', '#d97706'],
  };

  const PARTICLES = {
    snow: ['❄️', '❅', '✨'], autumn_leaves: ['🍂', '🍁', '🍃'], patriotic: ['✨', '⭐'],
    shamrocks: ['🍀', '✨'], halloween: ['🎃', '🍂'], hearts: ['❤️', '✨'],
    spring: ['🌸', '✨'], fireworks: ['🎆', '✨'],
  };

  function injectCss() {
    if (document.getElementById('webersThemeCss')) return;
    const st = document.createElement('style');
    st.id = 'webersThemeCss';
    st.textContent = `
      /* Subtle micro-lights anchored to bottom edge of .app-topbar on /ops, /schedule & /office */
      body.webers-ops-themed .app-topbar { position: sticky; overflow: visible !important; }
      .ops-light-strand {
        position: absolute; left: 0; right: 0; bottom: -9px; height: 10px; z-index: 46;
        pointer-events: none; overflow: hidden; display: flex; justify-content: space-around; padding: 0 10px;
      }
      .ops-bulb {
        width: 6px; height: 8px; border-radius: 45% 45% 55% 55%;
        animation: wTwinkle 2.2s infinite alternate ease-in-out; box-shadow: 0 0 6px currentColor;
      }
      .ops-bulb:nth-child(odd) { animation-duration: 1.6s; }
      @keyframes wTwinkle { 0% { opacity: 0.45; transform: scale(0.9); } 100% { opacity: 1; transform: scale(1.08); } }

      /* /tv: Bezel perimeter lights + Morale Strip INSIDE main.tv-stage (preserves 3-row 1080p grid!) */
      .tv-perimeter-lights { position: fixed; inset: 0; pointer-events: none; z-index: 85; }
      .tv-edge-row { position: absolute; left: 12px; right: 12px; display: flex; justify-content: space-around; }
      .tv-edge-row.top { top: 1px; } .tv-edge-row.bottom { bottom: 1px; }
      .tv-edge-col { position: absolute; top: 18px; bottom: 18px; display: flex; flex-direction: column; justify-content: space-around; }
      .tv-edge-col.left { left: 2px; } .tv-edge-col.right { right: 2px; }
      .tv-bulb { width: 7px; height: 9px; border-radius: 50%; animation: wTwinkle 1.5s infinite alternate ease-in-out; box-shadow: 0 0 8px currentColor; }

      .tv-morale-strip {
        display: flex; align-items: center; justify-content: space-between; gap: 12px;
        background: linear-gradient(90deg, rgba(12,38,27,0.92), rgba(22,58,42,0.90) 65%, rgba(146,64,14,0.88));
        backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
        border: 1px solid rgba(251,191,36,0.65); border-radius: 9px;
        padding: 4px 12px; color: #fff; flex-shrink: 0; box-shadow: 0 6px 18px -6px rgba(12,38,27,0.22);
      }
      .tv-morale-left { display: flex; align-items: center; gap: 10px; min-width: 0; flex: 1; }
      .tv-morale-left img { height: 32px; width: auto; border-radius: 6px; background: #fdf8ee; padding: 1px 4px; border: 1.5px solid #fde68a; flex-shrink: 0; }
      .tv-morale-msg { font-size: 0.8rem; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: #f8fafc; }
      .tv-countdown-pill { background: rgba(251,191,36,0.18); border: 1px solid #fde68a; color: #fde68a; border-radius: 999px; padding: 2px 10px; font-weight: 900; font-size: 0.74rem; white-space: nowrap; }
      .tv-mute-pill { background: rgba(5,13,10,0.65); border: 1px solid rgba(255,255,255,0.24); color: #cbd5e1; border-radius: 999px; padding: 2px 8px; font-size: 0.68rem; font-weight: 800; cursor: pointer; }

      .tv-particles-wrap {
        position: fixed; inset: 0; pointer-events: none; z-index: 2; overflow: hidden;
        opacity: 0.72; transition: opacity 1.6s ease;
      }
      body.tv-screensaver .tv-particles-wrap,
      body.tv-bg-reveal .tv-particles-wrap {
        opacity: 1; z-index: 20;
      }
      body.tv-screensaver .tv-bulb {
        width: 9px; height: 11px; box-shadow: 0 0 12px currentColor, 0 0 22px currentColor;
      }
      .tv-particle {
        position: absolute; top: -48px;
        filter: drop-shadow(0 3px 5px rgba(15, 23, 42, 0.14));
        animation: tvFallSway linear infinite;
        will-change: transform, opacity;
      }
      @keyframes tvFallSway {
        0%   { transform: translate3d(0px, -45px, 0) rotate(0deg); opacity: 0; }
        10%  { opacity: 0.92; }
        25%  { transform: translate3d(28px, 24vh, 0) rotate(85deg); }
        50%  { transform: translate3d(-24px, 52vh, 0) rotate(175deg); }
        75%  { transform: translate3d(26px, 78vh, 0) rotate(265deg); }
        90%  { opacity: 0.88; }
        100% { transform: translate3d(-16px, 108vh, 0) rotate(355deg); opacity: 0; }
      }
    `;
    document.head.appendChild(st);
  }

  function clearDom() {
    document.body.classList.remove('webers-ops-themed');
    ['webersOpsStrand', 'webersTvLights', 'webersTvParticles', 'webersTvMoraleBar'].forEach(id => document.getElementById(id)?.remove());
  }

  function renderOps() {
    const topBar = document.querySelector('.app-topbar');
    const topCtrls = document.querySelector('.top-controls');
    let btn = document.getElementById('opsThemeToggleBtn');
    const muted = localStorage.getItem(MUTE_KEY) === '1';

    if (!THEME || !THEME.active || !THEME.enable_ops) { btn?.remove(); clearDom(); return; }
    if (topCtrls && !btn) {
      btn = document.createElement('button');
      btn.id = 'opsThemeToggleBtn'; btn.type = 'button'; btn.className = 'menu-pill';
      btn.onclick = () => { localStorage.setItem(MUTE_KEY, localStorage.getItem(MUTE_KEY) === '1' ? '0' : '1'); renderOps(); };
      topCtrls.prepend(btn);
    }
    if (btn) {
      btn.textContent = muted ? `${THEME.icon} Off` : `${THEME.icon} Lights`;
      btn.title = 'Toggle subtle holiday header lights on this screen';
    }
    if (muted || !topBar) { clearDom(); return; }

    document.body.classList.add('webers-ops-themed');
    if (!document.getElementById('webersOpsStrand')) {
      const pal = PALETTES[THEME.garland] || PALETTES.gold_lights;
      const strand = document.createElement('div');
      strand.id = 'webersOpsStrand'; strand.className = 'ops-light-strand';
      strand.innerHTML = Array.from({ length: 34 }, (_, i) => `<span class="ops-bulb" style="background:${pal[i % pal.length]};color:${pal[i % pal.length]};"></span>`).join('');
      topBar.appendChild(strand);
    }
  }

  function renderTv() {
    const stage = document.querySelector('main.tv-stage');
    const muted = localStorage.getItem('webers_mute_tv_theme') === '1';
    if (!THEME || !THEME.active || !THEME.enable_tv || !stage) { clearDom(); return; }

    let strip = document.getElementById('webersTvMoraleBar');
    if (!strip) {
      strip = document.createElement('div');
      strip.id = 'webersTvMoraleBar';
      strip.className = 'tv-morale-strip';
      stage.prepend(strip); // Inside <main class="tv-stage"> so body grid stays 56px | 1fr | 38px!
    }

    if (muted) {
      ['webersTvLights', 'webersTvParticles'].forEach(id => document.getElementById(id)?.remove());
      strip.innerHTML = `<span class="tv-morale-msg" style="color:#94a3b8;">${esc(THEME.icon)} TV Holiday Theme Muted</span><button class="tv-mute-pill" onclick="window.toggleTvThemeMute()">Turn On</button>`;
      return;
    }

    strip.innerHTML = `
      <div class="tv-morale-left">
        <img src="/static/images/${esc(THEME.dog_image)}" onerror="this.onerror=null;this.src='/static/images/shop-dogs.jpg'" alt="Shop Dogs" />
        <div class="tv-morale-msg">${esc(THEME.dog_message)}</div>
      </div>
      <div style="display:flex;align-items:center;gap:8px;flex-shrink:0;">
        ${THEME.countdown_label ? `<span class="tv-countdown-pill">${esc(THEME.countdown_label)}</span>` : ''}
        <button type="button" class="tv-mute-pill" onclick="window.toggleTvThemeMute()">Mute</button>
      </div>`;

    const pal = PALETTES[THEME.garland] || PALETTES.christmas_lights;
    if (!document.getElementById('webersTvLights')) {
      const box = document.createElement('div'); box.id = 'webersTvLights'; box.className = 'tv-perimeter-lights';
      const mk = n => Array.from({ length: n }, (_, i) => `<span class="tv-bulb" style="background:${pal[i % pal.length]};color:${pal[i % pal.length]};"></span>`).join('');
      box.innerHTML = `<div class="tv-edge-row top">${mk(36)}</div><div class="tv-edge-row bottom">${mk(36)}</div><div class="tv-edge-col left">${mk(16)}</div><div class="tv-edge-col right">${mk(16)}</div>`;
      document.body.appendChild(box);
    }
    if (!document.getElementById('webersTvParticles')) {
      const ems = PARTICLES[THEME.effect] || ['✨'];
      const pw = document.createElement('div'); pw.id = 'webersTvParticles'; pw.className = 'tv-particles-wrap';
      pw.innerHTML = Array.from({ length: 32 }, (_, i) => {
        const left = ((i * 3.15) + (i % 3) * 1.1).toFixed(1);
        const sz = (1.15 + (i % 5) * 0.24).toFixed(2);
        const dur = (10.5 + (i % 6) * 2.1).toFixed(1);
        const del = ((i * 0.85) % 15).toFixed(1);
        return `<span class="tv-particle" style="left:${left}%;font-size:${sz}rem;animation-duration:${dur}s;animation-delay:-${del}s;">${ems[i % ems.length]}</span>`;
      }).join('');
      document.body.appendChild(pw);
    }
  }

  window.toggleTvThemeMute = () => {
    localStorage.setItem('webers_mute_tv_theme', localStorage.getItem('webers_mute_tv_theme') === '1' ? '0' : '1');
    clearDom(); renderTv();
  };

  async function syncTheme() {
    injectCss();
    try {
      const res = await fetch('/api/public/theme');
      if (!res.ok) return;
      const d = await res.json();
      const sig = `${d.key}|${d.enable_ops}|${d.enable_tv}|${d.dog_message}`;
      if (sig !== LAST_SIG) { LAST_SIG = sig; clearDom(); }
      THEME = d;
      const fav = document.getElementById('dynamicFavicon');
      if (fav) fav.href = `/pwa-icon.png?size=64&mode=any&theme=${encodeURIComponent(d.key || 'CLASSIC')}`;
      if (location.pathname === '/tv') renderTv();
      else if (document.getElementById('appShell') && !document.getElementById('appShell').hidden) renderOps();
    } catch (_) {}
  }

  window.openHolidayThemeModal = async function () {
    await syncTheme(); if (!THEME) return;
    let m = document.getElementById('holidayThemeModal');
    if (!m) {
      m = document.createElement('div'); m.id = 'holidayThemeModal'; m.className = 'modal-bg';
      m.onclick = e => { if (e.target === m) m.hidden = true; };
      m.innerHTML = '<div class="modal" style="max-width:620px;" id="holidayThemeModalInner"></div>';
      document.body.appendChild(m);
    }
    m.hidden = false;
    const opts = [
      `<option value="AUTO" ${THEME.mode === 'AUTO' ? 'selected' : ''}>🤖 Auto-Schedule (15 Days Before → 2 Days After All Holidays)</option>`,
      `<option value="OFF" ${THEME.mode === 'OFF' ? 'selected' : ''}>🌲 Off (Clean Year-Round)</option>`,
      ...(THEME.available_holidays || []).map(h => `<option value="${esc(h.key)}" ${THEME.mode === h.key ? 'selected' : ''}>${esc(h.icon)} Force Preview / Activate: ${esc(h.name)}</option>`),
    ].join('');
    document.getElementById('holidayThemeModalInner').innerHTML = `
      <div class="card-hdr">
        <div><h3>🎨 Holiday Themes &amp; Shop Pups</h3><small style="color:var(--muted);">Active: <strong>${esc(THEME.icon)} ${esc(THEME.name)}</strong></small></div>
        <button type="button" class="secondary" onclick="document.getElementById('holidayThemeModal').hidden=true">✖ Close</button>
      </div>
      <div class="day-sec-box">
        <label>Active Holiday Mode</label>
        <select id="tmModeSel" style="margin-bottom:10px;">${opts}</select>
        <div style="display:flex;gap:14px;flex-wrap:wrap;padding:8px 10px;background:#fff;border:1px solid var(--border-soft);border-radius:8px;margin-bottom:10px;">
          <label class="inline-check"><input type="checkbox" id="tmPubChk" ${THEME.enable_public ? 'checked' : ''} /><span>🌐 Main Site (Outer Wreath &amp; Dogs)</span></label>
          <label class="inline-check"><input type="checkbox" id="tmTvChk" ${THEME.enable_tv ? 'checked' : ''} /><span>📺 Live TV (Morale Strip &amp; Frame)</span></label>
          <label class="inline-check"><input type="checkbox" id="tmOpsChk" ${THEME.enable_ops ? 'checked' : ''} /><span>🔧 Ops/Office (Top Header Lights)</span></label>
        </div>
        <label>Custom Message from Pongo, Zeke &amp; Pierce (Blank = Auto Holiday Greeting)</label>
        <input id="tmDogMsg" value="${esc(THEME.custom_dog_message || '')}" placeholder="${esc(THEME.dog_message || '')}" />
      </div>
      <div style="display:flex;gap:8px;">
        <button type="button" class="primary" style="margin:0;flex:2;" onclick="window.saveHolidayThemeModal()">💾 Apply Theme</button>
        <a class="secondary" href="/tv" target="_blank" style="flex:1;justify-content:center;">📺 Preview TV ↗</a>
      </div>`;
  };

  window.saveHolidayThemeModal = async function () {
    const res = await fetch('/api/theme/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        theme_mode: document.getElementById('tmModeSel').value,
        enable_public: document.getElementById('tmPubChk').checked,
        enable_tv: document.getElementById('tmTvChk').checked,
        enable_ops: document.getElementById('tmOpsChk').checked,
        custom_dog_message: document.getElementById('tmDogMsg').value,
      }),
    });
    if (!res.ok) return alert('Could not save theme.');
    LAST_SIG = ''; await syncTheme();
    document.getElementById('holidayThemeModal').hidden = true;
  };

  window.addEventListener('load', () => setTimeout(syncTheme, 250));
  setInterval(syncTheme, location.pathname === '/tv' ? 20000 : 60000);
})();
