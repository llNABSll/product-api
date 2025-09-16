# tests/conftest.py
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from _pytest.config import Config
from app.main import app
from app.core.database import Base, get_db


# --------------------------------------------------------------------
# DB SQLite en mémoire pour les tests
# --------------------------------------------------------------------
engine = create_engine("sqlite:///:memory:", future=True)
TestingSessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, future=True
)

def pytest_configure(config: Config):
    """
    Déclare les marqueurs personnalisés pour éviter les warnings 'Unknown mark'
    """
    config.addinivalue_line("markers", "unit: tests unitaires")
    config.addinivalue_line("markers", "integration: tests d'intégration")
    config.addinivalue_line("markers", "acceptance: tests de recette (scénarios métier)")

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
