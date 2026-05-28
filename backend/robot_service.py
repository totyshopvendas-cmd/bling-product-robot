"""Robot state management + log persistence."""
import asyncio
from datetime import datetime, timezone
from typing import Optional
from db import db
from models import LogEntry


class RobotState:
    """In-memory robot state (single instance, MVP)."""
    def __init__(self):
        self.state = "idle"  # idle | running | paused | error
        self.current_product: Optional[str] = None
        self.processed = 0
        self.success = 0
        self.failed = 0
        self.started_at: Optional[datetime] = None
        self.finished_at: Optional[datetime] = None
        self.message: Optional[str] = None
        self.task: Optional[asyncio.Task] = None
        self.stop_flag = False

    def reset(self):
        self.current_product = None
        self.processed = 0
        self.success = 0
        self.failed = 0
        self.started_at = None
        self.finished_at = None
        self.message = None
        self.stop_flag = False

    def to_dict(self):
        return {
            "state": self.state,
            "current_product": self.current_product,
            "processed": self.processed,
            "success": self.success,
            "failed": self.failed,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "message": self.message,
        }


robot = RobotState()


async def add_log(level: str, message: str, **extra):
    entry = LogEntry(level=level, message=message, **extra)
    doc = entry.model_dump()
    doc["created_at"] = doc["created_at"].isoformat()
    await db.robot_logs.insert_one(doc)


async def get_logs(limit: int = 100):
    cur = db.robot_logs.find({}, {"_id": 0}).sort("created_at", -1).limit(limit)
    return await cur.to_list(limit)


async def clear_logs():
    await db.robot_logs.delete_many({})


async def count_logs_today(level: Optional[str] = None) -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    q = {"created_at": {"$gte": today}}
    if level:
        q["level"] = level
    return await db.robot_logs.count_documents(q)
