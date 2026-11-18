let BBQ = { settings: {}, menu: [], events: [], selected_event: null, club_checklist: [], payment_methods: [], pit_boss_user: null };
let CART = [];
let HQ = null;
let HQ_TAB = 'orders';

const CAT_META = {
  SMOKED_MEATS: { title: 'SMOKED MEATS', sub: 'All of our meats are smoked using hickory and fruitwoods. Meat orders include house BBQ sauce on the side.' },
  SPECIALTIES: { title: 'PIT SPECIALTIES & FRYER', sub: 'Smokehouse & trailer-fryer crowd favorites — smoked bacon-wrapped eggs, burnt ends & party trays.' },
  SIDES: { title: 'SIDES', sub: 'All sides come in 1/2 or full pans — 1/2 pans feed 10–15 / full pans feed 20–25 on average.' },
  EXTRAS: { title: 'EXTRAS & SETUP', sub: 'Chafing racks, Sternos, buns & heavy-duty party packs.' },
};

const esc = s => String(s ?? '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));

// Subtle rising ember particles over the looping pit video
(function startEmbers() {
  const cv = document.getElementById('emberCanvas');
  if (!cv) return;
  const ctx = cv.getContext('2d');
  const pts = Array.from({ length: 34 }, () => ({ x: Math.random(), y: Math.random(), r: 1 + Math.random() * 2.2, v: 0.0008 + Math.random() * 0.0018, d: (Math.random() - 0.5) * 0.0008 }));
  function tick() {
    cv.width = cv.clientWidth || window.innerWidth;
    cv.height = cv.clientHeight || 520;
    ctx.clearRect(0, 0, cv.width, cv.height);
    pts.forEach(p => {
      p.y -= p.v; p.x += p.d;
      if (p.y < -0.05) { p.y = 1.05; p.x = Math.random(); }
      ctx.beginPath();
      ctx.fillStyle = 'rgba(249, 115, 22, 0.55)';
      ctx.arc(p.x * cv.width, p.y * cv.height, p.r, 0, Math.PI * 2);
      ctx.fill();
    });
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
})();

function getUrlEventSlug() {
  const parts = location.pathname.split('/').filter(Boolean);
  if (parts[0] === 'bbq' && parts[1]) return parts[1];
  return new URLSearchParams(location.search).get('event') || '';
}

async function loadBbqPublic() {
  const slug = getUrlEventSlug();
  const res = await fetch('/api/bbq/public/bootstrap' + (slug ? `?event=${encodeURIComponent(slug)}` : ''));
  if (!res.ok) return;
  BBQ = await res.json();
  const s = BBQ.settings || {};

  document.getElementById('navBrandTop').textContent = s.brand_top || 'BACKROAD';
  document.getElementById('navBrandBot').textContent = s.brand_bottom || 'PIT & SMOKE';
  document.title = `${s.brand_top || 'Backroad'} ${s.brand_bottom || 'Pit & Smoke'} — Catering & Menu`;
  document.getElementById('heroEyebrow').textContent = s.hero_eyebrow || '';
  document.getElementById('heroWhite').textContent = s.hero_headline_white || 'LOW & SLOW';
  document.getElementById('heroOrange').textContent = s.hero_headline_orange || 'WOOD-FIRED BBQ';
  document.getElementById('heroLeadNote').textContent = s.lead_time_note || '';
  const ph = s.contact_phone || '724-266-8008';
  const callEl = document.getElementById('topCallLink');
  callEl.textContent = `Call / Text ${ph}`;
  callEl.href = `tel:${ph.replace(/\D/g, '')}`;
  document.getElementById('pitFooterLeft').textContent = `${s.brand_top || 'Backroad'} ${s.brand_bottom || 'Pit & Smoke'} • ${s.pickup_location || 'Beaver County, PA'} • ${ph}`;

  if (s.hero_video_url) {
    const vEl = document.getElementById('pitHeroVideo');
    const sEl = document.getElementById('pitHeroVideoSrc');
    if (sEl.getAttribute('src') !== s.hero_video_url) {
      sEl.setAttribute('src', s.hero_video_url);
      vEl.load();
      vEl.play().catch(() => {});
    }
  }

  document.getElementById('btnPitHqTop').hidden = !BBQ.pit_boss_user;
  renderEventBannerAndClub();
  renderPitMenu();
  renderPaySelect();
}

function renderEventBannerAndClub() {
  const ev = BBQ.selected_event;
  const wrap = document.getElementById('activeEventBannerWrap');
  if (!ev) { wrap.hidden = true; return; }
  wrap.hidden = false;
  const shareUrl = `${location.origin}/bbq/${ev.slug}`;
  wrap.innerHTML = `
    <div class="pit-panel" style="border:1.5px solid var(--pit-orange);background:linear-gradient(90deg,#1c140e,#141210);display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;">
      <div>
        <span class="pit-eyebrow" style="margin-bottom:2px;">FEATURED EVENT / SMOKE DROP</span>
        <div style="font-family:'Barlow Condensed',sans-serif;font-size:1.75rem;font-weight:800;text-transform:uppercase;color:#fff;">${esc(ev.title)} ${ev.event_date ? `<span style="color:#fbbf24;">• ${esc(ev.event_date)}</span>` : ''}</div>
        <div style="color:#d6d3d1;font-size:0.86rem;">${esc(ev.cutoff_note || '')}${ev.location ? ' • ' + esc(ev.location) : ''}</div>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;">
        <a class="btn-pit-ghost" href="/api/bbq/public/flyer/${encodeURIComponent(ev.slug)}.pdf" target="_blank">Menu QR Flyer PDF</a>
        <a class="btn-pit" href="#club">Club Sign-Up Sheet ↓</a>
      </div>
    </div>`;

  document.getElementById('clubEventTitle').textContent = `${ev.title} — Dish & Supply Sign-Up`;
  document.getElementById('clubEventMeta').textContent = [ev.event_date ? `Date: ${ev.event_date}` : '', ev.location || '', `Share Link: ${shareUrl}`].filter(Boolean).join('  •  ');
  document.getElementById('clubPosterBtns').innerHTML = `
    <a class="btn-pit" href="/api/bbq/public/club-poster/${encodeURIComponent(ev.slug)}.pdf" target="_blank">Print Wall QR Sign-Up Sheet (PDF)</a>
    <button type="button" class="btn-pit-ghost" onclick="navigator.clipboard.writeText('${esc(shareUrl)}');this.textContent='Copied Link!'">Copy Event Link</button>`;

  const pBox = document.getElementById('clubPitProvidingBox');
  if (ev.pit_providing) {
    pBox.hidden = false;
    pBox.innerHTML = `<strong style="color:#fbbf24;text-transform:uppercase;font-family:'Barlow Condensed',sans-serif;font-size:1.15rem;letter-spacing:0.04em;">Coming Off The Smoker (Already Covered):</strong> <span style="color:#fafaf9;margin-left:8px;">${esc(ev.pit_providing)}</span>`;
  } else pBox.hidden = true;

  const list = BBQ.club_checklist || [];
  document.getElementById('clubChecklistArea').innerHTML = list.length ? list.map(it => {
    const rem = Math.max(0, (it.qty_needed || 1) - (it.qty_claimed || 0));
    const claimNames = (it.claims || []).map(c => `${esc(c.name)}${c.qty > 1 ? ' (×' + c.qty + ')' : ''}`).join(', ');
    return `
      <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:11px 0;border-bottom:1px solid #292524;">
        <div>
          <div style="font-weight:700;color:#fff;"><span style="color:#fbbf24;font-size:0.74rem;text-transform:uppercase;margin-right:6px;">[${esc(it.category)}]</span>${esc(it.item_name)}</div>
          <div style="font-size:0.8rem;color:var(--pit-muted);">${claimNames ? 'Covered by: <strong style="color:#86efac;">' + claimNames + '</strong>' : 'Still needed — tap Claim!'}</div>
        </div>
        <div>
          ${(!it.is_open_potluck && rem > 0)
            ? `<button type="button" class="pit-add-chip" onclick="claimClubSupply(${it.id}, '${esc(it.item_name)}')">Claim (${rem} left)</button>`
            : `<span style="color:#4ade80;font-size:0.8rem;font-weight:800;">✓ Covered</span>`}
        </div>
      </div>`;
  }).join('') : '<div style="color:var(--pit-muted);font-size:0.88rem;">No supplies listed yet — tap + Host Add Needed Item to post a checklist.</div>';
}

function renderPitMenu() {
  const host = document.getElementById('pitMenuContainer');
  const orderCats = ['SMOKED_MEATS', 'SPECIALTIES', 'SIDES', 'EXTRAS'];
  host.innerHTML = orderCats.map(cat => {
    const items = (BBQ.menu || []).filter(m => m.category === cat);
    if (!items.length) return '';
    const meta = CAT_META[cat] || { title: cat, sub: '' };
    return `
      <div class="pit-cat-hdr"><h2>${esc(meta.title)}</h2><div class="pit-cat-dash"></div></div>
      <p class="pit-cat-sub">${esc(meta.sub)}</p>
      <div>
        ${items.map(it => {
          const flames = it.spice_level > 0 ? ' ' + '🔥'.repeat(Math.min(3, it.spice_level)) : '';
          const p1 = Number(it.opt1_price || 0), p2 = Number(it.opt2_price || 0);
          const priceStr = p2 > 0 ? `$${p1.toFixed(2)} / $${p2.toFixed(2)}` : `$${p1.toFixed(2)}`;
          return `
            <div class="pit-item-row">
              <div>
                <div class="pit-item-title"><span>${esc(it.name)}${flames}</span></div>
                <div class="pit-item-desc">${esc(it.description || '')}</div>
              </div>
              <div class="pit-price-col">
                <div class="pit-price-lbl">${priceStr}</div>
                <div class="pit-add-btns">
                  <button type="button" class="pit-add-chip" onclick="addToCart(${it.id}, 1)">+ ${esc(it.opt1_label)} ($${p1.toFixed(0)})</button>
                  ${p2 > 0 ? `<button type="button" class="pit-add-chip" onclick="addToCart(${it.id}, 2)">+ ${esc(it.opt2_label)} ($${p2.toFixed(0)})</button>` : ''}
                </div>
              </div>
            </div>`;
        }).join('')}
      </div>`;
  }).join('');
}

function addToCart(itemId, optNum) {
  const it = (BBQ.menu || []).find(x => x.id === itemId);
  if (!it) return;
  const option = optNum === 2 ? it.opt2_label : it.opt1_label;
  const price = Number(optNum === 2 ? it.opt2_price : it.opt1_price);
  const existing = CART.find(c => c.id === itemId && c.option === option);
  if (existing) existing.qty += 1;
  else CART.push({ id: itemId, name: it.name, option, price, qty: 1 });
  updateCartBadge();
}

function updateCartBadge() {
  const cnt = CART.reduce((s, x) => s + x.qty, 0);
  document.getElementById('cartCountChip').textContent = `(${cnt})`;
}

function renderPaySelect() {
  const sel = document.getElementById('ordPayMethod');
  sel.innerHTML = (BBQ.payment_methods || []).map(p =>
    `<option value="${esc(p.code)}">${esc(p.label)}${p.handle ? ' (' + esc(p.handle) + ')' : ''}</option>`
  ).join('');
}

function openCartModal() {
  document.getElementById('ordConfirmBox').hidden = true;
  if (BBQ.selected_event?.event_date && !document.getElementById('ordDate').value) {
    document.getElementById('ordDate').value = BBQ.selected_event.event_date;
  }
  renderCartLines();
  document.getElementById('pitCartModal').hidden = false;
}

function renderCartLines() {
  const box = document.getElementById('pitCartLines');
  let tot = 0;
  box.innerHTML = CART.length ? CART.map((c, idx) => {
    tot += c.qty * c.price;
    return `<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #292524;">
      <div><strong>${esc(c.name)}</strong> <small style="color:#fbbf24;">(${esc(c.option)})</small></div>
      <div style="display:flex;align-items:center;gap:8px;">
        <button type="button" class="pit-add-chip" onclick="CART[${idx}].qty--;if(CART[${idx}].qty<=0)CART.splice(${idx},1);updateCartBadge();renderCartLines()">−</button>
        <strong>${c.qty}</strong>
        <button type="button" class="pit-add-chip" onclick="CART[${idx}].qty++;updateCartBadge();renderCartLines()">+</button>
        <span style="min-width:62px;text-align:right;font-weight:700;">$${(c.qty * c.price).toFixed(2)}</span>
      </div>
    </div>`;
  }).join('') : '<div style="color:var(--pit-muted);padding:12px 0;">Your pan order is empty — tap any + Pan button on the menu!</div>';
  document.getElementById('pitCartTotal').textContent = `$${tot.toFixed(2)}`;
}

async function submitCartOrder() {
  if (!CART.length) return alert('Add at least one pan or meat item from the menu first.');
  const payCode = document.getElementById('ordPayMethod').value;
  const res = await fetch('/api/bbq/public/order', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      source: 'BBQ',
      event_id: BBQ.selected_event?.id || null,
      order_type: BBQ.selected_event ? 'EVENT_ORDER' : 'PREORDER',
      customer_name: document.getElementById('ordName').value,
      customer_phone: document.getElementById('ordPhone').value,
      pickup_or_event_date: document.getElementById('ordDate').value,
      items: CART,
      payment_method: payCode,
      notes: document.getElementById('ordNotes').value,
    }),
  });
  const d = await res.json();
  if (!res.ok) return alert(d.detail || 'Could not submit order.');
  const pm = (BBQ.payment_methods || []).find(x => x.code === payCode);
  CART = []; updateCartBadge(); renderCartLines();
  const cBox = document.getElementById('ordConfirmBox');
  cBox.hidden = false;
  cBox.innerHTML = `<div class="pit-panel" style="border-color:#22c55e;background:#0d1f14;">
    <strong style="color:#4ade80;font-size:1.05rem;">✓ Order ${esc(d.order_no)} Locked In ($${d.total_amount.toFixed(2)})!</strong>
    <div style="font-size:0.86rem;color:#d6d3d1;margin-top:4px;">Payment selected: <strong>${esc(payCode)}</strong>${pm?.handle ? ` — Send to <strong>${esc(pm.handle)}</strong> (put ${esc(d.order_no)} in note)` : ' at pickup'}. We'll text you to confirm pickup timing!</div>
  </div>`;
}

