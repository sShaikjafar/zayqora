from app.database import SessionLocal
from app.models import CHEF, User
from app.security import create_access_token, hash_password


def register(client, name, email):
    r = client.post("/api/v1/auth/register-restaurant", json={
        "restaurant_name": name, "owner_name": "Owner", "email": email, "password": "supersecret1"})
    assert r.status_code == 201
    return r.json()


def auth(data):
    return {"Authorization": f"Bearer {data['access_token']}"}


def setup_menu(client, owner):
    """One item: base 250.00, variants Half(+0)/Full(+100.00), add-on raita(+20.00)."""
    cat = client.post("/api/v1/menu/categories", json={"name": "Biryani"}, headers=auth(owner)).json()
    item = client.post("/api/v1/menu/items", headers=auth(owner), json={
        "category_id": cat["id"], "name": "Chicken Biryani", "price_cents": 25000,
        "options": [
            {"kind": "variant", "name": "Half", "price_delta_cents": 0},
            {"kind": "variant", "name": "Full", "price_delta_cents": 10000},
            {"kind": "addon", "name": "Extra raita", "price_delta_cents": 2000},
        ]}).json()
    opts = {o["name"]: o["id"] for o in item["options"]}
    return item, opts


def set_rules(client, owner, tax_bps=500, fee=3000, minimum=0):
    r = client.patch("/api/v1/restaurant/settings", headers=auth(owner), json={
        "tax_bps": tax_bps, "delivery_fee_cents": fee, "min_order_cents": minimum})
    assert r.status_code == 200


def order_body(item, opts, **over):
    body = {
        "fulfilment": "delivery", "customer_name": "Asha Rao", "customer_phone": "+91 98765 43210",
        "delivery_address": "12 Banjara Hills Road, Hyderabad",
        "items": [{"item_id": item["id"], "quantity": 2, "variant_id": opts["Full"], "addon_ids": [opts["Extra raita"]]}],
    }
    body.update(over)
    return body


