"""Regression coverage for the shared TotyShop-Bling card guard."""
import asyncio

import integration_selection
import johndrop_bot
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


def test_both_supplier_robots_use_the_same_verified_integration_guard():
    """A fix for the integration card must never diverge across suppliers."""
    assert johndrop_bot.select_required_integration is integration_selection.select_required_integration
    assert trimpro_bot.select_required_integration is integration_selection.select_required_integration


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


def test_both_supplier_robots_use_the_same_submission_confirmation_guard():
    assert johndrop_bot.confirm_supplier_submission is submission_confirmation.confirm_supplier_submission
    assert trimpro_bot.confirm_supplier_submission is submission_confirmation.confirm_supplier_submission


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
