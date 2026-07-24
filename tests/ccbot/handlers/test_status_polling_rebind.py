"""Tests for runtime rebinding of thread bindings when tmux re-numbers IDs.

Bug: on reboot / sleep-wake, tmux re-numbers window IDs from @0. The status
poller's stale-binding cleanup deleted a binding as soon as its persisted
window_id no longer resolved, even though a live window with the same name
existed under a new ID. Over successive reboots every binding was wiped,
leaving thread_bindings={} and all messages dropped as "No active users".

The fix: before unbinding, re-resolve by the persisted window name and remap
the binding to the new window_id. Only unbind when no window with that name
exists (window truly gone).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ccbot import session as session_mod
from ccbot.handlers import status_polling


@pytest.fixture
def isolated_sm(tmp_path, monkeypatch):
    """A SessionManager backed by a throwaway state file (never touches ~/.ccbot)."""
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(session_mod.config, "state_file", state_file)
    sm = session_mod.SessionManager()
    # status_polling imports session_manager by name, so patch the module ref.
    monkeypatch.setattr(status_polling, "session_manager", sm)
    return sm


@pytest.mark.asyncio
async def test_stale_window_id_remapped_by_name_not_deleted(
    isolated_sm, monkeypatch
):
    """A binding whose window_id died but whose name lives again → remapped."""
    isolated_sm.bind_thread(1, 10, "@5", window_name="personal")

    # @5 is gone, but 'personal' came back as @9 after a tmux restart.
    new_window = MagicMock()
    new_window.window_id = "@9"
    new_window.window_name = "personal"

    mock_tmux = MagicMock()
    mock_tmux.find_window_by_id = AsyncMock(return_value=None)
    mock_tmux.find_window_by_name = AsyncMock(return_value=new_window)
    monkeypatch.setattr(status_polling, "tmux_manager", mock_tmux)

    w = await status_polling.resolve_binding_window(1, 10, "@5")

    assert w is not None
    assert w.window_id == "@9"
    # Binding must now point at the new id, not be deleted.
    assert isolated_sm.get_window_for_thread(1, 10) == "@9"
    mock_tmux.find_window_by_name.assert_awaited_once_with("personal")


@pytest.mark.asyncio
async def test_live_window_id_returned_without_name_lookup(
    isolated_sm, monkeypatch
):
    """A binding whose window_id is still live → returned, no name lookup."""
    isolated_sm.bind_thread(1, 10, "@5", window_name="personal")

    live = MagicMock()
    live.window_id = "@5"
    live.window_name = "personal"

    mock_tmux = MagicMock()
    mock_tmux.find_window_by_id = AsyncMock(return_value=live)
    mock_tmux.find_window_by_name = AsyncMock(return_value=None)
    monkeypatch.setattr(status_polling, "tmux_manager", mock_tmux)

    w = await status_polling.resolve_binding_window(1, 10, "@5")

    assert w is not None
    assert w.window_id == "@5"
    assert isolated_sm.get_window_for_thread(1, 10) == "@5"
    mock_tmux.find_window_by_name.assert_not_awaited()


@pytest.mark.asyncio
async def test_truly_dead_window_returns_none(isolated_sm, monkeypatch):
    """Neither id nor name resolves → None (caller unbinds); binding untouched here."""
    isolated_sm.bind_thread(1, 10, "@5", window_name="personal")

    mock_tmux = MagicMock()
    mock_tmux.find_window_by_id = AsyncMock(return_value=None)
    mock_tmux.find_window_by_name = AsyncMock(return_value=None)
    monkeypatch.setattr(status_polling, "tmux_manager", mock_tmux)

    w = await status_polling.resolve_binding_window(1, 10, "@5")

    assert w is None
    # resolve_binding_window does not unbind — that's the loop's job.
    assert isolated_sm.get_window_for_thread(1, 10) == "@5"
