"""MongoDB connection and shared client."""
import os
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from dotenv import load_dotenv
from pathlib import Path

_BACKEND_DIR = Path(__file__).parent
_PROJECT_ROOT = _BACKEND_DIR.parent
load_dotenv(_PROJECT_ROOT / ".env")
load_dotenv(_BACKEND_DIR / ".env", override=False)

mongo_url: str = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
_db_name = os.environ.get("DB_NAME") or "bling_robot"

if mongo_url.startswith("memory"):
    # Offline / sandbox fallback — no mongod required.
    from mongomock_motor import AsyncMongoMockClient

    client = AsyncMongoMockClient()
else:
    client: AsyncIOMotorClient = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=5000)

db: AsyncIOMotorDatabase = client[_db_name]


async def init_indexes() -> None:
    """Create required MongoDB indexes (idempotent)."""
    await db.bling_tokens.create_index("account_id", unique=True)
    await db.pricing.create_index("cost_cents")
    await db.robot_logs.create_index([("created_at", -1)])
    await db.products_cache.create_index("johndrop_id", unique=True)
    await db.settings.create_index("key", unique=True)
