"""Shared required-marketplace-category guard for supplier create forms.

Both supplier portals may expose one or more category fields before accepting a
product.  The fields can be native ``select`` elements (for example
``sel_category_meli``) or custom controls.  The helper fills and verifies every
native field the supplier marks as required; it never treats a value in an
unrelated Shopee/category field as proof that the required marketplace field
was supplied.
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
    const required = (el) => el.required
        || el.getAttribute('aria-required') === 'true'
        || el.getAttribute('data-required') === 'true'
        || /\brequired\b/i.test(el.className || '');
    const fieldName = (el) => el.name || el.id || 'categoria';
    const identity = (el) => norm([el.name, el.id, el.getAttribute('data-field')].join(' '));
    // JohnDrop's server-side validation names these inputs explicitly. They do
    // not always carry the native `required` attribute, so relying only on the
    // DOM flag silently skips the field that the supplier will reject.
    const meliCategory = (el) => (
        identity(el) === 'selcategorymeli'
        || /(?:sel)?categorymeli|categori[a-z]*meli/.test(identity(el))
    ) && !/sub(?:category|categoria)/.test(identity(el));
    const meliSubcategory = (el) => /meli.*sub(?:category|categoria)|sub(?:category|categoria).*meli/.test(identity(el));
    const unique = (items) => items.filter((item, index) => items.indexOf(item) === index);
    const dispatch = (select) => {
        select.dispatchEvent(new Event('input', { bubbles: true }));
        select.dispatchEvent(new Event('change', { bubbles: true }));
        select.dispatchEvent(new Event('blur', { bubbles: true }));
        // Some supplier forms still use Select2/jQuery change handlers.
        if (window.jQuery) window.jQuery(select).trigger('change');
    };

    const selects = Array.from(document.querySelectorAll('select')).filter(matching);
    if (selects.length) {
        // A supplier can render both Shopee and Mercado Livre category fields.
        // The JohnDrop server requires `sel_category_meli` unless it has
        // rendered `sel_meli_subCategory_meli`; include the applicable Meli
        // field even when that portal forgot to mark it `required` in HTML.
        const requiredSelects = selects.filter(required);
        const meliSubcategories = selects.filter(meliSubcategory);
        const meliCategories = selects.filter(meliCategory);
        const meliRequired = meliSubcategories.length ? meliSubcategories : meliCategories;
        const visibleSelects = selects.filter(visible);
        const targets = unique(requiredSelects.concat(meliRequired));
        if (!targets.length) {
            targets.push(visibleSelects.length ? visibleSelects[0] : selects[0]);
        }
        const details = [];
        let selected = true;
        let changed = false;
        for (const select of targets) {
            let chosen = select.options[select.selectedIndex];
            if (!(chosen && usableOption(chosen))) {
                chosen = Array.from(select.options).find(usableOption);
                if (!chosen) {
                    selected = false;
                    details.push(`${fieldName(select)}: sem opção válida carregada`);
                    continue;
                }
                select.value = chosen.value;
                dispatch(select);
                changed = true;
            }
            const confirmed = String(select.value || '').trim() === String(chosen.value || '').trim()
                && usableOption(select.options[select.selectedIndex]);
            if (!confirmed) selected = false;
            details.push(
                confirmed
                    ? `${fieldName(select)}: ${(chosen.textContent || '').trim().slice(0, 80)}`
                    : `${fieldName(select)}: valor não confirmado`,
            );
        }
        return {
            present: true,
            selected,
            changed,
            detail: selected
                ? `categoria confirmada: ${details.join(' | ')}`
                : `categoria pendente: ${details.join(' | ')}`,
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
        "changed": bool(data.get("changed")),
    }


async def ensure_required_marketplace_category(page) -> dict:
    """Fill and verify supplier category fields before submitting a product.

    A portal without a category field is left unchanged. A known field that
    cannot be selected returns ``selected=False`` so the calling robot stops
    before creating a product the supplier would reject.
    """
    # A category selection can asynchronously reveal the Meli subcategory
    # field. Re-scan twice after a real native change so that field is also
    # filled/verified before the product form is submitted.
    first: dict = {}
    for _attempt in range(3):
        try:
            first = _result(await page.evaluate(_CATEGORY_FIELD_PROBE))
        except Exception as exc:
            return {
                "present": False,
                "selected": False,
                "detail": f"falha ao localizar categoria: {str(exc)[:160]}",
                "custom_opened": False,
                "changed": False,
            }
        if not first["selected"] or not first["present"] or not first["changed"]:
            break
        try:
            await page.wait_for_timeout(300)
        except Exception:
            pass
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
            "changed": False,
        }
    picked["present"] = True
    return picked
