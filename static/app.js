const $ = (id) => document.getElementById(id);
const TOKEN_KEY = "zayqora_token"; // dev convenience; harden (httpOnly cookies) before production

let currency = "USD";
let tz = "UTC";
let categories = [];
let items = [];
let editingItemId = null;

async function api(path, { method = "GET", body, auth = true } = {}) {
  const headers = { "Content-Type": "application/json" };
  const token = localStorage.getItem(TOKEN_KEY);
  if (auth && token) headers.Authorization = "Bearer " + token;
  const res = await fetch("/api/v1" + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  let data = null;
  try { data = await res.json(); } catch (_) {}
  if (!res.ok) {
    let msg = (data && data.detail) || "Something went wrong";
    if (Array.isArray(msg)) msg = msg.map((e) => e.msg).join("; ");
    const err = new Error(msg); err.status = res.status; throw err;
  }
  return data;
}

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  for (const c of children) node.append(c);
  return node;
}

function money(cents) {
  try { return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(cents / 100); }
  catch (_) { return (cents / 100).toFixed(2) + " " + currency; }
}

function show(view) {
  $("auth-view").classList.toggle("hidden", view !== "auth");
  $("app-view").classList.toggle("hidden", view !== "app");
  $("logout-btn").hidden = view !== "app";
}

/* ---------------------------------------------------------- dashboard */
function renderDashboard(user, r) {
  currency = r.currency || "USD";
  tz = r.timezone || "UTC";
  $("d-name").textContent = r.name;
  $("d-user").textContent = `${user.full_name} (${user.role})`;
  const link = $("d-link"); link.textContent = `/r/${r.slug}`; link.href = `/r/${r.slug}`;
  $("d-plan").textContent = r.plan;
  $("d-status").textContent = r.status;
  $("p-name").value = r.name;
  $("p-desc").value = r.description || "";
  $("p-phone").value = r.phone || "";
  $("p-address").value = r.address || "";
  $("p-currency").value = r.currency || "USD";
  $("p-timezone").value = r.timezone || "UTC";
  show("app");
  loadMenu();
  loadSettings();
  loadOrders();
  loadReservations();
  loadNotifications();
  initReportDates();
}

async function loadSession() {
  if (!localStorage.getItem(TOKEN_KEY)) return show("auth");
  try {
    const user = await api("/auth/me");
    if (user.role === "platform_admin") { location.href = "/admin"; return; }
    const restaurant = await api("/restaurant");
    renderDashboard(user, restaurant);
  } catch (e) {
    localStorage.removeItem(TOKEN_KEY);
    show("auth");
  }
}

function afterAuth(data) {
  localStorage.setItem(TOKEN_KEY, data.access_token);
  if (data.user.role === "platform_admin") { location.href = "/admin"; return; }
  renderDashboard(data.user, data.restaurant);
}

/* --------------------------------------------------------------- menu */
async function loadMenu() {
  try {
    [categories, items] = await Promise.all([api("/menu/categories"), api("/menu/items")]);
    renderCategories();
    renderItems();
    renderCategorySelect();
  } catch (err) {
    $("cat-err").textContent = err.message;
  }
}

function renderCategorySelect() {
  const sel = $("i-cat"); const keep = sel.value;
  sel.replaceChildren(...categories.map((c) => el("option", { value: c.id, textContent: c.name })));
  if (keep) sel.value = keep;
}

function renderCategories() {
  const box = $("cat-list");
  if (!categories.length) { box.replaceChildren(el("div", { className: "empty", textContent: "No categories yet. Add your first one below." })); return; }
  box.replaceChildren(...categories.map((c) => {
    const count = items.filter((i) => i.category_id === c.id).length;
    const title = el("div", { className: "title", textContent: c.name });
    if (!c.is_active) title.append(el("span", { className: "badge off", textContent: "Hidden" }));
    return el("div", { className: "row" },
      el("div", { className: "grow" }, title, el("div", { className: "meta", textContent: `${count} item${count === 1 ? "" : "s"}` })),
      el("div", { className: "actions" },
        el("button", { className: "small", textContent: "Rename", onclick: () => renameCategory(c) }),
        el("button", { className: "small", textContent: c.is_active ? "Hide" : "Show", onclick: () => patchCategory(c.id, { is_active: !c.is_active }) }),
        el("button", { className: "small danger", textContent: "Delete", onclick: () => deleteCategory(c) })));
  }));
}

