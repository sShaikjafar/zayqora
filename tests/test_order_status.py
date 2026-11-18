from app.database import SessionLocal
from app.models import CASHIER, CHEF, RIDER, User
from app.security import create_access_token, hash_password
from app.workflow import order_allowed_next, reservation_allowed_next


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


def place_order(client, owner, fulfilment="pickup"):
    cat = client.post("/api/v1/menu/categories", json={"name": "Mains"}, headers=auth(owner)).json()
    item = client.post("/api/v1/menu/items", headers=auth(owner), json={
        "category_id": cat["id"], "name": "Dosa", "price_cents": 8000}).json()
    body = {"fulfilment": fulfilment, "customer_name": "Asha Rao", "customer_phone": "9876543210",
            "delivery_address": "12 Banjara Hills Road" if fulfilment == "delivery" else None,
            "items": [{"item_id": item["id"], "quantity": 1}]}
    r = client.post(f"/api/v1/public/restaurants/{owner['restaurant']['slug']}/orders", json=body)
    assert r.status_code == 201, r.text
    orders = client.get("/api/v1/orders", headers=auth(owner)).json()
    return orders[0]["id"]


def move(client, headers, order_id, status, note=None):
    return client.post(f"/api/v1/orders/{order_id}/status", headers=headers, json={"status": status, "note": note})


# ------------------------------------------------------------ pure rules
def test_workflow_rules():
    assert order_allowed_next("PENDING", "pickup", "owner") == ["CONFIRMED", "REJECTED", "CANCELLED"]
    assert order_allowed_next("PENDING", "pickup", "chef") == []
    assert order_allowed_next("READY", "pickup", "owner") == ["COMPLETED"]
    assert order_allowed_next("READY", "delivery", "owner") == ["OUT_FOR_DELIVERY"]
    assert order_allowed_next("COMPLETED", "pickup", "owner") == []
    assert reservation_allowed_next("REQUESTED", "owner") == ["CONFIRMED", "DECLINED", "CANCELLED"]
    assert reservation_allowed_next("CONFIRMED", "waiter") == ["SEATED", "NO_SHOW"]


# ------------------------------------------------------------ order flow
def test_full_delivery_flow_records_history_and_payment(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    oid = place_order(client, owner, "delivery")
    for status in ["CONFIRMED", "PREPARING", "READY", "OUT_FOR_DELIVERY", "COMPLETED"]:
        r = move(client, auth(owner), oid, status)
        assert r.status_code == 200, (status, r.text)
    final = client.get(f"/api/v1/orders/{oid}", headers=auth(owner)).json()
    assert final["status"] == "COMPLETED" and final["payment_status"] == "PAID"
    assert [h["status"] for h in final["history"]] == [
        "PENDING", "CONFIRMED", "PREPARING", "READY", "OUT_FOR_DELIVERY", "COMPLETED"]
    assert final["allowed_next"] == []


def test_pickup_flow_skips_delivery_step(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    oid = place_order(client, owner, "pickup")
    for status in ["CONFIRMED", "PREPARING", "READY"]:
        assert move(client, auth(owner), oid, status).status_code == 200
    assert move(client, auth(owner), oid, "OUT_FOR_DELIVERY").status_code == 409
    assert move(client, auth(owner), oid, "COMPLETED").status_code == 200


def test_illegal_jumps_and_terminal_states(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    oid = place_order(client, owner)
    assert move(client, auth(owner), oid, "READY").status_code == 409  # cannot skip steps
    assert move(client, auth(owner), oid, "PENDING").status_code == 409
    assert move(client, auth(owner), oid, "REJECTED", "Kitchen closed").status_code == 200
    assert move(client, auth(owner), oid, "CONFIRMED").status_code == 409  # terminal


def test_reject_and_cancel_need_a_reason(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    oid = place_order(client, owner)
    assert move(client, auth(owner), oid, "REJECTED").status_code == 422
    assert move(client, auth(owner), oid, "REJECTED", "   ").status_code == 422
    ok = move(client, auth(owner), oid, "REJECTED", "Out of stock")
    assert ok.status_code == 200
    body = ok.json()
    assert body["payment_status"] == "CANCELLED"
    assert body["history"][-1]["note"] == "Out of stock"


def test_role_permissions_are_enforced(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    chef, cashier, rider = staff(owner, CHEF, "chef@t.dev"), staff(owner, CASHIER, "cash@t.dev"), staff(owner, RIDER, "rider@t.dev")
    oid = place_order(client, owner, "delivery")

    assert move(client, chef, oid, "CONFIRMED").status_code == 403       # chef cannot accept orders
    assert move(client, cashier, oid, "CONFIRMED").status_code == 200    # cashier can
    assert move(client, cashier, oid, "PREPARING").status_code == 403    # cashier cannot cook
    assert move(client, chef, oid, "PREPARING").status_code == 200
    assert move(client, chef, oid, "READY").status_code == 200
    assert move(client, chef, oid, "COMPLETED").status_code == 403
    assert move(client, rider, oid, "OUT_FOR_DELIVERY").status_code == 200
    assert move(client, rider, oid, "CANCELLED", "x").status_code == 403  # only owner/manager cancel
    assert move(client, rider, oid, "COMPLETED").status_code == 200


def test_allowed_next_depends_on_role(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    chef = staff(owner, CHEF, "chef@t.dev")
    oid = place_order(client, owner)
    move(client, auth(owner), oid, "CONFIRMED")
    assert client.get(f"/api/v1/orders/{oid}", headers=auth(owner)).json()["allowed_next"] == ["PREPARING", "CANCELLED"]
    assert client.get(f"/api/v1/orders/{oid}", headers=chef).json()["allowed_next"] == ["PREPARING"]


def test_status_change_is_tenant_isolated_and_needs_login(client):
    a = register(client, "Alpha Cafe", "a@test.dev")
    b = register(client, "Beta Grill", "b@test.dev")
    oid = place_order(client, a)
    assert move(client, auth(b), oid, "CONFIRMED").status_code == 404
    assert client.post(f"/api/v1/orders/{oid}/status", json={"status": "CONFIRMED"}).status_code == 401
    assert client.get(f"/api/v1/orders/{oid}", headers=auth(a)).json()["status"] == "PENDING"
    assert client.post(f"/api/v1/orders/{oid}/status", headers=auth(a), json={"status": "BOGUS"}).status_code == 422


# --------------------------------------------------------- notifications
def test_new_order_creates_email_notification(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    chef = staff(owner, CHEF, "chef@t.dev")
    place_order(client, owner)
    notes = client.get("/api/v1/notifications", headers=auth(owner)).json()
    assert len(notes) == 1
    assert notes[0]["kind"] == "order_created" and notes[0]["recipient"] == "a@test.dev"
    assert notes[0]["status"] == "LOGGED"  # no SMTP server configured in tests
    assert "New order" in notes[0]["subject"]
    assert client.get("/api/v1/notifications", headers=chef).status_code == 403
    assert client.get("/api/v1/notifications").status_code == 401


def test_notifications_are_tenant_isolated(client):
    a = register(client, "Alpha Cafe", "a@test.dev")
    b = register(client, "Beta Grill", "b@test.dev")
    place_order(client, a)
    assert client.get("/api/v1/notifications", headers=auth(b)).json() == []
