from datetime import datetime, timedelta, timezone

import pytest

from app.database import SessionLocal
from app.models import CASHIER, WAITER, User
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


def future(days=2, hour=19):
    d = datetime.now(timezone.utc) + timedelta(days=days)
    return d.strftime("%Y-%m-%d"), f"{hour:02d}:00"


def body(**over):
    date, time = future()
    b = {"customer_name": "Asha Rao", "customer_phone": "9876543210", "customer_email": "asha@example.com",
         "party_size": 4, "date": date, "time": time, "notes": "Birthday"}
    b.update(over)
    return b


def book(client, slug="alpha-cafe", headers=None, **over):
    return client.post(f"/api/v1/public/restaurants/{slug}/reservations", json=body(**over), headers=headers or {})


def move(client, headers, rid, status, note=None):
    return client.post(f"/api/v1/reservations/{rid}/status", headers=headers, json={"status": status, "note": note})


def first_id(client, owner):
    return client.get("/api/v1/reservations", headers=auth(owner)).json()[0]["id"]


def test_create_reservation(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    r = book(client)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["status"] == "REQUESTED" and data["reference"].startswith("RS-") and data["party_size"] == 4
    assert data["starts_at"].endswith("Z") or "+00:00" in data["starts_at"]  # UTC marker present
    assert "customer_phone" not in data and "id" not in data  # public view hides private fields
    listed = client.get("/api/v1/reservations", headers=auth(owner)).json()
    assert len(listed) == 1 and listed[0]["customer_phone"] == "9876543210"
    assert listed[0]["allowed_next"] == ["CONFIRMED", "DECLINED", "CANCELLED"]


def test_reservation_validation(client):
    register(client, "Alpha Cafe", "a@test.dev")
    past = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    far = (datetime.now(timezone.utc) + timedelta(days=200)).strftime("%Y-%m-%d")
    assert book(client, date=past).status_code == 422
    assert book(client, date=far).status_code == 422
    assert book(client, date="2026-13-45").status_code == 422
    assert book(client, time="25:99").status_code == 422
    assert book(client, party_size=0).status_code == 422
    assert book(client, party_size=21).status_code == 422
    assert book(client, customer_phone="abc").status_code == 422
    assert book(client, customer_email="not-an-email").status_code == 422
    assert client.post("/api/v1/public/restaurants/nope/reservations", json=body()).status_code == 404


def test_reservation_idempotency(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    h = {"Idempotency-Key": "res-key-1"}
    first, second = book(client, headers=h), book(client, headers=h)
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["reference"] == second.json()["reference"]
    assert len(client.get("/api/v1/reservations", headers=auth(owner)).json()) == 1


def test_reservation_lifecycle_and_guest_email(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    book(client)
    rid = first_id(client, owner)
    assert move(client, auth(owner), rid, "REQUESTED").status_code == 409     # not a real step
    assert move(client, auth(owner), rid, "SEATED").status_code == 409        # must confirm first
    assert move(client, auth(owner), rid, "CONFIRMED").status_code == 200
    assert move(client, auth(owner), rid, "SEATED").status_code == 200
    assert move(client, auth(owner), rid, "COMPLETED").status_code == 200
    assert move(client, auth(owner), rid, "CANCELLED", "x").status_code == 409  # terminal
    final = client.get("/api/v1/reservations", headers=auth(owner)).json()[0]
    assert [h["status"] for h in final["history"]] == ["REQUESTED", "CONFIRMED", "SEATED", "COMPLETED"]

    kinds = {n["kind"]: n for n in client.get("/api/v1/notifications", headers=auth(owner)).json()}
    assert "reservation_requested" in kinds                                    # staff were told
    assert kinds["reservation_confirmed"]["recipient"] == "asha@example.com"  # guest was told


def test_decline_needs_reason_and_emails_guest(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    book(client)
    rid = first_id(client, owner)
    assert move(client, auth(owner), rid, "DECLINED").status_code == 422
    r = move(client, auth(owner), rid, "DECLINED", "Fully booked")
    assert r.status_code == 200 and r.json()["status"] == "DECLINED"
    assert any(n["kind"] == "reservation_declined" for n in client.get("/api/v1/notifications", headers=auth(owner)).json())


def test_no_guest_email_means_no_guest_notification(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    book(client, customer_email=None)
    move(client, auth(owner), first_id(client, owner), "CONFIRMED")
    kinds = [n["kind"] for n in client.get("/api/v1/notifications", headers=auth(owner)).json()]
    assert kinds == ["reservation_requested"]


def test_reservation_roles(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    waiter, cashier = staff(owner, WAITER, "w@t.dev"), staff(owner, CASHIER, "c@t.dev")
    book(client)
    rid = first_id(client, owner)
    assert move(client, waiter, rid, "CONFIRMED").status_code == 403   # waiter cannot confirm
    assert move(client, cashier, rid, "CONFIRMED").status_code == 200  # cashier can
    assert move(client, waiter, rid, "SEATED").status_code == 200      # waiter seats guests
    assert move(client, waiter, rid, "COMPLETED").status_code == 200


def test_reservations_are_tenant_isolated(client):
    a = register(client, "Alpha Cafe", "a@test.dev")
    b = register(client, "Beta Grill", "b@test.dev")
    book(client, slug="alpha-cafe")
    rid = first_id(client, a)
    assert client.get("/api/v1/reservations", headers=auth(b)).json() == []
    assert move(client, auth(b), rid, "CONFIRMED").status_code == 404
    assert client.get("/api/v1/reservations").status_code == 401
    assert client.get("/api/v1/reservations", headers=auth(a)).json()[0]["status"] == "REQUESTED"


def test_reservation_uses_restaurant_timezone(client):
    owner = register(client, "Alpha Cafe", "a@test.dev")
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo("Asia/Kolkata")
    except Exception:
        pytest.skip("timezone database not installed (pip install tzdata)")
    client.patch("/api/v1/restaurant", headers=auth(owner), json={"timezone": "Asia/Kolkata"})
    date, _ = future(days=3)
    r = book(client, date=date, time="19:00")
    assert r.status_code == 201
    stored = datetime.fromisoformat(r.json()["starts_at"].replace("Z", "+00:00"))
    assert (stored.hour, stored.minute) == (13, 30)  # 19:00 in Kolkata (UTC+5:30) = 13:30 UTC