async function submitRigInquiry() {
  const res = await fetch('/api/bbq/public/order', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      source: 'BBQ', order_type: 'RIG_INQUIRY',
      customer_name: document.getElementById('rigName').value,
      customer_phone: document.getElementById('rigPhone').value,
      pickup_or_event_date: document.getElementById('rigDate').value,
      headcount: parseInt(document.getElementById('rigHeads').value || '0', 10),
      service_style: document.getElementById('rigStyle').value,
      notes: document.getElementById('rigNotes').value,
    }),
  });
  const d = await res.json();
  if (!res.ok) return alert(d.detail || 'Enter your name and 10-digit phone number.');
  const msg = document.getElementById('rigConfirmMsg');
  msg.hidden = false;
  msg.textContent = `✓ Request ${d.order_no} sent! Ryan or Kenny will reach out to talk through your menu & date.`;
  document.getElementById('rigNotes').value = '';
}

async function claimClubSupply(chkId, itemName) {
  if (!BBQ.selected_event) return;
  const who = prompt(`Claim "${itemName}" — Enter your name:`);
  if (!who) return;
  const res = await fetch(`/api/bbq/public/club/${BBQ.selected_event.id}/claim`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ checklist_id: chkId, guest_name: who, qty: 1 }),
  });
  if (!res.ok) return alert((await res.json()).detail || 'Could not claim.');
  loadBbqPublic();
}

