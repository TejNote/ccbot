"""Tests for Codex window Telegram message handling."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_update(
    text: str = "hello", user_id: int = 1, thread_id: int = 42
) -> MagicMock:
    update = MagicMock()
    update.effective_user = MagicMock(id=user_id)
    update.effective_chat = MagicMock(type="supergroup", id=-100)
    update.message = MagicMock()
    update.message.text = text
    update.message.message_thread_id = thread_id
    update.message.chat = MagicMock()
    update.message.chat.send_action = AsyncMock()
    return update


def _make_context() -> MagicMock:
    context = MagicMock()
    context.bot = AsyncMock()
    context.user_data = {}
    return context


@pytest.mark.asyncio
async def test_text_handler_forwards_codex_without_snapshot_capture():
    update = _make_update("run this")
    context = _make_context()

    with (
        patch("ccbot.bot.is_user_allowed", return_value=True),
        patch("ccbot.bot._get_thread_id", return_value=42),
        patch("ccbot.bot.session_manager") as mock_sm,
        patch("ccbot.bot.tmux_manager") as mock_tmux,
        patch("ccbot.bot.enqueue_status_update", new_callable=AsyncMock),
        patch("ccbot.bot.enqueue_direct_message", new_callable=AsyncMock) as enqueue,
    ):
        mock_sm.get_window_for_thread.return_value = "@9"
        mock_sm.get_window_provider.return_value = "codex"
        mock_sm.resolve_chat_id.return_value = -100
        mock_tmux.find_window_by_id = AsyncMock(return_value=MagicMock(window_id="@9"))
        mock_tmux.capture_pane = AsyncMock(return_value="")
        mock_sm.send_to_window = AsyncMock(return_value=(True, "ok"))

        from ccbot.bot import text_handler

        await text_handler(update, context)

        mock_sm.send_to_window.assert_awaited_once_with("@9", "run this")
        enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_codex_interactive_enter_only_sends_key():
    from ccbot.bot import callback_handler
    from ccbot.handlers.callback_data import CB_ASK_ENTER

    update = MagicMock()
    update.effective_user = MagicMock(id=1)
    update.effective_chat = MagicMock(type="supergroup", id=-100)
    update.message = None
    update.callback_query = MagicMock()
    update.callback_query.data = f"{CB_ASK_ENTER}@9"
    update.callback_query.message = MagicMock(message_thread_id=42)
    update.callback_query.answer = AsyncMock()
    context = _make_context()

    with (
        patch("ccbot.bot.is_user_allowed", return_value=True),
        patch("ccbot.bot.session_manager") as mock_sm,
        patch("ccbot.bot.tmux_manager") as mock_tmux,
        patch("ccbot.bot.handle_interactive_ui", new_callable=AsyncMock),
        patch("ccbot.bot.asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_sm.get_window_provider.return_value = "codex"
        mock_tmux.find_window_by_id = AsyncMock(return_value=MagicMock(window_id="@9"))
        mock_tmux.send_keys = AsyncMock(return_value=True)

        await callback_handler(update, context)

        mock_tmux.send_keys.assert_awaited_once_with(
            "@9", "Enter", enter=False, literal=False
        )
