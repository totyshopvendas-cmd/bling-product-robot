"""Shared browser helper for supplier-to-Bling integration selection.

JohnDrop and TrimPro use different pages, but both must select the same
``TotyShop-Bling`` integration before submitting a supplier product. A click
alone is not proof: dynamic forms can silently leave the card unselected.
This helper clicks the matching option and then verifies the selected state,
so callers can fail closed instead of reporting a false successful cadastro.
"""
from __future__ import annotations

from typing import Any


# The probe deliberately scopes a checked marker to the *TotyShop-Bling* card.
# Some supplier forms render all integration choices inside one generic .card;
# accepting any checked radio inside that ancestor can incorrectly treat Shopee
# as the selected TotyShop-Bling integration.
#
# The supplier pages in production also contain repeated Bling-looking cards.
# The operator-established card position is therefore an additional invariant:
# JohnDrop uses the fourth integration card and TrimPro uses the third one.
# A card at that position must still have the TotyShop-Bling label, otherwise
# the probe fails closed rather than falling back to a lookalike card.
_SELECT_INTEGRATION_PROBE = r"""
(config) => {
    const normalize = (value) => (value || '')
        .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
        .toLowerCase().replace(/[^a-z0-9]+/g, '');
    const supplied = typeof config === 'string' ? { target: config } : (config || {});
    const target = String(supplied.target || '');
    const wanted = normalize(target);
    const expectedPosition = Number.parseInt(supplied.position, 10) || 0;
    const controlSelector = 'input[type="radio"], input[type="checkbox"]';
    const visible = (el) => {
        if (!el) return false;
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
    };
    const controlsIn = (root) => {
        if (!root) return [];
        const controls = [];
        if (root.matches && root.matches(controlSelector)) controls.push(root);
        controls.push(...root.querySelectorAll(controlSelector));
        return controls;
    };
    const controlMatches = (input) => {
        const labels = input.labels && input.labels.length
            ? Array.from(input.labels).map((label) => label.innerText || label.textContent).join(' ')
            : '';
        const context = `${input.value || ''} ${input.name || ''} ${input.id || ''} ${input.getAttribute('aria-label') || ''} ${labels}`;
        return normalize(context).includes(wanted);
    };
    const rootFor = (candidate) => {
        let root = candidate;
        while (root && root !== document.body) {
            const text = normalize(root.innerText || root.textContent);
            const controls = controlsIn(root);
            // A target card is safe when its own text is exactly the target,
            // it owns a control whose label/value names the target, or it has
            // exactly one control and no other integration labels.
            if (
                text === wanted
                || controls.some(controlMatches)
                || (controls.length === 1 && text.includes(wanted) && !text.includes('shopee'))
            ) return root;
            root = root.parentElement;
        }
        return candidate;
    };
    const integrationRootFor = (candidate) => {
        let root = candidate;
        while (root && root !== document.body) {
            const text = normalize(root.innerText || root.textContent);
            const controls = controlsIn(root);
            // Each visible supplier integration card owns exactly one radio.
            // Do not use an outer wrapper: it can contain every integration.
            if (visible(root) && controls.length === 1 && text.includes('totyshop')) return root;
            root = root.parentElement;
        }
        return null;
    };
    const targetControl = (root) => {
        const controls = controlsIn(root);
        const named = controls.find(controlMatches);
        if (named) return named;
        if (root.matches && root.matches('label') && root.htmlFor) {
            const linked = document.getElementById(root.htmlFor);
            if (linked && linked.matches(controlSelector)) return linked;
        }
        const text = normalize(root.innerText || root.textContent);
        return controls.length === 1 && text.includes(wanted) ? controls[0] : null;
    };
    const selected = (root) => {
        const control = targetControl(root);
        if (control) return Boolean(control.checked);
        // Custom radio cards may not expose a native input. Check only the
        // target root itself, never a selected marker inherited from a sibling.
        const state = `${root.getAttribute('aria-checked') || ''} ${root.getAttribute('data-state') || ''} ${root.className || ''}`.toLowerCase();
        return root.getAttribute('aria-checked') === 'true'
            || root.getAttribute('data-state') === 'checked'
            || /(?:^|[\s_-])(selected|active|checked)(?:$|[\s_-])/.test(state);
    };
    const choose = (root, position) => {
        const positionDetail = position ? ` no ${position}º ícone` : '';
        if (selected(root)) {
            return { found: true, selected: true, detail: `TotyShop-Bling já estava selecionada${positionDetail}` };
        }
        try {
            root.scrollIntoView({ block: 'center', inline: 'nearest' });
            root.click();
        } catch (_) {}
        const control = targetControl(root);
        if (control && !control.checked) {
            try { control.click(); } catch (_) {}
        }
        if (selected(root)) {
            return { found: true, selected: true, detail: `TotyShop-Bling selecionada e confirmada${positionDetail}` };
        }
        return {
            found: true,
            selected: false,
            detail: `TotyShop-Bling foi localizada${positionDetail}, mas sua seleção não foi confirmada`,
        };
    };

    if (expectedPosition) {
        // Cards are collected by their own radio/checkbox and sorted in the
        // visual order shown to the operator. This avoids a duplicate label
        // elsewhere in the form winning merely because it appears first in
        // DOM order.
        const cardRoots = [];
        const addCard = (candidate) => {
            const root = integrationRootFor(candidate);
            if (root && !cardRoots.includes(root)) cardRoots.push(root);
        };
        Array.from(document.querySelectorAll(controlSelector)).forEach(addCard);
        Array.from(document.querySelectorAll(
            'label, button, [role="radio"], [role="checkbox"], [data-integration], [data-testid], div, span'
        )).filter((el) => {
            const text = normalize(el.innerText || el.textContent);
            return visible(el) && text.startsWith('totyshop');
        }).forEach(addCard);
        cardRoots.sort((left, right) => {
            const a = left.getBoundingClientRect();
            const b = right.getBoundingClientRect();
            // A portal can wrap cards onto a second line; use row then column.
            if (Math.abs(a.top - b.top) > 12) return a.top - b.top;
            return a.left - b.left;
        });
        const root = cardRoots[expectedPosition - 1];
        if (!root) {
            return {
                found: false,
                selected: false,
                detail: `${expectedPosition}º ícone TotyShop-Bling não encontrado; ${cardRoots.length} cartões TotyShop visíveis`,
            };
        }
        const actual = normalize(root.innerText || root.textContent);
        if (!actual.includes(wanted)) {
            return {
                found: false,
                selected: false,
                detail: `${expectedPosition}º ícone não é TotyShop-Bling; seleção bloqueada`,
            };
        }
        return choose(root, expectedPosition);
    }

    const textCandidates = Array.from(document.querySelectorAll(
        'label, button, [role="radio"], [role="checkbox"], [data-integration], [data-testid], div, span'
    )).filter((el) => visible(el) && normalize(el.innerText || el.textContent) === wanted);
    const inputCandidates = Array.from(document.querySelectorAll(controlSelector)).filter(controlMatches);
    const candidates = [...textCandidates, ...inputCandidates];
    if (!candidates.length) {
        return { found: false, selected: false, detail: 'opção não encontrada no formulário' };
    }

    const roots = [];
    for (const candidate of candidates) {
        const root = rootFor(candidate);
        if (!roots.includes(root)) roots.push(root);
    }
    for (const root of roots) {
        const result = choose(root, 0);
        if (result.selected) return result;
    }
    return {
        found: true,
        selected: false,
        detail: 'TotyShop-Bling foi localizada, mas sua seleção não foi confirmada',
    };
}
"""


