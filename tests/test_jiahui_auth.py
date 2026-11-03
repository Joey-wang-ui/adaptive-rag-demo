"""Isolated account, session, and API security checks; no business data is used."""

import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.routes.auth import router
from app.jiahui.auth import COOKIE_NAME, AuthStore, Principal, current_principal

PASSWORD = "test-only-safe-passphrase"


@pytest.fixture
def store(tmp_path):
    return AuthStore(tmp_path / "private-auth")


def client_for(store, profile="jiahui"):
    app = FastAPI()
    app.state.container = SimpleNamespace(
        settings=SimpleNamespace(business_profile=profile, jiahui_data_dir=str(store.directory))
    )
    app.include_router(router, prefix="/api/v1")

    @app.post("/protected")
    async def protected(payload: dict, principal: Principal = Depends(current_principal)):
        principal.require("products")
        return {"user_id": principal.user_id, "scopes": sorted(principal.scopes)}

    return TestClient(app)


def test_passwords_salted_sessions_hashed_and_account_create_idempotent(store):
    assert store.create_user("sales", PASSWORD, "sales")
    assert store.create_user("other", PASSWORD, "sales")
    assert not store.create_user("sales", "replacement-passphrase", "admin")
    token = store.login("sales", PASSWORD, "test")
    with sqlite3.connect(store.path) as db:
        hashes = db.execute("SELECT password_hash FROM users").fetchall()
        assert hashes[0] != hashes[1]
        assert PASSWORD not in str(hashes)
        saved = db.execute("SELECT token_hash FROM sessions").fetchone()[0]
        assert token != saved
        assert len(saved) == 64
    assert store.principal(token).role == "sales"
    assert list((store.directory / "backups").glob("*.sqlite3"))


def test_cookie_login_server_identity_logout_and_cross_origin(store):
    store.create_user("仓储", PASSWORD, "warehouse", frozenset({"products"}))
    with client_for(store) as client:
        assert client.get("/api/v1/auth/me").status_code == 401
        assert client.post("/protected", json={"user_id": "admin"}).status_code == 401
        assert (
            client.post(
                "/api/v1/auth/login",
                json={
                    "username": "仓储",
                    "password": PASSWORD,
                },
                headers={"Origin": "https://evil.invalid"},
            ).status_code
            == 403
        )
        login = client.post("/api/v1/auth/login", json={"username": "仓储", "password": PASSWORD})
        assert login.status_code == 200
        cookie = login.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie
        assert login.headers["cache-control"] == "no-store"
        assert client.get("/api/v1/auth/me").json()["username"] == "仓储"
        answer = client.post("/protected", json={"user_id": "admin"})
        assert answer.json()["user_id"] == "仓储"
        assert (
            client.post(
                "/protected", json={}, headers={"Origin": "http://testserver:666"}
            ).status_code
            == 403
        )
        old_cookie = client.cookies.get(COOKIE_NAME)
        assert client.post("/api/v1/auth/logout").status_code == 200
        assert (
            client.get(
                "/api/v1/auth/me", headers={"Authorization": "Bearer " + old_cookie}
            ).status_code
            == 401
        )


def test_permissions_and_disable_take_effect_for_existing_session(store):
    store.create_user("sales", PASSWORD, "sales")
    with client_for(store) as client:
        client.post("/api/v1/auth/login", json={"username": "sales", "password": PASSWORD})
        assert client.post("/protected", json={}).status_code == 200
        store.set_access("sales", "finance", frozenset({"knowledge"}))
        assert client.get("/api/v1/auth/me").json()["scopes"] == ["knowledge"]
        assert client.post("/protected", json={}).status_code == 403
        store.set_access("sales", "finance", frozenset({"knowledge"}), active=False)
        assert client.get("/api/v1/auth/me").status_code == 401


def test_expiration_and_invalid_login(store):
    store.create_user("sales", PASSWORD, "sales")
    token = store.login("sales", PASSWORD, "test")
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE sessions SET expires_at=0")
    with pytest.raises(Exception) as error:
        store.principal(token)
    assert error.value.status_code == 401
    with client_for(store) as client:
        for username in ["sales", "missing", "' OR 1=1--"]:
            response = client.post(
                "/api/v1/auth/login", json={"username": username, "password": "wrong"}
            )
            assert response.status_code == 401
            assert response.json()["detail"] == "账号或密码错误"


def test_persistent_rate_limit(store):
    for _ in range(8):
        with pytest.raises(Exception) as error:
            store.login("unknown", "wrong", "same-ip")
        assert error.value.status_code == 401
    # A new object/worker uses the same persistent throttle.
    with pytest.raises(Exception) as error:
        AuthStore(store.directory).login("unknown", "wrong", "same-ip")
    assert error.value.status_code == 429


def test_ecommerce_login_disabled_and_identity_unchanged(store):
    with client_for(store, "ecommerce") as client:
        assert client.get("/api/v1/auth/me").status_code == 404
        assert (
            client.post("/api/v1/auth/login", json={"username": "a", "password": "b"}).status_code
            == 404
        )


@pytest.mark.parametrize(
    "role,scopes", [("superuser", frozenset()), ("sales", frozenset({"invalid scope"}))]
)
def test_unknown_access_is_rejected(store, role, scopes):
    with pytest.raises(ValueError):
        store.create_user("user", PASSWORD, role, scopes)


def test_app_validation_never_echoes_password(store):
    from app.config.settings import Settings
    from app.main import create_app

    app = create_app(Settings(app_env="testing"))
    # No lifespan: isolate auth from unrelated databases/models.
    app.state.container = SimpleNamespace(
        settings=SimpleNamespace(business_profile="jiahui", jiahui_data_dir=str(store.directory))
    )
    client = TestClient(app)
    try:
        response = client.post("/api/v1/auth/login", json={"password": PASSWORD})
        assert response.status_code == 422
        assert PASSWORD not in response.text
        assert "input" not in response.text
    finally:
        client.close()


def test_custom_scope_tags_and_wildcard_are_server_owned(store):
    store.create_user(
        "buyer", PASSWORD, "procurement", frozenset({"products", "products:procurement"})
    )
    token = store.login("buyer", PASSWORD, "test")
    principal = store.principal(token)
    principal.require("products:procurement")
    with pytest.raises(Exception) as error:
        principal.require("products:finance")
    assert error.value.status_code == 403
    store.set_access("buyer", "procurement", frozenset({"*"}))
    store.principal(token).require("products:finance")


@pytest.mark.parametrize("scope", ["a" * 65, "invalid scope", "../../path", "中文", "<script>", ""])
def test_scope_whitelist_rejects_invalid_tags(store, scope):
    with pytest.raises(ValueError):
        store.create_user("buyer", PASSWORD, "procurement", frozenset({scope}))
