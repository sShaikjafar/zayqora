import pytest

from app import ratelimit
from app.config import _database_url


@pytest.mark.parametrize("given,expected", [
    ("postgres://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
    ("postgresql://u:p@host/db?sslmode=require", "postgresql+psycopg://u:p@host/db?sslmode=require"),
    ("postgresql+psycopg://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
    ("sqlite:///./x.db", "sqlite:///./x.db"),
])
def test_database_url_gets_the_driver_name(monkeypatch, given, expected):
    monkeypatch.setenv("DATABASE_URL", given)
    assert _database_url() == expected


def test_rate_limit_uses_forwarded_address_behind_a_proxy(client, monkeypatch):
    client.post("/api/v1/auth/register-restaurant", json={
        "restaurant_name": "Alpha Cafe", "owner_name": "Owner", "email": "a@test.dev", "password": "supersecret1"})
    monkeypatch.setenv("RATE_LIMIT_DISABLED", "0")
    monkeypatch.setenv("TRUST_PROXY", "1")
    ratelimit.reset()
    try:
        bad = {"email": "a@test.dev", "password": "wrong-password"}
        from_a = {"X-Forwarded-For": "203.0.113.5"}
        codes = [client.post("/api/v1/auth/login", json=bad, headers=from_a).status_code for _ in range(11)]
        assert codes[:10] == [401] * 10 and codes[10] == 429
        # a different visitor behind the same proxy is not blocked
        assert client.post("/api/v1/auth/login", json=bad, headers={"X-Forwarded-For": "198.51.100.9"}).status_code == 401
    finally:
        ratelimit.reset()