def test_settings_defaults_and_update(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    st = client.get("/api/v1/restaurant/settings", headers=auth(owner)).json()
    assert st["tax_bps"] == 0 and st["pickup_enabled"] is True
    set_rules(client, owner, tax_bps=500, fee=3000)
    assert client.get("/api/v1/restaurant/settings", headers=auth(owner)).json()["tax_bps"] == 500


def test_quote_and_order_totals_are_server_calculated(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    item, opts = setup_menu(client, owner)
    set_rules(client, owner)
    # unit = 250 + 100 + 20 = 370.00 ; x2 = 740.00 ; tax 5% = 37.00 ; delivery 30.00 ; total 807.00
    q = client.post("/api/v1/public/restaurants/alpha-cafe/quote",
                    json={"fulfilment": "delivery", "items": order_body(item, opts)["items"]})
    assert q.status_code == 200
    qb = q.json()
    assert (qb["subtotal_cents"], qb["tax_cents"], qb["delivery_fee_cents"], qb["total_cents"]) == (74000, 3700, 3000, 80700)

    # A malicious client sending its own prices is ignored.
    body = order_body(item, opts, expected_total_cents=80700)
    body["items"][0]["price_cents"] = 1
    body["items"][0]["unit_price_cents"] = 1
    r = client.post("/api/v1/public/restaurants/alpha-cafe/orders", json=body)
    assert r.status_code == 201, r.text
    o = r.json()
    assert o["total_cents"] == 80700 and o["status"] == "PENDING" and o["reference"].startswith("ZQ-")
    assert o["payment_method"] == "cash" and o["payment_status"] == "PENDING"
    assert o["items"][0]["variant_name"] == "Full" and o["items"][0]["addons"][0]["name"] == "Extra raita"


def test_pickup_has_no_delivery_fee(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    item, opts = setup_menu(client, owner)
    set_rules(client, owner)
    body = order_body(item, opts, fulfilment="pickup", delivery_address=None)
    o = client.post("/api/v1/public/restaurants/alpha-cafe/orders", json=body).json()
    assert o["delivery_fee_cents"] == 0 and o["total_cents"] == 77700 and o["delivery_address"] is None


def test_variant_required_and_invalid_options(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    item, opts = setup_menu(client, owner)
    base = {"fulfilment": "pickup", "customer_name": "Asha", "customer_phone": "9876543210"}
    no_variant = {**base, "items": [{"item_id": item["id"], "quantity": 1}]}
    assert client.post("/api/v1/public/restaurants/alpha-cafe/orders", json=no_variant).status_code == 422
    bad_variant = {**base, "items": [{"item_id": item["id"], "quantity": 1, "variant_id": opts["Extra raita"]}]}
    assert client.post("/api/v1/public/restaurants/alpha-cafe/orders", json=bad_variant).status_code == 422
    dup_addon = {**base, "items": [{"item_id": item["id"], "quantity": 1, "variant_id": opts["Half"],
                                    "addon_ids": [opts["Extra raita"], opts["Extra raita"]]}]}
    assert client.post("/api/v1/public/restaurants/alpha-cafe/orders", json=dup_addon).status_code == 422


def test_sold_out_item_cannot_be_ordered(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    item, opts = setup_menu(client, owner)
    client.patch(f"/api/v1/menu/items/{item['id']}", headers=auth(owner), json={"is_available": False})
    r = client.post("/api/v1/public/restaurants/alpha-cafe/orders", json=order_body(item, opts))
    assert r.status_code == 409 and "sold out" in r.json()["detail"].lower()


def test_price_change_requires_confirmation(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    item, opts = setup_menu(client, owner)
    set_rules(client, owner)
    body = order_body(item, opts, expected_total_cents=80700)
    # Owner raises the price after the customer saw the quote
    client.patch(f"/api/v1/menu/items/{item['id']}", headers=auth(owner), json={"price_cents": 30000})
    r = client.post("/api/v1/public/restaurants/alpha-cafe/orders", json=body)
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "PRICE_CHANGED" and detail["total_cents"] > 80700
    assert client.get("/api/v1/orders", headers=auth(owner)).json() == []  # nothing was created


def test_idempotent_submit_creates_one_order(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    item, opts = setup_menu(client, owner)
    headers = {"Idempotency-Key": "key-123"}
    first = client.post("/api/v1/public/restaurants/alpha-cafe/orders", json=order_body(item, opts), headers=headers)
    second = client.post("/api/v1/public/restaurants/alpha-cafe/orders", json=order_body(item, opts), headers=headers)
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["reference"] == second.json()["reference"]
    assert len(client.get("/api/v1/orders", headers=auth(owner)).json()) == 1


def test_order_is_a_snapshot(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    item, opts = setup_menu(client, owner)
    r = client.post("/api/v1/public/restaurants/alpha-cafe/orders", json=order_body(item, opts)).json()
    total_before = r["total_cents"]
    client.patch(f"/api/v1/menu/items/{item['id']}", headers=auth(owner), json={"name": "Renamed", "price_cents": 99900})
    stored = client.get("/api/v1/orders", headers=auth(owner)).json()[0]
    assert stored["total_cents"] == total_before
    assert stored["items"][0]["name"] == "Chicken Biryani" and stored["items"][0]["unit_price_cents"] == 37000


def test_order_validation(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    item, opts = setup_menu(client, owner)
    url = "/api/v1/public/restaurants/alpha-cafe/orders"
    assert client.post(url, json=order_body(item, opts, delivery_address=None)).status_code == 422
    assert client.post(url, json=order_body(item, opts, customer_phone="abc")).status_code == 422
    assert client.post(url, json=order_body(item, opts, items=[])).status_code == 422
    set_rules(client, owner, minimum=1000000)
    assert client.post(url, json=order_body(item, opts)).status_code == 422  # below minimum order
    set_rules(client, owner, minimum=0)
    client.patch("/api/v1/restaurant/settings", headers=auth(owner), json={"delivery_enabled": False})
    assert client.post(url, json=order_body(item, opts)).status_code == 422  # delivery switched off
    assert client.post("/api/v1/public/restaurants/nope/orders", json=order_body(item, opts)).status_code == 404


def test_cannot_order_another_restaurants_item(client):
    a = register(client, "Alpha Cafe", "a@test.dev")
    register(client, "Beta Grill", "b@test.dev")
    item, opts = setup_menu(client, a)
    r = client.post("/api/v1/public/restaurants/beta-grill/orders", json=order_body(item, opts))
    assert r.status_code == 409  # item id belongs to Alpha, not on Beta's menu


def test_orders_are_tenant_isolated(client):
    a = register(client, "Alpha Cafe", "a@test.dev")
    b = register(client, "Beta Grill", "b@test.dev")
    item, opts = setup_menu(client, a)
    client.post("/api/v1/public/restaurants/alpha-cafe/orders", json=order_body(item, opts))
    a_orders = client.get("/api/v1/orders", headers=auth(a)).json()
    assert len(a_orders) == 1
    assert client.get("/api/v1/orders", headers=auth(b)).json() == []
    assert client.get(f"/api/v1/orders/{a_orders[0]['id']}", headers=auth(b)).status_code == 404
    assert client.get(f"/api/v1/orders/{a_orders[0]['id']}", headers=auth(a)).status_code == 200
    assert client.get("/api/v1/orders").status_code == 401


def test_staff_cannot_change_settings(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    with SessionLocal() as db:
        chef = User(restaurant_id=owner["restaurant"]["id"], email="chef@test.dev",
                    password_hash=hash_password("supersecret1"), full_name="Chef", role=CHEF)
        db.add(chef); db.commit()
        token = create_access_token(chef.id, chef.role, chef.restaurant_id)
    h = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/restaurant/settings", headers=h).status_code == 200
    assert client.patch("/api/v1/restaurant/settings", headers=h, json={"tax_bps": 100}).status_code == 403
