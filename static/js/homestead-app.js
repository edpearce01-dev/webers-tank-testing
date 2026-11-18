let HS = { settings: {}, items: [], payment_methods: [], pit_boss_user: null };
let HS_CAT = 'ALL';
let HS_CART = [];
let HS_HQ = null;
let HS_HQ_TAB = 'stock';
let HS_EDIT_ID = null;

const esc = s => String(s ?? '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));

function fmtPhone(el) {
  const d = el.value.replace(/\D/g, '').slice(0, 10);
  if (d.length >= 7) el.value = `${d.slice(0, 3)}-${d.slice(3, 6)}-${d.slice(6)}`;
  else if (d.length >= 4) el.value = `${d.slice(0, 3)}-${d.slice(3)}`;
}

async function loadHomesteadPublic() {
  const res = await fetch('/api/homestead/public/bootstrap');
  if (!res.ok) return;
  HS = await res.json();
  const s = HS.settings || {};
  const standName = s.homestead_name || 'Backroad Homestead & Sauce Stand';

  document.title = standName;
  document.getElementById('hsNavTitle').textContent = standName;
  document.getElementById('hsHeroTitle').textContent = standName;
  document.getElementById('hsHeroTagline').textContent = s.homestead_tagline || '';
  document.getElementById('hsPickupNote').textContent = s.homestead_pickup_note || '';
  document.getElementById('hsFooterTxt').textContent = `${standName} • ${s.contact_phone || '724-266-8008'}`;
  document.getElementById('btnHsHqTop').hidden = !HS.pit_boss_user;

  renderHsCategories();
  renderHsGrid();
  renderHsPayMethods();
}

function renderHsCategories() {
  const cats = ['ALL', ...new Set((HS.items || []).map(i => i.category).filter(Boolean))];
  if (!cats.includes(HS_CAT)) HS_CAT = 'ALL';
  document.getElementById('hsCatBar').innerHTML = cats.map(c => `
    <button type="button" class="hs-cat-pill ${HS_CAT === c ? 'active' : ''}" onclick="setHsCat('${esc(c)}')">
      ${c === 'ALL' ? 'All Stand Items' : esc(c)}
    </button>
  `).join('');
}

function setHsCat(cat) {
  HS_CAT = cat;
  renderHsCategories();
  renderHsGrid();
}

function stockBadgeHtml(qty) {
  if (qty < 0) return '<span class="hs-stock-pill">Made Fresh to Order</span>';
  if (qty === 0) return '<span class="hs-stock-pill out">Sold Out — Next Batch Soon</span>';
  if (qty <= 3) return `<span class="hs-stock-pill low">Only ${qty} Left!</span>`;
  return `<span class="hs-stock-pill">${qty} Available</span>`;
}

function renderHsGrid() {
  const rows = (HS.items || []).filter(i => HS_CAT === 'ALL' || i.category === HS_CAT);
  const host = document.getElementById('hsProductGrid');
  host.innerHTML = rows.length ? rows.map(it => {
    const qty = Number(it.stock_qty ?? 0);
    const isOut = qty === 0;
    const inBasket = (HS_CART.find(c => c.id === it.id)?.qty) || 0;
    return `
      <div class="hs-card ${isOut ? 'sold-out' : ''}">
        <div>
          <div class="hs-top-tags">
            <span class="hs-cat-tag">${esc(it.category)}${it.badge ? ' • ' + esc(it.badge) : ''}</span>
            ${stockBadgeHtml(qty)}
          </div>
          <h3>${esc(it.name)}</h3>
          <div class="hs-unit">${esc(it.unit_label || 'Each')}</div>
          <p class="hs-desc">${esc(it.description || '')}</p>
        </div>
        <div class="hs-card-foot">
          <span class="hs-price">$${Number(it.price || 0).toFixed(2)}</span>
          <button type="button" class="btn-hs ${inBasket ? 'btn-hs-honey' : ''}" ${isOut ? 'disabled' : ''} onclick="addToHsBasket(${it.id})">
            ${isOut ? 'Sold Out' : (inBasket ? `In Basket (${inBasket}) +` : '+ Add to Basket')}
          </button>
        </div>
      </div>`;
  }).join('') : '<div style="grid-column:1/-1;text-align:center;color:var(--hs-muted);padding:32px 0;">Fresh batches are being bottled & gathered — check back soon!</div>';
}

function addToHsBasket(itemId) {
  const it = (HS.items || []).find(x => x.id === itemId);
  if (!it) return;
  const maxStock = Number(it.stock_qty ?? 0);
  const existing = HS_CART.find(c => c.id === itemId);
  const curQty = existing ? existing.qty : 0;
  if (maxStock >= 0 && curQty + 1 > maxStock) {
    alert(`Only ${maxStock} currently on the shelf for ${it.name}.`);
    return;
  }
  if (existing) existing.qty += 1;
  else HS_CART.push({ id: it.id, name: it.name, option: it.unit_label || 'Each', price: Number(it.price || 0), maxStock, qty: 1 });
  updateHsBasketBadge();
  renderHsGrid();
}

function updateHsBasketBadge() {
  const cnt = HS_CART.reduce((s, x) => s + x.qty, 0);
  document.getElementById('hsCartChip').textContent = `(${cnt})`;
}

function renderHsPayMethods() {
  const sel = document.getElementById('hsOrdPay');
  sel.innerHTML = (HS.payment_methods || []).map(p =>
    `<option value="${esc(p.code)}">${esc(p.label)}${p.handle ? ' (' + esc(p.handle) + ')' : ''}</option>`
  ).join('');
}

function openHsCart() {
  document.getElementById('hsOrdConfirm').hidden = true;
  document.getElementById('hsOrdPhone').oninput = function () { fmtPhone(this); };
  renderHsCartLines();
  document.getElementById('hsCartModal').hidden = false;
}

function stepHsCartItem(idx, delta) {
  const item = HS_CART[idx];
  if (!item) return;
  const next = item.qty + delta;
  if (next <= 0) HS_CART.splice(idx, 1);
  else if (item.maxStock >= 0 && next > item.maxStock) {
    alert(`Only ${item.maxStock} available right now.`);
    return;
  } else item.qty = next;
  updateHsBasketBadge();
  renderHsCartLines();
  renderHsGrid();
}

function renderHsCartLines() {
  const box = document.getElementById('hsCartLines');
  let tot = 0;
  box.innerHTML = HS_CART.length ? HS_CART.map((c, idx) => {
    tot += c.qty * c.price;
    return `
      <div style="display:flex;justify-content:space-between;align-items:center;padding:9px 0;border-bottom:1px solid var(--hs-border);gap:8px;">
        <div>
          <strong>${esc(c.name)}</strong>
          <div style="font-size:0.78rem;color:var(--hs-muted);">${esc(c.option)} • $${c.price.toFixed(2)} ea</div>
        </div>
        <div style="display:flex;align-items:center;gap:7px;">
          <button type="button" class="btn-hs-ghost" style="padding:4px 10px;" onclick="stepHsCartItem(${idx}, -1)">−</button>
          <strong>${c.qty}</strong>
          <button type="button" class="btn-hs-ghost" style="padding:4px 10px;" onclick="stepHsCartItem(${idx}, 1)">+</button>
          <span style="min-width:60px;text-align:right;font-weight:800;">$${(c.qty * c.price).toFixed(2)}</span>
        </div>
      </div>`;
  }).join('') : '<div style="color:var(--hs-muted);padding:12px 0;">Your basket is empty — tap + Add to Basket on any sauce, jar, or carton!</div>';
  document.getElementById('hsCartTotal').textContent = `$${tot.toFixed(2)}`;
}

async function submitHsOrder() {
  if (!HS_CART.length) return alert('Add at least one item to your basket first.');
  const payCode = document.getElementById('hsOrdPay').value;
  const res = await fetch('/api/bbq/public/order', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      source: 'HOMESTEAD',
      order_type: 'HOMESTEAD_PICKUP',
      customer_name: document.getElementById('hsOrdName').value,
      customer_phone: document.getElementById('hsOrdPhone').value,
      pickup_or_event_date: document.getElementById('hsOrdDate').value,
      items: HS_CART,
      payment_method: payCode,
      notes: document.getElementById('hsOrdNotes').value,
    }),
  });
  const d = await res.json();
  if (!res.ok) return alert(d.detail || 'Please enter your name and 10-digit phone number.');
  const pm = (HS.payment_methods || []).find(x => x.code === payCode);
  HS_CART = [];
  updateHsBasketBadge();
  renderHsCartLines();
  await loadHomesteadPublic();

  const cBox = document.getElementById('hsOrdConfirm');
  cBox.hidden = false;
  cBox.innerHTML = `
    <div class="hs-panel" style="border-color:#10b981;background:#ecfdf5;">
      <strong style="color:#065f46;font-size:1.02rem;">✓ Reserved! Order #${esc(d.order_no)} ($${d.total_amount.toFixed(2)})</strong>
      <div style="font-size:0.85rem;color:#047857;margin-top:4px;">
        Your items have been set aside from shelf stock. Payment: <strong>${esc(payCode)}</strong>${pm?.handle ? ` (${esc(pm.handle)})` : ''}. We'll text you to coordinate pickup!
      </div>
    </div>`;
}

