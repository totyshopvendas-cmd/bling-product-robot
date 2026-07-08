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


async def list_marketplaces() -> List[str]:
    """Retorna nomes de marketplaces já escaneados (a partir de category_mapping_trees)."""
    out: List[str] = []
    async for d in db.category_mapping_trees.find({}, {"_id": 0, "marketplace": 1}):
        m = d.get("marketplace")
        if m and m not in out:
            out.append(m)
    return sorted(out)


# --- Marketplaces via Bling API v3 (não depende de Playwright) --------------
_LOJA_NAME_HINTS = {
    "MLB": "Mercado Livre", "MLA": "Mercado Livre (AR)",
    "AMZ": "Amazon", "AMAZON": "Amazon",
    "SHP": "Shopee", "SHOPEE": "Shopee",
    "MGL": "Magalu", "MAGALU": "Magalu",
    "AME": "Americanas", "B2W": "B2W",
    "TRAY": "Tray", "NUV": "Nuvemshop", "NUVEM": "Nuvemshop",
    "KWAI": "Kwai Shop", "TIK": "TikTok Shop",
    "FB": "Facebook", "IG": "Instagram",
}


def _guess_marketplace_name(codigo: str) -> str:
    if not codigo:
        return "Loja"
    c = codigo.upper()
    for prefix, name in _LOJA_NAME_HINTS.items():
        if c.startswith(prefix):
            return name
    if c.isdigit() and len(c) >= 10:
        return "Shopee"  # códigos numéricos longos são padrão Shopee
    return f"Loja ({c[:6]})"


async def add_known_loja(loja_id: int, name: str, sample_code: str = "") -> dict:
    """Registra manualmente uma loja Bling que ainda não tem vínculos de categoria."""
    now = datetime.now(timezone.utc).isoformat()
    await db.category_mapping_known_lojas.update_one(
        {"loja_id": loja_id},
        {"$set": {"loja_id": loja_id, "name": name.strip(),
                  "sample_code": sample_code.strip(), "added_at": now}},
        upsert=True,
    )
    # Também grava como alias para prevalecer sobre heurística
    await set_loja_alias(loja_id, name.strip())
    return {"ok": True, "loja_id": loja_id, "name": name.strip()}


async def list_known_lojas() -> List[dict]:
    out: List[dict] = []
    async for d in db.category_mapping_known_lojas.find({}, {"_id": 0}):
        out.append(d)
    return out


async def list_bling_lojas() -> List[dict]:
    """Lista marketplaces conectados ao Bling via API `/categorias/lojas`.

    Não depende de Playwright. Agrupa por `loja.id`, infere nome pelo
    prefixo do código e conta quantos vínculos já existem.
    """
    import bling_service
    aggregated: dict = {}
    pagina = 1
    while pagina < 50:  # safety cap
        r = await bling_service.bling_request(
            "GET", "/categorias/lojas", params={"pagina": pagina, "limite": 100},
        )
        if r.status_code >= 400:
            break
        items = (r.json() or {}).get("data") or []
        if not items:
            break
        for it in items:
            loja_id = (it.get("loja") or {}).get("id")
            if not loja_id:
                continue
            entry = aggregated.setdefault(loja_id, {
                "loja_id": loja_id,
                "mapping_count": 0,
                "sample_code": it.get("codigo") or "",
                "linked_bling_ids": set(),
            })
            entry["mapping_count"] += 1
            cat_id = (it.get("categoriaProduto") or {}).get("id")
            if cat_id:
                entry["linked_bling_ids"].add(cat_id)
            if not entry["sample_code"]:
                entry["sample_code"] = it.get("codigo") or ""
        if len(items) < 100:
            break
        pagina += 1

    out: List[dict] = []
    aliases = await get_loja_aliases()
    known = await list_known_lojas()
    known_by_id = {k["loja_id"]: k for k in known}

    # Adiciona lojas manuais que NÃO apareceram na API (sem vínculos ainda)
    for k in known:
        if k["loja_id"] not in aggregated:
            aggregated[k["loja_id"]] = {
                "loja_id": k["loja_id"],
                "mapping_count": 0,
                "sample_code": k.get("sample_code", ""),
                "linked_bling_ids": set(),
            }

    for loja_id, e in aggregated.items():
        default_name = _guess_marketplace_name(e["sample_code"])
        alias = aliases.get(loja_id) or (known_by_id.get(loja_id) or {}).get("name")
        out.append({
            "loja_id": loja_id,
            "name": alias or default_name,
            "default_name": default_name,
            "custom_alias": bool(alias),
            "manual": loja_id in known_by_id,
            "sample_code": e["sample_code"],
            "mapping_count": e["mapping_count"],
            "linked_count": len(e["linked_bling_ids"]),
        })
    out.sort(key=lambda x: x["name"])
    return out


