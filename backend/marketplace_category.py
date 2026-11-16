"""Shared required-category guard for supplier create forms.

The suppliers can expose native or custom category controls before accepting a
product.  This helper verifies genuine required supplier categories, while
explicitly ignoring Mercado Livre-specific controls: JohnDrop and TrimPro
create only the ``TotyShop-Bling`` integration and must never select, depend on,
or submit a Mercado Livre category.
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
    const contextFor = (el) => [
        el.name, el.id, el.getAttribute('data-testid'), el.getAttribute('data-field'),
        el.getAttribute('aria-label'), el.getAttribute('placeholder'),
        el.closest('label, .form-group, .field, .form-field, [class*="category"], [class*="categoria"]')?.innerText,
    ].join(' ');
    const matching = (el) => target.test(norm(contextFor(el)));
    // A JohnDrop/TrimPro TotyShop-Bling form can still render old Meli fields.
    // They are not part of this integration and must never be clicked, filled,
    // or used to block its product flow.
    const isMeliField = (el) => /(?:mercadolivre|meli)/.test(norm(contextFor(el)));
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
    const unique = (items) => items.filter((item, index) => items.indexOf(item) === index);
    const dispatch = (select) => {
        select.dispatchEvent(new Event('input', { bubbles: true }));
        select.dispatchEvent(new Event('change', { bubbles: true }));
        select.dispatchEvent(new Event('blur', { bubbles: true }));
        // Some supplier forms still use Select2/jQuery change handlers.
        if (window.jQuery) window.jQuery(select).trigger('change');
    };

    const allSelects = Array.from(document.querySelectorAll('select')).filter(matching);
    const ignoredMeliSelects = allSelects.filter(isMeliField);
    const selects = allSelects.filter((select) => !isMeliField(select));
    if (selects.length) {
        const requiredSelects = selects.filter(required);
        const visibleSelects = selects.filter(visible);
        const targets = unique(requiredSelects);
        if (!targets.length) {
            targets.push(visibleSelects.length ? visibleSelects[0] : selects[0]);
        }
        const isPosted = (select, value) => {
            const form = select.form || select.closest('form');
            // A page with no native form is handled by its framework events.
            // When a form exists, FormData is the closest safe pre-submit
            // proof that this exact named control will reach the supplier.
            if (!form || !select.name) return true;
            try {
                return new FormData(form).getAll(select.name)
                    .map((item) => String(item || '').trim())
                    .includes(String(value || '').trim());
            } catch (_) {
                return !select.disabled;
            }
        };
        const details = [];
        const waitingOptions = [];
        let selected = true;
        let changed = false;
        for (const select of targets) {
            let chosen = select.options[select.selectedIndex];
            if (!(chosen && usableOption(chosen))) {
                chosen = Array.from(select.options).find(usableOption);
                if (!chosen) {
                    selected = false;
                    waitingOptions.push(fieldName(select));
                    details.push(`${fieldName(select)}: sem opção válida carregada`);
                    continue;
                }
                select.value = chosen.value;
                dispatch(select);
                changed = true;
            }
            const confirmed = String(select.value || '').trim() === String(chosen.value || '').trim()
                && usableOption(select.options[select.selectedIndex]);
            const posted = confirmed && isPosted(select, chosen.value);
            if (!posted) selected = false;
            const optionText = (chosen.textContent || '').trim().slice(0, 70);
            const value = String(chosen.value || '').trim().slice(0, 70);
            details.push(
                posted
                    ? `${fieldName(select)}=${value} [POST=sim; disabled=${select.disabled ? 'sim' : 'não'}] ${optionText}`
                    : `${fieldName(select)}=${value || '(vazio)'} [POST=não; disabled=${select.disabled ? 'sim' : 'não'}]`,
            );
        }
        return {
            present: true,
            selected,
            changed,
            waiting_options: waitingOptions.length > 0,
            ignored_meli: ignoredMeliSelects.length > 0,
            detail: selected
                ? `categoria confirmada: ${details.join(' | ')}`
                : `categoria pendente: ${details.join(' | ')}`,
        };
    }
    // Custom selects usually have a hidden input plus a visible button/combobox.
    // Do not return early for ignored native Meli fields: the same form can
    // still contain a genuine non-Meli custom category that must be verified.
    const allFields = Array.from(document.querySelectorAll('input, button, [role="combobox"], [data-select], [class*="select"]'))
        .filter(matching);
    const ignoredMeliFields = allFields.filter(isMeliField);
    const ignoredMeli = ignoredMeliSelects.length > 0 || ignoredMeliFields.length > 0;
    const fields = allFields.filter((field) => !isMeliField(field));
    if (!fields.length) {
        if (ignoredMeli) {
            return {
                present: false,
                selected: true,
                changed: false,
                waiting_options: false,
                ignored_meli: true,
                detail: 'campos do Mercado Livre ignorados para TotyShop-Bling',
            };
        }
        return { present: false, selected: true, detail: 'campo de categoria não existe neste portal' };
    }

    const field = fields.find(visible) || fields[0];
    if (field.matches('input') && String(field.value || '').trim()) {
        return {
            present: true,
            selected: true,
            ignored_meli: ignoredMeli,
            detail: 'categoria já preenchida',
        };
    }
    const trigger = field.matches('button, [role="combobox"]')
        ? field
        : field.closest('label, .form-group, .field, .form-field, [class*="category"], [class*="categoria"]')?.querySelector('button, [role="combobox"]') || field;
    try { trigger.scrollIntoView({ block: 'center' }); trigger.click(); } catch (_) {}
    return {
        present: true,
        selected: false,
        custom_opened: true,
        ignored_meli: ignoredMeli,
        detail: 'seletor de categoria aberto',
    };
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
    // Mercado Livre categories often expose only their MLB code in data-value,
    // so exclude both the marketplace name and that code prefix.
    const isMeliOption = (el) => /(?:mercadolivre|meli|mlb\d+)/.test(norm([
        el.innerText || el.textContent, el.getAttribute('data-value'), el.value,
        el.getAttribute('aria-label'), el.getAttribute('data-marketplace'),
    ].join(' ')));
    const options = Array.from(document.querySelectorAll('[role="option"], .select-option, .dropdown-item, [data-value], li, button'))
        .filter((el) => {
            const text = (el.innerText || el.textContent || '').trim();
            const value = el.getAttribute('data-value') || el.value || text;
            return visible(el) && text && !placeholder.test(text) && value && !isMeliOption(el);
        });
    const option = options.find((el) => !/cancelar|fechar|voltar|criar produto/i.test(el.innerText || el.textContent || ''));
    if (!option) return { selected: false, detail: 'seletor de categoria aberto, mas sem opção válida fora do Mercado Livre' };
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
        "waiting_options": bool(data.get("waiting_options")),
        "ignored_meli": bool(data.get("ignored_meli")),
    }


# Supplier options can be populated asynchronously. Poll briefly before
# declaring a genuine non-Meli category impossible, but never submit blank.
_CATEGORY_OPTION_WAIT_ATTEMPTS = 8
_CATEGORY_OPTION_WAIT_MS = 750


async def ensure_required_marketplace_category(page) -> dict:
    """Fill and verify non-Meli supplier category fields before a submission.

    A portal without a relevant category field is left unchanged. Mercado Livre
    fields are explicitly ignored because this project only creates products
    through TotyShop-Bling; a known non-Meli field that cannot be selected still
    returns ``selected=False`` and stops the caller before a supplier submit.
    """
    # A non-Meli category can asynchronously reveal another non-Meli field.
    # Re-scan after a real native change and briefly wait for an otherwise empty
    # native select before allowing the caller to give up.
    first: dict = {}
    waited_for_options = False
    for _attempt in range(_CATEGORY_OPTION_WAIT_ATTEMPTS):
        try:
            first = _result(await page.evaluate(_CATEGORY_FIELD_PROBE))
        except Exception as exc:
            return {
                "present": False,
                "selected": False,
                "detail": f"falha ao localizar categoria: {str(exc)[:160]}",
                "custom_opened": False,
                "changed": False,
                "waiting_options": False,
                "ignored_meli": False,
            }

        if first["selected"] or not first["present"]:
            if not first["changed"]:
                break
            try:
                await page.wait_for_timeout(300)
            except Exception:
                pass
            continue

        if first["waiting_options"] and _attempt < _CATEGORY_OPTION_WAIT_ATTEMPTS - 1:
            waited_for_options = True
            try:
                await page.wait_for_timeout(_CATEGORY_OPTION_WAIT_MS)
            except Exception:
                pass
            continue
        break

    if waited_for_options and first["waiting_options"]:
        first["detail"] = (
            f"{first['detail']} (opções aguardadas por "
            f"{(_CATEGORY_OPTION_WAIT_ATTEMPTS - 1) * _CATEGORY_OPTION_WAIT_MS // 1000}s)"
        )[:240]
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
            "waiting_options": False,
            "ignored_meli": first["ignored_meli"],
        }
    picked["present"] = True
    picked["ignored_meli"] = first["ignored_meli"]
    return picked