async function submitOpenPotluckDish() {
  if (!BBQ.selected_event) return alert('No active club event selected.');
  const res = await fetch(`/api/bbq/public/club/${BBQ.selected_event.id}/claim`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      guest_name: document.getElementById('potName').value,
      custom_category: document.getElementById('potCat').value,
      custom_dish: document.getElementById('potDish').value,
    }),
  });
  const d = await res.json();
  if (!res.ok) return alert(d.detail || 'Enter your name and dish.');
  document.getElementById('potDish').value = '';
  loadBbqPublic();
}

async function promptHostAddSupply() {
  if (!BBQ.selected_event) return;
  let pin = '';
  if (!BBQ.pit_boss_user) {
    pin = prompt('Enter the 4-digit Event Host Passcode (from Ryan or Kenny):') || '';
    if (!pin) return;
  }
  const item = prompt('What supply, drink, or dish is needed for the event? (e.g. 20 lb Bags of Ice)');
  if (!item) return;
  const qty = parseInt(prompt('How many are needed?', '2') || '1', 10);
  const res = await fetch(`/api/bbq/public/club/${BBQ.selected_event.id}/host-add`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ category: 'Supplies', item_name: item, qty_needed: qty, host_pin: pin }),
  });
  if (!res.ok) return alert((await res.json()).detail || 'Incorrect Host Passcode.');
  loadBbqPublic();
}

