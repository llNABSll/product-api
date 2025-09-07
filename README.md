# API Produit — Microservice PayeTonKawa

## Présentation

Ce dépôt contient le microservice **API Produit** de la plateforme PayeTonKawa.  
L’application est développée en **Python (FastAPI)**, conteneurisée avec **Docker**, et utilise une base de données **PostgreSQL** indépendante.

Ce microservice gère les opérations CRUD sur les produits du catalogue et s’inscrit dans une architecture microservices avec communication via message broker (RabbitMQ).

---

## Fonctionnalités principales

- Création, lecture, mise à jour et suppression de produits (CRUD)
- Exposition d’une API REST documentée automatiquement (Swagger UI)
- Gestion de la base de données PostgreSQL indépendante
- Prise en charge de la configuration via variables d’environnement (`.env`)
- Prêt à l’intégration dans un cluster Docker Compose multi-services

---

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

## Lancement rapide

1. **Configurer le fichier `.env` à la racine** (voir exemple fourni)
2. **Lancer l’application avec Docker Compose :**
```bash
# Initialiser le fichier `.env` à partir du modèle fourni :
cp env.example .env # Pensez à mettre les valeurs à jour

# Lancer l'application docker
docker-compose up --build

# Accéder à la documentation interactive de l’API :
http://localhost:8000/docs
```

## Notes

Ce microservice fait partie d’un ensemble (API Clients, API Commandes, message broker…)
Les interactions avec RabbitMQ pour la synchronisation entre services peuvent être ajoutées/modifiées selon les besoins.

## Auteurs
GIRARD Anthony, FIACSAN Nicolas, QUACH Simon, PRUJA Benjamin

Projet MSPR TPRE814 — EPSI 2024-2025