function renderItems() {
  const box = $("item-list");
  if (!items.length) { box.replaceChildren(el("div", { className: "empty", textContent: "No menu items yet. Add one with the form below." })); return; }
  const nodes = [];
  for (const c of categories) {
    const inCat = items.filter((i) => i.category_id === c.id);
    if (!inCat.length) continue;
    nodes.push(el("div", { className: "cat-title", textContent: c.name }));
    for (const i of inCat) {
      const title = el("div", { className: "title", textContent: i.name });
      if (!i.is_available) title.append(el("span", { className: "badge off", textContent: "Sold out" }));
      const left = el("div", { className: "grow" }, title);
      if (i.description) left.append(el("div", { className: "meta", textContent: i.description }));
      const opts = i.options.map((o) => `${o.kind === "variant" ? "Variant" : "Add-on"}: ${o.name}${o.price_delta_cents ? " +" + money(o.price_delta_cents) : ""}`);
      if (opts.length) left.append(el("div", { className: "opts", textContent: opts.join(" · ") }));
      nodes.push(el("div", { className: "row" + (i.is_available ? "" : " soldout") }, left,
        el("div", { className: "price", textContent: money(i.price_cents) }),
        el("div", { className: "actions" },
          el("button", { className: "small", textContent: "Edit", onclick: () => startEditItem(i) }),
          el("button", { className: "small", textContent: i.is_available ? "Sold out" : "Available", onclick: () => patchItem(i.id, { is_available: !i.is_available }) }),
          el("button", { className: "small danger", textContent: "Delete", onclick: () => deleteItem(i) }))));
    }
  }
  box.replaceChildren(...nodes);
}

async function patchCategory(id, body) {
  $("cat-err").textContent = "";
  try { await api(`/menu/categories/${id}`, { method: "PATCH", body }); await loadMenu(); }
  catch (err) { $("cat-err").textContent = err.message; }
}
function renameCategory(c) {
  const name = prompt("New category name", c.name);
  if (name && name.trim() && name.trim() !== c.name) patchCategory(c.id, { name: name.trim() });
}
async function deleteCategory(c) {
  if (!confirm(`Delete category "${c.name}"?`)) return;
  $("cat-err").textContent = "";
  try { await api(`/menu/categories/${c.id}`, { method: "DELETE" }); await loadMenu(); }
  catch (err) { $("cat-err").textContent = err.message; }
}
async function patchItem(id, body) {
  $("item-err").textContent = "";
  try { await api(`/menu/items/${id}`, { method: "PATCH", body }); await loadMenu(); }
  catch (err) { $("item-err").textContent = err.message; }
}
async function deleteItem(i) {
  if (!confirm(`Delete "${i.name}"?`)) return;
  $("item-err").textContent = "";
  try { await api(`/menu/items/${i.id}`, { method: "DELETE" }); if (editingItemId === i.id) resetItemForm(); await loadMenu(); }
  catch (err) { $("item-err").textContent = err.message; }
}

// "Large +2.00" -> {kind, name:"Large", price_delta_cents:200}
function parseOptions(text, kind) {
  return text.split("\n").map((s) => s.trim()).filter(Boolean).map((line) => {
    const m = line.match(/^(.*?)\s*(?:\+\s*(\d+(?:\.\d{1,2})?))?$/);
    return { kind, name: m[1].trim(), price_delta_cents: m[2] ? Math.round(parseFloat(m[2]) * 100) : 0 };
  });
}
function optionsToText(options, kind) {
  return options.filter((o) => o.kind === kind)
    .map((o) => o.name + (o.price_delta_cents ? " +" + (o.price_delta_cents / 100).toFixed(2) : "")).join("\n");
}

