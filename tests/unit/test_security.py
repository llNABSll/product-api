# tests/unit/test_security.py
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.security import security


# ----- _roles_from_claims -----
def test_roles_from_claims_realm_and_resource():
    payload = {
        "realm_access": {"roles": ["realm:admin"]},
        "resource_access": {
            "product-api": {"roles": ["product:read"]},
            "other": {"roles": ["user"]}
        },
        "roles": ["custom:role"]
    }
    roles = security._roles_from_claims(payload)
    assert {"realm:admin", "product:read", "user", "custom:role"} <= roles


# ----- _Verifier -----
def test_verifier_init_missing_settings(monkeypatch):
    with pytest.raises(RuntimeError):
        security._Verifier("", "")


# ----- require_user (gateway mode) -----
def test_require_user_gateway_mode():
    ctx = security.require_user(
        x_auth_request_user="bob",
        x_auth_request_email="bob@example.com",
        x_auth_request_groups="product:read,product:write"
    )
    assert ctx.user == "bob"
    assert ctx.email == "bob@example.com"
    assert {"product:read", "product:write"} <= set(ctx.roles)


def test_require_user_gateway_groups_empty():
    ctx = security.require_user(
        x_auth_request_user="bob",
        x_auth_request_groups=""
    )
    assert ctx.user == "bob"
    assert ctx.roles == []


def test_require_user_gateway_groups_invalid_type():
    ctx = security.require_user(
        x_auth_request_user="bob",
        x_auth_request_groups=123  # mauvais type → ignoré
    )
    assert ctx.roles == []


def test_require_user_gateway_user_invalid_type():
    # Ici x_auth_request_user est un int, donc invalid → None → pas de creds → Unauthorized
    with pytest.raises(HTTPException) as e:
        security.require_user(
            x_auth_request_user=123,
            x_auth_request_groups="product:read"
        )
    assert e.value.status_code == 401
    assert "Unauthorized" in e.value.detail

# ----- require_user (JWT mode) -----
def test_require_user_jwt_valid_with_username(monkeypatch):
    class FakeVerifier:
        def decode(self, token):
            return {
                "preferred_username": "alice",
                "email": "alice@example.com",
                "realm_access": {"roles": ["product:read"]}
            }

    monkeypatch.setattr(security, "_get_verifier", lambda: FakeVerifier())
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="faketoken")

    ctx = security.require_user(creds=creds)
    assert ctx.user == "alice"
    assert ctx.email == "alice@example.com"
    assert "product:read" in ctx.roles


def test_require_user_jwt_valid_with_sub(monkeypatch):
    class FakeVerifier:
        def decode(self, token):
            return {"sub": "user123", "roles": []}

    monkeypatch.setattr(security, "_get_verifier", lambda: FakeVerifier())
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="tok")

    ctx = security.require_user(creds=creds)
    assert ctx.user == "user123"
    assert ctx.email is None


def test_require_user_jwt_valid_unknown(monkeypatch):
    class FakeVerifier:
        def decode(self, token):
            return {}

    monkeypatch.setattr(security, "_get_verifier", lambda: FakeVerifier())
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="tok")

    ctx = security.require_user(creds=creds)
    assert ctx.user == "unknown"


def test_require_user_jwt_invalid(monkeypatch, caplog):
    class FakeVerifier:
        def decode(self, token):
            raise Exception("bad token")

    monkeypatch.setattr(security, "_get_verifier", lambda: FakeVerifier())
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="badtoken")

    caplog.set_level("WARNING")
    with pytest.raises(HTTPException) as e:
        security.require_user(creds=creds)
    assert e.value.status_code == 401
    assert "JWT invalide" in caplog.text


def test_require_user_jwt_wrong_scheme():
    creds = HTTPAuthorizationCredentials(scheme="Basic", credentials="tok")
    with pytest.raises(HTTPException):
        security.require_user(creds=creds)


# ----- require_user (no credentials) -----
def test_require_user_no_credentials():
    with pytest.raises(HTTPException) as e:
        security.require_user()
    assert e.value.status_code == 401


# ----- require_read / require_write -----
def test_require_read_ok():
    ctx = security.AuthContext("u", None, [security._ROLE_READ])
    assert security.require_read(ctx) is ctx


def test_require_read_forbidden():
    ctx = security.AuthContext("u", None, [])
    with pytest.raises(HTTPException) as e:
        security.require_read(ctx)
    assert e.value.status_code == 403
    assert security._ROLE_READ in e.value.detail


def test_require_write_ok():
    ctx = security.AuthContext("u", None, [security._ROLE_WRITE])
    assert security.require_write(ctx) is ctx


def test_require_write_forbidden():
    ctx = security.AuthContext("u", None, [])
    with pytest.raises(HTTPException) as e:
        security.require_write(ctx)
    assert e.value.status_code == 403
    assert security._ROLE_WRITE in e.value.detail

def test_get_verifier_creates_and_reuses(monkeypatch):
    # Patch settings pour éviter RuntimeError
    monkeypatch.setattr(security.settings, "KEYCLOAK_JWKS_URL", "http://fake/jwks")
    monkeypatch.setattr(security.settings, "KEYCLOAK_ISSUER", "http://issuer")

    # Patch PyJWKClient pour éviter un vrai appel réseau
    class DummyJWK:
        def get_signing_key_from_jwt(self, token):
            return type("K", (), {"key": "dummy"})()
    monkeypatch.setattr(security, "PyJWKClient", lambda url: DummyJWK())

    # Reset global
    security._verifier = None
    v1 = security._get_verifier()
    v2 = security._get_verifier()
    assert v1 is v2 



def test_verifier_decode(monkeypatch):
    # Patch PyJWKClient pour renvoyer une clé bidon
    class DummyJWK:
        def get_signing_key_from_jwt(self, token): return type("K", (), {"key": "dummy"})()

    monkeypatch.setattr(security, "PyJWKClient", lambda url: DummyJWK())
    monkeypatch.setattr(security.jwt, "decode", lambda token, key, **kw: {"sub": "u1", "roles": ["product:read"]})

    v = security._Verifier("http://jwks", "issuer")
    payload = v.decode("tok")
    assert payload["sub"] == "u1"
    assert "product:read" in payload["roles"]
