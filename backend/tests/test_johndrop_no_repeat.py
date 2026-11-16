"""Persistent no-repeat protection shared with the JohnDrop flow."""
import asyncio
import time

from db import db
import johndrop_bot


def test_johndrop_remembers_confirmed_cadastros_across_later_runs():
    """A confirmed JohnDrop source product cannot be submitted a second time."""
    source_id = str(time.time_ns())
    href = f"/dashboard/product/createv2/{source_id}"
    sku = f"JOHNDROP-NO-REPEAT-{source_id}"

    async def run():
        await johndrop_bot._remember_processed([href, source_id, sku])
        await db.enrich_pending.insert_one({
            "sku": f"OLD-{source_id}",
            "johndrop_id": source_id,
        })
        keys = await johndrop_bot._load_processed_keys()
        await db.johndrop_processed.delete_many({"key": {"$in": [href, source_id, sku]}})
        await db.enrich_pending.delete_many({"sku": f"OLD-{source_id}"})
        return keys

    keys = asyncio.run(run())
    assert href in keys
    assert source_id in keys
    assert sku in keys
    assert johndrop_bot._source_key(href) == source_id


def test_johndrop_blocks_an_inconclusive_submission_without_marking_it_confirmed():
    source_id = str(time.time_ns())
    href = f"/dashboard/product/createv2/{source_id}"

    async def run():
        await johndrop_bot._quarantine_submission_review([href, source_id])
        keys = await johndrop_bot._load_processed_keys()
        review = await db.johndrop_submission_review.find_one({"key": source_id}, {"_id": 0})
        confirmed = await db.johndrop_processed.find_one({"key": source_id}, {"_id": 0})
        await db.johndrop_submission_review.delete_many({"key": {"$in": [href, source_id]}})
        return keys, review, confirmed

    keys, review, confirmed = asyncio.run(run())
    assert href in keys
    assert source_id in keys
    assert review["status"] == "pending_review"
    assert confirmed is None