function resetItemForm() {
  editingItemId = null;
  $("item-form").reset();
  $("i-avail").checked = true;
  $("item-form-title").textContent = "Add menu item";
  $("item-save").textContent = "Add item";
  $("item-cancel").classList.add("hidden");
}
function startEditItem(i) {
  editingItemId = i.id;
  $("i-cat").value = i.category_id;
  $("i-name").value = i.name;
  $("i-desc").value = i.description || "";
  $("i-price").value = (i.price_cents / 100).toFixed(2);
  $("i-variants").value = optionsToText(i.options, "variant");
  $("i-addons").value = optionsToText(i.options, "addon");
  $("i-avail").checked = i.is_available;
  $("item-form-title").textContent = "Edit menu item";
  $("item-save").textContent = "Save changes";
  $("item-cancel").classList.remove("hidden");
  $("item-form").scrollIntoView({ behavior: "smooth" });
}

$("cat-form").onsubmit = async (e) => {
  e.preventDefault(); $("cat-err").textContent = "";
  try {
    await api("/menu/categories", { method: "POST", body: { name: $("c-name").value, sort_order: categories.length } });
    $("c-name").value = ""; await loadMenu();
  } catch (err) { $("cat-err").textContent = err.message; }
};

$("item-form").onsubmit = async (e) => {
  e.preventDefault(); $("item-err").textContent = "";
  if (!categories.length) { $("item-err").textContent = "Create a category first."; return; }
  const body = {
    category_id: parseInt($("i-cat").value, 10),
    name: $("i-name").value,
    description: $("i-desc").value || null,
    price_cents: Math.round(parseFloat($("i-price").value || "0") * 100),
    is_available: $("i-avail").checked,
    options: [...parseOptions($("i-variants").value, "variant"), ...parseOptions($("i-addons").value, "addon")],
  };
  try {
    if (editingItemId) await api(`/menu/items/${editingItemId}`, { method: "PATCH", body });
    else await api("/menu/items", { method: "POST", body });
    resetItemForm(); await loadMenu();
  } catch (err) { $("item-err").textContent = err.message; }
};
$("item-cancel").onclick = resetItemForm;

/* ----------------------------------------------------------- auth/profile */
$("to-login").onclick = () => { $("register-card").classList.add("hidden"); $("login-card").classList.remove("hidden"); };
$("to-register").onclick = () => { $("login-card").classList.add("hidden"); $("register-card").classList.remove("hidden"); };
$("logout-btn").onclick = () => { localStorage.removeItem(TOKEN_KEY); show("auth"); };

$("register-form").onsubmit = async (e) => {
  e.preventDefault(); $("register-err").textContent = "";
  try {
    afterAuth(await api("/auth/register-restaurant", { method: "POST", auth: false, body: {
      restaurant_name: $("r-name").value, owner_name: $("r-owner").value,
      email: $("r-email").value, password: $("r-pass").value } }));
    e.target.reset();
  } catch (err) { $("register-err").textContent = err.message; }
};

$("login-form").onsubmit = async (e) => {
  e.preventDefault(); $("login-err").textContent = "";
  try {
    afterAuth(await api("/auth/login", { method: "POST", auth: false, body: {
      email: $("l-email").value, password: $("l-pass").value } }));
    e.target.reset();
  } catch (err) { $("login-err").textContent = err.message; }
};

$("profile-form").onsubmit = async (e) => {
  e.preventDefault(); $("profile-ok").textContent = ""; $("profile-err").textContent = "";
  try {
    const r = await api("/restaurant", { method: "PATCH", body: {
      name: $("p-name").value, description: $("p-desc").value,
      phone: $("p-phone").value, address: $("p-address").value,
      currency: $("p-currency").value, timezone: $("p-timezone").value.trim() || "UTC" } });
    currency = r.currency; tz = r.timezone;
    $("d-name").textContent = r.name;
    $("profile-ok").textContent = "Saved.";
    renderItems();
  } catch (err) { $("profile-err").textContent = err.message; }
};


