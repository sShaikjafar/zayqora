from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, inspect, text

from app import ratelimit
from app.config import Settings
from app.migrate import ensure_columns


def register(client, name="Alpha Cafe", email="a@test.dev"):
    r = client.post("/api/v1/auth/register-restaurant", json={
        "restaurant_name": name, "owner_name": "Owner", "email": email, "password": "supersecret1"})
    assert r.status_code == 201
    return r.json()


def auth(data):
    return {"Authorization": f"Bearer {data['access_token']}"}


def menu_item(client, owner):
    cat = client.post("/api/v1/menu/categories", json={"name": "Mains"}, headers=auth(owner)).json()
    return client.post("/api/v1/menu/items", headers=auth(owner), json={
        "category_id": cat["id"], "name": "Dosa", "price_cents": 8000}).json()["id"]


def place(client, owner, item_id):
    r = client.post(f"/api/v1/public/restaurants/{owner['restaurant']['slug']}/orders", json={
        "fulfilment": "pickup", "customer_name": "Asha Rao", "customer_phone": "9876543210",
        "items": [{"item_id": item_id, "quantity": 1}]})
    assert r.status_code == 201
    return r.json()["reference"]


# --------------------------------------------------------------- hardening
def test_rate_limit_blocks_repeated_logins(client, monkeypatch):
    register(client)
    monkeypatch.setenv("RATE_LIMIT_DISABLED", "0")
    ratelimit.reset()
    try:
        codes = [client.post("/api/v1/auth/login", json={"email": "a@test.dev", "password": "wrong-password"}).status_code
                 for _ in range(12)]
        assert codes[:10] == [401] * 10
        assert codes[10:] == [429, 429]
        blocked = client.post("/api/v1/auth/login", json={"email": "a@test.dev", "password": "supersecret1"})
        assert blocked.status_code == 429 and "Retry-After" in blocked.headers
    finally:
        ratelimit.reset()


def test_security_headers(client):
    api = client.get("/api/v1/health")
    assert api.headers["x-content-type-options"] == "nosniff"
    assert api.headers["x-frame-options"] == "DENY"
    assert api.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert api.headers["cache-control"] == "no-store"
    page = client.get("/")
    assert page.headers["x-content-type-options"] == "nosniff" and "no-store" not in page.headers.get("cache-control", "")


def test_production_refuses_a_weak_secret():
    with pytest.raises(RuntimeError):
        Settings(environment="production").validate_for_environment()               # test key is short
    with pytest.raises(RuntimeError):
        Settings(environment="production", secret_key="dev-only-change-me").validate_for_environment()
    Settings(environment="production", secret_key="x" * 40).validate_for_environment()  # strong: fine
    Settings(environment="development").validate_for_environment()                  # dev: always fine


def test_ensure_columns_upgrades_an_old_database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE restaurant_settings (id INTEGER PRIMARY KEY, restaurant_id INTEGER)"))
        conn.execute(text("INSERT INTO restaurant_settings (restaurant_id) VALUES (1)"))
    ensure_columns(engine)
    ensure_columns(engine)  # safe to run twice
    cols = {c["name"] for c in inspect(engine).get_columns("restaurant_settings")}
    assert "reservation_capacity" in cols
    with engine.connect() as conn:
        assert conn.execute(text("SELECT reservation_capacity FROM restaurant_settings")).scalar() == 0


# ---------------------------------------------------------- order tracking
def test_customer_can_track_an_order(client):
    owner = register(client)
    ref = place(client, owner, menu_item(client, owner))
    url = f"/api/v1/public/restaurants/alpha-cafe/orders/{ref}"
    first = client.get(url)
    assert first.status_code == 200
    body = first.json()
    assert body["status"] == "PENDING" and [h["status"] for h in body["history"]] == ["PENDING"]
    assert "id" not in body and "changed_by_user_id" not in str(body)

    oid = client.get("/api/v1/orders", headers=auth(owner)).json()[0]["id"]
    step = lambda status, note=None: client.post(  # noqa: E731
        f"/api/v1/orders/{oid}/status", headers=auth(owner), json={"status": status, "note": note})
    step("CONFIRMED", "internal: regular customer, add extra sauce")
    tracked = client.get(url.replace(ref, ref.lower())).json()  # a lower-case reference also works
    assert tracked["status"] == "CONFIRMED"
    assert all(h["note"] is None for h in tracked["history"])  # internal notes are never shown
    step("CANCELLED", "Kitchen closed early")
    final = client.get(url).json()
    assert final["status"] == "CANCELLED" and final["history"][-1]["note"] == "Kitchen closed early"


def test_tracking_is_scoped_to_the_right_restaurant(client):
    owner = register(client)
    register(client, "Beta Grill", "b@test.dev")
    ref = place(client, owner, menu_item(client, owner))
    assert client.get(f"/api/v1/public/restaurants/beta-grill/orders/{ref}").status_code == 404
    assert client.get("/api/v1/public/restaurants/alpha-cafe/orders/ZQ-NOTREAL1").status_code == 404
    assert client.get("/api/v1/public/restaurants/nope/orders/" + ref).status_code == 404


# ------------------------------------------------------ reservation capacity
def future(days=2, hour=19):
    d = datetime.now(timezone.utc) + timedelta(days=days)
    return d.strftime("%Y-%m-%d"), f"{hour:02d}:00"


def book(client, party, hour=19, slug="alpha-cafe"):
    date, time = future(hour=hour)
    return client.post(f"/api/v1/public/restaurants/{slug}/reservations", json={
        "customer_name": "Asha Rao", "customer_phone": "9876543210", "party_size": party, "date": date, "time": time})


def test_no_capacity_means_no_limit(client):
    register(client)
    assert book(client, 20).status_code == 201 and book(client, 20).status_code == 201


def test_capacity_prevents_overbooking_and_frees_up_on_decline(client):
    owner = register(client)
    assert client.patch("/api/v1/restaurant/settings", headers=auth(owner), json={"reservation_capacity": 10}).status_code == 200
    first = book(client, 6)
    assert first.status_code == 201
    assert book(client, 6).status_code == 409                 # 6 + 6 > 10 at the same time
    assert book(client, 4).status_code == 201                 # 6 + 4 = 10, exactly full
    assert book(client, 1).status_code == 409
    assert book(client, 6, hour=20).status_code == 409        # 20:00 overlaps the 19:00-20:30 bookings
    assert book(client, 6, hour=22).status_code == 201        # 22:00 does not overlap
    assert book(client, 11).status_code == 422                # bigger than the whole restaurant

    rid = next(r["id"] for r in client.get("/api/v1/reservations", headers=auth(owner)).json()
               if r["reference"] == first.json()["reference"])
    declined = client.post(f"/api/v1/reservations/{rid}/status", headers=auth(owner),
                           json={"status": "DECLINED", "note": "Private event"})
    assert declined.status_code == 200
    assert book(client, 5).status_code == 201                 # the declined booking no longer holds seats