async def list_gaps() -> dict:
    """Categorias Bling que estão SEM vínculo em alguma loja conectada.

    Retorna {lojas: [...], gaps: {loja_id: [{id, descricao}]}}.
    """
    lojas = await list_bling_lojas()
    all_cats = await _get_bling_categories_from_api()

    # Para cada loja, quais Bling category ids já têm link?
    linked_by_loja: dict = {loja["loja_id"]: set() for loja in lojas}
    import bling_service
    pagina = 1
    while pagina < 50:
        r = await bling_service.bling_request(
            "GET", "/categorias/lojas", params={"pagina": pagina, "limite": 100},
        )
        if r.status_code >= 400 or not (r.json() or {}).get("data"):
            break
        for it in r.json()["data"]:
            lid = (it.get("loja") or {}).get("id")
            cid = (it.get("categoriaProduto") or {}).get("id")
            if lid in linked_by_loja and cid:
                linked_by_loja[lid].add(cid)
        if len(r.json()["data"]) < 100:
            break
        pagina += 1

    gaps: dict = {}
    for loja in lojas:
        lid = loja["loja_id"]
        missing = [
            {"id": c["id"], "descricao": c["descricao"]}
            for c in all_cats if c["id"] not in linked_by_loja.get(lid, set())
        ]
        gaps[lid] = missing
    return {
        "lojas": lojas,
        "total_bling_categories": len(all_cats),
        "gaps_by_loja": gaps,
    }


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


async def _get_cached_trees() -> dict:
    """Retorna as árvores de marketplace salvas em `category_mapping_trees`."""
    trees: dict = {}
    async for d in db.category_mapping_trees.find({}, {"_id": 0}):
        trees[d["marketplace"]] = d.get("categories") or []
    return trees


async def get_new_bling_categories() -> List[dict]:
    """Categorias Bling que ainda NÃO possuem preview em nenhum marketplace."""
    all_cats = await _get_bling_categories_from_api()
    if not all_cats:
        return []
    known_ids = set()
    async for p in db.category_mapping_previews.find(
        {}, {"_id": 0, "bling_category_id": 1},
    ):
        known_ids.add(p.get("bling_category_id"))
    return [c for c in all_cats if c["id"] not in known_ids]


async def map_single_category(
    bling_cat: dict, mkt_trees: dict, auto_approve: bool = True,
) -> int:
    """Gera sugestões IA para UMA categoria Bling contra todos os marketplaces.

    Retorna o nº de previews criados. Se `auto_approve=True` marca aprovado
    para permitir aplicação imediata via Playwright.
    """
    if not mkt_trees:
        return 0
    bling_name = bling_cat.get("descricao") or ""
    if not bling_name:
        return 0
    created = 0
    now = datetime.now(timezone.utc).isoformat()
    for marketplace, tree in mkt_trees.items():
        # Skip if already exists (idempotente)
        existing = await db.category_mapping_previews.find_one({
            "bling_category_id": bling_cat["id"],
            "marketplace": marketplace,
        })
        if existing:
            continue
        suggestion = await _llm_pick_best_match(bling_name, marketplace, tree or [])
        await db.category_mapping_previews.insert_one({
            "bling_category_id": bling_cat["id"],
            "bling_category_name": bling_name,
            "marketplace": marketplace,
            "suggestion_id": (suggestion or {}).get("id"),
            "suggestion_name": (suggestion or {}).get("name"),
            "confidence": (suggestion or {}).get("confidence", 0.0),
            "reason": (suggestion or {}).get("reason", "no_match"),
            "approved": bool(auto_approve and (suggestion or {}).get("id")),
            "applied": False,
            "created_at": now,
            "auto_synced": True,
        })
        created += 1
    return created


