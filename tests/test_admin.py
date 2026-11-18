from app.database import SessionLocal
from app.models import PLATFORM_ADMIN, User
from app.security import create_access_token, hash_password


def register(client, name, email):
    r = client.post("/api/v1/auth/register-restaurant", json={
        "restaurant_name": name, "owner_name": "Owner", "email": email, "password": "supersecret1"})
    assert r.status_code == 201
    return r.json()


def auth(data):
    return {"Authorization": f"Bearer {data['access_token']}"}


def make_admin():
    with SessionLocal() as db:
        u = User(restaurant_id=None, email="admin@platform.test", password_hash=hash_password("supersecret1"),
                 full_name="Platform Admin", role=PLATFORM_ADMIN)
        db.add(u); db.commit()
        return {"Authorization": "Bearer " + create_access_token(u.id, u.role, None)}


def setup_two(client):
    a = register(client, "Alpha Cafe", "a@test.dev")
    b = register(client, "Beta Grill", "b@test.dev")
    return a, b, make_admin()


def test_only_platform_admin_can_use_admin_api(client):
    a, _, admin = setup_two(client)
    assert client.get("/api/v1/admin/restaurants").status_code == 401
    assert client.get("/api/v1/admin/restaurants", headers=auth(a)).status_code == 403
    assert client.get("/api/v1/admin/audit-log", headers=auth(a)).status_code == 403
    assert client.post("/api/v1/admin/restaurants/1/status", headers=auth(a),
                       json={"status": "suspended", "reason": "x"}).status_code == 403
    assert client.get("/api/v1/admin/restaurants", headers=admin).status_code == 200


def test_admin_lists_restaurants_with_owner_and_order_count(client):
    a, _, admin = setup_two(client)
    cat = client.post("/api/v1/menu/categories", json={"name": "Mains"}, headers=auth(a)).json()
    item = client.post("/api/v1/menu/items", headers=auth(a), json={
        "category_id": cat["id"], "name": "Dosa", "price_cents": 8000}).json()
    client.post("/api/v1/public/restaurants/alpha-cafe/orders", json={
        "fulfilment": "pickup", "customer_name": "Asha", "customer_phone": "9876543210",
        "items": [{"item_id": item["id"], "quantity": 1}]})
    rows = {r["slug"]: r for r in client.get("/api/v1/admin/restaurants", headers=admin).json()}
    assert rows["alpha-cafe"]["owner_email"] == "a@test.dev" and rows["alpha-cafe"]["order_count"] == 1
    assert rows["beta-grill"]["order_count"] == 0 and rows["beta-grill"]["status"] == "active"


def test_suspend_blocks_the_restaurant_and_reactivate_restores_it(client):
    a, b, admin = setup_two(client)
    rid = a["restaurant"]["id"]
    # a reason is required to suspend
    assert client.post(f"/api/v1/admin/restaurants/{rid}/status", headers=admin, json={"status": "suspended"}).status_code == 422
    r = client.post(f"/api/v1/admin/restaurants/{rid}/status", headers=admin,
                    json={"status": "suspended", "reason": "Payment dispute"})
    assert r.status_code == 200 and r.json()["status"] == "suspended"

    assert client.get("/api/v1/restaurant", headers=auth(a)).status_code == 403          # existing session blocked
    login = client.post("/api/v1/auth/login", json={"email": "a@test.dev", "password": "supersecret1"})
    assert login.status_code == 403                                                        # cannot log in
    assert client.get("/api/v1/public/restaurants/alpha-cafe").status_code == 404          # public site hidden
    assert client.get("/api/v1/public/restaurants/alpha-cafe/menu").status_code == 404
    assert client.get("/api/v1/restaurant", headers=auth(b)).status_code == 200            # other tenants unaffected

    assert client.post(f"/api/v1/admin/restaurants/{rid}/status", headers=admin, json={"status": "suspended", "reason": "again"}).status_code == 409
    back = client.post(f"/api/v1/admin/restaurants/{rid}/status", headers=admin, json={"status": "active", "reason": "Resolved"})
    assert back.status_code == 200
    assert client.get("/api/v1/restaurant", headers=auth(a)).status_code == 200
    assert client.post("/api/v1/auth/login", json={"email": "a@test.dev", "password": "supersecret1"}).status_code == 200
    assert client.get("/api/v1/public/restaurants/alpha-cafe").status_code == 200


def test_plan_change_validation(client):
    a, _, admin = setup_two(client)
    rid = a["restaurant"]["id"]
    ok = client.post(f"/api/v1/admin/restaurants/{rid}/plan", headers=admin, json={"plan": "growth"})
    assert ok.status_code == 200 and ok.json()["plan"] == "growth"
    assert client.post(f"/api/v1/admin/restaurants/{rid}/plan", headers=admin, json={"plan": "platinum"}).status_code == 422
    assert client.post("/api/v1/admin/restaurants/9999/plan", headers=admin, json={"plan": "pro"}).status_code == 404
    assert client.post("/api/v1/admin/restaurants/9999/status", headers=admin, json={"status": "active"}).status_code == 404


def test_every_admin_action_and_settings_change_is_audited(client):
    a, _, admin = setup_two(client)
    rid = a["restaurant"]["id"]
    client.post(f"/api/v1/admin/restaurants/{rid}/status", headers=admin, json={"status": "suspended", "reason": "Payment dispute"})
    client.post(f"/api/v1/admin/restaurants/{rid}/status", headers=admin, json={"status": "active"})
    client.post(f"/api/v1/admin/restaurants/{rid}/plan", headers=admin, json={"plan": "pro"})
    client.patch("/api/v1/restaurant/settings", headers=auth(a), json={"tax_bps": 500})
    log = client.get("/api/v1/admin/audit-log", headers=admin).json()
    actions = [e["action"] for e in log]
    assert actions == ["settings.update", "tenant.plan_change", "tenant.reactivate", "tenant.suspend"]
    by_action = {e["action"]: e for e in log}
    assert by_action["tenant.suspend"]["detail"] == "Payment dispute"
    assert by_action["tenant.suspend"]["actor_email"] == "admin@platform.test"
    assert by_action["tenant.plan_change"]["detail"] == "starter -> pro"
    assert by_action["settings.update"]["actor_email"] == "a@test.dev" and "tax_bps=500" in by_action["settings.update"]["detail"]


def test_platform_admin_cannot_touch_tenant_data_endpoints(client):
    _, _, admin = setup_two(client)
    assert client.get("/api/v1/menu/categories", headers=admin).status_code == 403
    assert client.get("/api/v1/orders", headers=admin).status_code == 403
    assert client.get("/api/v1/reports/summary", headers=admin).status_code == 403
