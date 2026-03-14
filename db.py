from motor.motor_asyncio import AsyncIOMotorClient
import os

MONGO_URL = os.getenv(
    "MONGO_URL",
    "mongodb+srv://admin:admin_cst@cstcluster.40oocfi.mongodb.net/?appName=CSTCluster&authSource=admin"
)

DB_NAME = os.getenv("DB_NAME", "cst_db")

client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]

def get_db():
    return db
