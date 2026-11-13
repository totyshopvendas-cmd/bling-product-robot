"""Shared confirmation guard for supplier product submissions.

A clicked ``Criar Produto`` button is not evidence that the supplier accepted
its form. Both supplier robots must see a redirect or an explicit success
message before counting a cadastro as successful and scheduling Bling sync.
"""
from __future__ import annotations

from typing import Any


async def _feedback(page) -> dict:
    """Read the provider's visible toast/banner feedback without assuming UI markup."""
    try:
        data: Any = await page.evaluate(
            r"""() => {
                const selectors = ['.toast', '.alert', '.swal2-popup', '.notyf__message',
                    '[role="alert"]', '.error-message', '.invalid-feedback', '[role="status"]'];
                const messages = [];
                for (const selector of selectors) {
                    for (const el of document.querySelectorAll(selector)) {
                        const text = (el.innerText || el.textContent || '').trim();
                        if (text) messages.push(text.slice(0, 300));
                    }
                }
                const all = messages.join(' | ');
                const low = all.toLowerCase();
                const failed = /erro|inv[aá]lid|obrigat[oó]ri|duplicad|j[aá]\s+(?:cadastrad|exist|registrad)|conflito|falh/.test(low);
                const success = /produto\s+(?:criad|cadastrad|salv)|cadastro\s+(?:realizado|conclu[ií]d)|sucesso/.test(low);
                return { failed, success, message: all.slice(0, 300) };
            }"""
        )
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


async def confirm_supplier_submission(page, create_path_fragment: str = "createv2") -> dict:
    """Confirm a supplier create operation with redirect or success feedback.

    Returns ``{"confirmed": bool, "detail": str}``.  The caller should not
    queue a Bling enrichment job on a false result.
    """
    initial = await _feedback(page)
    if initial.get("failed"):
        return {"confirmed": False, "detail": initial.get("message") or "o fornecedor rejeitou o formulário"}
    if initial.get("success"):
        return {"confirmed": True, "detail": initial.get("message") or "cadastro confirmado pelo fornecedor"}

    redirected = False
    try:
        await page.wait_for_function(
            "fragment => !window.location.pathname.includes(fragment)",
            create_path_fragment,
            timeout=45000,
        )
        redirected = True
    except Exception:
        pass

    if redirected:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=20000)
        except Exception:
            pass
        return {"confirmed": True, "detail": "redirecionamento confirmado"}

    final = await _feedback(page)
    if final.get("success"):
        return {"confirmed": True, "detail": final.get("message") or "cadastro confirmado pelo fornecedor"}
    if final.get("failed"):
        return {"confirmed": False, "detail": final.get("message") or "o fornecedor rejeitou o formulário"}
    return {
        "confirmed": False,
        "detail": "o fornecedor não confirmou o cadastro nem redirecionou a página",
    }
