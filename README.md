# ZAYQORA — Day 1 foundation (by VIRELEO)

A standalone project folder, so it will not touch any other project you have open.
Stack: Python + FastAPI + SQLAlchemy (SQLite by default, PostgreSQL-ready) with a
plain HTML/JS front end served by the same app (no Node or build step needed).

## Run it
```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate        macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```
- App:            http://127.0.0.1:8000
- API docs:       http://127.0.0.1:8000/docs
- Public site:    http://127.0.0.1:8000/r/<your-restaurant-slug>

## Test it
```bash
pytest -q
```

## Optional settings (environment variables)
- `DATABASE_URL`  default `sqlite:///./zayqora.db` (PostgreSQL: `postgresql+psycopg://user:pass@localhost/zayqora`)
- `SECRET_KEY`    **change this** for anything beyond local development

## Day 1 checklist
- [x] Project structure and configuration
- [x] Database schema baseline: `restaurants` (tenants) and `users` (with roles)
- [x] Auth: register restaurant + owner, login (JWT), `/auth/me`
- [x] Tenant isolation: tenant comes from the logged-in user, never from the client
- [x] Backend role checks (`require_roles`), per the spec's permission rule
- [x] UI shell: register, login, dashboard with profile editing, public page `/r/{slug}`
- [x] Tests for auth, validation and tenant isolation

## Day 2 checklist (menu management)
- [x] Tables: `menu_categories`, `menu_items`, `item_options` (variants and add-ons)
- [x] Owner/manager API to create, edit and delete categories and items; staff can read
- [x] Sold-out toggle, hide/show category, prices stored as integer minor units
- [x] Dashboard screens for categories and items (with variants and add-ons)
- [x] Public menu page at `/r/<slug>/menu`
- [x] Tests: validation, roles, tenant isolation, public menu rules

Menu API (all under `/api/v1`): `GET/POST /menu/categories`, `PATCH/DELETE /menu/categories/{id}`,
`GET/POST /menu/items`, `GET/PATCH/DELETE /menu/items/{id}`, public `GET /public/restaurants/{slug}/menu`.

## Day 3 checklist (cart, checkout, orders)
- [x] Customer cart on the public menu: required variant, add-ons, quantity, notes, live price
- [x] Server-side pricing: prices, availability and totals are recomputed by the backend (never trusted from the browser)
- [x] Price breakdown: subtotal, delivery fee, tax (added), total. Rules in `app/pricing.py`
- [x] Checkout: pickup or delivery, cash payment, order reference, confirmation page
- [x] Order + item snapshots and status history (starts as PENDING)
- [x] Idempotency (`Idempotency-Key` header) and price-change confirmation (HTTP 409 `PRICE_CHANGED`)
- [x] Ordering settings (tax, delivery fee, minimum order, pickup/delivery on/off) and a read-only Recent orders list

## Day 4 checklist (order management, reservations, notifications)
- [x] Order status flow: PENDING, CONFIRMED, PREPARING, READY, OUT_FOR_DELIVERY, COMPLETED, REJECTED, CANCELLED
- [x] Legal transitions and per-role permissions in one file: `app/workflow.py` (enforced on the backend)
- [x] Reason required for reject / cancel; every change recorded in the status history
- [x] Cash orders become PAID on completion; rejected/cancelled orders cancel the payment
- [x] Table reservations: public request form on `/r/<slug>`, owner confirm / decline / seat / complete / no-show
- [x] Reservation times stored in UTC and read in the restaurant's timezone (set it in the profile, e.g. `Asia/Kolkata`)
- [x] Email notifications: new orders and reservations go to owners and managers; guests are emailed about their reservation
- [x] Every email is kept in the notification log; real sending is optional (below)

Staff roles that exist in the database but have no sign-up screen yet (chef, cashier, waiter, rider) are enforced by the
rules above and covered by tests; a staff-management screen is planned for a later phase.

### Sending real emails (optional)
Set these environment variables before starting the server. Without `SMTP_HOST`, emails stay in the log as `LOGGED`.
```
SMTP_HOST=smtp.example.com   SMTP_PORT=587   SMTP_USER=...   SMTP_PASSWORD=...   SMTP_FROM="ZAYQORA <orders@example.com>"
```

## Day 5 checklist (reports, administration, security, deployment)
- [x] Sales reports with defined calculations and a CSV export (owner, manager, accountant): dashboard card "Sales reports"
- [x] Customer order tracking: `/r/<slug>/track` (auto-refreshes while the order is in progress)
- [x] Reservation capacity rule (setting "Reservation capacity"): overlapping bookings cannot exceed it
- [x] VIRELEO super admin at `/admin`: list restaurants, suspend / reactivate with a reason, change plan, audit log
- [x] Suspended restaurants cannot log in, use the dashboard or take orders; their public pages disappear
- [x] Rate limiting (login, registration, quotes, orders, reservations, tracking), secure response headers, no-store for API data
- [x] Production safety check: with `ENVIRONMENT=production` the app refuses to start with a weak `SECRET_KEY`
- [x] Demo data and admin creation commands, Docker deployment files, backup instructions (`DEPLOY.md`)

### Commands
```
python -m app.manage create-admin you@example.com      # asks for a password (12+ characters)
python -m app.manage seed-demo                         # demo restaurant at /r/demo-bistro
```
Demo logins (password `Demo12345!`): `demo@zayqora.test` (owner), `chef@zayqora.test`, `cashier@zayqora.test`.

### Report definitions
- Revenue = COMPLETED orders only, grouped by the day the order was placed, in the restaurant's timezone.
- "Orders" per day and "Top items" exclude REJECTED and CANCELLED orders.
- Tax is added on the subtotal only; the delivery fee is not taxed.

## Not built (listed in the specification)
Email verification, logo / branding / opening hours, online payments, staff-management screens, billing, loyalty and POS.
