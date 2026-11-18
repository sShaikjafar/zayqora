const $ = (id) => document.getElementById(id);
const slug = location.pathname.split("/").filter(Boolean)[1];
let currency = "USD", timer = null;
const LABEL = {
  PENDING: "Waiting for the restaurant to accept your order", CONFIRMED: "Accepted by the restaurant", PREPARING: "Being prepared",
  READY: "Ready", OUT_FOR_DELIVERY: "Out for delivery", COMPLETED: "Completed", REJECTED: "Declined by the restaurant", CANCELLED: "Cancelled" };
const FINAL = new Set(["COMPLETED", "REJECTED", "CANCELLED"]);

function el(tag, props = {}, ...children) { const n = document.createElement(tag); Object.assign(n, props); for (const c of children) n.append(c); return n; }
function money(c) { try { return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(c / 100); } catch (_) { return (c / 100).toFixed(2) + " " + currency; } }
function parseDate(s) { return new Date(/Z$|[+-]\d\d:\d\d$/.test(s) ? s : s + "Z"); }

async function track(ref, quiet) {
  if (!quiet) $("track-err").textContent = "";
  const res = await fetch("/api/v1/public/restaurants/" + encodeURIComponent(slug) + "/orders/" + encodeURIComponent(ref));
  let data = null; try { data = await res.json(); } catch (_) {}
  if (!res.ok) {
    clearInterval(timer); $("result").classList.add("hidden");
    $("track-err").textContent = res.status === 429 ? "Too many lookups. Please wait a moment." : (data && typeof data.detail === "string" ? data.detail : "Could not find that order");
    return;
  }
  render(data);
  clearInterval(timer);
  if (!FINAL.has(data.status)) timer = setInterval(() => track(ref, true), 15000); // refresh while the order is still in progress
}

function render(o) {
  const box = $("result"); box.classList.remove("hidden");
  const steps = o.history.map((h) => el("li", {}, LABEL[h.status] || h.status,
    el("small", { textContent: parseDate(h.created_at).toLocaleString() + (h.note ? " · " + h.note : "") })));
  const items = o.items.map((i) => {
    const extras = [i.variant_name, ...i.addons.map((a) => a.name)].filter(Boolean).join(", ");
    return el("div", { className: "row" }, el("div", { className: "grow" }, el("div", { className: "title", textContent: `${i.quantity} × ${i.name}` }), extras ? el("div", { className: "meta", textContent: extras }) : ""),
      el("div", { className: "price", textContent: money(i.line_total_cents) }));
  });
  const rows = [["Subtotal", o.subtotal_cents]];
  if (o.fulfilment === "delivery") rows.push(["Delivery fee", o.delivery_fee_cents]);
  if (o.tax_cents) rows.push(["Tax", o.tax_cents]);
  box.replaceChildren(
    el("div", { className: "ref", textContent: o.reference }),
    el("div", { className: "bigstatus", textContent: LABEL[o.status] || o.status }),
    el("p", { className: "sub", textContent: (o.fulfilment === "delivery" ? "Delivery" : "Pickup") + " · payment: cash (" + o.payment_status.toLowerCase() + ")" + (FINAL.has(o.status) ? "" : " · this page refreshes automatically") }),
    el("ul", { className: "steps" }, ...steps), ...items,
    ...rows.map(([k, v]) => el("div", { className: "total-row" }, el("span", { textContent: k }), el("span", { textContent: money(v) }))),
    el("div", { className: "total-row big" }, el("span", { textContent: "Total" }), el("span", { textContent: money(o.total_cents) })));
}

(async () => {
  $("back").href = "/r/" + encodeURIComponent(slug);
  const r = await fetch("/api/v1/public/restaurants/" + encodeURIComponent(slug));
  if (r.ok) { const d = await r.json(); currency = d.currency; $("site-name").textContent = d.name; document.title = "Track order – " + d.name; }
  $("track-form").onsubmit = (e) => { e.preventDefault(); const v = $("ref").value.trim(); if (v) track(v); };
  const pre = new URLSearchParams(location.search).get("ref");
  if (pre) { $("ref").value = pre; track(pre); }
})();
