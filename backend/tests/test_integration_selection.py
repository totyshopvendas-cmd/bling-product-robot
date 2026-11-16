"""Regression coverage for the shared TotyShop-Bling card guard."""
import asyncio
import inspect

import pytest

import integration_selection
import johndrop_bot
import marketplace_category
import submission_confirmation
import trimpro_bot


class ProbePage:
    """A page double that records the exact browser-probe contract."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.waits = []

    async def evaluate(self, script, target):
        self.calls.append((script, target))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)


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
    assert page.calls == [(integration_selection._SELECT_INTEGRATION_PROBE, "TotyShop-Bling")]
    assert page.waits == []


def test_shared_selector_fails_closed_when_card_has_no_selected_state():
    page = ProbePage(
        {"found": True, "selected": False, "detail": "opção encontrada, mas a seleção não foi confirmada"},
        {"found": True, "selected": False, "detail": "opção encontrada, mas a seleção não foi confirmada"},
    )

    result = asyncio.run(
        integration_selection.select_required_integration(page, "TotyShop-Bling"),
    )

    assert result["found"] is True
    assert result["selected"] is False
    assert page.waits == [350]
    assert [target for _, target in page.calls] == ["TotyShop-Bling", "TotyShop-Bling"]


def test_shared_selector_rechecks_the_same_text_target_after_async_render():
    page = ProbePage(
        {"found": True, "selected": False, "detail": "aguardando React"},
        {"found": True, "selected": True, "detail": "selecionada após atualização"},
    )

    result = asyncio.run(
        integration_selection.select_required_integration(page, "TotyShop-Bling"),
    )

    assert result["selected"] is True
    assert result["detail"] == "selecionada após atualização"
    assert page.waits == [350]
    assert page.calls == [
        (integration_selection._SELECT_INTEGRATION_PROBE, "TotyShop-Bling"),
        (integration_selection._SELECT_INTEGRATION_PROBE, "TotyShop-Bling"),
    ]


def test_empty_or_unreadable_integration_never_reports_selection():
    empty_page = ProbePage({"found": True, "selected": True, "detail": "não deve executar"})
    empty = asyncio.run(integration_selection.select_required_integration(empty_page, ""))
    assert empty == {"found": False, "selected": False, "detail": "integração vazia"}
    assert empty_page.calls == []

    broken_page = ProbePage(RuntimeError("supplier DOM unavailable"))
    broken = asyncio.run(
        integration_selection.select_required_integration(broken_page, "TotyShop-Bling"),
    )
    assert broken["found"] is False
    assert broken["selected"] is False
    assert broken["detail"].startswith("falha ao verificar a integração:")


def test_textual_probe_is_scoped_to_the_named_control_not_a_checked_sibling():
    """A generic card with a checked Shopee radio cannot confirm TotyShop-Bling."""
    script = integration_selection._SELECT_INTEGRATION_PROBE

    assert script.lstrip().startswith("(target) =>")
    assert "expectedPosition" not in script
    assert "controls.some((input) => input.checked)" not in script
    assert "const control = targetControl(root);" in script
    assert "if (control) return Boolean(control.checked);" in script
    assert "Check only the" in script
    assert "target root itself" in script
    assert "TotyShop-Bling foi localizada, mas sua seleção não foi confirmada" in script


def test_recovered_selector_uses_textual_target_without_card_indexes():
    assert "expected_position" not in inspect.signature(
        integration_selection.select_required_integration,
    ).parameters
    assert "INTEGRATION_CARD_POSITION" not in inspect.getsource(johndrop_bot)
    assert "INTEGRATION_CARD_POSITION" not in inspect.getsource(trimpro_bot)
    assert "expected_position" not in inspect.getsource(johndrop_bot.diagnose_integration_controls)
    assert "expected_position" not in inspect.getsource(trimpro_bot.diagnose_integration_controls)


def test_both_supplier_robots_use_the_same_verified_integration_guard():
    """A selector correction must never diverge between JohnDrop and TrimPro."""
    assert johndrop_bot.select_required_integration is integration_selection.select_required_integration
    assert trimpro_bot.select_required_integration is integration_selection.select_required_integration


@pytest.mark.parametrize(
    ("bot_module", "log_name"),
    [
        (johndrop_bot, "add_log"),
        (trimpro_bot, "_trimpro_log"),
    ],
)
def test_both_bots_request_only_totyshop_bling(monkeypatch, bot_module, log_name):
    calls = []

    async def choose(page, target):
        calls.append((page, target))
        return {"found": True, "selected": True, "detail": "confirmada"}

    async def log(*_args, **_kwargs):
        return None

    monkeypatch.setattr(bot_module, "select_required_integration", choose)
    monkeypatch.setattr(bot_module, log_name, log)

    page = object()
    assert asyncio.run(bot_module._select_integration(page)) is True
    assert calls == [(page, "TotyShop-Bling")]


class _Navigation:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _PendingCard:
    async def get_attribute(self, attribute):
        assert attribute == "href"
        return "/dashboard/product/createv2/123"

    async def click(self):
        return None


class _GuardPage:
    async def query_selector_all(self, _selector):
        return [_PendingCard()]

    def expect_navigation(self, **_kwargs):
        return _Navigation()


@pytest.mark.parametrize(
    ("bot_module", "state_name", "log_name"),
    [
        (johndrop_bot, "robot", "add_log"),
        (trimpro_bot, "trimpro_robot", "_trimpro_log"),
    ],
)
def test_unconfirmed_integration_stops_before_product_fields_or_submit(
    monkeypatch, bot_module, state_name, log_name,
):
    """Both flows fail closed before any fill or provider submission."""
    state = getattr(bot_module, state_name)
    state_before = vars(state).copy()
    opened_catalog = []

    async def reject(_page):
        return False

    async def open_catalog(page):
        opened_catalog.append(page)

    async def unexpected_product_work(*_args, **_kwargs):
        raise AssertionError("product fields or submit must not run without TotyShop-Bling")

    async def log(*_args, **_kwargs):
        return None

    monkeypatch.setattr(bot_module, "_select_integration", reject)
    monkeypatch.setattr(bot_module, "_open_catalog", open_catalog)
    monkeypatch.setattr(bot_module, "_read_product_fields", unexpected_product_work)
    monkeypatch.setattr(bot_module, "_submit_product", unexpected_product_work)
    monkeypatch.setattr(bot_module, log_name, log)

    try:
        handled = asyncio.run(bot_module._process_one_product(_GuardPage(), dry_run=False, seen_skus=set()))
    finally:
        vars(state).clear()
        vars(state).update(state_before)

    assert handled is True
    assert opened_catalog and isinstance(opened_catalog[0], _GuardPage)


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
