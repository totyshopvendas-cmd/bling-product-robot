"""Seed 3 previews to test status badges, then verify the UI shows the
correct data-testid badges: status-applied, status-pending-apply, status-no-suggestion.
Cleans up TEST_-prefixed seed data at teardown.
"""
import asyncio, os
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / "backend" / ".env")
from motor.motor_asyncio import AsyncIOMotorClient

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]

SEED_DOCS = [
    {  # applied
        "bling_category_id": 9990001,
        "bling_category_name": "TEST_Applied_Category",
        "marketplace": "TEST_MKT_A",
        "suggestion_id": "sug-a-1",
        "suggestion_name": "TEST_Sug_A",
        "confidence": 0.9,
        "reason": "seed",
        "approved": True,
        "applied": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    },
    {  # pending apply
        "bling_category_id": 9990002,
        "bling_category_name": "TEST_Pending_Category",
        "marketplace": "TEST_MKT_B",
        "suggestion_id": "sug-b-1",
        "suggestion_name": "TEST_Sug_B",
        "confidence": 0.75,
        "reason": "seed",
        "approved": True,
        "applied": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    },
    {  # no suggestion
        "bling_category_id": 9990003,
        "bling_category_name": "TEST_NoSuggest_Category",
        "marketplace": "TEST_MKT_C",
        "suggestion_id": None,
        "suggestion_name": None,
        "confidence": 0.0,
        "reason": "no_match",
        "approved": False,
        "applied": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    },
]

async def seed():
    cli = AsyncIOMotorClient(MONGO_URL)
    db = cli[DB_NAME]
    await db.category_mapping_previews.delete_many({"bling_category_name": {"$regex": "^TEST_"}})
    await db.category_mapping_previews.insert_many(SEED_DOCS)
    print("SEEDED 3 docs")
    cli.close()

async def cleanup():
    cli = AsyncIOMotorClient(MONGO_URL)
    db = cli[DB_NAME]
    r = await db.category_mapping_previews.delete_many({"bling_category_name": {"$regex": "^TEST_"}})
    print(f"CLEANED {r.deleted_count} docs")
    cli.close()

if __name__ == "__main__":
    import sys
    if sys.argv[-1] == "clean":
        asyncio.run(cleanup())
    else:
        asyncio.run(seed())