// --- PIN-UNLOCKED HOMESTEAD STAND HQ (KENNY, RYAN, WIFE & ADMIN) ---
async function openHsLoginOrHq() {
  if (HS.pit_boss_user) return openHsHq();
  const pin = prompt('Enter Stand / Shop PIN:');
  if (!pin) return;
  const res = await fetch('/api/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pin }),
  });
  if (!res.ok) return alert('Incorrect PIN.');
  await loadHomesteadPublic();
  openHsHq();
}

async function openHsHq() {
  const res = await fetch('/api/bbq/admin/state');
  if (!res.ok) return openHsLoginOrHq();
  HS_HQ = await res.json();
  document.getElementById('hsHqWho').textContent = `Signed in as ${HS.pit_boss_user?.name || 'Stand Manager'}`;
  document.getElementById('hsHqModal').hidden = false;
  renderHsHqBody();
}

function switchHsHqTab(tab) {
  HS_HQ_TAB = tab;
  document.querySelectorAll('[data-hshq]').forEach(b => b.classList.toggle('active', b.getAttribute('data-hshq') === tab));
  renderHsHqBody();
}

function editHsItemInForm(id) {
  const it = (HS_HQ.homestead_items || []).find(x => x.id === id);
  if (!it) return;
  HS_EDIT_ID = it.id;
  document.getElementById('hiCat').value = it.category || 'House BBQ Sauces';
  document.getElementById('hiName').value = it.name || '';
  document.getElementById('hiUnit').value = it.unit_label || '16 oz Bottle';
  document.getElementById('hiPrice').value = it.price ?? 10;
  document.getElementById('hiStock').value = it.stock_qty ?? 8;
  document.getElementById('hiBadge').value = it.badge || '';
  document.getElementById('hiDesc').value = it.description || '';
  document.getElementById('hiFormTitle').textContent = `Editing: ${it.name}`;
  document.getElementById('hiSaveBtn').textContent = 'Update Stand Item';
}

