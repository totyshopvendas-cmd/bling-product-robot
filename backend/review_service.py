"""Revisão em lote de produtos já cadastrados no Bling (itens 6 e 7).

Varre o catálogo Bling e re-aplica, em cada produto:
  • Item 4 — marca (fabricante real ou nome da loja, conforme Configurações → Marca)
  • Item 5 — atributos de marketplace (modelo, material, cor, voltagem,
    dimensões, palavras-chave) via IA, anexados como 'Ficha Técnica'.

O job roda em background com:
  • barra de progresso (completed / total)
  • pausa e retomada
  • estimativa de custo de IA ANTES de começar
  • lista de produtos para aprovação antes de qualquer exclusão (item 6)

Estado é mantido em memória (apenas um job de revisão por vez).
"""
import asyncio
import uuid
from datetime import datetime, timezone
from typing import Optional, List

import bling_service
import bling_enrichment
from robot_service import add_log


class ReviewJob:
    def __init__(self) -> None:
        self.id: Optional[str] = None
        self.state: str = "idle"  # idle | running | paused | done | error | stopped
        self.total: int = 0
        self.completed: int = 0
        self.success: int = 0
        self.errors: int = 0
        self.skipped: int = 0
        self.current_sku: Optional[str] = None
        self.current_name: Optional[str] = None
        self.started_at: Optional[str] = None
        self.finished_at: Optional[str] = None
        self.items: List[dict] = []
        # Produtos marcados para exclusão (estrutura de variação errada).
        # Só são excluídos depois que o usuário aprova na tela.
        self.pending_delete: List[dict] = []
        self.delete_approved: bool = False
        self._pause = False
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
            "items": self.items[-200:],
            "pending_delete": self.pending_delete,
            "delete_approved": self.delete_approved,
        }


job = ReviewJob()


# ---------------------------------------------------------------- estimativa


async def _count_bling_products() -> int:
    """Contém produtos no Bling (paginação simples, só para a estimativa)."""
    total = 0
    pagina = 1
    while pagina < 100:
        resp = await bling_service.bling_request(
            "GET", "/produtos", params={"pagina": pagina, "limite": 100},
        )
        if resp.status_code >= 400:
            break
        items = (resp.json() or {}).get("data") or []
        if not items:
            break
        total += len(items)
        if len(items) < 100:
            break
        pagina += 1
        await asyncio.sleep(0.2)
    return total


async def estimate_cost(max_items: Optional[int] = None) -> dict:
    """Estima quantos produtos serão revisados e o custo aproximado de IA.

    Cada produto consome até ~4 chamadas de IA (marca, descrição curta,
    8 bullets, atributos) + leitura do produto no Bling.
    """
    total = await _count_bling_products()
    if max_items:
        total = min(total, max_items)
    # Chamadas de IA por produto (no pior caso todas falham e são re-tentadas)
    ai_calls_per_product = 4
    return {
        "produtos": total,
        "chamadas_ia_estimadas": total * ai_calls_per_product,
        "max_items": max_items,
        "nota": "Estimativa aproximada. O custo real depende do provedor/modelo de IA configurado.",
    }


# ---------------------------------------------------------------- execução


async def _fetch_full(product_id: int) -> Optional[dict]:
    resp = await bling_service.bling_request("GET", f"/produtos/{product_id}")
    if resp.status_code >= 400:
        return None
    return (resp.json() or {}).get("data")


