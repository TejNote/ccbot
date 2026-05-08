"""Integration test for codex provider routing in status_polling."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ccbot.handlers.status_polling import update_status_message
from ccbot.session import SessionManager, WindowState


@pytest.fixture
def mgr(monkeypatch) -> SessionManager:
    monkeypatch.setattr(SessionManager, "_load_state", lambda self: None)
    monkeypatch.setattr(SessionManager, "_save_state", lambda self: None)
    return SessionManager()


@pytest.mark.asyncio
async def test_codex_window_routes_to_codex_parser(
    mgr: SessionManager, monkeypatch
) -> None:
    """codex provider window 는 parse_codex_status_line 으로 분기되고
    그 결과가 enqueue_status_update 에 전달된다."""
    ws = WindowState(provider="codex", cwd="/x", window_name="codex")
    mgr.window_states["@27"] = ws
    mgr.window_display_names["@27"] = "codex"
    monkeypatch.setattr("ccbot.handlers.status_polling.session_manager", mgr)

    fake_window = MagicMock(window_id="@27")
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.tmux_manager.find_window_by_id",
        AsyncMock(return_value=fake_window),
    )
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.tmux_manager.capture_pane",
        AsyncMock(
            return_value=(
                "› hi\n• Working (3s • esc to interrupt)\n  gpt-5.5 high · main\n"
            )
        ),
    )

    claude_parser = MagicMock(return_value="should-not-be-called")
    codex_parser = MagicMock(return_value="⏳ Working (3s • esc to interrupt)")
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.parse_status_line", claude_parser
    )
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.parse_codex_status_line", codex_parser
    )

    enqueue = AsyncMock()
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.enqueue_status_update", enqueue
    )
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.is_interactive_ui", lambda _t: False
    )
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.get_interactive_window", lambda _u, _t: None
    )

    bot = MagicMock()
    await update_status_message(bot, user_id=1, window_id="@27", thread_id=42)

    codex_parser.assert_called_once()
    claude_parser.assert_not_called()
    enqueue.assert_awaited_once()
    args = enqueue.await_args.args
    assert args[3] == "⏳ Working (3s • esc to interrupt)"


@pytest.mark.asyncio
async def test_claude_window_routes_to_claude_parser(
    mgr: SessionManager, monkeypatch
) -> None:
    """기본 provider(claude)는 기존 parse_status_line 흐름 유지 — 회귀 보호."""
    ws = WindowState(provider="claude", cwd="/x", window_name="claude")
    mgr.window_states["@5"] = ws
    mgr.window_display_names["@5"] = "claude"
    monkeypatch.setattr("ccbot.handlers.status_polling.session_manager", mgr)

    fake_window = MagicMock(window_id="@5")
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.tmux_manager.find_window_by_id",
        AsyncMock(return_value=fake_window),
    )
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.tmux_manager.capture_pane",
        AsyncMock(return_value="✻ Sautéed for 5s · 1 shell still running\n"),
    )

    claude_parser = MagicMock(return_value="✻ Sautéed for 5s")
    codex_parser = MagicMock(return_value="should-not-be-called")
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.parse_status_line", claude_parser
    )
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.parse_codex_status_line", codex_parser
    )

    enqueue = AsyncMock()
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.enqueue_status_update", enqueue
    )
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.is_interactive_ui", lambda _t: False
    )
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.get_interactive_window", lambda _u, _t: None
    )

    bot = MagicMock()
    await update_status_message(bot, user_id=1, window_id="@5", thread_id=42)

    claude_parser.assert_called_once()
    codex_parser.assert_not_called()


@pytest.mark.asyncio
async def test_codex_fallback_via_display_name(
    mgr: SessionManager, monkeypatch
) -> None:
    """window_states 가 비어 있어도 display_name == 'codex' 면 codex 분기."""
    # WindowState 의도적으로 등록 X — startup cleanup 직후 시나리오
    mgr.window_display_names["@99"] = "codex"
    monkeypatch.setattr("ccbot.handlers.status_polling.session_manager", mgr)

    fake_window = MagicMock(window_id="@99")
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.tmux_manager.find_window_by_id",
        AsyncMock(return_value=fake_window),
    )
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.tmux_manager.capture_pane",
        AsyncMock(return_value="• Working (1s • esc to interrupt)\n"),
    )

    claude_parser = MagicMock(return_value=None)
    codex_parser = MagicMock(return_value="⏳ Working (1s)")
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.parse_status_line", claude_parser
    )
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.parse_codex_status_line", codex_parser
    )

    enqueue = AsyncMock()
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.enqueue_status_update", enqueue
    )
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.is_interactive_ui", lambda _t: False
    )
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.get_interactive_window", lambda _u, _t: None
    )

    bot = MagicMock()
    await update_status_message(bot, user_id=1, window_id="@99", thread_id=42)

    codex_parser.assert_called_once()
    claude_parser.assert_not_called()
