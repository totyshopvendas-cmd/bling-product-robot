"""Bulk Bling enrichment service.

Lets the user select Bling products (or all not-yet-enriched) and re-runs
the LLM enrichment pipeline (short description + 8 bullets + category) on each.

Job state is kept in-memory: only one bulk job runs at a time.
"""
import asyncio
import uuid
from datetime import datetime, timezone
from typing import Optional, List

import bling_service
import bling_enrichment
from robot_service import add_log


# ---------------------------------------------------------------- helpers

def _is_enriched(product: dict) -> bool:
    """A product is considered 'already enriched' when it has both a short
    description and a complementary description AND its brand is Generico/Generica."""
    short = (product.get("descricaoCurta") or "").strip()
    comp = (product.get("descricaoComplementar") or "").strip()
    brand = (product.get("marca") or "").strip().lower()
    return bool(short) and bool(comp) and brand in ("generico", "generica")


async def _fetch_full(product_id: int) -> Optional[dict]:
    resp = await bling_service.bling_request("GET", f"/produtos/{product_id}")
    if resp.status_code >= 400:
        return None
    body = resp.json()
    return body.get("data")


async def list_products_with_status(
    pagina: int = 1, limite: int = 50, filtro: str = "all", busca: str = ""
) -> dict:
    """Page through Bling products and tag each as enriched / not enriched.
    Filter values: 'all', 'enriched', 'not_enriched'."""
    params = {"pagina": pagina, "limite": min(max(limite, 1), 100)}
    if busca:
        params["pesquisa"] = busca
    resp = await bling_service.bling_request("GET", "/produtos", params=params)
    if resp.status_code >= 400:
        return {"items": [], "pagina": pagina, "error": resp.text[:200]}
    body = resp.json()
    items_raw = body.get("data") or []

    # The /produtos LIST does NOT return descriptions — we must fetch each item
    # individually to know its enrichment state. To stay under Bling's rate limit,
    # process sequentially (the global semaphore in bling_service already enforces this).
    enriched_items: List[dict] = []
    for it in items_raw:
        pid = it.get("id")
        full = await _fetch_full(pid) if pid else None
        product = full or it
        is_enr = _is_enriched(product)
        enriched_items.append({
            "id": pid,
            "codigo": product.get("codigo") or it.get("codigo") or "",
            "nome": product.get("nome") or it.get("nome") or "",
            "preco": product.get("preco") or it.get("preco") or 0,
            "situacao": product.get("situacao") or it.get("situacao") or "",
            "enriched": is_enr,
            "marca": product.get("marca") or "",
            "categoria_id": (product.get("categoria") or {}).get("id"),
        })

    if filtro == "enriched":
        enriched_items = [p for p in enriched_items if p["enriched"]]
    elif filtro == "not_enriched":
        enriched_items = [p for p in enriched_items if not p["enriched"]]

    return {
        "items": enriched_items,
        "pagina": pagina,
        "limite": limite,
        "filtro": filtro,
        "has_more": len(items_raw) >= params["limite"],
    }


# ---------------------------------------------------------------- job state

class BulkJob:
    def __init__(self) -> None:
        self.id: Optional[str] = None
        self.state: str = "idle"  # idle | running | stopped | done | error
        self.total: int = 0
        self.completed: int = 0
        self.success: int = 0
        self.errors: int = 0
        self.skipped: int = 0
        self.current_sku: Optional[str] = None
        self.current_name: Optional[str] = None
        self.started_at: Optional[str] = None
        self.finished_at: Optional[str] = None
        self.items: List[dict] = []  # per-product result log
        self._stop = False
        self._task: Optional[asyncio.Task] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "state": self.state,
            "total": self.total,
            "completed": self.completed,
            "success": self.success,
            "errors": self.errors,
            "skipped": self.skipped,
            "current_sku": self.current_sku,
            "current_name": self.current_name,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "items": self.items[-200:],  # cap returned list
        }


job = BulkJob()


# ---------------------------------------------------------------- runners

