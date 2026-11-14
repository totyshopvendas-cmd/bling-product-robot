"""Regression coverage for the atomic single-run guard on every supplier robot."""
import asyncio

import pytest

import johndrop_bot
import shopee_bot
import trimpro_bot


class PendingTask:
    def done(self):
        return False


@pytest.mark.parametrize(
    ("bot_module", "state_name", "running_error"),
    [
        (johndrop_bot, "robot", "Robô já está em execução"),
        (trimpro_bot, "trimpro_robot", "Robô TrimPro já está em execução"),
        (shopee_bot, "shopee_robot", "Robô Shopee já está em execução"),
    ],
)
def test_start_marks_robot_running_before_background_task_can_run(
    monkeypatch, bot_module, state_name, running_error
):
    """Two rapid start requests must never create two supplier sessions."""
    state = getattr(bot_module, state_name)
    state.reset()
    state.state = "idle"
    state.task = None
    scheduled = []

    def fake_create_task(coro):
        # The coroutine would otherwise never be awaited in this unit test.
        coro.close()
        scheduled.append(PendingTask())
        return scheduled[-1]

    monkeypatch.setattr(bot_module.asyncio, "create_task", fake_create_task)
    try:
        asyncio.run(bot_module.start_bot(max_products=1, dry_run=True))

        assert state.state == "running"
        assert state.task is scheduled[0]
        with pytest.raises(ValueError, match=running_error):
            asyncio.run(bot_module.start_bot(max_products=1, dry_run=True))
        assert len(scheduled) == 1
    finally:
        state.reset()
        state.state = "idle"
        state.task = None
