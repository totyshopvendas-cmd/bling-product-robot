"""Regression coverage for the shared TotyShop-Bling card guard."""
import asyncio

import integration_selection
import johndrop_bot
import marketplace_category
import submission_confirmation
import trimpro_bot


class ProbePage:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def evaluate(self, script, target):
        self.calls.append((script, target))
        return self.result


def test_shared_selector_accepts_only_a_verified_selected_state():
    page = ProbePage({"found": True, "selected": True, "detail": "selecionada e confirmada"})
    result = asyncio.run(
        integration_selection.select_required_integration(page, "TotyShop-Bling"),
    )

    assert result == {
        "found": True,
        "selected": True,
        "detail": "selecionada e confirmada",
    }
    assert page.calls[0][1] == "TotyShop-Bling"


def test_shared_selector_fails_closed_when_card_has_no_selected_state():
    page = ProbePage({
        "found": True,
        "selected": False,
        "detail": "opção encontrada, mas a seleção não foi confirmada",
    })
    result = asyncio.run(
        integration_selection.select_required_integration(page, "TotyShop-Bling"),
    )

    assert result["found"] is True
    assert result["selected"] is False


def test_shared_selector_passes_operator_confirmed_card_position_to_browser():
    page = ProbePage({"found": True, "selected": True, "detail": "selecionada"})

    result = asyncio.run(
        integration_selection.select_required_integration(
            page,
            "TotyShop-Bling",
            expected_position=4,
        ),
    )

    assert result["selected"] is True
    assert page.calls[0][1] == {"target": "TotyShop-Bling", "position": 4}


def test_selector_rejects_invalid_card_position_without_touching_supplier_form():
    page = ProbePage({"found": True, "selected": True, "detail": "não deve executar"})

    result = asyncio.run(
        integration_selection.select_required_integration(
            page,
            "TotyShop-Bling",
            expected_position=0,
        ),
    )

    assert result["selected"] is False
    assert "posição" in result["detail"]
    assert page.calls == []


def test_target_probe_never_accepts_a_checked_sibling_in_generic_card():
    """A checked Shopee radio inside a shared wrapper is not TotyShop-Bling."""
    script = integration_selection._SELECT_INTEGRATION_PROBE

    assert "controls.some((input) => input.checked)" not in script
    assert "const control = exactControl || targetControl(root);" in script
    assert "TotyShop-Bling foi localizada, mas sua seleção não foi confirmada" in script


def test_positioned_selector_uses_native_visible_radio_order_and_blocks_a_wrong_card():
    """Nested labels/wrappers cannot change the user-confirmed icon index."""
    script = integration_selection._SELECT_INTEGRATION_PROBE

    assert "const entry = integrationControls[expectedPosition - 1];" in script
    assert "Array.from(document.querySelectorAll(controlSelector))" in script
    assert ".filter(visibleControl)" in script
    assert "a.top - b.top" in script
    assert "a.left - b.left" in script
    assert "ícone não é TotyShop-Bling; seleção bloqueada" in script
    assert "count nested divs, duplicate labels, or generic card wrappers" in script
    assert "const allVisibleControls" in script
    assert "inventário dos rádios visíveis" in script
    assert "describeControls(allVisibleControls)" in script


def test_integration_probe_keeps_radio_inventory_in_the_error_detail():
    detail = "radio=" + ("x" * 700)

    result = integration_selection._result({
        "found": False,
        "selected": False,
        "detail": detail,
    })

    assert result["detail"] == detail


def test_positioned_selector_clicks_the_exact_native_control_not_a_matching_sibling():
    script = integration_selection._SELECT_INTEGRATION_PROBE

    assert "return choose(entry.root, expectedPosition, entry.input);" in script
    assert "const control = exactControl || targetControl(root);" in script
    assert "labelsFor(control).find(visible)?.click()" in script
    assert "control.click()" in script


def test_both_supplier_robots_use_the_same_verified_integration_guard():
    """A fix for the integration card must never diverge across suppliers."""
    assert johndrop_bot.select_required_integration is integration_selection.select_required_integration
    assert trimpro_bot.select_required_integration is integration_selection.select_required_integration
    assert johndrop_bot.INTEGRATION_CARD_POSITION == 4
    assert trimpro_bot.INTEGRATION_CARD_POSITION == 3


