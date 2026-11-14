// Weber's Tank Testing — Floating Crew Help & Bug-Logger AI Widget
(function () {
  const STORAGE_KEY = 'webers_help_chat_v1';
  let CHAT_HISTORY = [];
  try {
    CHAT_HISTORY = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || '[]');
  } catch (_) { CHAT_HISTORY = []; }

  function esc(s) {
    const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' };
    return String(s ?? '').replace(/[&<>"]/g, m => map[m]);
  }

  function formatBotText(raw) {
    return esc(raw).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  }

  function injectWidget() {
    if (document.getElementById('webersHelpFab')) return;

    const fab = document.createElement('button');
    fab.id = 'webersHelpFab';
    fab.type = 'button';
    fab.className = 'help-fab';
    fab.innerHTML = '<span>🤖</span><span>Shop Help AI</span>';
    fab.onclick = () => toggleHelpBot();

    const panel = document.createElement('div');
    panel.id = 'webersHelpPanel';
    panel.className = 'help-panel';
    panel.hidden = true;
    panel.innerHTML = `
      <div class="help-hdr">
        <div>
          <strong>🤖 Weber's Shop Help &amp; Troubleshooter</strong>
          <small>Knows live shop status • Logs bugs straight to Eric</small>
        </div>
        <div style="display:flex;gap:5px;">
          <button type="button" class="secondary" style="padding:3px 7px;font-size:0.72rem;" onclick="window.clearHelpBotChat()" title="Reset Chat">↺</button>
          <button type="button" class="secondary" style="padding:3px 8px;font-size:0.75rem;" onclick="window.toggleHelpBot(false)">✖</button>
        </div>
      </div>
      <div class="help-chips">
        <button type="button" class="help-chip" onclick="window.askHelpPreset('How do I quickly log a phone call vs a truck pickup run?')">📞 Call vs 🚚 Truck</button>
        <button type="button" class="help-chip" onclick="window.askHelpPreset('Why is the app blocking me right now? Check my live shop status.')">⚠️ Why am I blocked?</button>
        <button type="button" class="help-chip" onclick="window.askHelpPreset('How do I use the Month Calendar and set daily shift modes?')">📅 Calendar Help</button>
        <button type="button" class="help-chip" onclick="window.askHelpPreset('I found a bug or something awkward that Eric needs to update: ')">🐛 Report Bug to Eric</button>
      </div>
      <div class="help-msgs" id="webersHelpMsgs"></div>
      <div class="help-foot">
        <input id="webersHelpInput" placeholder="Ask how something works or report an issue..." onkeydown="if(event.key==='Enter')window.sendHelpBotMsg()" />
        <button type="button" class="primary" id="webersHelpSendBtn" style="width:auto;margin:0;padding:9px 13px;" onclick="window.sendHelpBotMsg()">Send</button>
      </div>
    `;

    document.body.appendChild(fab);
    document.body.appendChild(panel);
    renderMessages();
  }

  function renderMessages() {
    const box = document.getElementById('webersHelpMsgs');
    if (!box) return;
    if (!CHAT_HISTORY.length) {
      box.innerHTML = `
        <div class="help-bubble bot">Hey! Ask me anything about <strong>Shop Ops</strong>, the <strong>Month Calendar</strong>, <strong>Truck Dispatches</strong>, <strong>Valve Tags</strong>, or <strong>Invoicing</strong>.<br><br>If an input error pops up, ask <em>"Why am I blocked?"</em> — or if you spot a real bug/quirk, tell me and I'll log it straight to Eric's update list!</div>`;
      return;
    }
    box.innerHTML = CHAT_HISTORY.map(m => `
      <div class="help-bubble ${m.role === 'user' ? 'user' : 'bot'}">${m.role === 'user' ? esc(m.text) : formatBotText(m.text)}${m.ticket ? `<div class="help-ticket-pill">✓ Logged #${m.ticket} for Eric's Next Update</div>` : ''}</div>
    `).join('');
    box.scrollTop = box.scrollHeight;
  }

  window.toggleHelpBot = function (forceState) {
    const panel = document.getElementById('webersHelpPanel');
    if (!panel) return;
    panel.hidden = typeof forceState === 'boolean' ? !forceState : !panel.hidden;
    if (!panel.hidden) {
      renderMessages();
      setTimeout(() => document.getElementById('webersHelpInput')?.focus(), 40);
    }
  };

  window.clearHelpBotChat = function () {
    CHAT_HISTORY = [];
    sessionStorage.removeItem(STORAGE_KEY);
    renderMessages();
  };

  window.askHelpPreset = function (txt) {
    const inp = document.getElementById('webersHelpInput');
    if (!inp) return;
    if (txt.endsWith(': ')) {
      inp.value = txt;
      inp.focus();
      return;
    }
    inp.value = txt;
    window.sendHelpBotMsg();
  };

  window.sendHelpBotMsg = async function () {
    const inp = document.getElementById('webersHelpInput');
    const btn = document.getElementById('webersHelpSendBtn');
    const msg = (inp?.value || '').trim();
    if (!msg) return;

    const prior = CHAT_HISTORY.slice(-8).map(x => ({ role: x.role === 'user' ? 'user' : 'assistant', text: x.text }));
    CHAT_HISTORY.push({ role: 'user', text: msg });
    inp.value = '';
    renderMessages();

    btn.disabled = true;
    btn.textContent = '...';
    try {
      const activeTab = (typeof CURRENT_TAB !== 'undefined' && CURRENT_TAB) ? CURRENT_TAB : '';
      const crew = localStorage.getItem('webers_active_crew') || 'RP';
      const res = await fetch('/api/assistant/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: msg,
          current_page: location.pathname,
          current_tab: activeTab,
          submitted_by: crew,
          history: prior,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        CHAT_HISTORY.push({ role: 'assistant', text: data.detail || 'Could not reach Help AI right now.' });
      } else {
        CHAT_HISTORY.push({
          role: 'assistant',
          text: data.reply,
          ticket: data.logged_ticket ? data.ticket_id : null,
        });
      }
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(CHAT_HISTORY.slice(-16)));
      renderMessages();
    } catch (_) {
      CHAT_HISTORY.push({ role: 'assistant', text: 'Network hiccup reaching Help AI — check connection and try again.' });
      renderMessages();
    } finally {
      btn.disabled = false;
      btn.textContent = 'Send';
    }
  };

  setInterval(() => {
    const shell = document.getElementById('appShell');
    const fab = document.getElementById('webersHelpFab');
    if (shell && !shell.hidden) {
      if (!fab) injectWidget();
      else fab.hidden = false;
    } else if (fab) {
      fab.hidden = true;
    }
  }, 600);
})();
