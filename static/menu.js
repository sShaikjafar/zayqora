const $ = (id) => document.getElementById(id);
const slug = location.pathname.split("/").filter(Boolean)[1];
const CART_KEY = "zq_cart_" + slug;

let currency = "USD";
let cart = [];          // {item_id, name, variant_id, variant_name, addon_ids, addon_names, quantity, note}
let quote = null;       // last server quote
let fulfilment = "pickup";
let idemKey = newKey();

function newKey() {
  return (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : Date.now() + "-" + Math.random().toString(16).slice(2);
}
function el(tag, props = {}, ...children) {
  const n = document.createElement(tag); Object.assign(n, props);
  for (const c of children) n.append(c); return n;
}
function money(c) {
  try { return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(c / 100); }
  catch (_) { return (c / 100).toFixed(2) + " " + currency; }
}
function errMsg(data, fallback) {
  const d = data && data.detail;
  if (!d) return fallback || "Something went wrong";
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((e) => e.msg).join("; ");
  return d.message || fallback || "Something went wrong";
}
async function post(path, body, headers = {}) {
  const res = await fetch("/api/v1/public/restaurants/" + encodeURIComponent(slug) + path, {
    method: "POST", headers: { "Content-Type": "application/json", ...headers }, body: JSON.stringify(body) });
  let data = null; try { data = await res.json(); } catch (_) {}
  return { ok: res.ok, status: res.status, data };
}

/* ------------------------------------------------------------ cart state */
function loadCart() { try { cart = JSON.parse(localStorage.getItem(CART_KEY)) || []; } catch (_) { cart = []; } }
function saveCart() {
  idemKey = newKey(); // a changed cart is a new checkout attempt
  try { localStorage.setItem(CART_KEY, JSON.stringify(cart)); } catch (_) {}
}
const toApi = (l) => ({ item_id: l.item_id, quantity: l.quantity, variant_id: l.variant_id, addon_ids: l.addon_ids, note: l.note || null });

async function refreshQuote() {
  $("cart-err").textContent = "";
  if (!cart.length) { quote = null; renderCart(); return; }
  const r = await post("/quote", { fulfilment, items: cart.map(toApi) });
  if (r.ok) quote = r.data; else { quote = null; $("cart-err").textContent = errMsg(r.data); }
  renderCart();
}

function changeCart() { saveCart(); renderCart(); refreshQuote(); }

function renderCart() {
  $("cart-card").classList.toggle("hidden", cart.length === 0);
  $("addr-wrap").classList.toggle("hidden", fulfilment !== "delivery");
  $("c-addr").required = fulfilment === "delivery";
  const box = $("cart-lines");
  box.replaceChildren(...cart.map((l, i) => {
    const extras = [l.variant_name, ...(l.addon_names || [])].filter(Boolean).join(", ");
    const left = el("div", { className: "grow" }, el("div", { className: "title", textContent: l.name }));
    if (extras) left.append(el("div", { className: "meta", textContent: extras }));
    if (l.note) left.append(el("div", { className: "meta", textContent: "Note: " + l.note }));
    const qty = el("div", { className: "qty" },
      el("button", { type: "button", className: "small", textContent: "−", onclick: () => { l.quantity -= 1; if (l.quantity < 1) cart.splice(i, 1); changeCart(); } }),
      el("span", { textContent: String(l.quantity) }),
      el("button", { type: "button", className: "small", textContent: "+", onclick: () => { if (l.quantity < 50) l.quantity += 1; changeCart(); } }));
    const price = el("div", { className: "price", textContent: quote && quote.lines[i] ? money(quote.lines[i].line_total_cents) : "…" });
    const remove = el("button", { type: "button", className: "small danger", textContent: "Remove", onclick: () => { cart.splice(i, 1); changeCart(); } });
    return el("div", { className: "row" }, left, qty, price, remove);
  }));
  const bd = $("breakdown");
  if (!quote) { bd.replaceChildren(); return; }
  const rows = [["Subtotal", quote.subtotal_cents]];
  if (fulfilment === "delivery") rows.push(["Delivery fee", quote.delivery_fee_cents]);
  if (quote.tax_cents) rows.push(["Tax", quote.tax_cents]);
  bd.replaceChildren(
    ...rows.map(([k, v]) => el("div", { className: "total-row" }, el("span", { textContent: k }), el("span", { textContent: money(v) }))),
    el("div", { className: "total-row big" }, el("span", { textContent: "Total" }), el("span", { textContent: money(quote.total_cents) })));
}

document.querySelectorAll('input[name="fulfilment"]').forEach((r) => {
  r.onchange = () => { fulfilment = r.value; saveCart(); renderCart(); refreshQuote(); };
});

/* ------------------------------------------------------------ customizer */
function openCustomizer(item) {
  const variants = item.options.filter((o) => o.kind === "variant");
  const addons = item.options.filter((o) => o.kind === "addon");
  const box = $("customizer"); box.classList.remove("hidden");
  let variantId = variants.length ? variants[0].id : null;
  const picked = new Set(); let qty = 1;
  const priceEl = el("div", { className: "price" });
  const noteIn = el("input", { maxLength: 200, placeholder: "Special instructions (optional)" });
  const update = () => {
    const v = variants.find((x) => x.id === variantId);
    const unit = item.price_cents + (v ? v.price_delta_cents : 0) + addons.filter((a) => picked.has(a.id)).reduce((s, a) => s + a.price_delta_cents, 0);
    priceEl.textContent = "Total: " + money(unit * qty);
  };
  const children = [el("h2", { textContent: item.name })];
  if (item.description) children.push(el("p", { className: "sub", textContent: item.description }));
  if (variants.length) {
    children.push(el("div", { className: "cat-title", textContent: "Choose one" }));
    variants.forEach((v, idx) => {
      const radio = el("input", { type: "radio", name: "variant", checked: idx === 0, onchange: () => { variantId = v.id; update(); } });
      children.push(el("label", { className: "choice" }, radio,
        el("span", { className: "grow", textContent: v.name }), el("span", { textContent: v.price_delta_cents ? "+" + money(v.price_delta_cents) : "" })));
    });
  }
  if (addons.length) {
    children.push(el("div", { className: "cat-title", textContent: "Add-ons (optional)" }));
    addons.forEach((a) => {
      const cb = el("input", { type: "checkbox", onchange: () => { cb.checked ? picked.add(a.id) : picked.delete(a.id); update(); } });
      children.push(el("label", { className: "choice" }, cb,
        el("span", { className: "grow", textContent: a.name }), el("span", { textContent: a.price_delta_cents ? "+" + money(a.price_delta_cents) : "" })));
    });
  }
  const qtyIn = el("input", { type: "number", min: 1, max: 50, value: 1, style: "width:90px", oninput: () => { qty = Math.max(1, Math.min(50, parseInt(qtyIn.value, 10) || 1)); update(); } });
  children.push(el("label", { textContent: "Quantity" }), qtyIn, el("label", { textContent: "Note" }), noteIn, el("div", { style: "margin-top:12px" }, priceEl));
  children.push(el("div", {},
    el("button", { type: "button", textContent: "Add to cart", onclick: () => {
      const v = variants.find((x) => x.id === variantId);
      cart.push({ item_id: item.id, name: item.name, variant_id: v ? v.id : null, variant_name: v ? v.name : null,
        addon_ids: addons.filter((a) => picked.has(a.id)).map((a) => a.id), addon_names: addons.filter((a) => picked.has(a.id)).map((a) => a.name),
        quantity: qty, note: noteIn.value.trim() });
      box.classList.add("hidden"); changeCart(); $("cart-card").scrollIntoView({ behavior: "smooth" });
    } }),
    el("button", { type: "button", className: "secondary", textContent: "Cancel", onclick: () => box.classList.add("hidden") })));
  box.replaceChildren(...children); update();
  box.scrollIntoView({ behavior: "smooth" });
}

/* ------------------------------------------------------------- checkout */
$("checkout-form").onsubmit = async (e) => {
  e.preventDefault(); $("order-err").textContent = "";
  if (!quote) { $("order-err").textContent = "Please fix the problem above before ordering."; return; }
  const btn = $("place-btn"); btn.disabled = true;
  const body = { fulfilment, items: cart.map(toApi), customer_name: $("c-name").value, customer_phone: $("c-phone").value,
    delivery_address: fulfilment === "delivery" ? $("c-addr").value : null, notes: $("c-notes").value || null, expected_total_cents: quote.total_cents };
  const r = await post("/orders", body, { "Idempotency-Key": idemKey });
  btn.disabled = false;
  if (r.ok) { showConfirmation(r.data); return; }
  if (r.status === 409 && r.data && r.data.detail && r.data.detail.code === "PRICE_CHANGED") {
    $("order-err").textContent = errMsg(r.data); await refreshQuote(); return;
  }
  $("order-err").textContent = errMsg(r.data);
};

function parseDate(s) { return new Date(/Z$|[+-]\d\d:\d\d$/.test(s) ? s : s + "Z"); }

function showConfirmation(o) {
  cart = []; try { localStorage.removeItem(CART_KEY); } catch (_) {}
  $("cart-card").classList.add("hidden"); $("menu").classList.add("hidden"); $("customizer").classList.add("hidden");
  const card = $("confirm-card"); card.classList.remove("hidden");
  const lines = o.items.map((i) => {
    const extras = [i.variant_name, ...i.addons.map((a) => a.name)].filter(Boolean).join(", ");
    return el("div", { className: "row" },
      el("div", { className: "grow" }, el("div", { className: "title", textContent: `${i.quantity} × ${i.name}` }), extras ? el("div", { className: "meta", textContent: extras }) : ""),
      el("div", { className: "price", textContent: money(i.line_total_cents) }));
  });
  const rows = [["Subtotal", o.subtotal_cents]];
  if (o.fulfilment === "delivery") rows.push(["Delivery fee", o.delivery_fee_cents]);
  if (o.tax_cents) rows.push(["Tax", o.tax_cents]);
  card.replaceChildren(
    el("h1", { textContent: "Thank you, " + o.customer_name + "!" }),
    el("p", { className: "sub", textContent: "Your order has been received. Keep this reference:" }),
    el("div", { className: "ref", textContent: o.reference }),
    el("div", { className: "kv" }, el("span", { textContent: "Status" }), el("span", { textContent: o.status })),
    el("div", { className: "kv" }, el("span", { textContent: o.fulfilment === "delivery" ? "Delivery to" : "Pickup" }), el("span", { textContent: o.fulfilment === "delivery" ? o.delivery_address : "At the restaurant" })),
    el("div", { className: "kv" }, el("span", { textContent: "Payment" }), el("span", { textContent: "Cash (" + o.payment_status.toLowerCase() + ")" })),
    el("div", { className: "kv" }, el("span", { textContent: "Placed" }), el("span", { textContent: parseDate(o.created_at).toLocaleString() })),
    ...lines,
    ...rows.map(([k, v]) => el("div", { className: "total-row" }, el("span", { textContent: k }), el("span", { textContent: money(v) }))),
    el("div", { className: "total-row big" }, el("span", { textContent: "Total" }), el("span", { textContent: money(o.total_cents) })),
    el("p", {}, el("a", { href: "/r/" + encodeURIComponent(slug) + "/track?ref=" + encodeURIComponent(o.reference), textContent: "Track this order →" })),
    el("button", { textContent: "Order again", onclick: () => location.reload() }));
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* ------------------------------------------------------------------ menu */
(async () => {
  $("back").href = "/r/" + encodeURIComponent(slug);
  const res = await fetch("/api/v1/public/restaurants/" + encodeURIComponent(slug) + "/menu");
  if (!res.ok) { $("notfound").classList.remove("hidden"); return; }
  const data = await res.json();
  currency = data.restaurant.currency;
  document.title = data.restaurant.name + " – Menu";
  $("site-name").textContent = data.restaurant.name;
  $("menu-title").textContent = "Our menu";
  const root = $("menu");
  if (!data.categories.length) root.append(el("p", { className: "sub", textContent: "The menu is being prepared. Please check back soon." }));
  for (const cat of data.categories) {
    const card = el("div", { className: "card" }, el("h2", { textContent: cat.name }));
    if (cat.description) card.append(el("p", { className: "sub", textContent: cat.description }));
    for (const item of cat.items) {
      const variants = item.options.filter((o) => o.kind === "variant");
      const addons = item.options.filter((o) => o.kind === "addon");
      const fmt = (o) => o.name + (o.price_delta_cents ? " (+" + money(o.price_delta_cents) + ")" : "");
      const title = el("div", { className: "title", textContent: item.name });
      if (!item.is_available) title.append(el("span", { className: "badge off", textContent: "Sold out" }));
      const left = el("div", { className: "grow" }, title);
      if (item.description) left.append(el("div", { className: "meta", textContent: item.description }));
      if (variants.length) left.append(el("div", { className: "opts", textContent: "Options: " + variants.map(fmt).join(", ") }));
      if (addons.length) left.append(el("div", { className: "opts", textContent: "Add-ons: " + addons.map(fmt).join(", ") }));
      const add = el("button", { className: "small", textContent: "Add", disabled: !item.is_available, onclick: () => openCustomizer(item) });
      card.append(el("div", { className: "row" + (item.is_available ? "" : " soldout") }, left, el("div", { className: "price", textContent: money(item.price_cents) }), add));
    }
    root.append(card);
  }
  loadCart(); renderCart(); refreshQuote();
})();