def test_johndrop_pins_totyshop_bling_to_its_fourth_icon(monkeypatch):
    calls = []

    async def choose(page, target, expected_position=None):
        calls.append((page, target, expected_position))
        return {"found": True, "selected": True, "detail": "confirmada"}

    async def log(*_args, **_kwargs):
        return None

    monkeypatch.setattr(johndrop_bot, "select_required_integration", choose)
    monkeypatch.setattr(johndrop_bot, "add_log", log)

    page = object()
    assert asyncio.run(johndrop_bot._select_integration(page)) is True
    assert calls == [(page, "TotyShop-Bling", 4)]


def test_trimpro_pins_totyshop_bling_to_its_third_icon(monkeypatch):
    calls = []

    async def choose(page, target, expected_position=None):
        calls.append((page, target, expected_position))
        return {"found": True, "selected": True, "detail": "confirmada"}

    async def log(*_args, **_kwargs):
        return None

    monkeypatch.setattr(trimpro_bot, "select_required_integration", choose)
    monkeypatch.setattr(trimpro_bot, "_trimpro_log", log)

    page = object()
    assert asyncio.run(trimpro_bot._select_integration(page)) is True
    assert calls == [(page, "TotyShop-Bling", 3)]


class ConfirmationPage:
    def __init__(self, feedback, redirects=True):
        self.feedback = feedback
        self.redirects = redirects
        self.wait_calls = []

    async def evaluate(self, *_args):
        return self.feedback

    async def wait_for_function(self, *args, **kwargs):
        self.wait_calls.append((args, kwargs))
        if not self.redirects:
            raise TimeoutError("still on create form")

    async def wait_for_load_state(self, *_args, **_kwargs):
        return None


def test_submission_is_confirmed_only_after_provider_redirect_or_success_message():
    page = ConfirmationPage({}, redirects=True)
    result = asyncio.run(submission_confirmation.confirm_supplier_submission(page))

    assert result["confirmed"] is True
    assert "redirecionamento" in result["detail"]
    assert page.wait_calls


def test_submission_rejects_visible_provider_validation_error():
    page = ConfirmationPage({"failed": True, "success": False, "message": "SKU já cadastrado"})
    result = asyncio.run(submission_confirmation.confirm_supplier_submission(page))

    assert result == {"confirmed": False, "detail": "SKU já cadastrado"}
    assert page.wait_calls == []


def test_submission_probe_reports_supplier_invalid_fields_instead_of_hiding_them():
    source = submission_confirmation._feedback.__code__.co_consts
    # The page-side probe must inspect native and framework validation state,
    # because several supplier forms do not display a toast after rejection.
    script = next(value for value in source if isinstance(value, str) and ":invalid" in value)

    assert '[aria-invalid="true"]' in script
    assert ".is-invalid" in script
    assert "validationMessage" in script


def test_both_supplier_robots_use_the_same_submission_confirmation_guard():
    assert johndrop_bot.confirm_supplier_submission is submission_confirmation.confirm_supplier_submission
    assert trimpro_bot.confirm_supplier_submission is submission_confirmation.confirm_supplier_submission


class CategoryPage:
    def __init__(self, first, second=None):
        self.responses = [first] + ([second] if second is not None else [])
        self.waits = 0

    async def evaluate(self, _script):
        return self.responses.pop(0)

    async def wait_for_timeout(self, _ms):
        self.waits += 1


def test_required_native_supplier_category_is_confirmed():
    page = CategoryPage({
        "present": True,
        "selected": True,
        "detail": "categoria selecionada: Acessórios",
    })
    result = asyncio.run(marketplace_category.ensure_required_marketplace_category(page))

    assert result["present"] is True
    assert result["selected"] is True
    assert "Acessórios" in result["detail"]


def test_missing_category_option_stops_before_provider_submit():
    page = CategoryPage({
        "present": True,
        "selected": False,
        "detail": "campo de categoria localizado, mas não há opção válida carregada",
    })
    result = asyncio.run(marketplace_category.ensure_required_marketplace_category(page))

    assert result["present"] is True
    assert result["selected"] is False


def test_both_supplier_robots_use_the_same_category_guard():
    assert johndrop_bot.ensure_required_marketplace_category is marketplace_category.ensure_required_marketplace_category
    assert trimpro_bot.ensure_required_marketplace_category is marketplace_category.ensure_required_marketplace_category


