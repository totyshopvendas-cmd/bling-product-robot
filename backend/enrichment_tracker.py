"""Tracker do enriquecimento JohnDrop/TrimPro em tempo real.

Mantém em memória o estado de cada SKU:
  - waiting_sync: aguardando o fornecedor sincronizar dados/imagens
  - enriching: gerando descrição curta, bullets e metadados
  - waiting_ai: descrição incompleta; usuário deve corrigir IA/saldo e retentar
  - done: descrição curta e 6 ou 8 bullets concluídos
  - failed: erro técnico

A função `track(sku, stage, **info)` é chamada de dentro de `bling_enrichment.py`
em cada transição. O endpoint /api/enrich/progress lista o estado atual."""
from datetime import datetime, timezone
from typing import Optional
from collections import OrderedDict

from fastapi import APIRouter


router = APIRouter(prefix="/enrich", tags=["enrichment-progress"])

# OrderedDict so we keep insertion order (most recent at the end).
# Cap size at ~50 entries to avoid memory growth.
_TRACK: "OrderedDict[str, dict]" = OrderedDict()
_MAX = 50


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def track(sku: str, stage: str, **info) -> None:
    """Update or insert an entry for ``sku``.

    Stages are queued, waiting_sync, enriching, waiting_ai, done and failed.
    Extra info kwargs go into the entry as-is.
    """
    if not sku:
        return
    entry = _TRACK.pop(sku, None) or {"sku": sku, "created_at": _now()}
    entry["stage"] = stage
    entry["updated_at"] = _now()
    entry.update(info)
    _TRACK[sku] = entry
    # Evict oldest if over cap (only when adding new keys, not refreshing existing)
    while len(_TRACK) > _MAX:
        _TRACK.popitem(last=False)


@router.get("/progress")
async def progress(limit: int = 30) -> dict:
    items = list(reversed(_TRACK.values()))[:limit]
    active = [it for it in _TRACK.values() if it.get("stage") in ("queued", "waiting_sync", "enriching")]
    return {
        "items": items,
        "summary": {
            "total": len(_TRACK),
            "active": len(active),
            "done": sum(1 for it in _TRACK.values() if it.get("stage") == "done"),
            "waiting_ai": sum(1 for it in _TRACK.values() if it.get("stage") == "waiting_ai"),
            "failed": sum(1 for it in _TRACK.values() if it.get("stage") == "failed"),
        },
    }


@router.delete("/progress")
async def clear_progress() -> dict:
    _TRACK.clear()
    return {"ok": True}