/* ------------------------------------------------- ordering settings */
async function loadSettings() {
  try {
    const st = await api("/restaurant/settings");
    $("s-tax").value = (st.tax_bps / 100).toString();
    $("s-fee").value = (st.delivery_fee_cents / 100).toFixed(2);
    $("s-min").value = (st.min_order_cents / 100).toFixed(2);
    $("s-pickup").checked = st.pickup_enabled;
    $("s-delivery").checked = st.delivery_enabled;
    $("s-capacity").value = st.reservation_capacity;
  } catch (err) { $("settings-err").textContent = err.message; }
}
$("settings-form").onsubmit = async (e) => {
  e.preventDefault(); $("settings-ok").textContent = ""; $("settings-err").textContent = "";
  try {
    await api("/restaurant/settings", { method: "PATCH", body: {
      tax_bps: Math.round(parseFloat($("s-tax").value || "0") * 100),
      delivery_fee_cents: Math.round(parseFloat($("s-fee").value || "0") * 100),
      min_order_cents: Math.round(parseFloat($("s-min").value || "0") * 100),
      pickup_enabled: $("s-pickup").checked, delivery_enabled: $("s-delivery").checked,
      reservation_capacity: parseInt($("s-capacity").value || "0", 10) } });
    $("settings-ok").textContent = "Saved.";
  } catch (err) { $("settings-err").textContent = err.message; }
};

/* ------------------------------------------------------ recent orders */
function parseDate(s) { return new Date(/Z$|[+-]\d\d:\d\d$/.test(s) ? s : s + "Z"); }
const ORDER_LABELS = { CONFIRMED: "Accept", REJECTED: "Reject", PREPARING: "Start preparing", READY: "Mark ready",
  OUT_FOR_DELIVERY: "Out for delivery", COMPLETED: "Complete", CANCELLED: "Cancel" };
const RES_LABELS = { CONFIRMED: "Confirm", DECLINED: "Decline", CANCELLED: "Cancel", SEATED: "Seat", COMPLETED: "Complete", NO_SHOW: "No-show" };
const NEEDS_REASON = new Set(["REJECTED", "CANCELLED", "DECLINED"]);
const BAD = new Set(["REJECTED", "CANCELLED", "DECLINED", "NO_SHOW"]);

