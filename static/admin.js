const $ = (id) => document.getElementById(id);
const TOKEN_KEY = "zayqora_token";
const PLANS = ["starter", "growth", "pro"];

function el(tag, props = {}, ...children) { const n = document.createElement(tag); Object.assign(n, props); for (const c of children) n.append(c); return n; }
function parseDate(s) { return new Date(/Z$|[+-]\d\d:\d\d$/.test(s) ? s : s + "Z"); }

async function api(path, { method = "GET", body, auth = true } = {}) {
  const headers = { "Content-Type": "application/json" };
  const token = localStorage.getItem(TOKEN_KEY);
  if (auth && token) headers.Authorization = "Bearer " + token;
  const res = await fetch("/api/v1" + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  let data = null; try { data = await res.json(); } catch (_) {}
  if (!res.ok) {
    let msg = (data && data.detail) || "Something went wrong";
    if (Array.isArray(msg)) msg = msg.map((e) => e.msg).join("; ");
    const err = new Error(msg); err.status = res.status; throw err;
  }
  return data;
}

function showPanel(on) {
  $("login-card").classList.toggle("hidden", on);
  $("panel").classList.toggle("hidden", !on);
  $("logout").hidden = !on;
}

async function load() {
  try { await Promise.all([loadTenants(), loadAudit()]); showPanel(true); }
  catch (err) { localStorage.removeItem(TOKEN_KEY); showPanel(false); }
}

async function loadTenants() {
  const rows = await api("/admin/restaurants");
  $("tenants").replaceChildren(...rows.map((r) => {
    const title = el("div", { className: "title" }, el("a", { href: "/r/" + r.slug, target: "_blank", rel: "noopener", textContent: r.name }));
    title.append(el("span", { className: "badge" + (r.status === "active" ? "" : " off"), textContent: r.status }));
    const plan = el("select", { style: "width:auto" }, ...PLANS.map((p) => el("option", { value: p, textContent: p, selected: p === r.plan })));
    plan.onchange = () => act(`/admin/restaurants/${r.id}/plan`, { plan: plan.value });
    const toggle = el("button", { className: "small" + (r.status === "active" ? " danger" : ""), textContent: r.status === "active" ? "Suspend" : "Reactivate",
      onclick: () => {
        let reason = prompt(r.status === "active" ? "Reason for suspending this restaurant (required)" : "Note for reactivation (optional)") || "";
        if (r.status === "active" && !reason.trim()) return;
        act(`/admin/restaurants/${r.id}/status`, { status: r.status === "active" ? "suspended" : "active", reason: reason.trim() || null });
      } });
    return el("div", { className: "row" },
      el("div", { className: "grow" }, title, el("div", { className: "meta", textContent: `/r/${r.slug} · owner ${r.owner_email || "—"} · ${r.order_count} orders · joined ${parseDate(r.created_at).toLocaleDateString()}` })),
      plan, toggle);
  }));
}

async function loadAudit() {
  const rows = await api("/admin/audit-log");
  $("audit").replaceChildren(...(rows.length ? rows.map((a) => el("div", { className: "row" }, el("div", { className: "grow" },
    el("div", { className: "title", textContent: a.action }),
    el("div", { className: "meta", textContent: `${a.actor_email || "system"} · restaurant #${a.restaurant_id ?? "—"} · ${parseDate(a.created_at).toLocaleString()}${a.detail ? " · " + a.detail : ""}` }))))
    : [el("div", { className: "empty", textContent: "No actions recorded yet." })]));
}

async function act(path, body) {
  $("tenant-err").textContent = "";
  try { await api(path, { method: "POST", body }); await Promise.all([loadTenants(), loadAudit()]); }
  catch (err) { $("tenant-err").textContent = err.message; await loadTenants(); }
}

$("login-form").onsubmit = async (e) => {
  e.preventDefault(); $("login-err").textContent = "";
  try {
    const data = await api("/auth/login", { method: "POST", auth: false, body: { email: $("email").value, password: $("password").value } });
    if (data.user.role !== "platform_admin") { $("login-err").textContent = "This account is not a platform administrator."; return; }
    localStorage.setItem(TOKEN_KEY, data.access_token); e.target.reset(); await load();
  } catch (err) { $("login-err").textContent = err.message; }
};
$("logout").onclick = () => { localStorage.removeItem(TOKEN_KEY); showPanel(false); };
$("audit-refresh").onclick = loadAudit;

if (localStorage.getItem(TOKEN_KEY)) load();
