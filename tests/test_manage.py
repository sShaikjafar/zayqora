from app.manage import create_admin, seed_demo, main


def login(client, email, password):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def test_create_admin_command(client):
    assert create_admin("boss@virel.test", password="a-long-password-123") == 0
    r = login(client, "boss@virel.test", "a-long-password-123")
    assert r.status_code == 200 and r.json()["user"]["role"] == "platform_admin" and r.json()["restaurant"] is None
    token = {"Authorization": "Bearer " + r.json()["access_token"]}
    assert client.get("/api/v1/admin/restaurants", headers=token).status_code == 200
    assert create_admin("boss@virel.test", password="a-long-password-123") == 1   # already exists
    assert create_admin("short@virel.test", password="short") == 1                # too short
    assert main(["create-admin", "second@virel.test", "--password", "another-long-pass-1"]) == 0


def test_seed_demo_creates_a_complete_working_restaurant(client):
    assert seed_demo() == 0
    assert seed_demo() == 0  # running twice changes nothing
    owner = login(client, "demo@zayqora.test", "Demo12345!").json()
    h = {"Authorization": f"Bearer {owner['access_token']}"}
    assert owner["restaurant"]["slug"] == "demo-bistro"

    menu = client.get("/api/v1/public/restaurants/demo-bistro/menu").json()
    assert [c["name"] for c in menu["categories"]] == ["Starters", "Mains", "Desserts"]
    orders = client.get("/api/v1/orders", headers=h).json()
    assert len(orders) == 28 and "COMPLETED" in {o["status"] for o in orders}
    assert any(o["status"] in ("PENDING", "PREPARING") for o in orders)  # some orders are still in progress
    for o in orders:  # every seeded order obeys the pricing rules
        assert o["subtotal_cents"] == sum(i["line_total_cents"] for i in o["items"])
        fee = 3000 if o["fulfilment"] == "delivery" else 0
        assert o["total_cents"] == o["subtotal_cents"] + (o["subtotal_cents"] * 500 + 5000) // 10000 + fee

    rep = client.get("/api/v1/reports/summary", headers=h).json()
    assert rep["orders_total"] > 20 and rep["revenue"]["total_cents"] > 0 and rep["top_items"]
    assert len(client.get("/api/v1/reservations", headers=h).json()) == 4

    chef = login(client, "chef@zayqora.test", "Demo12345!").json()
    assert chef["user"]["role"] == "chef"
    assert client.get("/api/v1/reports/summary", headers={"Authorization": f"Bearer {chef['access_token']}"}).status_code == 403