function renderHsHqBody() {
  const box = document.getElementById('hsHqBody');
  if (!HS_HQ) return;

  if (HS_HQ_TAB === 'stock') {
    HS_EDIT_ID = null;
    box.innerHTML = `
      <div class="hs-panel" style="margin-bottom:16px;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
          <strong id="hiFormTitle" style="color:var(--hs-pine);">+ Add New Sauce, Jar, Eggs, or Homemade Item</strong>
          <small style="color:var(--hs-muted);">Tip: Set Stock to -1 for Unlimited / Made-to-Order</small>
        </div>
        <datalist id="hsCatSuggestions">
          <option value="House BBQ Sauces"></option>
          <option value="Honey & Maple Syrup"></option>
          <option value="Coop Eggs"></option>
          <option value="Jams & Pickles"></option>
          <option value="Baked Goods"></option>
          <option value="Homestead Goods"></option>
        </datalist>
        <div class="hs-grid-2">
          <div><label>Category (Pick or Type Any New Category)</label><input id="hiCat" list="hsCatSuggestions" value="House BBQ Sauces" /></div>
          <div><label>Item Name *</label><input id="hiName" placeholder="e.g. Smoked Apple Butter or Duck Eggs" /></div>
          <div><label>Size / Unit Label</label><input id="hiUnit" value="16 oz Jar" placeholder="1 Dozen / Pint / Bottle" /></div>
          <div><label>Price ($)</label><input type="number" step="0.50" id="hiPrice" value="10" /></div>
          <div><label>On-Hand Shelf Stock</label><input type="number" id="hiStock" value="8" /></div>
          <div><label>Highlight Tag (Optional)</label><input id="hiBadge" placeholder="e.g. Fresh Today, Small Batch" /></div>
          <div style="grid-column:1/-1;"><label>Short Description</label><input id="hiDesc" placeholder="Ingredients, flavor notes, or how you make it..." /></div>
        </div>
        <button type="button" class="btn-hs" id="hiSaveBtn" style="margin-top:12px;width:100%;" onclick="saveHsItemForm()">Save to Homestead Stand</button>
      </div>

      <div>
        ${(HS_HQ.homestead_items || []).map(it => `
          <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid var(--hs-border);flex-wrap:wrap;">
            <div>
              <strong>${esc(it.name)}</strong> <small style="color:var(--hs-honey);font-weight:700;">($${Number(it.price).toFixed(2)} / ${esc(it.unit_label)})</small>
              ${it.active ? '' : '<span style="color:#b91c1c;font-weight:800;font-size:0.76rem;"> [HIDDEN]</span>'}
              <div style="font-size:0.78rem;color:var(--hs-muted);">${esc(it.category)}</div>
            </div>
            <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
              <button type="button" class="btn-hs-ghost" style="padding:4px 10px;" onclick="bumpHsStock(${it.id}, -1)">−</button>
              <strong style="min-width:54px;text-align:center;font-size:0.88rem;">${it.stock_qty < 0 ? '∞' : it.stock_qty + ' left'}</strong>
              <button type="button" class="btn-hs-ghost" style="padding:4px 10px;" onclick="bumpHsStock(${it.id}, 1)">+</button>
              <button type="button" class="btn-hs-ghost" style="padding:4px 10px;" onclick="editHsItemInForm(${it.id})">Edit</button>
              <button type="button" class="btn-hs-ghost" style="padding:4px 10px;" onclick="toggleHsItemActive(${it.id}, ${!it.active})">${it.active ? 'Hide' : 'Show'}</button>
              <button type="button" class="btn-hs-ghost" style="padding:4px 10px;color:#b91c1c;" onclick="removeHsItem(${it.id})">✕</button>
            </div>
          </div>
        `).join('')}
      </div>`;
    return;
  }

  if (HS_HQ_TAB === 'orders') {
    const hsOrders = (HS_HQ.orders || []).filter(o => o.source === 'HOMESTEAD');
    const sep = /iPad|iPhone|iPod/i.test(navigator.userAgent) ? '&' : '?';
    box.innerHTML = hsOrders.length ? hsOrders.map(o => {
      const items = JSON.parse(o.items_json || '[]').map(i => `${i.qty}× ${i.name}`).join(', ');
      const digits = String(o.customer_phone || '').replace(/\D/g, '');
      const sms = `Hi ${o.customer_name}! Your Homestead Stand order (${items} - $${Number(o.total_amount||0).toFixed(2)}) is set aside and ready for pickup!`;
      return `
        <div style="padding:11px 0;border-bottom:1px solid var(--hs-border);display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;">
          <div>
            <strong>${esc(o.order_no)} — ${esc(o.customer_name)}</strong> (${esc(o.customer_phone)}) • <span style="color:var(--hs-honey);font-weight:800;">$${Number(o.total_amount||0).toFixed(2)} [${esc(o.payment_method)}]</span> • <strong>${esc(o.status)}</strong>
            <div style="font-size:0.83rem;color:var(--hs-soft);">${esc(items)}${o.pickup_or_event_date ? ' • Pickup: ' + esc(o.pickup_or_event_date) : ''}${o.notes ? ' • ' + esc(o.notes) : ''}</div>
          </div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;">
            ${digits ? `<a class="btn-hs-ghost" href="sms:${digits}${sep}body=${encodeURIComponent(sms)}">Text Ready</a>` : ''}
            <button type="button" class="btn-hs-ghost" onclick="setHsOrderStatus(${o.id}, 'PAID_PICKED_UP')">Paid &amp; Picked Up</button>
            <button type="button" class="btn-hs-ghost" style="color:#b91c1c;" onclick="deleteHsOrder(${o.id})">✕</button>
          </div>
        </div>`;
    }).join('') : '<div style="color:var(--hs-muted);padding:12px 0;">No Homestead reservations waiting right now.</div>';
    return;
  }

  const s = HS_HQ.settings || {};
  box.innerHTML = `
    <div class="hs-panel">
      <div class="hs-grid-2">
        <div style="grid-column:1/-1;"><label>Stand Title</label><input id="hsStName" value="${esc(s.homestead_name || '')}" /></div>
        <div style="grid-column:1/-1;"><label>Subtitle / What You Sell</label><input id="hsStTag" value="${esc(s.homestead_tagline || '')}" /></div>
        <div style="grid-column:1/-1;"><label>Porch / Pickup Instructions</label><input id="hsStPick" value="${esc(s.homestead_pickup_note || '')}" /></div>
        <div><label>Contact Phone</label><input id="hsStPhone" value="${esc(s.contact_phone || '')}" /></div>
        <div></div>
        <div><label><input type="checkbox" id="hsVenmo" ${s.pay_venmo === '1' ? 'checked' : ''} style="width:auto;" /> Venmo @Handle</label><input id="hsVenmoH" value="${esc(s.venmo_handle || '')}" placeholder="@YourVenmo" /></div>
        <div><label><input type="checkbox" id="hsCashApp" ${s.pay_cashapp === '1' ? 'checked' : ''} style="width:auto;" /> Cash App $Cashtag</label><input id="hsCashH" value="${esc(s.cashapp_handle || '')}" placeholder="$YourCashtag" /></div>
        <div><label><input type="checkbox" id="hsPaypal" ${s.pay_paypal === '1' ? 'checked' : ''} style="width:auto;" /> PayPal</label><input id="hsPayH" value="${esc(s.paypal_handle || '')}" /></div>
        <div><label><input type="checkbox" id="hsZelle" ${s.pay_zelle === '1' ? 'checked' : ''} style="width:auto;" /> Zelle Phone/Email</label><input id="hsZelH" value="${esc(s.zelle_handle || '')}" /></div>
      </div>
      <button type="button" class="btn-hs" style="width:100%;margin-top:14px;" onclick="saveHsStandSettings()">Save Stand Settings</button>
    </div>`;
}

