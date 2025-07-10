from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.schemas.product_schema import ProductCreate, ProductResponse, ProductUpdate
from app.repositories import product_repository
from app.core.database import SessionLocal

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.post("/", response_model=ProductResponse)
def create(product: ProductCreate, db: Session = Depends(get_db)):
    return product_repository.create_product(db, product)

@router.get("/", response_model=list[ProductResponse])
def list_all(skip: int = 0, limit: int = 10, db: Session = Depends(get_db)):
    return product_repository.get_products(db, skip=skip, limit=limit)

@router.get("/{product_id}", response_model=ProductResponse)
def read(product_id: int, db: Session = Depends(get_db)):
    db_product = product_repository.get_product(db, product_id)
    if not db_product:
        raise HTTPException(status_code=404, detail="Produit non trouvé")
    return db_product

@router.put("/{product_id}", response_model=ProductResponse)
def update(product_id: int, product: ProductUpdate, db: Session = Depends(get_db)):
    db_product = product_repository.update_product(db, product_id, product)
    if not db_product:
        raise HTTPException(status_code=404, detail="Produit non trouvé")
    return db_product

@router.delete("/{product_id}", response_model=ProductResponse)
def delete(product_id: int, db: Session = Depends(get_db)):
    db_product = product_repository.delete_product(db, product_id)
    if not db_product:
        raise HTTPException(status_code=404, detail="Produit non trouvé")
    return db_product
