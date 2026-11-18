import csv
import io
from datetime import datetime, timedelta, timezone

from app.database import SessionLocal
from app.models import ACCOUNTANT, CASHIER, CHEF, User
from app.security import create_access_token, hash_password


def register(client, name, email):
    r = client.post("/api/v1/auth/register-restaurant", json={
        "restaurant_name": name, "owner_name": "Owner", "email": email, "password": "supersecret1"})
    assert r.status_code == 201
    return r.json()


def auth(data):
    return {"Authorization": f"Bearer {data['access_token']}"}


def staff(owner, role, email):
    with SessionLocal() as db:
        u = User(restaurant_id=owner["restaurant"]["id"], email=email,
                 password_hash=hash_password("supersecret1"), full_name=role.title(), role=role)
        db.add(u); db.commit()
        token = create_access_token(u.id, u.role, u.restaurant_id)
    return {"Authorization": f"Bearer {token}"}


def setup_menu(client, owner):
    client.patch("/api/v1/restaurant/settings", headers=auth(owner), json={"tax_bps": 1000, "delivery_fee_cents": 3000})
    cat = client.post("/api/v1/menu/categories", json={"name": "Mains"}, headers=auth(owner)).json()
    mk = lambda name, price: client.post("/api/v1/menu/items", headers=auth(owner), json={  # noqa: E731
        "category_id": cat["id"], "name": name, "price_cents": price}).json()["id"]
    return mk("Dosa", 10000), mk("Thali", 20000)


def place(client, owner, item_id, qty=1, fulfilment="pickup", name="Asha Rao"):
    body = {"fulfilment": fulfilment, "customer_name": name, "customer_phone": "9876543210",
            "delivery_address": "12 Banjara Hills Road" if fulfilment == "delivery" else None,
            "items": [{"item_id": item_id, "quantity": qty}]}
    r = client.post(f"/api/v1/public/restaurants/{owner['restaurant']['slug']}/orders", json=body)
    assert r.status_code == 201, r.text
    return r.json()["reference"]


def order_id(client, owner, reference):
    return next(o["id"] for o in client.get("/api/v1/orders", headers=auth(owner)).json() if o["reference"] == reference)


def advance(client, owner, reference, steps):
    oid = order_id(client, owner, reference)
    for status, note in steps:
        r = client.post(f"/api/v1/orders/{oid}/status", headers=auth(owner), json={"status": status, "note": note})
        assert r.status_code == 200, r.text


PICKUP = [("CONFIRMED", None), ("PREPARING", None), ("READY", None), ("COMPLETED", None)]
DELIVERY = [("CONFIRMED", None), ("PREPARING", None), ("READY", None), ("OUT_FOR_DELIVERY", None), ("COMPLETED", None)]


def build_sample(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    dosa, thali = setup_menu(client, owner)
    a = place(client, owner, dosa, qty=2)                        # completed pickup: 20000 + 2000 tax
    b = place(client, owner, thali, fulfilment="delivery")       # completed delivery: 20000 + 2000 tax + 3000 fee
    c = place(client, owner, dosa)                               # rejected
    place(client, owner, thali)                                  # still pending
    advance(client, owner, a, PICKUP)
    advance(client, owner, b, DELIVERY)
    advance(client, owner, c, [("REJECTED", "Out of stock")])
    return owner


def test_summary_numbers_follow_the_stated_definitions(client):
    owner = build_sample(client)
    r = client.get("/api/v1/reports/summary", headers=auth(owner))
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["orders_total"] == 4
    assert rep["orders_by_status"] == {"COMPLETED": 2, "REJECTED": 1, "PENDING": 1}
    rev = rep["revenue"]
    assert (rev["completed_orders"], rev["subtotal_cents"], rev["tax_cents"], rev["delivery_fee_cents"], rev["total_cents"]) == (
        2, 40000, 4000, 3000, 47000)
    assert rev["average_order_cents"] == 23500
    # top items exclude the rejected order: Dosa sold 2, Thali sold 2 (one completed, one pending)
    top = {i["name"]: i for i in rep["top_items"]}
    assert top["Dosa"]["quantity"] == 2 and top["Dosa"]["revenue_cents"] == 20000
    assert top["Thali"]["quantity"] == 2 and top["Thali"]["revenue_cents"] == 40000
    today = rep["daily"][-1]
    assert today["orders"] == 3 and today["revenue_cents"] == 47000
    assert len(rep["daily"]) == 30 and rep["currency"] == "USD" and rep["timezone"] == "UTC"


def test_summary_range_and_validation(client):
    owner = build_sample(client)
    old = client.get("/api/v1/reports/summary?from=2020-01-01&to=2020-01-05", headers=auth(owner)).json()
    assert old["orders_total"] == 0 and len(old["daily"]) == 5 and old["revenue"]["total_cents"] == 0
    assert client.get("/api/v1/reports/summary?from=2026-02-01&to=2026-01-01", headers=auth(owner)).status_code == 422
    assert client.get("/api/v1/reports/summary?from=2020-01-01&to=2022-01-01", headers=auth(owner)).status_code == 422
    assert client.get("/api/v1/reports/summary?from=not-a-date", headers=auth(owner)).status_code == 422


def test_reservations_are_counted_in_the_report(client):
    owner = build_sample(client)
    start = datetime.now(timezone.utc) + timedelta(days=2)
    r = client.post(f"/api/v1/public/restaurants/{owner['restaurant']['slug']}/reservations", json={
        "customer_name": "Asha Rao", "customer_phone": "9876543210", "party_size": 2,
        "date": start.strftime("%Y-%m-%d"), "time": "19:00"})
    assert r.status_code == 201
    end = start.strftime("%Y-%m-%d")
    rep = client.get(f"/api/v1/reports/summary?from={end}&to={end}", headers=auth(owner)).json()
    assert rep["reservations_total"] == 1 and rep["reservations_by_status"] == {"REQUESTED": 1}


def test_reports_are_tenant_isolated_and_role_protected(client):
    owner = build_sample(client)
    other = register(client, "Beta Grill", "b@test.dev")
    empty = client.get("/api/v1/reports/summary", headers=auth(other)).json()
    assert empty["orders_total"] == 0 and empty["revenue"]["total_cents"] == 0 and empty["top_items"] == []
    assert client.get("/api/v1/reports/summary", headers=staff(owner, CHEF, "chef@t.dev")).status_code == 403
    assert client.get("/api/v1/reports/summary", headers=staff(owner, CASHIER, "cash@t.dev")).status_code == 403
    assert client.get("/api/v1/reports/summary", headers=staff(owner, ACCOUNTANT, "acc@t.dev")).status_code == 200
    assert client.get("/api/v1/reports/summary").status_code == 401
    assert client.get("/api/v1/reports/orders.csv").status_code == 401


def test_csv_export_and_formula_injection_protection(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    dosa, _ = setup_menu(client, owner)
    ref = place(client, owner, dosa, name="=HYPERLINK(\"http://evil\")")
    r = client.get("/api/v1/reports/orders.csv", headers=auth(owner))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows[0][0] == "reference" and "created_local (UTC)" in rows[0][1]
    assert len(rows) == 2 and rows[1][0] == ref
    assert rows[1][4].startswith("'=")  # a spreadsheet will show this as text, not run it
    assert rows[1][7] == "100.00" and rows[1][10] == "110.00" and rows[1][11] == "USD"