// --- PIT BOSS HQ (KENNY, RYAN & ERIC SHOP PIN UNLOCK) ---
async function openPitBossLoginOrHq() {
  if (BBQ.pit_boss_user) return openPitBossHq();
  const pin = prompt('Enter your Shop PIN (Kenny, Ryan, or Admin):');
  if (!pin) return;
  const res = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pin }) });
  if (!res.ok) return alert('Incorrect PIN.');
  await loadBbqPublic();
  openPitBossHq();
}

async function openPitBossHq() {
  const res = await fetch('/api/bbq/admin/state');
  if (!res.ok) return openPitBossLoginOrHq();
  HQ = await res.json();
  document.getElementById('pitBossWho').textContent = `Signed in as ${BBQ.pit_boss_user?.name || 'Pit Boss'}`;
  document.getElementById('pitBossModal').hidden = false;
  renderHqBody();
}

function switchHqTab(t) {
  HQ_TAB = t;
  document.querySelectorAll('.hq-tab').forEach(b => b.classList.toggle('active', b.getAttribute('data-hq') === t));
  renderHqBody();
}

function renderHqBody() {
  const box = document.getElementById('pitBossBody');
  if (!HQ) return;
  if (HQ_TAB === 'orders') {
    const bbqOrders = (HQ.orders || []).filter(o => o.source === 'BBQ');
    box.innerHTML = `
      <div class="pit-panel" style="margin-bottom:14px;border-color:#d97706;">
        <strong style="color:#fbbf24;">Auto-Tallied Pit Prep Summary (Active Unfinished Orders):</strong>
        <div style="margin-top:6px;display:flex;gap:8px;flex-wrap:wrap;">
          ${(HQ.prep_tally || []).length ? HQ.prep_tally.map(t => `<span style="background:#292524;padding:4px 10px;border-radius:999px;font-size:0.82rem;"><strong>${t.qty}×</strong> ${esc(t.label)}</span>`).join('') : '<span style="color:var(--pit-muted);font-size:0.84rem;">No open pan orders right now.</span>'}
        </div>
      </div>
      ${bbqOrders.map(o => {
        const items = JSON.parse(o.items_json || '[]').map(i => `${i.qty}× ${i.name} (${i.option})`).join(', ');
        return `<div style="padding:10px 0;border-bottom:1px solid #292524;display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;">
          <div>
            <strong>${esc(o.order_no)} — ${esc(o.customer_name)}</strong> (${esc(o.customer_phone)}) • <span style="color:#fbbf24;">$${Number(o.total_amount||0).toFixed(2)} [${esc(o.payment_method)}]</span> • <span style="color:#86efac;">${esc(o.status)}</span>
            <div style="font-size:0.82rem;color:#d6d3d1;">${esc(items || o.service_style)} ${o.pickup_or_event_date ? '• Date: ' + esc(o.pickup_or_event_date) : ''} ${o.notes ? '• Note: ' + esc(o.notes) : ''}</div>
          </div>
          <div style="display:flex;gap:6px;align-items:center;">
            <button class="pit-add-chip" onclick="setOrderStat(${o.id},'CONFIRMED')">Confirm</button>
            <button class="pit-add-chip" onclick="setOrderStat(${o.id},'PAID_PICKED_UP')">Paid &amp; Done</button>
            <button class="pit-add-chip" style="color:#f87171;" onclick="delOrder(${o.id})">✕</button>
          </div>
        </div>`;
      }).join('') || '<div style="color:var(--pit-muted);">No BBQ orders logged yet.</div>'}`;
    return;
  }

  if (HQ_TAB === 'events') {
    box.innerHTML = `
      <div class="pit-panel" style="margin-bottom:14px;">
        <h4 style="margin:0 0 10px;color:#fbbf24;">+ Create Event Menu Link &amp; Printable Club QR Poster</h4>
        <div class="pit-grid-2">
          <div><label>Event Title *</label><input id="evTitle" placeholder="e.g. Friday Stag Smoke or Super Bowl Pans" /></div>
          <div><label>Short Link Slug *</label><input id="evSlug" placeholder="e.g. super-bowl" /></div>
          <div><label>Event / Pickup Date</label><input type="date" id="evDate" /></div>
          <div><label>4-Digit Host Passcode (For Club Organizer)</label><input id="evPin" value="2026" maxlength="8" /></div>
          <div><label>Location / Club Name</label><input id="evLoc" placeholder="e.g. Baden American Legion" /></div>
          <div><label>Order Cutoff Note</label><input id="evCut" placeholder="e.g. Orders close Wednesday 6 PM" /></div>
          <div style="grid-column:1/-1;"><label>What We're Smoking / Providing (Shows on Club Poster)</label><input id="evProv" placeholder="e.g. Pulled Pork, Brisket & Smoked Mac & Cheese" /></div>
        </div>
        <button class="btn-pit" style="margin-top:10px;" onclick="createPitEvent()">Create Event Link &amp; QR Flyers</button>
      </div>
      ${(HQ.events || []).map(e => `
        <div style="padding:10px 0;border-bottom:1px solid #292524;display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">
          <div>
            <strong>${esc(e.title)}</strong> <small style="color:#fbbf24;">(/bbq/${esc(e.slug)} • Host PIN: ${esc(e.host_pin)})</small>
            <div style="font-size:0.8rem;color:var(--pit-muted);">${esc(e.event_date)} • ${esc(e.location)}</div>
          </div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;">
            <a class="pit-add-chip" href="/bbq/${encodeURIComponent(e.slug)}" target="_blank">Open Link ↗</a>
            <a class="pit-add-chip" href="/api/bbq/public/club-poster/${encodeURIComponent(e.slug)}.pdf" target="_blank">Club Sign-Up Poster PDF</a>
            <a class="pit-add-chip" href="/api/bbq/public/flyer/${encodeURIComponent(e.slug)}.pdf" target="_blank">Menu Flyer PDF</a>
            <button class="pit-add-chip" style="color:#f87171;" onclick="delEvent(${e.id})">✕</button>
          </div>
        </div>`).join('')}`;
    return;
  }

  if (HQ_TAB === 'menu') {
    box.innerHTML = `
      <div class="pit-panel" style="margin-bottom:14px;">
        <h4 style="margin:0 0 10px;color:#fbbf24;">+ Add Dish, Meat, or Fryer Specialty</h4>
        <div class="pit-grid-2">
          <div><label>Category</label><select id="mCat"><option value="SMOKED_MEATS">Smoked Meats</option><option value="SPECIALTIES">Pit Specialties &amp; Fryer</option><option value="SIDES">Sides &amp; Pans</option><option value="EXTRAS">Extras</option></select></div>
          <div><label>Item Name *</label><input id="mName" placeholder="e.g. Smoked Jalapeno Cheddar Sausage" /></div>
          <div><label>Option 1 Label &amp; Price ($)</label><div style="display:flex;gap:6px;"><input id="mO1" value="1/2 Pan" /><input type="number" id="mP1" value="45" /></div></div>
          <div><label>Option 2 Label &amp; Price (Optional)</label><div style="display:flex;gap:6px;"><input id="mO2" placeholder="Full Pan" /><input type="number" id="mP2" value="0" /></div></div>
          <div style="grid-column:1/-1;"><label>Mouthwatering Description</label><input id="mDesc" placeholder="Wood type, rub, glaze..." /></div>
        </div>
        <button class="btn-pit" style="margin-top:10px;" onclick="addMenuItem()">Add to Catering Menu</button>
      </div>
      ${(HQ.menu || []).map(m => `
        <div style="padding:8px 0;border-bottom:1px solid #292524;display:flex;justify-content:space-between;align-items:center;gap:8px;">
          <div><strong>${esc(m.name)}</strong> <small style="color:#fbbf24;">($${m.opt1_price}${m.opt2_price ? ' / $' + m.opt2_price : ''})</small> ${m.available ? '' : '<span style="color:#f87171;">[HIDDEN]</span>'}</div>
          <div style="display:flex;gap:6px;">
            <button class="pit-add-chip" onclick="toggleMenuAvail(${m.id}, ${!m.available})">${m.available ? 'Hide' : 'Show'}</button>
            <button class="pit-add-chip" style="color:#f87171;" onclick="delMenuItem(${m.id})">✕</button>
          </div>
        </div>`).join('')}`;
    return;
  }

  const s = HQ.settings || {};
  box.innerHTML = `
    <div class="pit-panel">
      <div class="pit-grid-2">
        <div><label>Brand Name Top Line (White)</label><input id="stTop" value="${esc(s.brand_top||'')}" /></div>
        <div><label>Brand Name Bottom Line (Orange)</label><input id="stBot" value="${esc(s.brand_bottom||'')}" /></div>
        <div><label>Hero Headline Top</label><input id="stHw" value="${esc(s.hero_headline_white||'')}" /></div>
        <div><label>Hero Headline Orange</label><input id="stHo" value="${esc(s.hero_headline_orange||'')}" /></div>
        <div><label>Contact Phone</label><input id="stPh" value="${esc(s.contact_phone||'')}" /></div>
        <div><label>Hero Looping Video MP4 URL</label><input id="stVid" value="${esc(s.hero_video_url||'')}" /></div>
        <div><label><input type="checkbox" id="stVenmo" ${s.pay_venmo==='1'?'checked':''} style="width:auto;" /> Accept Venmo — @Handle:</label><input id="stVenmoH" value="${esc(s.venmo_handle||'')}" placeholder="@YourVenmo" /></div>
        <div><label><input type="checkbox" id="stCashApp" ${s.pay_cashapp==='1'?'checked':''} style="width:auto;" /> Accept Cash App — $Cashtag:</label><input id="stCashH" value="${esc(s.cashapp_handle||'')}" placeholder="$YourCashtag" /></div>
        <div><label><input type="checkbox" id="stPaypal" ${s.pay_paypal==='1'?'checked':''} style="width:auto;" /> Accept PayPal — Link/Email:</label><input id="stPayH" value="${esc(s.paypal_handle||'')}" /></div>
        <div><label><input type="checkbox" id="stZelle" ${s.pay_zelle==='1'?'checked':''} style="width:auto;" /> Accept Zelle — Phone/Email:</label><input id="stZelH" value="${esc(s.zelle_handle||'')}" /></div>
      </div>
      <button class="btn-pit" style="margin-top:14px;width:100%;" onclick="savePitBrandSettings()">Save Brand, Video &amp; Payment Settings</button>
    </div>`;
}

