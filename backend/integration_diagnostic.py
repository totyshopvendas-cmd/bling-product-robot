"""Read-only DOM snapshot for supplier integration controls.

The supplier pages are private and can change markup without notice.  This
module records only public control metadata from a create-product form so the
robot can be bound to the exact integration radio on a later change.  It never
clicks, fills, submits, or exposes credentials.
"""
from __future__ import annotations

from typing import Any


_INTEGRATION_CONTROL_SNAPSHOT = r"""
() => {
    const controlSelector = 'input[type="radio"], input[type="checkbox"]';
    const clip = (value, limit = 120) => String(value || '')
        .replace(/\s+/g, ' ').trim().slice(0, limit);
    const normalize = (value) => clip(value, 500)
        .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
        .toLowerCase().replace(/[^a-z0-9]+/g, '');
    const visible = (el) => {
        if (!el) return false;
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
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
    const controlsIn = (root) => {
        if (!root) return [];
        const controls = [];
        if (root.matches && root.matches(controlSelector)) controls.push(root);
        controls.push(...root.querySelectorAll(controlSelector));
        return controls;
    };
    const cardFor = (input) => {
        let root = input.parentElement;
        let fallback = input;
        while (root && root !== document.body) {
            if (visible(root) && controlsIn(root).length === 1) {
                fallback = root;
                if (clip(root.innerText || root.textContent)) return root;
            }
            root = root.parentElement;
        }
        return fallback;
    };
    const dataAttributes = (input) => Object.fromEntries(
        Array.from(input.attributes || [])
            .filter((attr) => /^(?:data-|aria-)/i.test(attr.name))
            .map((attr) => [attr.name, clip(attr.value, 80)])
    );
    const controls = Array.from(document.querySelectorAll(controlSelector))
        .filter(visibleControl)
        .sort((left, right) => {
            const a = rectFor(left);
            const b = rectFor(right);
            if (Math.abs(a.top - b.top) > 12) return a.top - b.top;
            return a.left - b.left;
        })
        .slice(0, 30)
        .map((input, index) => {
            const card = cardFor(input);
            const label = labelsFor(input).map((item) => item.innerText || item.textContent).join(' ');
            const cardText = clip(card?.innerText || card?.textContent);
            const context = normalize(`${input.id} ${input.name} ${input.value} ${input.getAttribute('aria-label') || ''} ${label} ${cardText}`);
            return {
                position: index + 1,
                type: input.type || '',
                id: clip(input.id, 100),
                name: clip(input.name, 100),
                value: clip(input.value, 100),
                label: clip(label),
                card_text: cardText,
                aria_label: clip(input.getAttribute('aria-label'), 100),
                data_attributes: dataAttributes(input),
                checked: Boolean(input.checked),
                totyshop_bling_hint: context.includes('totyshopbling'),
                rect: {
                    x: Math.round(rectFor(input).x),
                    y: Math.round(rectFor(input).y),
                    width: Math.round(rectFor(input).width),
                    height: Math.round(rectFor(input).height),
                },
            };
        });
    return {
        page_path: location.pathname,
        total_visible_controls: controls.length,
        controls,
    };
}
"""


async def snapshot_integration_controls(page) -> dict[str, Any]:
    """Return a sanitized description of visible supplier radio/checkboxes.

    The page-side script is intentionally read-only: it uses DOM reads only and
    does not dispatch any event or call a navigation/form API.
    """
    raw = await page.evaluate(_INTEGRATION_CONTROL_SNAPSHOT)
    if not isinstance(raw, dict):
        return {"page_path": "", "total_visible_controls": 0, "controls": []}
    controls = raw.get("controls")
    return {
        "page_path": str(raw.get("page_path") or "")[:240],
        "total_visible_controls": int(raw.get("total_visible_controls") or 0),
        "controls": controls if isinstance(controls, list) else [],
    }
