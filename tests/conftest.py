# tests/conftest.py
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.core.database import Base, get_db


# --------------------------------------------------------------------
# DB SQLite en mémoire pour les tests
# --------------------------------------------------------------------
engine = create_engine("sqlite:///:memory:", future=True)
TestingSessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, future=True
)


@pytest.fixture(scope="session", autouse=True)
def setup_database():
    """Créer et détruire les tables pour toute la session de tests"""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def session():
    """Session DB propre par test"""
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


# --------------------------------------------------------------------
# Patcher RabbitMQ pour ne rien envoyer
# --------------------------------------------------------------------
@pytest.fixture
def patch_rabbitmq(monkeypatch):
    async def fake_publish_message(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "app.infra.events.rabbitmq.RabbitMQ.publish_message", fake_publish_message
    )


# --------------------------------------------------------------------
# Patcher la sécurité pour bypasser Keycloak
# --------------------------------------------------------------------
# @pytest.fixture(autouse=True)
# def patch_security(monkeypatch):
#     from app.security import security
#
#     # Toujours un utilisateur "test" avec rôle admin
#     fake_ctx = security.AuthContext(user="test", email="test@example.com", roles=["product:write"])
#
#     monkeypatch.setattr("app.security.security.require_user", lambda *a, **k: fake_ctx)
#     monkeypatch.setattr("app.security.security.require_write", lambda *a, **k: fake_ctx)
#     monkeypatch.setattr("app.security.security.require_read", lambda *a, **k: fake_ctx)


# --------------------------------------------------------------------
# Fournir un client FastAPI avec DB testée
# --------------------------------------------------------------------
@pytest.fixture
def client(session, monkeypatch):
    # Override get_db pour injecter notre session SQLite in-memory
    def override_get_db():
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)
