// Weber's Tank Testing — v9.3 Clarity-First Holiday Theme Engine
(function () {
  const MUTE_KEY = 'webers_mute_ops_theme';
  let THEME = null;
  let LAST_KEY = '';

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
      body.webers-ops-themed .ops-topbar { position: sticky; overflow: visible !important; }
      .ops-light-strand {
        position: absolute; left: 0; right: 0; bottom: -9px; height: 10px; z-index: 45;
        pointer-events: none; overflow: hidden; display: flex; justify-content: space-around; padding: 0 8px;
      }
      .ops-bulb {
        width: 6px; height: 8px; border-radius: 45% 45% 55% 55%;
        animation: wTwinkle 2.4s infinite alternate ease-in-out; box-shadow: 0 0 5px currentColor;
      }
      .ops-bulb:nth-child(odd) { animation-duration: 1.8s; }
      @keyframes wTwinkle { 0% { opacity: 0.45; transform: scale(0.92); } 100% { opacity: 0.95; transform: scale(1.06); } }

      body.webers-tv-themed { padding: 6px !important; box-sizing: border-box !important; }
      .tv-perimeter-lights { position: fixed; inset: 0; pointer-events: none; z-index: 90; border: 2px solid rgba(251,191,36,0.25); }
      .tv-edge-row { position: absolute; left: 10px; right: 10px; display: flex; justify-content: space-around; }
      .tv-edge-row.top { top: 1px; } .tv-edge-row.bottom { bottom: 1px; }
      .tv-edge-col { position: absolute; top: 16px; bottom: 16px; display: flex; flex-direction: column; justify-content: space-around; }
      .tv-edge-col.left { left: 1px; } .tv-edge-col.right { right: 1px; }
      .tv-bulb { width: 7px; height: 9px; border-radius: 50%; animation: wTwinkle 1.6s infinite alternate ease-in-out; box-shadow: 0 0 7px currentColor; }

      .tv-morale-strip {
        display: flex; align-items: center; justify-content: space-between; gap: 10px;
        background: linear-gradient(90deg, rgba(15,46,32,0.95), rgba(27,67,50,0.95) 65%, rgba(120,53,15,0.92));
        border: 1.5px solid rgba(251,191,36,0.6); border-radius: 9px;
        padding: 4px 12px; margin: 3px 10px 4px; color: #fff; flex-shrink: 0; position: relative; z-index: 20;
      }
      .tv-morale-left { display: flex; align-items: center; gap: 9px; min-width: 0; flex: 1; }
      .tv-morale-left img { height: 32px; width: auto; border-radius: 6px; background: #fdf8ee; padding: 1px 3px; border: 1.5px solid #fde68a; }
      .tv-morale-msg { font-size: 0.8rem; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .tv-countdown-pill { background: rgba(254,243,199,0.16); border: 1.5px solid #fde68a; color: #fde68a; border-radius: 999px; padding: 2px 11px; font-weight: 900; font-size: 0.76rem; white-space: nowrap; }
      .tv-mute-pill { background: rgba(15,23,42,0.55); border: 1px solid rgba(255,255,255,0.28); color: #cbd5e1; border-radius: 999px; padding: 2px 8px; font-size: 0.68rem; font-weight: 800; cursor: pointer; }

      .tv-particles-wrap { position: fixed; inset: 0; pointer-events: none; z-index: 4; overflow: hidden; }
      .tv-particle { position: absolute; top: -30px; font-size: 1rem; opacity: 0.2; animation: tvFall linear infinite; }
      @keyframes tvFall { 0% { transform: translateY(-25px) rotate(0deg); opacity: 0.24; } 100% { transform: translateY(105vh) rotate(260deg); opacity: 0.06; } }
    `;
    document.head.appendChild(st);
  }

  function clearDom() {
    document.body.classList.remove('webers-ops-themed', 'webers-tv-themed');
    ['webersOpsStrand', 'webersTvLights', 'webersTvParticles', 'webersTvMoraleBar'].forEach(id => document.getElementById(id)?.remove());
  }

  function renderOps() {
    const topBar = document.querySelector('.ops-topbar');
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
    if (btn) btn.textContent = muted ? `${THEME.icon} Off` : `${THEME.icon} ON`;
    if (muted || !topBar) { clearDom(); return; }

    document.body.classList.add('webers-ops-themed');
    if (!document.getElementById('webersOpsStrand')) {
      const pal = PALETTES[THEME.garland] || PALETTES.gold_lights;
      const strand = document.createElement('div');
      strand.id = 'webersOpsStrand'; strand.className = 'ops-light-strand';
      strand.innerHTML = Array.from({ length: 32 }, (_, i) => `<span class="ops-bulb" style="background:${pal[i % pal.length]};color:${pal[i % pal.length]};"></span>`).join('');
      topBar.appendChild(strand);
    }
  }

  function renderTv() {
    const muted = localStorage.getItem('webers_mute_tv_theme') === '1';
    if (!THEME || !THEME.active || !THEME.enable_tv) { clearDom(); return; }

    let strip = document.getElementById('webersTvMoraleBar');
    if (!strip) {
      strip = document.createElement('div'); strip.id = 'webersTvMoraleBar'; strip.className = 'tv-morale-strip';
      const hdr = document.querySelector('header, .tv-header, .tv-topbar') || document.body.firstElementChild;
      if (hdr?.parentNode) hdr.insertAdjacentElement('afterend', strip); else document.body.prepend(strip);
    }
    if (muted) {
      document.body.classList.remove('webers-tv-themed');
      ['webersTvLights', 'webersTvParticles'].forEach(id => document.getElementById(id)?.remove());
      strip.innerHTML = `<span class="tv-morale-msg" style="color:#94a3b8;">${esc(THEME.icon)} TV Holiday Theme Paused</span><button class="tv-mute-pill" onclick="window.toggleTvThemeMute()">Enable</button>`;
      return;
    }

    document.body.classList.add('webers-tv-themed');
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
      box.innerHTML = `<div class="tv-edge-row top">${mk(34)}</div><div class="tv-edge-row bottom">${mk(34)}</div><div class="tv-edge-col left">${mk(16)}</div><div class="tv-edge-col right">${mk(16)}</div>`;
      document.body.appendChild(box);
    }
    if (!document.getElementById('webersTvParticles')) {
      const ems = PARTICLES[THEME.effect] || ['✨'];
      const pw = document.createElement('div'); pw.id = 'webersTvParticles'; pw.className = 'tv-particles-wrap';
      pw.innerHTML = Array.from({ length: 10 }, (_, i) => `<span class="tv-particle" style="left:${Math.round(4 + i * 9.2)}%;animation-duration:${(13 + (i % 5) * 2.2).toFixed(1)}s;animation-delay:-${(i * 1.3).toFixed(1)}s;">${ems[i % ems.length]}</span>`).join('');
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
      if (sig !== LAST_KEY) { LAST_KEY = sig; clearDom(); }
      THEME = d;
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
      `<option value="OFF" ${THEME.mode === 'OFF' ? 'selected' : ''}>🌲 Off (Neutral Cream &amp; Green Year-Round)</option>`,
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
          <label class="inline-check"><input type="checkbox" id="tmPubChk" ${THEME.enable_public ? 'checked' : ''} /><span>🌐 Main Site (Outer Arch &amp; Dogs)</span></label>
          <label class="inline-check"><input type="checkbox" id="tmTvChk" ${THEME.enable_tv ? 'checked' : ''} /><span>📺 Live TV (Morale Strip &amp; Frame)</span></label>
          <label class="inline-check"><input type="checkbox" id="tmOpsChk" ${THEME.enable_ops ? 'checked' : ''} /><span>🔧 Ops/Office (Header Micro-Lights)</span></label>
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
    LAST_KEY = ''; await syncTheme();
    document.getElementById('holidayThemeModal').hidden = true;
  };

  window.addEventListener('load', () => setTimeout(syncTheme, 250));
  setInterval(syncTheme, 20000);
})();