def _result(data: Any) -> dict:
    """Normalize the browser probe result into a stable public shape."""
    data = data if isinstance(data, dict) else {}
    return {
        "found": bool(data.get("found")),
        "selected": bool(data.get("selected")),
        "detail": str(data.get("detail") or "estado não confirmado")[:240],
    }


async def _probe_selection(page, target: str, expected_position: int | None = None) -> dict:
    config: str | dict[str, Any]
    if expected_position is None:
        config = target
    else:
        config = {"target": target, "position": expected_position}
    try:
        return _result(await page.evaluate(_SELECT_INTEGRATION_PROBE, config))
    except Exception as exc:
        return {
            "found": False,
            "selected": False,
            "detail": f"falha ao verificar a integração: {str(exc)[:160]}",
        }


async def select_required_integration(
    page,
    integration_name: str,
    expected_position: int | None = None,
) -> dict:
    """Select and verify exactly the requested supplier integration.

    A target-specific checked state is mandatory. If ``expected_position`` is
    supplied, that one-based visual card position must also be TotyShop-Bling.
    If either condition is not met, callers stop before submitting the product.
    """
    target = (integration_name or "").strip()
    if not target:
        return {"found": False, "selected": False, "detail": "integração vazia"}
    if expected_position is not None and expected_position < 1:
        return {
            "found": False,
            "selected": False,
            "detail": "posição da integração inválida",
        }

    result = await _probe_selection(page, target, expected_position)
    if not result["found"] or result["selected"]:
        return result

    # React/Vue forms can update the checked marker on the next render. Probe
    # the exact same TotyShop-Bling target once more, never a generic ancestor.
    try:
        await page.wait_for_timeout(350)
    except Exception:
        pass
    return await _probe_selection(page, target, expected_position)
