"""MongoDB connection and shared client."""
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]


async def init_indexes():
    await db.bling_tokens.create_index("account_id", unique=True)
    await db.pricing.create_index("cost_cents", unique=True)
    await db.robot_logs.create_index([("created_at", -1)])
    await db.products_cache.create_index("johndrop_id", unique=True)
    await db.settings.create_index("key", unique=True)