async function saveHsItemForm() {
  const name = document.getElementById('hiName').value.trim();
  if (!name) return alert('Enter an item name first.');
  await fetch('/api/bbq/admin/homestead', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      id: HS_EDIT_ID,
      category: document.getElementById('hiCat').value || 'Homestead Goods',
      name,
      unit_label: document.getElementById('hiUnit').value,
      price: parseFloat(document.getElementById('hiPrice').value || '0'),
      stock_qty: parseInt(document.getElementById('hiStock').value || '0', 10),
      badge: document.getElementById('hiBadge').value,
      description: document.getElementById('hiDesc').value,
      active: true,
    }),
  });
  await loadHomesteadPublic();
  openHsHq();
}

async function bumpHsStock(id, delta) {
  await fetch(`/api/bbq/admin/homestead/${id}/stock`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ delta }),
  });
  await loadHomesteadPublic();
  openHsHq();
}

async function toggleHsItemActive(id, nextAct) {
  const it = (HS_HQ.homestead_items || []).find(x => x.id === id);
  if (!it) return;
  await fetch('/api/bbq/admin/homestead', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...it, active: nextAct }),
  });
  await loadHomesteadPublic();
  openHsHq();
}

async function removeHsItem(id) {
  if (!confirm('Remove this item from the stand?')) return;
  await fetch(`/api/bbq/admin/homestead/${id}`, { method: 'DELETE' });
  await loadHomesteadPublic();
  openHsHq();
}

