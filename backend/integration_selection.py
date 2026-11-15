"""Shared browser helper for supplier-to-Bling integration selection.

JohnDrop and TrimPro use different pages, but both must select the same
``TotyShop-Bling`` integration before submitting a supplier product. A click
alone is not proof: dynamic forms can silently leave the card unselected.
This helper clicks the matching option and then verifies the selected state,
so callers can fail closed instead of reporting a false successful cadastro.
"""
from __future__ import annotations

from typing import Any


# The probe scopes a checked marker to the TotyShop-Bling control. Some supplier
# forms render every integration card inside one generic wrapper; accepting a
# checked radio from that wrapper could otherwise treat Shopee as Bling.
#
# The operator confirmed the visible native-radio order for each portal:
# JohnDrop uses radio 4 and TrimPro uses radio 3. We deliberately order radios,
# not containers whose repeated/nested HTML can differ from the visual cards.
# When that invariant fails, the probe emits a compact, non-secret inventory of
# the actual radio IDs/labels so the next selector can be tied to the supplier's
# real control rather than another inferred position.
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
    const labelsFor = (input) => input.labels && input.labels.length
        ? Array.from(input.labels)
        : [];
    const visibleControl = (input) => visible(input) || labelsFor(input).some(visible);
    const rectFor = (input) => {
        if (visible(input)) return input.getBoundingClientRect();
        const label = labelsFor(input).find(visible);
        return label ? label.getBoundingClientRect() : input.getBoundingClientRect();
    };
    const rootForControl = (input) => {
        // Start at the parent so a visible input itself is not mistaken for a
        // card. Prefer the nearest one-control ancestor that has card text.
        let root = input.parentElement;
        let fallback = input;
        while (root && root !== document.body) {
            const controls = controlsIn(root);
            if (visible(root) && controls.length === 1) {
                fallback = root;
                if (String(root.innerText || root.textContent || '').trim()) return root;
            }
            root = root.parentElement;
        }
        return fallback;
    };
    const controlContext = (input, root = rootForControl(input)) => {
        const labels = labelsFor(input).map((label) => label.innerText || label.textContent).join(' ');
        return normalize(
            `${input.value || ''} ${input.name || ''} ${input.id || ''} `
            + `${input.getAttribute('aria-label') || ''} ${labels} `
            + `${root?.innerText || root?.textContent || ''}`
        );
    };
    const controlMatches = (input) => controlContext(input).includes(wanted);
    const clip = (value, limit = 32) => String(value || '')
        .replace(/\s+/g, ' ').trim().slice(0, limit);
    const describeControl = (input, index) => {
        const root = rootForControl(input);
        const label = labelsFor(input).map((item) => item.innerText || item.textContent).join(' ');
        const attrs = Array.from(input.attributes || [])
            .filter((attr) => /^(?:data-|aria-)/i.test(attr.name))
            .map((attr) => `${attr.name}=${clip(attr.value, 18)}`)
            .join(',');
        return `${index + 1}[id=${clip(input.id, 20)};name=${clip(input.name, 20)};value=${clip(input.value, 20)};label=${clip(label)};card=${clip(root?.innerText || root?.textContent)}${attrs ? `;${attrs}` : ''}]`;
    };
    const describeControls = (controls) => controls.slice(0, 10)
        .map((input, index) => describeControl(input, index)).join(' | ');
    const rootFor = (candidate) => {
        let root = candidate;
        while (root && root !== document.body) {
            const text = normalize(root.innerText || root.textContent);
            const controls = controlsIn(root);
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
        return controls.length === 1 && text.includes(wanted) ? controls[0] : null;
    };
    const selected = (root, control = targetControl(root)) => {
        if (control?.checked) return true;
        // Custom radio cards may not expose a native checked property. Inspect
        // only this card and its own descendants, never a generic parent.
        const marked = [root, ...Array.from(root.querySelectorAll('*'))];
        return marked.some((el) => {
            const state = `${el.getAttribute('aria-checked') || ''} ${el.getAttribute('data-state') || ''} ${el.className || ''}`.toLowerCase();
            return el.getAttribute('aria-checked') === 'true'
                || el.getAttribute('data-state') === 'checked'
                || /(?:^|[\s_-])(selected|active|checked)(?:$|[\s_-])/.test(state);
        });
    };
    const choose = (root, position, exactControl = null) => {
        const control = exactControl || targetControl(root);
        const positionDetail = position ? ` no ${position}º ícone` : '';
        if (selected(root, control)) {
            return { found: true, selected: true, detail: `TotyShop-Bling já estava selecionada${positionDetail}` };
        }
        try {
            root.scrollIntoView({ block: 'center', inline: 'nearest' });
            root.click();
        } catch (_) {}
        if (control && !control.checked) {
            // A label click is needed by some controlled Bootstrap cards; a
            // direct input click is the final, exact fallback for this card.
            try { labelsFor(control).find(visible)?.click(); } catch (_) {}
            if (!control.checked) {
                try { control.click(); } catch (_) {}
            }
        }
        if (selected(root, control)) {
            return { found: true, selected: true, detail: `TotyShop-Bling selecionada e confirmada${positionDetail}` };
        }
        return {
            found: true,
            selected: false,
            detail: `TotyShop-Bling foi localizada${positionDetail}, mas sua seleção não foi confirmada`,
        };
    };

    if (expectedPosition) {
        // The visible radios are the controls the operator sees as the circles
        // beside each integration. Sort them by screen row then column; do not
        // count nested divs, duplicate labels, or generic card wrappers.
        const allVisibleControls = Array.from(document.querySelectorAll(controlSelector))
            .filter(visibleControl)
            .sort((left, right) => {
                const a = rectFor(left);
                const b = rectFor(right);
                if (Math.abs(a.top - b.top) > 12) return a.top - b.top;
                return a.left - b.left;
            });
        const integrationControls = allVisibleControls
            .map((input) => ({ input, root: rootForControl(input) }))
            .filter(({ input, root }) => controlContext(input, root).includes('totyshop'));
        const entry = integrationControls[expectedPosition - 1];
        const inventory = describeControls(allVisibleControls);
        if (!entry) {
            return {
                found: false,
                selected: false,
                detail: `${expectedPosition}º ícone TotyShop-Bling não encontrado; inventário dos rádios visíveis: ${inventory || 'nenhum'}`,
            };
        }
        if (!controlContext(entry.input, entry.root).includes(wanted)) {
            return {
                found: false,
                selected: false,
                detail: `${expectedPosition}º ícone não é TotyShop-Bling; seleção bloqueada; inventário dos rádios visíveis: ${inventory || 'nenhum'}`,
            };
        }
        return choose(entry.root, expectedPosition, entry.input);
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
        # The failure detail may carry the compact identifiers of the visible
        # supplier radios. It contains no credentials and is kept long enough
        # for the operator to report the exact control without another guess.
        "detail": str(data.get("detail") or "estado não confirmado")[:900],
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
    supplied, that one-based visual radio position must also be TotyShop-Bling.
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
