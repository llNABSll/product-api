# API Produit — Microservice PayeTonKawa

## Présentation

Ce dépôt contient le microservice **API Produit** de la plateforme PayeTonKawa.  
L’application est développée en **Python (FastAPI)**, conteneurisée avec **Docker**, et utilise une base de données **PostgreSQL** indépendante.

Ce microservice gère les opérations CRUD sur les produits du catalogue et s’inscrit dans une architecture microservices avec communication via message broker (RabbitMQ).

API REST **Produits** (microservice) pour PayeTonKawa.  
Stack : **FastAPI**, **SQLAlchemy**, **PostgreSQL**, **RabbitMQ**, **Prometheus metrics**, **Traefik** (ForwardAuth JWT).

---

## Fonctionnalités principales

- CRUD produits (`/api/products`)
- Validation entrée/sortie (Pydantic schemas)
- Sécurité par **JWT** (rôles `product:read`, `product:write`)
- **Événements** via RabbitMQ (ex. `product.created`, `product.updated`, `product.deleted`)
- **Metrics** Prometheus (`/metrics`) + **Health** (`/health`)
- Logs structurés (request id, latence)

---

## Architecture (vue rapide)
```
[Client] -> [Traefik] --(ForwardAuth jwt-auth)--> [Product API] --> [PostgreSQL]
                                            \--> [RabbitMQ] (publish/consume)
```

## Structure du projet
app/  
├── core/ # Configuration et connexion BDD  
├── models/ # Modèles SQLAlchemy des produits  
├── schemas/ # Schémas Pydantic (validation)  
├── routers/ # Définition des routes API  
├── repositories/ # Opérations CRUD sur la base  
├── services/ # Logique métier supplémentaire  
├── tests/ # Tests unitaires  
└── main.py # Point d'entrée FastAPI  
- `Dockerfile` : Image de l’application  
- `docker-compose.yml` : Lancement API + BDD  
- `.env` : Variables d’environnement sensibles  

---

---

## Sécurité
- Auth via JWT validé en edge (Traefik → `jwt-auth`) + recheck des **scopes** côté API.
- Bonnes pratiques OWASP : validation stricte des payloads, messages d’erreur neutres, secrets via env.

---

## Lancer avec Docker (recommandé)
```bash
docker compose up -d product-db rabbitmq
docker compose up -d product-api
```

## Lancer en dev (hors Docker)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
copy env.example .env #remplir les informations nécessaires
uvicorn app.main:app --reload --port 8000
```

---

## Tests & couverture
```bash
pytest #test terminal de commande
pytest --cov=app --cov-branch --cov-report=xml:coverage.xml #test covergae généré
```
> Objectif : **≥ 95 %** de couverture.

---

## Observabilité
- `/metrics` (Prometheus) : requêtes, codes HTTP, histogrammes de latence par route
- `/health` : ready/liveness

---

## Événements (RabbitMQ)
- **Publie** : `product.created`, `product.updated`, `product.deleted`
- **Consomme** : selon besoins (ex. invalidation cache)

---

## CI/CD (GitHub Actions)
- Lint, tests + coverage → `coverage.xml`
- Build image Docker, scan, push registry
- Artefacts/Images récupérables pour déploiement manuel (exigence MSPR)

---

## Notes

Ce microservice fait partie d’un ensemble (API Clients, API Commandes, message broker…)
Les interactions avec RabbitMQ pour la synchronisation entre services peuvent être ajoutées/modifiées selon les besoins.

## Auteurs
GIRARD Anthony, FIACSAN Nicolas, QUACH Simon, PRUJA Benjamin

Projet MSPR TPRE814 — EPSI 2024-2025