async def sync_new_categories(
    bling_user: str, bling_pass: str, apply: bool = True,
) -> dict:
    """Detecta categorias Bling novas, gera mapeamentos IA e (opcional) aplica.

    Fluxo:
    1. Consulta API Bling → lista categorias novas (sem preview).
    2. Usa árvores de marketplace já cacheadas em `category_mapping_trees`.
       Se não houver cache, dispara um scan primeiro.
    3. Para cada nova categoria, roda LLM em cada marketplace, salva approved.
    4. Se `apply=True`, chama Playwright para aplicar os mapeamentos no Bling.
    """
    from db import db as _db
    started = datetime.now(timezone.utc).isoformat()
    await _db.category_mapping_runs.update_one(
        {"name": "auto_sync"},
        {"$set": {"status": "running", "started_at": started, "phase": "detect"}},
        upsert=True,
    )

    trees = await _get_cached_trees()
    if not trees:
        await add_log(
            "info",
            "AutoSync: nenhum cache de árvores — executando scan primeiro",
        )
        await _db.category_mapping_runs.update_one(
            {"name": "auto_sync"}, {"$set": {"phase": "scanning_trees"}},
        )
        import category_mapping_bot
        trees = await category_mapping_bot.scan_marketplace_trees(bling_user, bling_pass)

    new_cats = await get_new_bling_categories()
    await add_log(
        "info", f"AutoSync: {len(new_cats)} categorias novas detectadas",
    )
    if not new_cats:
        await _db.category_mapping_runs.update_one(
            {"name": "auto_sync"},
            {"$set": {"status": "done", "finished_at": datetime.now(timezone.utc).isoformat(),
                      "new_count": 0, "applied_count": 0}},
        )
        return {"new_count": 0, "created_pairs": 0, "applied": 0}

    await _db.category_mapping_runs.update_one(
        {"name": "auto_sync"},
        {"$set": {"phase": "matching", "new_count": len(new_cats)}},
    )
    created_total = 0
    for cat in new_cats:
        created_total += await map_single_category(cat, trees, auto_approve=True)
    await add_log(
        "success",
        f"AutoSync: {created_total} pares gerados para {len(new_cats)} categorias novas",
    )

    applied_count = 0
    if apply:
        await _db.category_mapping_runs.update_one(
            {"name": "auto_sync"}, {"$set": {"phase": "applying"}},
        )
        try:
            import category_mapping_bot
            # Aplica somente os previews desta rodada (não aplicados, approved)
            new_cat_ids = [c["id"] for c in new_cats]
            applied_count = await category_mapping_bot.apply_mappings_for_categories(
                bling_user, bling_pass, new_cat_ids,
            )
        except Exception as e:
            logger.exception("apply failed: %s", e)
            await add_log("error", f"AutoSync apply falhou: {e}")

    await _db.category_mapping_runs.update_one(
        {"name": "auto_sync"},
        {"$set": {"status": "done",
                  "finished_at": datetime.now(timezone.utc).isoformat(),
                  "new_count": len(new_cats), "created_pairs": created_total,
                  "applied_count": applied_count, "phase": "done"}},
    )
    return {
        "new_count": len(new_cats),
        "created_pairs": created_total,
        "applied": applied_count,
    }


async def get_auto_sync_status() -> dict:
    doc = await db.category_mapping_runs.find_one({"name": "auto_sync"}, {"_id": 0})
    return doc or {"status": "idle"}


async def count_pending_new() -> int:
    """Quantas categorias Bling ainda não têm mapeamento."""
    cats = await get_new_bling_categories()
    return len(cats)



# =========================================================================
# API-BASED AUTO SYNC (não depende de Playwright)
# =========================================================================
async def _fetch_existing_mappings() -> List[dict]:
    """Todos os vínculos Bling→marketplace já existentes."""
    import bling_service
    out: List[dict] = []
    pagina = 1
    while pagina < 50:
        r = await bling_service.bling_request(
            "GET", "/categorias/lojas", params={"pagina": pagina, "limite": 100},
        )
        if r.status_code >= 400:
            break
        items = (r.json() or {}).get("data") or []
        if not items:
            break
        out.extend(items)
        if len(items) < 100:
            break
        pagina += 1
    return out