def test_johndrop_does_not_report_mock_success_when_browser_is_unavailable(monkeypatch):
    """A missing browser must be an explicit failure, never a fake cadastro."""
    async def credentials():
        return {"username": "robot@example.com", "password": "secret"}

    async def unavailable():
        return False

    monkeypatch.setattr(johndrop_bot, "_get_credentials", credentials)
    monkeypatch.setattr(johndrop_bot, "_playwright_available", unavailable)
    asyncio.run(johndrop_bot.run_bot(max_products=1, dry_run=False))

    assert not hasattr(johndrop_bot, "_run_mock")
    assert johndrop_bot.robot.state == "error"
    assert johndrop_bot.robot.success == 0


def test_trimpro_does_not_report_mock_success_when_browser_is_unavailable(monkeypatch):
    """The TrimPro path follows the same honest failure rule as JohnDrop."""
    async def credentials():
        return {"username": "robot@example.com", "password": "secret"}

    async def unavailable():
        return False

    monkeypatch.setattr(trimpro_bot, "_get_credentials", credentials)
    monkeypatch.setattr(trimpro_bot, "_playwright_available", unavailable)
    asyncio.run(trimpro_bot.run_bot(max_products=1, dry_run=False))

    assert not hasattr(trimpro_bot, "_run_mock")
    assert trimpro_bot.trimpro_robot.state == "error"
    assert trimpro_bot.trimpro_robot.success == 0


def test_native_category_guard_targets_johndrop_meli_field_even_without_html_required():
    """JohnDrop validates sel_category_meli server-side, not always in HTML."""
    script = marketplace_category._CATEGORY_FIELD_PROBE

    assert "meliCategory" in script
    assert "meliSubcategory" in script
    assert "meliRequired" in script
    assert "requiredSelects.concat(meliRequired)" in script
    assert "identity(el) === 'selcategorymeli'" in script


def test_johndrop_root_meli_category_is_filled_even_if_subcategory_element_exists():
    """A disabled subcategory is absent from POST, so root Meli must remain set."""
    script = marketplace_category._CATEGORY_FIELD_PROBE

    assert "meliCategories.concat(subcategoriesToFill)" in script
    assert "meliSubcategories.length ? meliSubcategories" not in script
    assert "new FormData(form).getAll(select.name)" in script
    assert "POST=sim" in script
    assert "POST=não" in script


def test_native_category_guard_rechecks_fields_revealed_by_category_selection():
    page = CategoryPage(
        {
            "present": True,
            "selected": True,
            "changed": True,
            "detail": "sel_category_meli: Moda",
        },
        {
            "present": True,
            "selected": True,
            "changed": False,
            "detail": "sel_meli_subCategory_meli: Bolsas",
        },
    )

    result = asyncio.run(marketplace_category.ensure_required_marketplace_category(page))

    assert result["selected"] is True
    assert "subCategory" in result["detail"]
    assert page.waits == 1


def test_category_guard_waits_for_async_supplier_options_before_failing():
    """JohnDrop must give sel_category_meli time to populate asynchronously."""
    page = CategoryPage(
        {
            "present": True,
            "selected": False,
            "waiting_options": True,
            "detail": "sel_category_meli: sem opção válida carregada",
        },
        {
            "present": True,
            "selected": True,
            "changed": False,
            "detail": "sel_category_meli=MLB1 [POST=sim; disabled=não] Casa",
        },
    )

    result = asyncio.run(marketplace_category.ensure_required_marketplace_category(page))

    assert result["selected"] is True
    assert "POST=sim" in result["detail"]
    assert page.waits == 1


def test_category_guard_reports_waited_timeout_without_submitting_blank_category():
    page = CategoryPage({
        "present": True,
        "selected": False,
        "waiting_options": True,
        "detail": "sel_category_meli: sem opção válida carregada",
    })
    # The same supplier state persists on every poll.
    page.responses *= marketplace_category._CATEGORY_OPTION_WAIT_ATTEMPTS

    result = asyncio.run(marketplace_category.ensure_required_marketplace_category(page))

    assert result["selected"] is False
    assert "opções aguardadas" in result["detail"]
    assert page.waits == marketplace_category._CATEGORY_OPTION_WAIT_ATTEMPTS - 1