function whenText(iso) {
  const d = parseDate(iso);
  try { return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short", timeZone: tz }).format(d); }
  catch (_) { return d.toLocaleString(); }
}
function statusBadge(status) {
  return el("span", { className: "badge" + (BAD.has(status) ? " off" : ""), textContent: status });
}
function actionButtons(item, labels, onPick) {
  return el("div", { className: "actions" }, ...item.allowed_next.map((st) =>
    el("button", { type: "button", className: "small" + (BAD.has(st) ? " danger" : ""), textContent: labels[st] || st, onclick: () => onPick(item, st) })));
}
async function changeStatus(kind, item, status, errBox, reload) {
  let note = null;
  if (NEEDS_REASON.has(status)) {
    note = prompt("Reason (required)");
    if (!note || !note.trim()) return;
  }
  errBox.textContent = "";
  try { await api(`/${kind}/${item.id}/status`, { method: "POST", body: { status, note } }); await reload(); }
  catch (err) { errBox.textContent = err.message; }
}

async function loadOrders() {
  $("orders-err").textContent = "";
  try {
    const orders = await api("/orders");
    const box = $("order-list");
    if (!orders.length) { box.replaceChildren(el("div", { className: "empty", textContent: "No orders yet. Orders placed on your public menu appear here." })); return; }
    box.replaceChildren(...orders.map((o) => {
      const title = el("div", { className: "title", textContent: o.reference }); title.append(statusBadge(o.status));
      const summary = o.items.map((i) => `${i.quantity}× ${i.name}${i.variant_name ? " (" + i.variant_name + ")" : ""}`).join(", ");
      const left = el("div", { className: "grow" }, title,
        el("div", { className: "meta", textContent: `${o.customer_name} · ${o.customer_phone} · ${o.fulfilment} · ${parseDate(o.created_at).toLocaleString()}` }),
        el("div", { className: "opts", textContent: summary + (o.delivery_address ? " — " + o.delivery_address : "") }),
        el("div", { className: "opts", textContent: "Payment: " + o.payment_method + " (" + o.payment_status.toLowerCase() + ")" }));
      const trail = o.history.map((h) => h.status).join(" → ");
      const reasons = o.history.filter((h) => h.note && h.status !== "PENDING").map((h) => `${h.status}: ${h.note}`);
      left.append(el("div", { className: "opts", textContent: "History: " + trail }));
      if (reasons.length) left.append(el("div", { className: "opts", textContent: reasons.join(" · ") }));
      if (o.allowed_next.length) left.append(actionButtons(o, ORDER_LABELS, (it, st) => changeStatus("orders", it, st, $("orders-err"), loadOrders)));
      return el("div", { className: "row" }, left, el("div", { className: "price", textContent: money(o.total_cents) }));
    }));
  } catch (err) { $("orders-err").textContent = err.message; }
}
$("orders-refresh").onclick = loadOrders;


/* ------------------------------------------------------------ reservations */
async function loadReservations() {
  $("res-err").textContent = "";
  try {
    const list = await api("/reservations");
    const box = $("res-list");
    if (!list.length) { box.replaceChildren(el("div", { className: "empty", textContent: "No reservations yet. Guests can book from your public page." })); return; }
    box.replaceChildren(...list.map((r) => {
      const title = el("div", { className: "title", textContent: `${r.reference} · ${r.customer_name} · party of ${r.party_size}` }); title.append(statusBadge(r.status));
      const left = el("div", { className: "grow" }, title,
        el("div", { className: "meta", textContent: `${whenText(r.starts_at)} (${tz}) · ${r.customer_phone}${r.customer_email ? " · " + r.customer_email : ""}` }));
      if (r.notes) left.append(el("div", { className: "opts", textContent: "Notes: " + r.notes }));
      left.append(el("div", { className: "opts", textContent: "History: " + r.history.map((h) => h.status).join(" → ") }));
      const reasons = r.history.filter((h) => h.note && h.status !== "REQUESTED").map((h) => `${h.status}: ${h.note}`);
      if (reasons.length) left.append(el("div", { className: "opts", textContent: reasons.join(" · ") }));
      if (r.allowed_next.length) left.append(actionButtons(r, RES_LABELS, (it, st) => changeStatus("reservations", it, st, $("res-err"), loadReservations)));
      return el("div", { className: "row" }, left);
    }));
  } catch (err) { $("res-err").textContent = err.message; }
}
$("res-refresh").onclick = loadReservations;

/* ------------------------------------------------------ notification log */
async function loadNotifications() {
  try {
    const list = await api("/notifications");
    const box = $("notif-list");
    if (!list.length) { box.replaceChildren(el("div", { className: "empty", textContent: "No emails yet." })); return; }
    box.replaceChildren(...list.map((n) => {
      const title = el("div", { className: "title", textContent: n.subject }); title.append(el("span", { className: "badge" + (n.status === "FAILED" ? " off" : ""), textContent: n.status }));
      return el("div", { className: "row" }, el("div", { className: "grow" }, title,
        el("div", { className: "meta", textContent: `To ${n.recipient} · ${parseDate(n.created_at).toLocaleString()}` })));
    }));
  } catch (err) { $("notif-list").replaceChildren(el("div", { className: "empty", textContent: "Only owners and managers can see the email log." })); }
}
$("notif-refresh").onclick = loadNotifications;


/* ---------------------------------------------------------------- reports */
function isoDate(d) { return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); }
function initReportDates() {
  if ($("rep-to").value) return;
  $("rep-to").value = isoDate(new Date());
  $("rep-from").value = isoDate(new Date(Date.now() - 29 * 86400000));
}
function tile(label, value) {
  return el("div", { className: "tile" }, el("div", { className: "tile-label", textContent: label }), el("div", { className: "tile-value", textContent: value }));
}
function kvRow(k, v, big) {
  return el("div", { className: "total-row" + (big ? " big" : "") }, el("span", { textContent: k }), el("span", { textContent: v }));
}
function renderReport(r) {
  const rev = r.revenue;
  const max = Math.max(1, ...r.daily.map((d) => d.revenue_cents));
  const days = r.daily.slice(-31);
  const bars = days.map((d) => el("div", { className: "barrow" },
    el("span", { className: "bardate", textContent: d.date.slice(5) }),
    el("div", { className: "bar" }, el("div", { className: "barfill", style: `width:${Math.round(d.revenue_cents / max * 100)}%` })),
    el("span", { className: "barval", textContent: `${d.orders} orders · ${money(d.revenue_cents)}` })));
  const status = Object.entries(r.orders_by_status).sort().map(([k, v]) => kvRow(k, String(v)));
  const resv = Object.entries(r.reservations_by_status).sort().map(([k, v]) => kvRow(k, String(v)));
  const top = r.top_items.map((i) => el("div", { className: "row" },
    el("div", { className: "grow" }, el("div", { className: "title", textContent: i.name })),
    el("div", { className: "meta", textContent: `${i.quantity} sold` }), el("div", { className: "price", textContent: money(i.revenue_cents) })));
  $("rep-out").replaceChildren(
    el("div", { className: "tiles" }, tile("Orders", String(r.orders_total)), tile("Completed sales", money(rev.total_cents)), tile("Average order", money(rev.average_order_cents))),
    el("div", { className: "cat-title", textContent: "Revenue (completed orders)" }),
    kvRow("Items subtotal", money(rev.subtotal_cents)), kvRow("Delivery fees", money(rev.delivery_fee_cents)), kvRow("Tax", money(rev.tax_cents)), kvRow("Total", money(rev.total_cents), true),
    el("div", { className: "cat-title", textContent: "Orders by status" }), ...(status.length ? status : [el("div", { className: "empty", textContent: "No orders in this period." })]),
    el("div", { className: "cat-title", textContent: "Top items" }), ...(top.length ? top : [el("div", { className: "empty", textContent: "No sales yet." })]),
    el("div", { className: "cat-title", textContent: days.length < r.daily.length ? "Daily sales (last 31 days of the range)" : "Daily sales" }), ...bars,
    el("div", { className: "cat-title", textContent: "Reservations in this period" }), ...(resv.length ? resv : [el("div", { className: "empty", textContent: "No reservations." })]),
    el("div", { className: "opts", style: "margin-top:12px", textContent: `${r.from_date} to ${r.to_date} · timezone ${r.timezone} · currency ${r.currency}` }));
}
async function runReport() {
  $("rep-err").textContent = "";
  try { renderReport(await api(`/reports/summary?from=${$("rep-from").value}&to=${$("rep-to").value}`)); }
  catch (err) { $("rep-err").textContent = err.message; $("rep-out").replaceChildren(); }
}
async function downloadCsv() {
  $("rep-err").textContent = "";
  const from = $("rep-from").value, to = $("rep-to").value;
  const res = await fetch(`/api/v1/reports/orders.csv?from=${from}&to=${to}`, { headers: { Authorization: "Bearer " + localStorage.getItem(TOKEN_KEY) } });
  if (!res.ok) { let m = "Could not download the report"; try { m = (await res.json()).detail || m; } catch (_) {} $("rep-err").textContent = m; return; }
  const url = URL.createObjectURL(await res.blob());
  const a = el("a", { href: url, download: `orders_${from}_${to}.csv` });
  document.body.append(a); a.click(); a.remove(); URL.revokeObjectURL(url);
}
$("rep-run").onclick = runReport;
$("rep-csv").onclick = downloadCsv;

loadSession();
