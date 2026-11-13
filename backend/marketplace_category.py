"""Shared required-marketplace-category guard for supplier create forms.

Both supplier portals may expose a required category field before accepting a
product. The most common current form field is ``sel_category_shopee``.  This
helper selects the first valid category when the portal provides a native or
custom selector, then verifies that the field is no longer empty.
"""
from __future__ import annotations

from typing import Any


_CATEGORY_FIELD_PROBE = r"""
() => {
    const norm = (value) => (value || '')
        .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
        .toLowerCase().replace(/[^a-z0-9]+/g, '');
    const target = /(?:category|categoria).*shopee|shopee.*(?:category|categoria)|(?:category|categoria)/;
    const placeholder = /^(?:selecione|selecionar|escolha|categoria|category|nenhuma|none|-+)$/i;
    const usableOption = (option) => {
        const text = (option.textContent || '').trim();
        return !option.disabled && String(option.value || '').trim() && !placeholder.test(text);
    };
    const matching = (el) => {
        const context = [
            el.name, el.id, el.getAttribute('data-testid'), el.getAttribute('data-field'),
            el.getAttribute('aria-label'), el.getAttribute('placeholder'),
            el.closest('label, .form-group, .field, .form-field, [class*="category"], [class*="categoria"]')?.innerText,
        ].join(' ');
        return target.test(norm(context));
    };
    const visible = (el) => {
        if (!el) return false;
        const style = window.getComputedStyle(el);
        const box = el.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' && box.width > 0 && box.height > 0;
    };

    const selects = Array.from(document.querySelectorAll('select')).filter(matching);
    if (selects.length) {
        const select = selects.find(visible) || selects[0];
        const chosen = select.options[select.selectedIndex];
        if (chosen && usableOption(chosen)) {
            return { present: true, selected: true, detail: `categoria já preenchida: ${(chosen.textContent || '').trim().slice(0, 100)}` };
        }
        const option = Array.from(select.options).find(usableOption);
        if (!option) {
            return { present: true, selected: false, detail: 'campo de categoria localizado, mas não há opção válida carregada' };
        }
        select.value = option.value;
        select.dispatchEvent(new Event('input', { bubbles: true }));
        select.dispatchEvent(new Event('change', { bubbles: true }));
        const confirmed = String(select.value || '').trim() === String(option.value || '').trim();
        return {
            present: true,
            selected: confirmed,
            detail: confirmed
                ? `categoria selecionada: ${(option.textContent || '').trim().slice(0, 100)}`
                : 'não foi possível confirmar a categoria selecionada',
        };
    }

    // Custom selects usually have a hidden input plus a visible button/combobox.
    const fields = Array.from(document.querySelectorAll('input, button, [role="combobox"], [data-select], [class*="select"]'))
        .filter(matching);
    if (!fields.length) return { present: false, selected: true, detail: 'campo de categoria não existe neste portal' };

    const field = fields.find(visible) || fields[0];
    if (field.matches('input') && String(field.value || '').trim()) {
        return { present: true, selected: true, detail: 'categoria já preenchida' };
    }
    const trigger = field.matches('button, [role="combobox"]')
        ? field
        : field.closest('label, .form-group, .field, .form-field, [class*="category"], [class*="categoria"]')?.querySelector('button, [role="combobox"]') || field;
    try { trigger.scrollIntoView({ block: 'center' }); trigger.click(); } catch (_) {}
    return { present: true, selected: false, custom_opened: true, detail: 'seletor de categoria aberto' };
}
"""


_CATEGORY_CUSTOM_OPTION_PROBE = r"""
() => {
    const norm = (value) => (value || '')
        .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
        .toLowerCase().replace(/[^a-z0-9]+/g, '');
    const placeholder = /^(?:selecione|selecionar|escolha|categoria|category|nenhuma|none|-+)$/i;
    const visible = (el) => {
        if (!el) return false;
        const style = window.getComputedStyle(el);
        const box = el.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' && box.width > 0 && box.height > 0;
    };
    const options = Array.from(document.querySelectorAll('[role="option"], .select-option, .dropdown-item, [data-value], li, button'))
        .filter((el) => {
            const text = (el.innerText || el.textContent || '').trim();
            const value = el.getAttribute('data-value') || el.value || text;
            return visible(el) && text && !placeholder.test(text) && value;
        });
    const option = options.find((el) => !/cancelar|fechar|voltar|criar produto/i.test(el.innerText || el.textContent || ''));
    if (!option) return { selected: false, detail: 'seletor de categoria aberto, mas sem opção válida visível' };
    const text = (option.innerText || option.textContent || '').trim();
    try { option.click(); } catch (_) { return { selected: false, detail: 'não foi possível escolher a categoria' }; }
    return { selected: true, detail: `categoria escolhida: ${text.slice(0, 100)}` };
}
"""


def _result(data: Any) -> dict:
    data = data if isinstance(data, dict) else {}
    return {
        "present": bool(data.get("present")),
        "selected": bool(data.get("selected")),
        "detail": str(data.get("detail") or "estado da categoria não confirmado")[:240],
        "custom_opened": bool(data.get("custom_opened")),
    }


async def ensure_required_marketplace_category(page) -> dict:
    """Fill and verify a supplier category when its form declares one.

    A portal without a category field is left unchanged. A known field that
    cannot be selected returns ``selected=False`` so the calling robot stops
    before creating a product that the supplier will reject.
    """
    try:
        first = _result(await page.evaluate(_CATEGORY_FIELD_PROBE))
    except Exception as exc:
        return {
            "present": False,
            "selected": False,
            "detail": f"falha ao localizar categoria: {str(exc)[:160]}",
            "custom_opened": False,
        }
    if first["selected"] or not first["present"] or not first["custom_opened"]:
        return first

    try:
        await page.wait_for_timeout(300)
    except Exception:
        pass
    try:
        picked = _result(await page.evaluate(_CATEGORY_CUSTOM_OPTION_PROBE))
    except Exception as exc:
        return {
            "present": True,
            "selected": False,
            "detail": f"falha ao escolher categoria: {str(exc)[:160]}",
            "custom_opened": True,
        }
    picked["present"] = True
    return picked
