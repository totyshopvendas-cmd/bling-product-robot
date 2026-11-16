"""Shared confirmation guard for supplier product submissions.

A clicked ``Criar Produto`` button is not evidence that the supplier accepted
its form. Both supplier robots need a redirect, explicit success message, or a
strict read-only reconciliation of the supplier catalog before counting a
cadastro as successful and scheduling Bling sync.
"""
from __future__ import annotations

import re
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
                // Supplier pages sometimes leave the form in place and only
                // mark its invalid fields. Capture those labels so the robot
                // log explains why a cadastro was rejected instead of only
                // saying that no redirect happened.
                const invalid = Array.from(document.querySelectorAll(
                    ':invalid, [aria-invalid="true"], .is-invalid, .invalid-feedback, .field-error'
                )).map((el) => {
                    const labels = el.labels && el.labels.length
                        ? Array.from(el.labels).map((label) => label.innerText || label.textContent).join(' ')
                        : '';
                    const nearby = el.closest('label, .form-group, .field, .form-field')?.innerText || '';
                    const own = el.validationMessage || el.getAttribute('aria-label') || el.getAttribute('name') || el.id || '';
                    return `${labels || nearby || own}`.trim().replace(/\s+/g, ' ').slice(0, 160);
                }).filter(Boolean);
                for (const detail of invalid) {
                    if (!messages.includes(detail)) messages.push(detail);
                }
                const all = messages.join(' | ');
                const low = all.toLowerCase();
                const failed = invalid.length > 0
                    || /erro|inv[aá]lid|obrigat[oó]ri|duplicad|j[aá]\s+(?:cadastrad|exist|registrad)|conflito|falh/.test(low);
                const success = /produto\s+(?:criad|cadastrad|salv)|cadastro\s+(?:realizado|conclu[ií]d)|sucesso/.test(low);
                return { failed, success, message: all.slice(0, 300) };
            }"""
        )
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


_CATALOG_FILTER_STATE_PROBE = r"""
() => {
    const normalize = (value) => (value || '')
        .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
        .toLowerCase().replace(/[^a-z0-9]+/g, '');
    const selectedNotRegistered = Array.from(document.querySelectorAll('select')).some((select) => {
        const option = select.options && select.options[select.selectedIndex];
        return normalize(option?.textContent).includes('naocadastrei');
    });
    const urlFilter = new URL(window.location.href).searchParams.get('integration_filter') === 'without_integration';
    return {
        catalog: window.location.pathname.includes('/dashboard/catalog'),
        without_integration_filter: Boolean(urlFilter || selectedNotRegistered),
    };
}
"""


async def _catalog_filter_state(page) -> dict:
    try:
        state = await page.evaluate(_CATALOG_FILTER_STATE_PROBE)
    except Exception:
        return {"catalog": False, "without_integration_filter": False}
    state = state if isinstance(state, dict) else {}
    return {
        "catalog": bool(state.get("catalog")),
        "without_integration_filter": bool(state.get("without_integration_filter")),
    }


_CATALOG_EMPTY_STATE_PROBE = r"""
() => {
    const text = (document.querySelector('main, .content-page, .content, body')?.innerText || '')
        .replace(/\s+/g, ' ').trim().toLowerCase();
    const explicitEmpty = Boolean(document.querySelector(
        '[data-empty-state], .empty-state, .no-results, .no-data, .empty-results'
    ));
    const emptyWords = /nenhum\s+(?:produto|resultado|item)|n[aã]o\s+foram\s+encontrad|sem\s+(?:produtos|resultados|itens)/.test(text);
    return { readable_empty: Boolean(explicitEmpty || emptyWords) };
}
"""


async def _catalog_content_is_readable(page, cards: list) -> bool:
    """Require cards or an explicit empty state before trusting an absence.

    An empty Playwright selector list alone is ambiguous: the supplier catalog
    may still be loading, its markup may have changed, or its list may actually
    be empty. Only a visible empty-state indication makes the latter safe.
    """
    if cards:
        return True
    try:
        state = await page.evaluate(_CATALOG_EMPTY_STATE_PROBE)
    except Exception:
        return False
    return bool(isinstance(state, dict) and state.get("readable_empty"))


async def _card_reference(card) -> str:
    """Read only source identifiers exposed by a catalog control."""
    try:
        reference = await card.evaluate(
            """el => [
                el.getAttribute('href'), el.getAttribute('data-href'),
                el.getAttribute('data-url'), el.getAttribute('data-id'),
                el.getAttribute('id'), el.getAttribute('onclick'),
                (el.outerHTML || '').slice(0, 1600),
            ].filter(Boolean).join(' ')"""
        )
        return str(reference or "")
    except Exception:
        return ""


def _reference_contains_source(reference: str, href: str, key: str) -> bool:
    """Match an exact create-form source ID without accepting a prefix."""
    normalized = str(reference or "")
    if href and href in normalized:
        return True
    if not key:
        return False
    return bool(re.search(rf"createv2/{re.escape(key)}(?=[^0-9]|$)", normalized))


async def confirm_silent_submission_from_catalog(
    page,
    *,
    source_href: str,
    source_key: str,
    refresh_catalog,
    card_selector: str,
) -> dict:
    """Confirm a silent supplier submission only by two catalog observations.

    Some supplier portals accept a form but leave the browser on ``createv2``
    without a toast or redirect.  This routine never clicks a product or
    submits anything. It reapplies the normal *without integration* catalog
    filter twice and confirms success only when the exact source item is absent
    in both observed catalog states.  A visible source item or any inconclusive
    state remains fail-closed.
    """
    href = str(source_href or "").strip()
    key = str(source_key or "").strip()
    if not href and not key:
        return {
            "confirmed": False,
            "detail": "não há identificador da fonte para conferir o catálogo",
            "uncertain": True,
        }

    for attempt in range(2):
        try:
            await refresh_catalog(page)
        except Exception as exc:
            return {
                "confirmed": False,
                "detail": f"não foi possível conferir o catálogo: {str(exc)[:150]}",
                "uncertain": True,
            }

        state = await _catalog_filter_state(page)
        if not state["catalog"] or not state["without_integration_filter"]:
            return {
                "confirmed": False,
                "detail": "catálogo sem o filtro 'Todos que eu não cadastrei'; confirmação bloqueada",
                "uncertain": True,
            }

        try:
            cards = await page.query_selector_all(card_selector)
        except Exception as exc:
            return {
                "confirmed": False,
                "detail": f"não foi possível ler os cartões do catálogo: {str(exc)[:150]}",
                "uncertain": True,
            }

        if not await _catalog_content_is_readable(page, cards):
            return {
                "confirmed": False,
                "detail": "leitura do catálogo inconclusiva; confirmação bloqueada",
                "uncertain": True,
            }

        references = [await _card_reference(card) for card in cards]
        # A pending item is useful evidence only when its card exposes its
        # create-form source URL. Otherwise a markup change could make every
        # item look absent even though it is still pending.
        if cards and any("createv2/" not in reference for reference in references):
            return {
                "confirmed": False,
                "detail": "cartão do catálogo sem identificador legível; confirmação bloqueada",
                "uncertain": True,
            }

        for reference in references:
            if _reference_contains_source(reference, href, key):
                return {
                    "confirmed": False,
                    "detail": "a fonte ainda aparece em 'Todos que eu não cadastrei'",
                    "uncertain": False,
                }

        if attempt == 0:
            try:
                await page.wait_for_timeout(1500)
            except Exception:
                pass

    return {
        "confirmed": True,
        "detail": "fonte ausente em duas consultas do catálogo sem integração",
        "uncertain": False,
    }


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
        # Callers may run the read-only catalog reconciliation above. Explicit
        # provider validation errors never reach this path.
        "catalog_verification_needed": True,
    }
