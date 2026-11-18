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


def make_category(client, owner, name="Biryani"):
    r = client.post("/api/v1/menu/categories", json={"name": name}, headers=auth(owner))
    assert r.status_code == 201
    return r.json()


def make_item(client, owner, category_id, **extra):
    body = {"category_id": category_id, "name": "Chicken Biryani", "price_cents": 25000, **extra}
    r = client.post("/api/v1/menu/items", json=body, headers=auth(owner))
    assert r.status_code == 201, r.text
    return r.json()


def test_create_and_list_menu(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    cat = make_category(client, owner)
    item = make_item(client, owner, cat["id"], options=[
        {"kind": "variant", "name": "Half", "price_delta_cents": 0},
        {"kind": "variant", "name": "Full", "price_delta_cents": 10000},
        {"kind": "addon", "name": "Extra raita", "price_delta_cents": 2000},
    ])
    assert item["price_cents"] == 25000 and len(item["options"]) == 3
    items = client.get("/api/v1/menu/items", headers=auth(owner)).json()
    assert [i["name"] for i in items] == ["Chicken Biryani"]
    cats = client.get("/api/v1/menu/categories", headers=auth(owner)).json()
    assert [c["name"] for c in cats] == ["Biryani"]


def test_update_item_replaces_options_and_availability(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    cat = make_category(client, owner)
    item = make_item(client, owner, cat["id"], options=[{"kind": "addon", "name": "Egg", "price_delta_cents": 1000}])
    r = client.patch(f"/api/v1/menu/items/{item['id']}", headers=auth(owner), json={
        "is_available": False, "price_cents": 30000,
        "options": [{"kind": "variant", "name": "Large", "price_delta_cents": 5000}]})
    assert r.status_code == 200
    body = r.json()
    assert body["is_available"] is False and body["price_cents"] == 30000
    assert [o["name"] for o in body["options"]] == ["Large"]


def test_validation(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    cat = make_category(client, owner)
    bad = client.post("/api/v1/menu/items", headers=auth(owner),
                      json={"category_id": cat["id"], "name": "X", "price_cents": -5})
    assert bad.status_code == 422
    blank = client.post("/api/v1/menu/categories", headers=auth(owner), json={"name": "   "})
    assert blank.status_code == 422


def test_delete_category_rules(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    cat = make_category(client, owner)
    item = make_item(client, owner, cat["id"])
    assert client.delete(f"/api/v1/menu/categories/{cat['id']}", headers=auth(owner)).status_code == 409
    assert client.delete(f"/api/v1/menu/items/{item['id']}", headers=auth(owner)).status_code == 204
    assert client.delete(f"/api/v1/menu/categories/{cat['id']}", headers=auth(owner)).status_code == 204


def test_menu_tenant_isolation(client):
    a = register(client, "Alpha Cafe", "a@test.dev")
    b = register(client, "Beta Grill", "b@test.dev")
    a_cat = make_category(client, a, "Alpha Starters")
    a_item = make_item(client, a, a_cat["id"])

    # B sees none of A's menu
    assert client.get("/api/v1/menu/items", headers=auth(b)).json() == []
    assert client.get("/api/v1/menu/categories", headers=auth(b)).json() == []
    # B cannot read, change or delete A's item or category
    assert client.get(f"/api/v1/menu/items/{a_item['id']}", headers=auth(b)).status_code == 404
    assert client.patch(f"/api/v1/menu/items/{a_item['id']}", headers=auth(b), json={"name": "Hacked"}).status_code == 404
    assert client.delete(f"/api/v1/menu/items/{a_item['id']}", headers=auth(b)).status_code == 404
    assert client.patch(f"/api/v1/menu/categories/{a_cat['id']}", headers=auth(b), json={"name": "Hacked"}).status_code == 404
    assert client.delete(f"/api/v1/menu/categories/{a_cat['id']}", headers=auth(b)).status_code == 404
    # B cannot put an item into A's category
    r = client.post("/api/v1/menu/items", headers=auth(b),
                    json={"category_id": a_cat["id"], "name": "Sneaky", "price_cents": 100})
    assert r.status_code == 404
    # A's data is untouched
    still = client.get(f"/api/v1/menu/items/{a_item['id']}", headers=auth(a)).json()
    assert still["name"] == "Chicken Biryani"


def test_staff_can_read_but_not_edit_menu(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    make_category(client, owner)
    with SessionLocal() as db:
        chef = User(restaurant_id=owner["restaurant"]["id"], email="chef@test.dev",
                    password_hash=hash_password("supersecret1"), full_name="Chef", role=CHEF)
        db.add(chef); db.commit()
        token = create_access_token(chef.id, chef.role, chef.restaurant_id)
    h = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/menu/categories", headers=h).status_code == 200
    assert client.post("/api/v1/menu/categories", headers=h, json={"name": "Nope"}).status_code == 403


def test_menu_requires_login(client):
    assert client.get("/api/v1/menu/items").status_code == 401
    assert client.post("/api/v1/menu/categories", json={"name": "x"}).status_code == 401


def test_public_menu(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    shown = make_category(client, owner, "Mains")
    hidden = make_category(client, owner, "Secret")
    make_category(client, owner, "Empty")
    make_item(client, owner, shown["id"], name="Dosa", price_cents=8000)
    make_item(client, owner, shown["id"], name="Idli", price_cents=4000, is_available=False)
    make_item(client, owner, hidden["id"], name="Hidden dish")
    client.patch(f"/api/v1/menu/categories/{hidden['id']}", headers=auth(owner), json={"is_active": False})

    r = client.get("/api/v1/public/restaurants/alpha-cafe/menu")
    assert r.status_code == 200
    data = r.json()
    assert [c["name"] for c in data["categories"]] == ["Mains"]  # hidden + empty categories removed
    items = {i["name"]: i for i in data["categories"][0]["items"]}
    assert items["Dosa"]["is_available"] is True and items["Idli"]["is_available"] is False
    assert "restaurant_id" not in items["Dosa"] and "plan" not in data["restaurant"]
    assert client.get("/api/v1/public/restaurants/nope/menu").status_code == 404
