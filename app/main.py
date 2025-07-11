from fastapi import FastAPI
from app.routers import product_router
from app.core.rabbitmq import rabbitmq

app = FastAPI(
    title="API Produit - PayeTonKawa",
    description="Microservice gestion des produits (CRUD)",
    version="1.0.0"
)

# async def on_message(message):
#     logging.info(f"Received message: {message.body.decode()}")
#     await message.ack()

@app.on_event("startup")
async def startup_event():
    await rabbitmq.connect()
    # asyncio.create_task(rabbitmq.subscribe("product_events", on_message))

@app.on_event("shutdown")
async def shutdown_event():
    await rabbitmq.disconnect()

app.include_router(product_router.router, prefix="/products", tags=["produits"])
