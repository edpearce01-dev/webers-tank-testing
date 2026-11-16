// Weber's Tank Testing — v15.0 Workspace Launcher & Instant Light/Dark Mode Engine
(function () {
  const THEME_STORAGE_KEY = 'webers_ui_color_mode';

  // Apply saved Light/Dark preference immediately before paint so there is zero flash!
  const savedMode = localStorage.getItem(THEME_STORAGE_KEY) || 'light';
  document.documentElement.setAttribute('data-ui-theme', savedMode);

  const APPS = [
    {
      key: 'ops',
      href: '/ops',
      title: 'Shop Floor Ops',
      desc: 'Jacket calibration, 360° stamp scanner, hydro stand & finish bench',
      color: '#059669',
      svg: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v6m0 0a4 4 0 104 4v8a2 2 0 01-2 2h-4a2 2 0 01-2-2v-8a4 4 0 004-4z"/></svg>',
    },
    {
      key: 'schedule',
      href: '/schedule',
      title: 'Schedule & Dispatch',
      desc: 'Month calendar, phone drop-off logger, truck runs & route ETAs',
      color: '#2563eb',
      svg: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="3"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>',
    },
    {
      key: 'office',
      href: '/office',
      title: 'Office & Billing',
      desc: 'Invoices, customer Rolodex, 5-yr recalls, stock limits & timecards',
      color: '#0d9488',
      svg: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="2" width="16" height="20" rx="2"/><path d="M8 7h8M8 11h8M8 15h5"/></svg>',
    },
    {
      key: 'print',
      href: '/print',
      title: 'Print & Audit Vault',
      desc: '5-year DOT filing cabinet, 12-row sheets, B386 stickers & audit ZIP',
      color: '#6366f1',
      svg: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9V2h12v7"/><path d="M6 18H4a2 2 0 01-2-2v-5a2 2 0 012-2h16a2 2 0 012 2v5a2 2 0 01-2 2h-2"/><rect x="6" y="14" width="12" height="8"/></svg>',
    },
    {
      key: 'owner',
      href: '/owner',
      title: 'Owner Controls',
      desc: 'Operating hours, holidays, pricing rates, crew PINs & alert setup',
      color: '#d97706',
      ownerOnly: true,
      svg: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09a1.65 1.65 0 00-1-1.51 1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09a1.65 1.65 0 001.51-1 1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06a1.65 1.65 0 001.82.33h.09a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82v.09a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/></svg>',
    },
    {
      key: 'tv',
      href: '/tv',
      title: 'Live Wall TV',
      desc: 'Testing room 1080p stand display & 14-day schedule broadcast',
      color: '#0f766e',
      newTab: true,
      svg: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/></svg>',
    },
  ];

  function currentWorkspaceKey() {
    const p = location.pathname.toLowerCase();
    if (p.startsWith('/schedule')) return 'schedule';
    if (p.startsWith('/print')) return 'print';
    if (p.startsWith('/owner')) return 'owner';
    if (p.startsWith('/office')) return 'office';
    return 'ops';
  }

  function themeIconSvg(mode) {
    return mode === 'dark'
      ? '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>'
      : '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z"/></svg>';
  }

  window.toggleWeberColorMode = function () {
    const cur = document.documentElement.getAttribute('data-ui-theme') === 'dark' ? 'dark' : 'light';
    const nxt = cur === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-ui-theme', nxt);
    localStorage.setItem(THEME_STORAGE_KEY, nxt);
    const btn = document.getElementById('uiColorModeBtn');
    if (btn) {
      btn.innerHTML = themeIconSvg(nxt);
      btn.title = nxt === 'dark' ? 'Switch to Daylight Mode' : 'Switch to Midnight Dark Mode';
    }
  };

  function mountShellControls() {
    const topbar = document.querySelector('.app-topbar');
    const brand = topbar?.querySelector('.top-brand');
    const ctrls = topbar?.querySelector('.top-controls');
    if (!topbar || !brand) return;

    const wsKey = currentWorkspaceKey();
    document.body.dataset.workspace = wsKey;
    document.body.classList.add('has-ws-launcher');

    if (!document.getElementById('wsAppsBtn')) {
      const cluster = document.createElement('div');
      cluster.className = 'ws-brand-cluster';
      brand.parentNode.insertBefore(cluster, brand);
      cluster.appendChild(brand);

      const appsBtn = document.createElement('button');
      appsBtn.type = 'button';
      appsBtn.id = 'wsAppsBtn';
      appsBtn.className = 'ws-apps-pill';
      appsBtn.onclick = () => window.openWorkspaceSheet();
      appsBtn.innerHTML = `
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="3" width="7" height="7" rx="1.8"/><rect x="14" y="3" width="7" height="7" rx="1.8"/>
          <rect x="3" y="14" width="7" height="7" rx="1.8"/><rect x="14" y="14" width="7" height="7" rx="1.8"/>
        </svg>
        <span>Workspaces</span>
      `;
      cluster.appendChild(appsBtn);
    }

    if (ctrls && !document.getElementById('uiColorModeBtn')) {
      const mode = document.documentElement.getAttribute('data-ui-theme') || 'light';
      const tBtn = document.createElement('button');
      tBtn.type = 'button';
      tBtn.id = 'uiColorModeBtn';
      tBtn.className = 'theme-switch-pill';
      tBtn.title = mode === 'dark' ? 'Switch to Daylight Mode' : 'Switch to Midnight Dark Mode';
      tBtn.innerHTML = themeIconSvg(mode);
      tBtn.onclick = () => window.toggleWeberColorMode();
      ctrls.insertBefore(tBtn, ctrls.firstChild);
    }
  }

  window.openWorkspaceSheet = function () {
    let sheet = document.getElementById('wsLauncherSheet');
    if (!sheet) {
      sheet = document.createElement('div');
      sheet.id = 'wsLauncherSheet';
      sheet.className = 'ws-sheet-bg';
      sheet.onclick = e => { if (e.target === sheet) sheet.hidden = true; };
      document.body.appendChild(sheet);
    }
    const activeKey = currentWorkspaceKey();
    const role = window.CURRENT_USER?.role || document.body.dataset.role || 'OWNER';
    const isBoss = role === 'OWNER' || role === 'SYSTEM_ADMIN';
    const mode = document.documentElement.getAttribute('data-ui-theme') || 'light';
    const visibleApps = APPS.filter(a => !a.ownerOnly || isBoss);

    sheet.hidden = false;
    sheet.innerHTML = `
      <div class="ws-sheet-card">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding-bottom:12px;border-bottom:1px solid var(--ui-border);">
          <div>
            <div style="font-size:1.08rem;font-weight:800;">Weber's Workspaces</div>
            <small style="color:var(--ui-muted);font-weight:600;">Switch screen or toggle Daylight / Midnight appearance</small>
          </div>
          <div style="display:flex;gap:8px;align-items:center;">
            <button type="button" class="secondary" onclick="window.toggleWeberColorMode();window.openWorkspaceSheet()">
              ${mode === 'dark' ? 'Daylight Mode' : 'Midnight Mode'}
            </button>
            <button type="button" class="secondary" onclick="document.getElementById('wsLauncherSheet').hidden=true">Close</button>
          </div>
        </div>
        <div class="ws-sheet-grid">
          ${visibleApps.map(a => `
            <a class="ws-app-card ${a.key === activeKey ? 'active' : ''}" href="${a.href}" ${a.newTab ? 'target="_blank" rel="noopener"' : ''}>
              <div class="ws-app-ico" style="background:${a.color};">${a.svg}</div>
              <div>
                <strong>${a.title}${a.newTab ? ' ↗' : ''}</strong>
                <span>${a.desc}</span>
              </div>
            </a>
          `).join('')}
        </div>
      </div>
    `;
  };

  window.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      const s = document.getElementById('wsLauncherSheet');
      if (s && !s.hidden) s.hidden = true;
    }
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mountShellControls);
  } else {
    mountShellControls();
  }
})();