async def _llm_pick_from_existing(
    bling_name: str, loja_name: str, existing: List[dict],
) -> Optional[dict]:
    """Escolhe a melhor categoria marketplace EXISTENTE para reutilizar.

    Como não temos a árvore completa do marketplace, reusamos códigos de
    vínculos já criados no Bling. Se o marketplace tem 'Camisetas' → codigo
    17682366011, e uma nova categoria Bling 'Camiseta Manga Longa' surgir,
    pode reutilizar o mesmo código.
    """
    if not existing:
        return None
    # Filtrar candidatos por loja
    candidates = [
        {"codigo": e.get("codigo"), "descricao": e.get("descricao") or ""}
        for e in existing
        if e.get("codigo") and e.get("descricao")
    ]
    # Dedupe por (codigo, descricao)
    seen = set()
    uniq = []
    for c in candidates:
        k = (c["codigo"], c["descricao"])
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    if not uniq:
        return None

    scored = sorted(
        [{**c, "_s": _match_score(bling_name, c["descricao"])} for c in uniq],
        key=lambda c: c["_s"], reverse=True,
    )
    # Não descartamos por score 0 — deixamos a LLM decidir sobre TODOS os candidatos.
    shortlist = scored[:12]
    # LLM refina escolha entre top-12
    try:
        import os
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        key = os.environ.get("EMERGENT_LLM_KEY", "")
        if not key:
            best = shortlist[0]
            return {"codigo": best["codigo"], "descricao": best["descricao"],
                    "confidence": best["_s"], "reason": "score fallback"}
        prompt = (
            f"Categoria Bling: '{bling_name}'\n"
            f"Loja: {loja_name}\n"
            f"Candidatos (id | descrição):\n"
        )
        for i, c in enumerate(shortlist):
            prompt += f"  {i+1}. {c['codigo']} | {c['descricao']}\n"
        prompt += (
            "\nResponda APENAS: NUMERO|CONFIANCA (0-1). Ex: 3|0.85\n"
            "Se nenhum for adequado responda: 0|0.0"
        )
        chat = LlmChat(
            api_key=key, session_id=f"catmap-api-{loja_name}",
            system_message="Você é um especialista em taxonomia de e-commerce.",
        ).with_model("anthropic", "claude-haiku-4-5-20251001")
        reply = await chat.send_message(UserMessage(text=prompt))
        m = re.match(r"(\d+)\s*\|\s*([\d.]+)", (reply or "").strip())
        if not m:
            best = shortlist[0]
            return {"codigo": best["codigo"], "descricao": best["descricao"],
                    "confidence": best["_s"], "reason": "llm parse fail"}
        idx = int(m.group(1)) - 1
        conf = float(m.group(2))
        if idx < 0 or idx >= len(shortlist):
            return None
        pick = shortlist[idx]
        return {"codigo": pick["codigo"], "descricao": pick["descricao"],
                "confidence": conf, "reason": "llm"}
    except Exception as e:
        logger.warning("LLM pick failed: %s", e)
        best = shortlist[0]
        return {"codigo": best["codigo"], "descricao": best["descricao"],
                "confidence": best["_s"], "reason": f"fallback:{type(e).__name__}"}


