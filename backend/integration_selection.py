"""Shared browser helper for supplier-to-Bling integration selection.

JohnDrop and TrimPro use different pages, but both must select the same
``TotyShop-Bling`` integration before submitting a supplier product.  A click
alone is not proof: dynamic forms can silently leave the card unselected.
This helper clicks the matching option and then verifies the selected state,
so callers can fail closed instead of reporting a false successful cadastro.
"""
from __future__ import annotations

from typing import Any


def _result(data: Any) -> dict:
    """Normalize the browser probe result into a stable public shape."""
    data = data if isinstance(data, dict) else {}
    return {
        "found": bool(data.get("found")),
        "selected": bool(data.get("selected")),
        "detail": str(data.get("detail") or "estado não confirmado")[:240],
    }


async def select_required_integration(page, integration_name: str) -> dict:
    """Select and verify a supplier integration in a dynamic Playwright form.

    The supplier UIs have changed markup over time (labels, radio cards,
    buttons and React-controlled inputs).  The DOM probe intentionally handles
    those common shapes and only reports success after a checked/selected state
    is visible.  A product must not be submitted when that state is unknown.
    """
    target = (integration_name or "").strip()
    if not target:
        return {"found": False, "selected": False, "detail": "integração vazia"}

    try:
        browser_result = await page.evaluate(
            """
            (target) => {
                const normalize = (value) => (value || '')
                    .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')
                    .toLowerCase().replace(/[^a-z0-9]+/g, '');
                const wanted = normalize(target);
                const visible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                };
                const selected = (root) => {
                    if (!root) return false;
                    const inputs = [];
                    if (root.matches && root.matches('input[type="radio"], input[type="checkbox"]')) inputs.push(root);
                    inputs.push(...root.querySelectorAll('input[type="radio"], input[type="checkbox"]'));
                    if (inputs.some((input) => input.checked)) return true;
                    const marked = [root, ...root.querySelectorAll('*')];
                    return marked.some((el) => {
                        const state = `${el.getAttribute('aria-checked') || ''} ${el.getAttribute('data-state') || ''} ${el.className || ''}`.toLowerCase();
                        return el.getAttribute('aria-checked') === 'true'
                            || el.getAttribute('data-state') === 'checked'
                            || /(?:^|[\\s_-])(selected|active|checked)(?:$|[\\s_-])/.test(state);
                    });
                };
                const textCandidates = Array.from(document.querySelectorAll('label, button, [role="radio"], [role="checkbox"], [data-integration], [data-testid], div, span'))
                    .filter((el) => visible(el) && normalize(el.innerText || el.textContent) === wanted);
                const inputCandidates = Array.from(document.querySelectorAll('input[type="radio"], input[type="checkbox"]'))
                    .filter((input) => {
                        const label = input.labels && input.labels.length ? Array.from(input.labels).map((x) => x.innerText || x.textContent).join(' ') : '';
                        const context = `${input.value || ''} ${input.name || ''} ${input.getAttribute('aria-label') || ''} ${label}`;
                        return normalize(context).includes(wanted);
                    });
                const candidates = [...textCandidates, ...inputCandidates];
                if (!candidates.length) return { found: false, selected: false, detail: 'opção não encontrada no formulário' };

                for (const candidate of candidates) {
                    const container = candidate.closest('label, button, [role="radio"], [role="checkbox"], [data-integration], [data-testid], .card, .integration') || candidate;
                    if (selected(container)) return { found: true, selected: true, detail: 'já estava selecionada' };
                    try {
                        container.scrollIntoView({ block: 'center', inline: 'nearest' });
                        container.click();
                    } catch (_) {}
                    const input = candidate.matches && candidate.matches('input[type="radio"], input[type="checkbox"]')
                        ? candidate
                        : container.querySelector('input[type="radio"], input[type="checkbox"]');
                    if (input && !input.checked) {
                        try { input.click(); } catch (_) {}
                    }
                    if (selected(container)) return { found: true, selected: true, detail: 'selecionada e confirmada' };
                }
                return { found: true, selected: false, detail: 'opção encontrada, mas a seleção não foi confirmada' };
            }
            """,
            target,
        )
    except Exception as exc:
        return {
            "found": False,
            "selected": False,
            "detail": f"falha ao verificar a integração: {str(exc)[:160]}",
        }

    result = _result(browser_result)
    if not result["found"] or result["selected"]:
        return result

    # React/Vue forms can update the checked marker on the next render. Wait a
    # short moment and probe once more before declaring a true selection error.
    try:
        await page.wait_for_timeout(350)
    except Exception:
        pass
    try:
        verified = _result(await page.evaluate(
            """
            (target) => {
                const normalize = (value) => (value || '')
                    .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')
                    .toLowerCase().replace(/[^a-z0-9]+/g, '');
                const wanted = normalize(target);
                const roots = Array.from(document.querySelectorAll('label, button, [role="radio"], [role="checkbox"], [data-integration], [data-testid], .card, .integration'))
                    .filter((el) => normalize(el.innerText || el.textContent).includes(wanted));
                for (const root of roots) {
                    const inputs = Array.from(root.querySelectorAll('input[type="radio"], input[type="checkbox"]'));
                    if (inputs.some((input) => input.checked)) {
                        return { found: true, selected: true, detail: 'selecionada e confirmada após atualização da tela' };
                    }
                    const marked = [root, ...root.querySelectorAll('*')];
                    if (marked.some((el) => el.getAttribute('aria-checked') === 'true' || el.getAttribute('data-state') === 'checked')) {
                        return { found: true, selected: true, detail: 'selecionada e confirmada após atualização da tela' };
                    }
                }
                return { found: roots.length > 0, selected: false, detail: 'opção encontrada, mas a seleção não foi confirmada' };
            }
            """,
            target,
        ))
        if verified["selected"]:
            return verified
    except Exception:
        pass
    return result
