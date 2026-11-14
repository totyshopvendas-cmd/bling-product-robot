"""Shared browser helper for supplier-to-Bling integration selection.

JohnDrop and TrimPro use different pages, but both must select the same
``TotyShop-Bling`` integration before submitting a supplier product.  A click
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
_SELECT_INTEGRATION_PROBE = r"""
(target) => {
    const normalize = (value) => (value || '')
        .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
        .toLowerCase().replace(/[^a-z0-9]+/g, '');
    const wanted = normalize(target);
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
    const targetControl = (root) => {
        const controls = controlsIn(root);
        const named = controls.find(controlMatches);
        if (named) return named;
        if (root.matches && root.matches('label') && root.htmlFor) {
            const linked = document.getElementById(root.htmlFor);
            if (linked && linked.matches(controlSelector)) return linked;
        }
        const text = normalize(root.innerText || root.textContent);
        return controls.length === 1 && text === wanted ? controls[0] : null;
    };
    const selected = (root) => {
        const control = targetControl(root);
        if (control) return Boolean(control.checked);
        // Custom radio cards may not expose a native input.  Check only the
        // target root itself, never a selected marker inherited from a sibling.
        const state = `${root.getAttribute('aria-checked') || ''} ${root.getAttribute('data-state') || ''} ${root.className || ''}`.toLowerCase();
        return root.getAttribute('aria-checked') === 'true'
            || root.getAttribute('data-state') === 'checked'
            || /(?:^|[\s_-])(selected|active|checked)(?:$|[\s_-])/.test(state);
    };

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
        if (selected(root)) {
            return { found: true, selected: true, detail: 'TotyShop-Bling já estava selecionada' };
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
            return { found: true, selected: true, detail: 'TotyShop-Bling selecionada e confirmada' };
        }
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


async def _probe_selection(page, target: str) -> dict:
    try:
        return _result(await page.evaluate(_SELECT_INTEGRATION_PROBE, target))
    except Exception as exc:
        return {
            "found": False,
            "selected": False,
            "detail": f"falha ao verificar a integração: {str(exc)[:160]}",
        }


async def select_required_integration(page, integration_name: str) -> dict:
    """Select and verify exactly the requested supplier integration.

    A target-specific checked state is mandatory.  If a generic supplier card
    reports another integration (for example Shopee) as selected, this helper
    returns ``selected=False`` and callers stop before submitting the product.
    """
    target = (integration_name or "").strip()
    if not target:
        return {"found": False, "selected": False, "detail": "integração vazia"}

    result = await _probe_selection(page, target)
    if not result["found"] or result["selected"]:
        return result

    # React/Vue forms can update the checked marker on the next render. Probe
    # the exact same TotyShop-Bling target once more, never a generic ancestor.
    try:
        await page.wait_for_timeout(350)
    except Exception:
        pass
    return await _probe_selection(page, target)