async function setHsOrderStatus(id, status) {
  await fetch(`/api/bbq/admin/orders/${id}/status`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }),
  });
  openHsHq();
}

async function deleteHsOrder(id) {
  if (!confirm('Delete this order record?')) return;
  await fetch(`/api/bbq/admin/orders/${id}`, { method: 'DELETE' });
  openHsHq();
}

async function saveHsStandSettings() {
  await fetch('/api/bbq/admin/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      homestead_name: document.getElementById('hsStName').value,
      homestead_tagline: document.getElementById('hsStTag').value,
      homestead_pickup_note: document.getElementById('hsStPick').value,
      contact_phone: document.getElementById('hsStPhone').value,
      pay_venmo: document.getElementById('hsVenmo').checked ? '1' : '0',
      venmo_handle: document.getElementById('hsVenmoH').value,
      pay_cashapp: document.getElementById('hsCashApp').checked ? '1' : '0',
      cashapp_handle: document.getElementById('hsCashH').value,
      pay_paypal: document.getElementById('hsPaypal').checked ? '1' : '0',
      paypal_handle: document.getElementById('hsPayH').value,
      pay_zelle: document.getElementById('hsZelle').checked ? '1' : '0',
      zelle_handle: document.getElementById('hsZelH').value,
    }),
  });
  alert('Homestead Stand settings saved!');
  await loadHomesteadPublic();
  openHsHq();
}

loadHomesteadPublic();