async def _run_job(product_ids: List[int], max_items: Optional[int] = None) -> None:
    job.state = "running"
    job.total = len(product_ids)
    job.completed = 0
    job.success = 0
    job.errors = 0
    job.skipped = 0
    job.items = []
    job.started_at = datetime.now(timezone.utc).isoformat()
    job.finished_at = None
    job.pending_delete = []
    job.delete_approved = False
    await add_log("info", f"Revisão em lote iniciada: {job.total} produtos")

    try:
        for pid in product_ids:
            # respeita parada
            if job._stop:
                job.state = "stopped"
                await add_log("warning", "Revisão interrompida pelo usuário")
                break

            # respeita pausa (bloqueia até ser retomada)
            while job._pause and not job._stop:
                job.state = "paused"
                await add_log("info", "Revisão pausada — aguardando retomada...")
                await asyncio.sleep(3)
            job.state = "running"

            preview = await _fetch_full(pid) or {}
            job.current_sku = (preview.get("codigo") or "").strip() or str(pid)
            job.current_name = (preview.get("nome") or "").strip()[:80]

            sku = job.current_sku
            if not sku:
                job.skipped += 1
                job.completed += 1
                continue

            try:
                raw_desc = (preview.get("descricao") or "").strip()
                result = await bling_enrichment.enrich_product_by_sku(
                    sku, (preview.get("nome") or "").strip(), raw_desc,
                )
                if result.get("ok"):
                    job.success += 1
                    job.items.append({
                        "product_id": pid, "sku": sku, "name": job.current_name,
                        "status": "success",
                        "message": "marca + atributos revisados",
                    })
                else:
                    job.errors += 1
                    job.items.append({
                        "product_id": pid, "sku": sku, "name": job.current_name,
                        "status": "error", "message": result.get("reason", "falhou"),
                    })
            except Exception as e:
                job.errors += 1
                job.items.append({
                    "product_id": pid, "sku": sku, "name": job.current_name,
                    "status": "error", "message": str(e)[:200],
                })
                await add_log("error", f"Review {pid}: {e}")

            job.completed += 1
        else:
            job.state = "done"
    except Exception as e:
        job.state = "error"
        await add_log("error", f"Revisão em lote falhou: {e}")
    finally:
        job.current_sku = None
        job.current_name = None
        job.finished_at = datetime.now(timezone.utc).isoformat()
        await add_log(
            "info" if job.state == "done" else "warning",
            f"Revisão finalizada: {job.success} ok, {job.errors} erros, {job.skipped} pulados",
        )


async def start(product_ids: List[int]) -> dict:
    if job.state in ("running", "paused"):
        return {"ok": False, "reason": "job em execução ou pausado"}
    if not product_ids:
        return {"ok": False, "reason": "lista vazia"}
    job.id = str(uuid.uuid4())
    job._stop = False
    job._pause = False
    job._task = asyncio.create_task(_run_job(product_ids))
    return {"ok": True, "job_id": job.id, "total": len(product_ids)}


async def pause() -> dict:
    if job.state != "running":
        return {"ok": False, "reason": "nenhuma revisão em execução"}
    job._pause = True
    return {"ok": True}


async def resume() -> dict:
    if job.state != "paused":
        return {"ok": False, "reason": "revisão não está pausada"}
    job._pause = False
    job.state = "running"
    return {"ok": True}


async def stop() -> dict:
    if job.state not in ("running", "paused"):
        return {"ok": False, "reason": "nenhuma revisão em execução"}
    job._stop = True
    job._pause = False
    return {"ok": True}


async def mark_for_delete(product_id: int, sku: str, name: str, reason: str) -> dict:
    """Adiciona um produto à lista de exclusão pendente (item 6).
    A exclusão só acontece após aprovação explícita do usuário."""
    for item in job.pending_delete:
        if item.get("sku") == sku:
            return {"ok": False, "reason": "já está na lista de exclusão"}
    job.pending_delete.append({
        "product_id": product_id, "sku": sku, "name": name, "reason": reason,
    })
    await add_log("warning", f"Produto marcado para exclusão (pendente aprovação): {sku} — {reason}")
    return {"ok": True}


async def approve_deletions() -> dict:
    """Marca as exclusões pendentes como aprovadas. A exclusão física é feita
    pelo front/backend só depois desta confirmação (item 6: aprovação prévia)."""
    if not job.pending_delete:
        return {"ok": False, "reason": "nenhuma exclusão pendente"}
    job.delete_approved = True
    return {"ok": True, "total": len(job.pending_delete)}


async def clear_delete_list() -> dict:
    job.pending_delete = []
    job.delete_approved = False
    return {"ok": True}


async def status() -> dict:
    return job.to_dict()
