from __future__ import annotations

# --- Dépendances JWT Keycloak (RS256 via JWKS) ---
import logging
from typing import Optional, Set

import jwt
from jwt import PyJWKClient
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi import Security, HTTPException

from app.core.config import settings

logger = logging.getLogger(__name__)

# Schéma d'extraction d'Authorization: Bearer <token>
# auto_error=False permet de renvoyer un 401 propre depuis notre code.
_http_bearer = HTTPBearer(auto_error=False)


class KeycloakJWTVerifier:
    """
    Vérificateur JWT basé sur la JWKS Keycloak.
    - Récupère la clé publique en fonction du 'kid' du token (PyJWKClient).
    - Vérifie la signature RS256, l'expiration et l'émetteur (iss).
    - L'audience (aud) est ignorée pour simplifier les appels machine-to-machine.
    """

    def __init__(self, jwks_url: str, issuer: str):
        self._jwks_client = PyJWKClient(jwks_url)
        self._issuer = issuer

    def decode(self, token: str) -> dict:
        """
        Décodage + vérification du token.
        Ne loggue jamais le token en clair (sécurité).
        """
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token).key
        except Exception:
            # Échec résolution clé (kid inconnu, jwks indisponible, token mal formé, etc.)
            logger.warning("échec récupération clé JWKS pour le token")
            raise

        try:
            payload = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                options={"verify_aud": False},  # aud ignoré ici
                issuer=self._issuer,
            )
            return payload
        except Exception:
            # Signature/iss/exp invalides
            logger.warning("échec vérification JWT (signature/iss/exp)")
            raise


# Instance unique (lazy). Évite de re-créer un client JWKS à chaque requête.
_jwt_verifier: Optional[KeycloakJWTVerifier] = None

def _get_verifier() -> KeycloakJWTVerifier:
    global _jwt_verifier
    if _jwt_verifier is None:
        if not settings.KEYCLOAK_JWKS_URL or not settings.KEYCLOAK_ISSUER:
            raise RuntimeError("KEYCLOAK_JWKS_URL / KEYCLOAK_ISSUER non configurés")
        _jwt_verifier = KeycloakJWTVerifier(settings.KEYCLOAK_JWKS_URL, settings.KEYCLOAK_ISSUER)
        logger.info("Keycloak JWT verifier initialisé")
    return _jwt_verifier


def _extract_roles(payload: dict) -> Set[str]:
    """
    Extrait les rôles du token.
    - Realm roles (par défaut dans realm_access.roles)
    - (Option) client roles si vous mappez sur resource_access["product-api"].roles
    """
    roles: Set[str] = set(payload.get("realm_access", {}).get("roles", []) or [])
    # Décommenter si vous utilisez des "client roles"
    # roles |= set(payload.get("resource_access", {}).get("product-api", {}).get("roles", []) or [])
    return roles


async def require_scope(required: str, creds: HTTPAuthorizationCredentials = Security(_http_bearer)) -> dict:
    """
    Dépendance FastAPI commune:
    - Exige un en-tête Authorization Bearer.
    - Vérifie le JWT via Keycloak JWKS.
    - Contrôle la présence d'un rôle 'required' dans le token.
    Retourne le payload si OK (peut être utilisé par les handlers).
    """
    if creds is None:
        raise HTTPException(status_code=401, detail="Authorization: Bearer <token> requis")

    token = creds.credentials
    try:
        payload = _get_verifier().decode(token)
    except Exception:
        # Détails déjà loggués au niveau WARNING dans le vérifieur.
        raise HTTPException(status_code=401, detail="JWT invalide")

    roles = _extract_roles(payload)
    if required not in roles:
        # On loggue sans exposer d'information sensible; on indique simplement le manque de rôle.
        logger.debug("accès refusé: rôle absent", extra={"required": required, "roles": list(roles)})
        raise HTTPException(status_code=403, detail=f"Rôle requis: {required}")

    # Optionnel: log en DEBUG le sujet (sub) et les rôles pour corrélation.
    logger.debug("accès autorisé", extra={"sub": payload.get("sub"), "roles": list(roles)})
    return payload


# --- Raccourcis par rôle ---
# Les noms de rôles peuvent être centralisés dans la config pour homogénéiser entre services.
_REQUIRED_READ = getattr(settings, "ROLE_READ", "product:read")
_REQUIRED_WRITE = getattr(settings, "ROLE_WRITE", "product:write")

async def require_read(creds: HTTPAuthorizationCredentials = Security(_http_bearer)) -> dict:
    """Lecture: exige le rôle configuré (par défaut 'product:read')."""
    return await require_scope(_REQUIRED_READ, creds)

async def require_write(creds: HTTPAuthorizationCredentials = Security(_http_bearer)) -> dict:
    """Écriture: exige le rôle configuré (par défaut 'product:write')."""
    return await require_scope(_REQUIRED_WRITE, creds)
