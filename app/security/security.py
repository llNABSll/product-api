from typing import List, Optional, Set
import logging
from fastapi import Header, HTTPException, Security, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.core.config import settings

logger = logging.getLogger(__name__)
_http_bearer = HTTPBearer(auto_error=False)

_ROLE_READ = getattr(settings, "ROLE_READ", "product:read")
_ROLE_WRITE = getattr(settings, "ROLE_WRITE", "product:write")

USE_FALLBACK_JWT = str(getattr(settings, "ALLOW_DIRECT_JWT", "false")).lower() == "true"
if USE_FALLBACK_JWT:
    import jwt
    from jwt import PyJWKClient

    class _Verifier:
        def __init__(self, jwks_url: str, issuer: str):
            self._jwk = PyJWKClient(jwks_url)
            self._iss = issuer
        def decode(self, token: str) -> dict:
            key = self._jwk.get_signing_key_from_jwt(token).key
            return jwt.decode(token, key, algorithms=["RS256"], issuer=self._iss, options={"verify_aud": False})
    _verifier = _Verifier(settings.KEYCLOAK_JWKS_URL, settings.KEYCLOAK_ISSUER)

def _roles_from_claims(payload: dict) -> Set[str]:
    roles: Set[str] = set(payload.get("realm_access", {}).get("roles", []) or [])
    # roles |= set(payload.get("resource_access", {}).get("product-api", {}).get("roles", []) or [])
    return roles

class AuthContext:
    def __init__(self, user: str, email: Optional[str], roles: List[str]):
        self.user = user
        self.email = email
        self.roles = roles

def require_user(
    # ---- headers de la gateway (prioritaires) ----
    x_auth_request_user: Optional[str] = Header(None, alias="X-Auth-Request-User"),
    x_auth_request_email: Optional[str] = Header(None, alias="X-Auth-Request-Email"),
    x_auth_request_groups: Optional[str] = Header(None, alias="X-Auth-Request-Groups"),
    # ---- compat éventuelle si tu envoies X-User/X-Email/X-Groups ----
    x_user: Optional[str] = Header(None, alias="X-User"),
    x_email: Optional[str] = Header(None, alias="X-Email"),
    x_groups: Optional[str] = Header(None, alias="X-Groups"),
    # ---- fallback JWT direct (désactivé par défaut) ----
    creds: Optional[HTTPAuthorizationCredentials] = Security(_http_bearer),
) -> AuthContext:
    # 1) Gateway d’abord
    user = x_auth_request_user or x_user
    email = x_auth_request_email or x_email
    groups = x_auth_request_groups or x_groups
    if user:
        roles = [r.strip() for r in (groups or "").split(",") if r.strip()]
        return AuthContext(user=user, email=email, roles=roles)

    # 2) Fallback direct JWT (optionnel)
    if USE_FALLBACK_JWT and creds is not None:
        token = creds.credentials
        try:
            payload = _verifier.decode(token)
        except Exception:
            raise HTTPException(status_code=401, detail="JWT invalide")
        roles = list(_roles_from_claims(payload))
        sub = payload.get("preferred_username") or payload.get("sub")
        return AuthContext(user=sub or "unknown", email=payload.get("email"), roles=roles)

    raise HTTPException(status_code=401, detail="Unauthorized (gateway headers missing)")

def require_read(auth: AuthContext = Depends(require_user)) -> AuthContext:
    """
    Dépendance qui exige le rôle lecture (ex: 'product:read').
    Utilisation: Depends(require_read) dans tes routes GET.
    """
    if _ROLE_READ not in auth.roles:
        raise HTTPException(status_code=403, detail="forbidden: missing role "+_ROLE_READ)
    return auth

def require_write(auth: AuthContext = Depends(require_user)) -> AuthContext:
    """
    Dépendance qui exige le rôle écriture (ex: 'product:write').
    Utilisation: Depends(require_write) dans tes routes POST/PUT/DELETE.
    """
    if _ROLE_WRITE not in auth.roles:
        raise HTTPException(status_code=403, detail="forbidden: missing role "+_ROLE_WRITE)
    return auth