async def sync_via_api(
    include_subcategorias: bool = True,
    dry_run: bool = False,
) -> dict:
    """Fluxo 100% API Bling (sem Playwright).

    1. Lista todas as categorias Bling (raiz + subs, ou só raiz).
    2. Lista lojas conectadas + mapeamentos existentes.
    3. Para cada (categoria sem vínculo, loja): LLM escolhe o melhor
       `codigo` reaproveitando vínculos já criados no Bling.
    4. POST /categorias/lojas cria o vínculo.
    """
    import bling_service
    from db import db as _db
    started = datetime.now(timezone.utc).isoformat()
    await _db.category_mapping_runs.update_one(
        {"name": "api_sync"},
        {"$set": {"status": "running", "started_at": started, "phase": "fetching"}},
        upsert=True,
    )

    all_cats = await _get_bling_categories_from_api()
    if not include_subcategorias:
        all_cats = [c for c in all_cats if not c.get("pai_id")]

    lojas = await list_bling_lojas()
    existing = await _fetch_existing_mappings()

    # Mapa: loja_id → set(bling_category_ids já vinculadas)
    linked: dict = {loja["loja_id"]: set() for loja in lojas}
    # Mapa: loja_id → lista de {codigo, descricao} existentes (para reuso)
    existing_by_loja: dict = {loja["loja_id"]: [] for loja in lojas}
    for e in existing:
        lid = (e.get("loja") or {}).get("id")
        cid = (e.get("categoriaProduto") or {}).get("id")
        if lid in linked and cid:
            linked[lid].add(cid)
            existing_by_loja[lid].append(e)

    total_pairs = 0
    created = 0
    skipped_no_ref = 0
    errors = 0
    await _db.category_mapping_runs.update_one(
        {"name": "api_sync"},
        {"$set": {"phase": "mapping", "total_pairs": 0}},
    )

    now = datetime.now(timezone.utc).isoformat()
    for cat in all_cats:
        for loja in lojas:
            lid = loja["loja_id"]
            if cat["id"] in linked[lid]:
                continue  # já vinculada
            total_pairs += 1
            if not existing_by_loja[lid]:
                # loja sem nenhum vínculo prévio → não temos código de referência
                skipped_no_ref += 1
                await _db.category_mapping_previews.update_one(
                    {"bling_category_id": cat["id"], "marketplace": loja["name"]},
                    {"$set": {
                        "bling_category_id": cat["id"],
                        "bling_category_name": cat["descricao"],
                        "marketplace": loja["name"],
                        "loja_id": lid,
                        "suggestion_id": None,
                        "suggestion_name": None,
                        "confidence": 0.0,
                        "reason": "loja sem vínculos de referência",
                        "approved": False,
                        "applied": False,
                        "auto_synced": True,
                        "created_at": now,
                    }}, upsert=True,
                )
                continue
            pick = await _llm_pick_from_existing(
                cat["descricao"], loja["name"], existing_by_loja[lid],
            )
            if not pick or not pick.get("codigo"):
                await _db.category_mapping_previews.update_one(
                    {"bling_category_id": cat["id"], "marketplace": loja["name"]},
                    {"$set": {
                        "bling_category_id": cat["id"],
                        "bling_category_name": cat["descricao"],
                        "marketplace": loja["name"],
                        "loja_id": lid,
                        "suggestion_id": None,
                        "confidence": 0.0,
                        "reason": (pick or {}).get("reason", "sem match IA"),
                        "approved": False,
                        "applied": False,
                        "auto_synced": True,
                        "created_at": now,
                    }}, upsert=True,
                )
                continue

            preview_doc = {
                "bling_category_id": cat["id"],
                "bling_category_name": cat["descricao"],
                "marketplace": loja["name"],
                "loja_id": lid,
                "suggestion_id": pick["codigo"],
                "suggestion_name": pick["descricao"],
                "confidence": pick.get("confidence", 0.0),
                "reason": pick.get("reason", "llm"),
                "approved": True,
                "applied": False,
                "auto_synced": True,
                "created_at": now,
            }

            if not dry_run:
                try:
                    body = {
                        "descricao": pick["descricao"],
                        "codigo": pick["codigo"],
                        "loja": {"id": lid},
                        "categoriaProduto": {"id": cat["id"]},
                    }
                    resp = await bling_service.bling_request(
                        "POST", "/categorias/lojas", json=body,
                    )
                    if resp.status_code < 300:
                        preview_doc["applied"] = True
                        preview_doc["applied_at"] = datetime.now(timezone.utc).isoformat()
                        created += 1
                    else:
                        errors += 1
                        preview_doc["last_error"] = resp.text[:300]
                except Exception as e:
                    errors += 1
                    preview_doc["last_error"] = str(e)[:300]

            await _db.category_mapping_previews.update_one(
                {"bling_category_id": cat["id"], "marketplace": loja["name"]},
                {"$set": preview_doc}, upsert=True,
            )

        if total_pairs % 20 == 0:
            await _db.category_mapping_runs.update_one(
                {"name": "api_sync"},
                {"$set": {"total_pairs": total_pairs, "created": created,
                          "errors": errors, "skipped": skipped_no_ref}},
            )

    await _db.category_mapping_runs.update_one(
        {"name": "api_sync"},
        {"$set": {"status": "done",
                  "finished_at": datetime.now(timezone.utc).isoformat(),
                  "phase": "done", "total_pairs": total_pairs,
                  "created": created, "errors": errors,
                  "skipped": skipped_no_ref,
                  "include_subcategorias": include_subcategorias}},
    )
    await add_log(
        "success" if not errors else "warning",
        f"API-Sync: {created} vínculos criados, {errors} erros, "
        f"{skipped_no_ref} sem referência (de {total_pairs} pares)",
    )
    return {
        "total_pairs": total_pairs,
        "created": created,
        "errors": errors,
        "skipped_no_ref": skipped_no_ref,
    }


async def get_api_sync_status() -> dict:
    doc = await db.category_mapping_runs.find_one({"name": "api_sync"}, {"_id": 0})
    return doc or {"status": "idle"}


# =========================================================================
# Loja aliases (renomeação amigável para lojas com códigos numéricos)
# =========================================================================
async def set_loja_alias(loja_id: int, alias: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    await db.category_mapping_loja_aliases.update_one(
        {"loja_id": loja_id},
        {"$set": {"loja_id": loja_id, "alias": alias.strip(), "updated_at": now}},
        upsert=True,
    )
    return {"ok": True, "loja_id": loja_id, "alias": alias.strip()}


async def get_loja_aliases() -> dict:
    out: dict = {}
    async for d in db.category_mapping_loja_aliases.find({}, {"_id": 0}):
        out[d["loja_id"]] = d.get("alias") or ""
    return out