async function setOrderStat(id, status) {
  await fetch(`/api/bbq/admin/orders/${id}/status`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }) });
  openPitBossHq();
}
async function delOrder(id) {
  if (!confirm('Delete this order?')) return;
  await fetch(`/api/bbq/admin/orders/${id}`, { method: 'DELETE' });
  openPitBossHq();
}
async function createPitEvent() {
  const res = await fetch('/api/bbq/admin/events', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      title: document.getElementById('evTitle').value, slug: document.getElementById('evSlug').value || document.getElementById('evTitle').value,
      event_date: document.getElementById('evDate').value, host_pin: document.getElementById('evPin').value,
      location: document.getElementById('evLoc').value, cutoff_note: document.getElementById('evCut').value,
      pit_providing: document.getElementById('evProv').value, active: true,
    }),
  });
  if (!res.ok) return alert('Enter an event title first.');
  await loadBbqPublic(); openPitBossHq();
}
async function delEvent(id) {
  if (!confirm('Delete this event?')) return;
  await fetch(`/api/bbq/admin/events/${id}`, { method: 'DELETE' });
  await loadBbqPublic(); openPitBossHq();
}
async function addMenuItem() {
  await fetch('/api/bbq/admin/menu', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      category: document.getElementById('mCat').value, name: document.getElementById('mName').value,
      description: document.getElementById('mDesc').value, opt1_label: document.getElementById('mO1').value,
      opt1_price: parseFloat(document.getElementById('mP1').value || '0'), opt2_label: document.getElementById('mO2').value,
      opt2_price: parseFloat(document.getElementById('mP2').value || '0'), available: true,
    }),
  });
  await loadBbqPublic(); openPitBossHq();
}
async function toggleMenuAvail(id, nextAvail) {
  const m = (HQ.menu || []).find(x => x.id === id);
  if (!m) return;
  await fetch('/api/bbq/admin/menu', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...m, available: nextAvail }) });
  await loadBbqPublic(); openPitBossHq();
}
async function delMenuItem(id) {
  if (!confirm('Remove this menu item?')) return;
  await fetch(`/api/bbq/admin/menu/${id}`, { method: 'DELETE' });
  await loadBbqPublic(); openPitBossHq();
}
async function savePitBrandSettings() {
  await fetch('/api/bbq/admin/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      brand_top: document.getElementById('stTop').value, brand_bottom: document.getElementById('stBot').value,
      hero_headline_white: document.getElementById('stHw').value, hero_headline_orange: document.getElementById('stHo').value,
      contact_phone: document.getElementById('stPh').value, hero_video_url: document.getElementById('stVid').value,
      pay_venmo: document.getElementById('stVenmo').checked ? '1' : '0', venmo_handle: document.getElementById('stVenmoH').value,
      pay_cashapp: document.getElementById('stCashApp').checked ? '1' : '0', cashapp_handle: document.getElementById('stCashH').value,
      pay_paypal: document.getElementById('stPaypal').checked ? '1' : '0', paypal_handle: document.getElementById('stPayH').value,
      pay_zelle: document.getElementById('stZelle').checked ? '1' : '0', zelle_handle: document.getElementById('stZelH').value,
    }),
  });
  alert('Pit brand, video & payment apps saved!');
  await loadBbqPublic(); openPitBossHq();
}

loadBbqPublic();