async def _enrich_one(product_id: int) -> dict:
    """Fetch a single product from Bling, then run the standard enrichment flow.
    Uses the Bling product's nome as title. raw_description comes from the
    `descricao` field if present, otherwise enrich_product_by_sku falls back to
    the persisted `product_raw` Mongo collection (saved by the JohnDrop bot)."""
    full = await _fetch_full(product_id)
    if not full:
        return {"ok": False, "reason": "produto não encontrado no Bling"}
    sku = (full.get("codigo") or "").strip()
    name = (full.get("nome") or "").strip()
    # Prefer the RAW description from JohnDrop (the only place variation text
    # lives). `descricaoCurta`/`descricaoComplementar` are already enriched and
    # don't contain the variation phrases.
    raw_desc = (full.get("descricao") or "").strip()
    if not sku:
        return {"ok": False, "reason": "produto sem SKU"}
    # Pass empty raw_desc when Bling has nothing — enrich_product_by_sku will
    # look up the persisted version from Mongo (product_raw collection).
    return await bling_enrichment.enrich_product_by_sku(sku, name, raw_desc)


async def _run_job(product_ids: List[int]) -> None:
    job.state = "running"
    job.total = len(product_ids)
    job.completed = 0
    job.success = 0
    job.errors = 0
    job.skipped = 0
    job.items = []
    job.started_at = datetime.now(timezone.utc).isoformat()
    job.finished_at = None
    await add_log("info", f"Enriquecimento em lote iniciado: {job.total} produtos")

    try:
        for pid in product_ids:
            if job._stop:
                job.state = "stopped"
                await add_log("warning", "Enriquecimento em lote interrompido pelo usuário")
                break
            # Preview current item
            preview = await _fetch_full(pid) or {}
            job.current_sku = (preview.get("codigo") or "").strip() or str(pid)
            job.current_name = (preview.get("nome") or "").strip()[:80]

            # NOTE: previously we skipped products already marked as enriched
            # (marca=Generica + descricaoCurta). That broke the use case of
            # re-running enrichment to CREATE VARIATIONS on previously-enriched
            # products. Now we always re-run — enrich_product_by_sku is
            # idempotent and rerunning gives a chance to create variations
            # from the persisted `product_raw` data.

            try:
                result = await _enrich_one(pid)
                if result.get("ok"):
                    job.success += 1
                    job.items.append({
                        "product_id": pid,
                        "sku": job.current_sku,
                        "name": job.current_name,
                        "status": "success",
                        "message": (
                            f"enriquecido + {result.get('variations_created', 0)} variações"
                            if result.get("variations_created") else "enriquecido"
                        ),
                    })
                else:
                    job.errors += 1
                    job.items.append({
                        "product_id": pid,
                        "sku": job.current_sku,
                        "name": job.current_name,
                        "status": "error",
                        "message": result.get("reason", "falhou"),
                    })
            except Exception as e:
                job.errors += 1
                job.items.append({
                    "product_id": pid,
                    "sku": job.current_sku,
                    "name": job.current_name,
                    "status": "error",
                    "message": str(e)[:200],
                })
                await add_log("error", f"Bulk enrich {pid}: {e}")

            job.completed += 1
        else:
            job.state = "done"
    except Exception as e:
        job.state = "error"
        await add_log("error", f"Bulk enrich crash: {e}")
    finally:
        job.current_sku = None
        job.current_name = None
        job.finished_at = datetime.now(timezone.utc).isoformat()
        await add_log(
            "info" if job.state == "done" else "warning",
            f"Enriquecimento em lote finalizado: {job.success} ok, {job.errors} erros, {job.skipped} pulados",
        )


async def start(product_ids: List[int]) -> dict:
    if job.state == "running":
        return {"ok": False, "reason": "job em execução"}
    if not product_ids:
        return {"ok": False, "reason": "lista vazia"}
    job.id = str(uuid.uuid4())
    job._stop = False
    job._task = asyncio.create_task(_run_job(product_ids))
    return {"ok": True, "job_id": job.id, "total": len(product_ids)}


