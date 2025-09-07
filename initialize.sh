#!/bin/bash

mkdir -p app/core
mkdir -p app/models
mkdir -p app/schemas
mkdir -p app/routers
mkdir -p app/services
mkdir -p app/repositories
mkdir -p app/tests

touch app/main.py
touch app/core/__init__.py app/core/config.py app/core/database.py
touch app/models/__init__.py app/models/product.py
touch app/schemas/__init__.py app/schemas/product_schema.py
touch app/routers/__init__.py app/routers/product_router.py
touch app/services/__init__.py app/services/product_service.py
touch app/repositories/__init__.py app/repositories/product_repository.py
touch app/tests/__init__.py app/tests/test_product.py

touch Dockerfile docker-compose.yml requirements.txt .env README.md