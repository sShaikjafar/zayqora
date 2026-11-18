def register(client, name="Spice Garden", email="owner@spice.test", password="supersecret1"):
    return client.post("/api/v1/auth/register-restaurant", json={
        "restaurant_name": name, "owner_name": "Owner One", "email": email, "password": password,
    })


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_health(client):
    assert client.get("/api/v1/health").json()["status"] == "ok"


def test_register_creates_tenant_and_owner(client):
    r = register(client)
    assert r.status_code == 201
    body = r.json()
    assert body["user"]["role"] == "owner"
    assert body["restaurant"]["slug"] == "spice-garden"
    assert "password" not in str(body)


def test_duplicate_email_rejected(client):
    register(client)
    assert register(client, name="Other Place").status_code == 409


def test_slug_collision_gets_suffix(client):
    register(client)
    r = register(client, email="two@spice.test")
    assert r.json()["restaurant"]["slug"] == "spice-garden-2"


def test_weak_password_rejected(client):
    assert register(client, password="short").status_code == 422


def test_login_ok_and_bad_password(client):
    register(client)
    ok = client.post("/api/v1/auth/login", json={"email": "OWNER@spice.test", "password": "supersecret1"})
    assert ok.status_code == 200
    bad = client.post("/api/v1/auth/login", json={"email": "owner@spice.test", "password": "wrongpass"})
    assert bad.status_code == 401


def test_protected_routes_need_token(client):
    assert client.get("/api/v1/restaurant").status_code == 401
    assert client.get("/api/v1/auth/me", headers=auth("garbage")).status_code == 401


def test_tenant_isolation(client):
    a = register(client, name="Alpha Cafe", email="a@test.dev").json()
    b = register(client, name="Beta Grill", email="b@test.dev").json()

    r = client.patch("/api/v1/restaurant", json={"name": "Alpha Cafe Renamed"},
                     headers=auth(a["access_token"]))
    assert r.status_code == 200 and r.json()["name"] == "Alpha Cafe Renamed"

    # B still sees only B's data, and A's edit did not leak.
    mine = client.get("/api/v1/restaurant", headers=auth(b["access_token"])).json()
    assert mine["name"] == "Beta Grill"
    assert mine["id"] != a["restaurant"]["id"]


def test_public_endpoint_hides_internal_fields(client):
    register(client)
    r = client.get("/api/v1/public/restaurants/spice-garden")
    assert r.status_code == 200
    assert "plan" not in r.json() and "status" not in r.json() and "id" not in r.json()
    assert client.get("/api/v1/public/restaurants/nope").status_code == 404
