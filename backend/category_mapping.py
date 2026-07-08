"""Mapeamento de categorias multiloja Bling → Marketplaces.

ISOLADO. Não toca no fluxo de cadastro nem enriquecimento existentes.

Fluxo:
1. `scan_marketplace_trees()` — Playwright loga no Bling, abre o modal
   "Vincular categorias multiloja" e para cada marketplace conectado, extrai
   a árvore completa de categorias disponíveis (Amazon, Shopee, ML, etc).
2. `generate_suggestions()` — Para cada categoria Bling × cada marketplace,
   usa Claude para achar a categoria correspondente por similaridade
   semântica.
3. Persiste em `category_mapping_previews` para revisão via UI.
4. (fase 2) `apply_mappings()` — aplica os mapeamentos aprovados via Playwright.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import List, Optional

from db import db
from robot_service import add_log

logger = logging.getLogger(__name__)


async def _get_bling_categories_from_api() -> List[dict]:
    """Lista todas as categorias Bling via API (fonte de verdade)."""
    import bling_service
    out: List[dict] = []
    pagina = 1
    while pagina < 20:
        r = await bling_service.bling_request(
            "GET", "/categorias/produtos", params={"pagina": pagina, "limite": 100},
        )
        if r.status_code >= 400:
            break
        items = (r.json() or {}).get("data") or []
        if not items:
            break
        for it in items:
            out.append({
                "id": it.get("id"),
                "descricao": it.get("descricao") or "",
                "pai_id": (it.get("categoriaPai") or {}).get("id"),
            })
        if len(items) < 100:
            break
        pagina += 1
    return out


def _match_score(bling_name: str, mkt_name: str) -> float:
    """Score simples 0-1 por tokens compartilhados (fallback sem LLM)."""
    def _tokens(s: str) -> set:
        s = re.sub(r"[^\w\s]", " ", s.lower())
        stop = {"de", "da", "do", "para", "e", "em", "com", "a", "o"}
        return {t for t in s.split() if len(t) > 2 and t not in stop}
    a, b = _tokens(bling_name), _tokens(mkt_name)
    if not a or not b:
        return 0.0
    return len(a & b) / max(len(a | b), 1)


async def _llm_pick_best_match(
    bling_name: str, marketplace: str, candidates: List[dict]
) -> Optional[dict]:
    """Usa Claude para escolher a melhor categoria dentre candidatos."""
    if not candidates:
        return None
    # Top-N por score simples pra reduzir o prompt
    scored = sorted(
        [{**c, "_s": _match_score(bling_name, c.get("name", ""))} for c in candidates],
        key=lambda c: c["_s"], reverse=True,
    )
    shortlist = scored[:15]
    if not shortlist[0]["_s"]:
        # Nenhum overlap — retorna None para o usuário decidir
        return None
    try:
        import os
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        key = os.environ.get("EMERGENT_LLM_KEY", "")
        if not key:
            return {"id": shortlist[0].get("id"), "name": shortlist[0].get("name"),
                    "confidence": 0.5, "reason": "fallback (no LLM key)"}
        prompt = (
            f"Categoria Bling: '{bling_name}'.\n"
            f"Marketplace: {marketplace}.\n"
            f"Candidatos (id | nome):\n"
        )
        for i, c in enumerate(shortlist):
            prompt += f"  {i+1}. {c.get('id')} | {c.get('name')}\n"
        prompt += (
            "\nResponda APENAS com o número do melhor candidato e a confiança "
            "(0.0-1.0), no formato: NUMERO|CONFIANCA\n"
            "Ex: 3|0.85\n"
            "Se nenhum for adequado responda: 0|0.0"
        )
        chat = LlmChat(
            api_key=key, session_id=f"catmap-{marketplace}",
            system_message="Você é um especialista em taxonomia de e-commerce.",
        ).with_model("anthropic", "claude-haiku-4-5-20251001")
        reply = await chat.send_message(UserMessage(text=prompt))
        m = re.match(r"(\d+)\s*\|\s*([\d.]+)", (reply or "").strip())
        if not m:
            best = shortlist[0]
            return {"id": best.get("id"), "name": best.get("name"),
                    "confidence": best["_s"], "reason": f"llm parse falhou: {reply[:60]}"}
        idx = int(m.group(1)) - 1
        conf = float(m.group(2))
        if idx < 0 or idx >= len(shortlist):
            return None
        pick = shortlist[idx]
        return {"id": pick.get("id"), "name": pick.get("name"),
                "confidence": conf, "reason": "llm"}
    except Exception as e:
        logger.warning("LLM match failed for %s/%s: %s", marketplace, bling_name, e)
        best = shortlist[0]
        return {"id": best.get("id"), "name": best.get("name"),
                "confidence": best["_s"], "reason": f"fallback: {type(e).__name__}"}


async def generate_suggestions(mkt_trees: dict, bling_categories: List[dict]) -> dict:
    """Para cada categoria Bling × cada marketplace, gera sugestão."""
    total = len(bling_categories) * len(mkt_trees)
    done = 0
    started = datetime.now(timezone.utc).isoformat()
    await db.category_mapping_runs.update_one(
        {"name": "main"},
        {"$set": {"status": "matching", "started_at": started,
                  "total_pairs": total, "done": 0}},
        upsert=True,
    )
    # Reset previous previews for a fresh run
    await db.category_mapping_previews.delete_many({})

    for bling_cat in bling_categories:
        bling_name = bling_cat["descricao"]
        if not bling_name:
            continue
        for marketplace, tree in mkt_trees.items():
            candidates = tree or []
            suggestion = await _llm_pick_best_match(bling_name, marketplace, candidates)
            await db.category_mapping_previews.insert_one({
                "bling_category_id": bling_cat["id"],
                "bling_category_name": bling_name,
                "marketplace": marketplace,
                "suggestion_id": (suggestion or {}).get("id"),
                "suggestion_name": (suggestion or {}).get("name"),
                "confidence": (suggestion or {}).get("confidence", 0.0),
                "reason": (suggestion or {}).get("reason", "no_match"),
                "approved": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            done += 1
            if done % 10 == 0:
                await db.category_mapping_runs.update_one(
                    {"name": "main"}, {"$set": {"done": done}},
                )
    finished = datetime.now(timezone.utc).isoformat()
    await db.category_mapping_runs.update_one(
        {"name": "main"},
        {"$set": {"status": "done", "finished_at": finished, "done": done}},
    )
    await add_log("success", f"Mapeamento IA concluído: {done} pares gerados")
    return {"total": total, "done": done}


async def list_previews(marketplace: Optional[str] = None, limit: int = 500) -> List[dict]:
    q: dict = {}
    if marketplace:
        q["marketplace"] = marketplace
    out = []
    async for d in db.category_mapping_previews.find(q, {"_id": 0}).limit(limit):
        out.append(d)
    return out


async def approve_preview(
    bling_category_id: int, marketplace: str, new_suggestion_id: Optional[str] = None,
    approved: bool = True,
) -> dict:
    """Marca um preview como aprovado (ou muda a sugestão manualmente)."""
    update: dict = {"approved": approved,
                    "reviewed_at": datetime.now(timezone.utc).isoformat()}
    if new_suggestion_id:
        update["suggestion_id"] = new_suggestion_id
    r = await db.category_mapping_previews.update_one(
        {"bling_category_id": bling_category_id, "marketplace": marketplace},
        {"$set": update},
    )
    return {"ok": True, "modified": r.modified_count}


async def get_run_status() -> dict:
    doc = await db.category_mapping_runs.find_one({"name": "main"}, {"_id": 0})
    return doc or {"status": "idle"}
