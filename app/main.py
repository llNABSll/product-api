from fastapi import FastAPI
from app.routers import product_router

app = FastAPI(
    title="API Produit - PayeTonKawa",
    description="Microservice gestion des produits (CRUD)",
    version="1.0.0"
)

app.include_router(product_router.router, prefix="/products", tags=["produits"])
