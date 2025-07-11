import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    POSTGRES_USER = os.environ["POSTGRES_USER"]
    POSTGRES_PASSWORD = os.environ["POSTGRES_PASSWORD"]
    POSTGRES_DB = os.environ["POSTGRES_DB"]
    POSTGRES_SERVER = os.environ["POSTGRES_SERVER"]
    POSTGRES_PORT = os.environ["POSTGRES_PORT"]
    DATABASE_URL = (
        f"postgresql+psycopg2://{POSTGRES_USER}:{POSTGRES_PASSWORD}@"
        f"{POSTGRES_SERVER}:{POSTGRES_PORT}/{POSTGRES_DB}"
    )
    RABBITMQ_URL = os.environ["RABBITMQ_URL"]

settings = Settings()
