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

LOCAL_MODE = mongo_url.startswith("memory")
_local_sync_db = None  # mongomock database (sync) — only in LOCAL_MODE

if LOCAL_MODE:
    # Modo local sem MongoDB instalado. Os dados ficam num arquivo JSON
    # (LOCAL_DB_FILE, padrão backend/data/local_db.json) gravado a cada
    # poucos segundos e ao desligar — assim nada se perde ao reiniciar o PC.
    from mongomock_motor import AsyncMongoMockClient

    client = AsyncMongoMockClient()
    _local_sync_db = client._AsyncMongoMockClient__client[_db_name]
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


# ─────────────────────────── persistência do modo local ───────────────────────────
import asyncio as _asyncio
import hashlib as _hashlib
import logging as _logging

_log = _logging.getLogger("db.local")
_LOCAL_FILE = Path(os.environ.get("LOCAL_DB_FILE") or (_BACKEND_DIR / "data" / "local_db.json"))
_PERSIST = LOCAL_MODE and (os.environ.get("LOCAL_DB_PERSIST", "1").strip().lower() not in ("0", "false", "no", "nao", "não"))
_last_hash: str | None = None
_task = None


def local_db_info() -> dict:
    return {"local_mode": LOCAL_MODE, "persist": _PERSIST, "file": str(_LOCAL_FILE) if _PERSIST else None}


def load_local_db() -> int:
    """Carrega o arquivo salvo para o banco em memória. Devolve nº de documentos."""
    global _last_hash
    if not _PERSIST or _local_sync_db is None or not _LOCAL_FILE.exists():
        return 0
    from bson import json_util

    try:
        raw = _LOCAL_FILE.read_text(encoding="utf-8")
        data = json_util.loads(raw)
    except Exception as e:  # arquivo corrompido: guarda cópia e começa limpo
        bad = _LOCAL_FILE.with_suffix(".corrompido.json")
        try:
            _LOCAL_FILE.replace(bad)
        except Exception:
            pass
        _log.error("Banco local ilegível (%s). Cópia salva em %s", e, bad)
        return 0
    total = 0
    for name, docs in (data.get("collections") or {}).items():
        coll = _local_sync_db[name]
        coll.delete_many({})
        if docs:
            coll.insert_many(docs)
            total += len(docs)
    _last_hash = _hashlib.sha1(raw.encode("utf-8")).hexdigest()
    _log.info("Banco local carregado: %s documentos de %s", total, _LOCAL_FILE)
    return total


def flush_local_db(force: bool = False) -> bool:
    """Grava o banco em memória no arquivo (atômico). Só escreve se mudou."""
    global _last_hash
    if not _PERSIST or _local_sync_db is None:
        return False
    from bson import json_util

    cols = {}
    for name in sorted(_local_sync_db.list_collection_names()):
        cols[name] = list(_local_sync_db[name].find({}))
    raw = json_util.dumps({"version": 1, "collections": cols}, ensure_ascii=False)
    h = _hashlib.sha1(raw.encode("utf-8")).hexdigest()
    if not force and h == _last_hash:
        return False
    _LOCAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _LOCAL_FILE.with_suffix(".tmp")
    tmp.write_text(raw, encoding="utf-8")
    os.replace(tmp, _LOCAL_FILE)
    _last_hash = h
    return True


async def _flush_loop(interval: float) -> None:
    while True:
        await _asyncio.sleep(interval)
        try:
            flush_local_db()
        except Exception as e:
            _log.warning("falha ao salvar banco local: %s", e)


def start_local_persistence(interval: float = 10.0) -> None:
    """Chamar no startup do app (dentro do event loop)."""
    global _task
    if not _PERSIST or _task is not None:
        return
    try:
        load_local_db()
    except Exception as e:
        _log.warning("falha ao carregar banco local: %s", e)
    _task = _asyncio.get_event_loop().create_task(_flush_loop(interval))


def stop_local_persistence() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        _task = None
    try:
        flush_local_db(force=True)
    except Exception as e:
        _log.warning("falha ao salvar banco local no desligamento: %s", e)