async def stop() -> dict:
    if job.state != "running":
        return {"ok": False, "reason": "nenhum job em execução"}
    job._stop = True
    return {"ok": True}


async def list_recent_skus(limit: int = 50) -> dict:
    """List the N most recently registered SKUs.

    Pulls from the local MongoDB queues (`enrich_pending` and `product_raw`) — these
    are populated by the JohnDrop bot the moment a product is cadastrated. Joins
    each SKU with its current Bling state to flag enriched vs not-enriched.
    """
    from db import db as _db

    limit = max(1, min(int(limit or 50), 200))

    # Aggregate by SKU keeping the most recent timestamp. We merge two sources:
    #   - enrich_pending.queued_at (set when bot just registered the product)
    #   - product_raw.updated_at (set when raw description was persisted, also by bot)
    skus: dict = {}

    async for doc in _db.enrich_pending.find(
        {}, {"sku": 1, "raw_title": 1, "queued_at": 1, "status": 1, "product_id": 1, "_id": 0},
    ).sort("queued_at", -1).limit(limit * 2):
        sku = (doc.get("sku") or "").strip()
        if not sku:
            continue
        skus[sku] = {
            "sku": sku,
            "nome": doc.get("raw_title") or "",
            "queue_status": doc.get("status"),
            "product_id": doc.get("product_id"),
            "registered_at": doc.get("queued_at"),
            "source": "enrich_pending",
        }

    async for doc in _db.product_raw.find(
        {}, {"sku": 1, "raw_title": 1, "updated_at": 1, "_id": 0},
    ).sort("updated_at", -1).limit(limit * 2):
        sku = (doc.get("sku") or "").strip()
        if not sku:
            continue
        if sku not in skus:
            skus[sku] = {
                "sku": sku,
                "nome": doc.get("raw_title") or "",
                "queue_status": None,
                "product_id": None,
                "registered_at": doc.get("updated_at"),
                "source": "product_raw",
            }

    # Sort by registered_at desc
    ordered = sorted(
        skus.values(),
        key=lambda x: x.get("registered_at") or "",
        reverse=True,
    )[:limit]

    # Hydrate each entry with Bling product state (enriched / not / nome / preco)
    for entry in ordered:
        sku = entry["sku"]
        try:
            resp = await bling_service.bling_request(
                "GET", "/produtos", params={"codigo": sku, "limite": 5},
            )
            if resp.status_code >= 400:
                entry["enriched"] = False
                entry["bling_found"] = False
                continue
            items = (resp.json() or {}).get("data") or []
            target = next(
                (it for it in items
                 if (it.get("codigo") or "").strip().upper() == sku.upper()),
                None,
            )
            if not target:
                entry["enriched"] = False
                entry["bling_found"] = False
                continue
            entry["product_id"] = target.get("id")
            full = await _fetch_full(target["id"])
            if not full:
                entry["enriched"] = False
                entry["bling_found"] = True
                continue
            entry["bling_found"] = True
            entry["enriched"] = _is_enriched(full)
            entry["nome"] = full.get("nome") or entry.get("nome") or ""
            entry["preco"] = full.get("preco") or 0
            entry["marca"] = full.get("marca") or ""
            entry["situacao"] = full.get("situacao") or ""
        except Exception:
            entry["enriched"] = False
            entry["bling_found"] = False

    return {
        "items": ordered,
        "total": len(ordered),
        "limit": limit,
    }


async def collect_not_enriched_ids(max_items: int = 500) -> List[int]:
    """Walk through Bling pages collecting IDs of products that are NOT yet enriched."""
    ids: List[int] = []
    pagina = 1
    while pagina < 50 and len(ids) < max_items:
        data = await list_products_with_status(pagina=pagina, limite=100, filtro="not_enriched")
        for it in data.get("items", []):
            if it.get("id"):
                ids.append(int(it["id"]))
                if len(ids) >= max_items:
                    break
        if not data.get("has_more"):
            break
        pagina += 1
    return ids
