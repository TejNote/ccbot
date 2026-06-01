"""Tests for SessionManager pure dict operations."""

import pytest

from ccbot.session import SessionManager, WindowState
from ccbot.tmux_manager import TmuxWindow


@pytest.fixture
def mgr(monkeypatch) -> SessionManager:
    monkeypatch.setattr(SessionManager, "_load_state", lambda self: None)
    monkeypatch.setattr(SessionManager, "_save_state", lambda self: None)
    return SessionManager()


class TestThreadBindings:
    def test_bind_and_get(self, mgr: SessionManager) -> None:
        mgr.bind_thread(100, 1, "@1")
        assert mgr.get_window_for_thread(100, 1) == "@1"

    def test_bind_unbind_get_returns_none(self, mgr: SessionManager) -> None:
        mgr.bind_thread(100, 1, "@1")
        mgr.unbind_thread(100, 1)
        assert mgr.get_window_for_thread(100, 1) is None

    def test_unbind_nonexistent_returns_none(self, mgr: SessionManager) -> None:
        assert mgr.unbind_thread(100, 999) is None

    def test_iter_thread_bindings(self, mgr: SessionManager) -> None:
        mgr.bind_thread(100, 1, "@1")
        mgr.bind_thread(100, 2, "@2")
        mgr.bind_thread(200, 3, "@3")
        result = set(mgr.iter_thread_bindings())
        assert result == {(100, 1, "@1"), (100, 2, "@2"), (200, 3, "@3")}


class TestGroupChatId:
    """Tests for group chat_id routing (supergroup forum topic support).

    IMPORTANT: These tests protect against regression. The group_chat_ids
    mapping is required for Telegram supergroup forum topics — without it,
    all outbound messages fail with "Message thread not found". This was
    erroneously removed once (26cb81f) and restored in PR #23. Do NOT
    delete these tests or the underlying functionality.
    """

    def test_resolve_with_stored_group_id(self, mgr: SessionManager) -> None:
        """resolve_chat_id returns stored group chat_id for known thread."""
        mgr.set_group_chat_id(100, 1, -1001234567890)
        assert mgr.resolve_chat_id(100, 1) == -1001234567890

    def test_resolve_without_group_id_falls_back_to_user_id(
        self, mgr: SessionManager
    ) -> None:
        """resolve_chat_id falls back to user_id when no group_id stored."""
        assert mgr.resolve_chat_id(100, 1) == 100

    def test_resolve_none_thread_id_falls_back_to_user_id(
        self, mgr: SessionManager
    ) -> None:
        """resolve_chat_id returns user_id when thread_id is None (private chat)."""
        mgr.set_group_chat_id(100, 1, -1001234567890)
        assert mgr.resolve_chat_id(100) == 100

    def test_set_group_chat_id_overwrites(self, mgr: SessionManager) -> None:
        """set_group_chat_id updates the stored value on change."""
        mgr.set_group_chat_id(100, 1, -999)
        mgr.set_group_chat_id(100, 1, -888)
        assert mgr.resolve_chat_id(100, 1) == -888

    def test_multiple_threads_independent(self, mgr: SessionManager) -> None:
        """Different threads for the same user store independent group chat_ids."""
        mgr.set_group_chat_id(100, 1, -111)
        mgr.set_group_chat_id(100, 2, -222)
        assert mgr.resolve_chat_id(100, 1) == -111
        assert mgr.resolve_chat_id(100, 2) == -222

    def test_multiple_users_independent(self, mgr: SessionManager) -> None:
        """Different users store independent group chat_ids."""
        mgr.set_group_chat_id(100, 1, -111)
        mgr.set_group_chat_id(200, 1, -222)
        assert mgr.resolve_chat_id(100, 1) == -111
        assert mgr.resolve_chat_id(200, 1) == -222

    def test_set_group_chat_id_with_none_thread(self, mgr: SessionManager) -> None:
        """set_group_chat_id handles None thread_id (mapped to 0)."""
        mgr.set_group_chat_id(100, None, -999)
        # thread_id=None in resolve falls back to user_id (by design)
        assert mgr.resolve_chat_id(100, None) == 100
        # The stored key is "100:0", only accessible with explicit thread_id=0
        assert mgr.group_chat_ids.get("100:0") == -999


class TestWindowState:
    def test_get_creates_new(self, mgr: SessionManager) -> None:
        state = mgr.get_window_state("@0")
        assert state.session_id == ""
        assert state.cwd == ""

    def test_get_returns_existing(self, mgr: SessionManager) -> None:
        state = mgr.get_window_state("@1")
        state.session_id = "abc"
        assert mgr.get_window_state("@1").session_id == "abc"

    def test_clear_window_session(self, mgr: SessionManager) -> None:
        state = mgr.get_window_state("@1")
        state.session_id = "abc"
        mgr.clear_window_session("@1")
        assert mgr.get_window_state("@1").session_id == ""


class TestResolveWindowForThread:
    def test_none_thread_id_returns_none(self, mgr: SessionManager) -> None:
        assert mgr.resolve_window_for_thread(100, None) is None

    def test_unbound_thread_returns_none(self, mgr: SessionManager) -> None:
        assert mgr.resolve_window_for_thread(100, 42) is None

    def test_bound_thread_returns_window(self, mgr: SessionManager) -> None:
        mgr.bind_thread(100, 42, "@3")
        assert mgr.resolve_window_for_thread(100, 42) == "@3"


class TestDisplayNames:
    def test_get_display_name_fallback(self, mgr: SessionManager) -> None:
        """get_display_name returns window_id when no display name is set."""
        assert mgr.get_display_name("@99") == "@99"

    def test_set_and_get_display_name(self, mgr: SessionManager) -> None:
        mgr.bind_thread(100, 1, "@1", window_name="myproject")
        assert mgr.get_display_name("@1") == "myproject"

    def test_set_display_name_update(self, mgr: SessionManager) -> None:
        mgr.bind_thread(100, 1, "@1", window_name="old-name")
        mgr.window_display_names["@1"] = "new-name"
        assert mgr.get_display_name("@1") == "new-name"

    def test_bind_thread_sets_display_name(self, mgr: SessionManager) -> None:
        mgr.bind_thread(100, 1, "@1", window_name="proj")
        assert mgr.get_display_name("@1") == "proj"

    def test_bind_thread_without_name_no_display(self, mgr: SessionManager) -> None:
        mgr.bind_thread(100, 1, "@1")
        # No display name set, fallback to window_id
        assert mgr.get_display_name("@1") == "@1"


class TestIsWindowId:
    def test_valid_ids(self, mgr: SessionManager) -> None:
        assert mgr._is_window_id("@0") is True
        assert mgr._is_window_id("@12") is True
        assert mgr._is_window_id("@999") is True

    def test_invalid_ids(self, mgr: SessionManager) -> None:
        assert mgr._is_window_id("myproject") is False
        assert mgr._is_window_id("@") is False
        assert mgr._is_window_id("") is False
        assert mgr._is_window_id("@abc") is False


class TestWindowProvider:
    def test_window_state_round_trips_codex_provider(self) -> None:
        from ccbot.session import WindowState

        state = WindowState(
            session_id="codex-thread-01",
            cwd="/tmp/project",
            window_name="codex",
            provider="codex",
        )

        restored = WindowState.from_dict(state.to_dict())

        assert restored.provider == "codex"
        assert restored.window_name == "codex"
        assert restored.session_id == "codex-thread-01"

    def test_to_dict_omits_default_provider_for_backward_compat(self) -> None:
        """default('claude')일 때 provider 키를 직렬화하지 않아 기존
        state.json 모든 row 가 무수정으로 호환된다 (claude 브랜치 흡수)."""
        from ccbot.session import WindowState

        codex_ws = WindowState(provider="codex", window_name="codex", cwd="/x")
        assert codex_ws.to_dict()["provider"] == "codex"

        claude_ws = WindowState(window_name="claude", cwd="/x")
        assert "provider" not in claude_ws.to_dict()

    def test_from_dict_legacy_state_defaults_to_claude(self) -> None:
        """provider 키 없는 기존 state.json 도 'claude' 로 복원 (claude 브랜치 흡수)."""
        from ccbot.session import WindowState

        legacy = {"session_id": "abc", "cwd": "/x", "window_name": "claude"}
        ws = WindowState.from_dict(legacy)
        assert ws.provider == "claude"

    def test_bind_thread_detects_codex_provider_from_window_name(
        self, mgr: SessionManager
    ) -> None:
        mgr.bind_thread(100, 1, "@9", window_name="codex")

        assert mgr.get_window_provider("@9") == "codex"

    @pytest.mark.asyncio
    async def test_load_session_map_preserves_codex_window_without_claude_session_map(
        self, mgr: SessionManager, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        session_map = tmp_path / "session_map.json"
        session_map.write_text(
            '{"ccbot:@1":{"session_id":"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",'
            '"cwd":"/tmp/claude","window_name":"claude"}}'
        )
        monkeypatch.setattr("ccbot.session.config.session_map_file", session_map)
        monkeypatch.setattr("ccbot.session.config.tmux_session_name", "ccbot")

        mgr.bind_thread(100, 1, "@2", window_name="codex")
        mgr.get_window_state("@2").session_id = "019e0198-7c94-7390-8b9a-a36a62b14747"
        mgr.get_window_state("@2").cwd = "/tmp/codex"

        await mgr.load_session_map()

        assert "@2" in mgr.window_states
        assert mgr.get_window_provider("@2") == "codex"

    @pytest.mark.asyncio
    async def test_load_session_map_preserves_codex_from_display_name_only(
        self, mgr: SessionManager, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        session_map = tmp_path / "session_map.json"
        session_map.write_text(
            '{"ccbot:@1":{"session_id":"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",'
            '"cwd":"/tmp/claude","window_name":"claude"}}'
        )
        monkeypatch.setattr("ccbot.session.config.session_map_file", session_map)
        monkeypatch.setattr("ccbot.session.config.tmux_session_name", "ccbot")

        mgr.thread_bindings[100] = {1: "@2"}
        mgr.window_display_names["@2"] = "codex"
        mgr.get_window_state("@2")

        await mgr.load_session_map()

        assert "@2" in mgr.window_states
        assert mgr.get_window_state("@2").window_name == "codex"
        assert mgr.get_window_provider("@2") == "codex"


class TestResolveStaleIds:
    """Startup re-resolution when tmux re-assigns window IDs after a reboot.

    tmux re-numbers window IDs from @0 on every server (re)start, so a
    persisted ID like @6 can still exist but now point at a *different*
    window. The old logic trusted any window_id that was merely live,
    silently routing topics to the wrong window. Re-resolution must compare
    the persisted display name against the live window's actual name and
    remap by name when they disagree.
    """

    def _patch_session_map(self, monkeypatch, tmp_path):
        session_map = tmp_path / "session_map.json"
        session_map.write_text("{}")
        monkeypatch.setattr("ccbot.session.config.session_map_file", session_map)
        monkeypatch.setattr("ccbot.session.config.tmux_session_name", "ccbot")

    def _patch_live_windows(self, monkeypatch, windows: list[TmuxWindow]):
        async def fake_list_windows():
            return windows

        monkeypatch.setattr(
            "ccbot.session.tmux_manager.list_windows", fake_list_windows
        )

    @pytest.mark.asyncio
    async def test_thread_binding_follows_display_name_when_id_reused(
        self, mgr: SessionManager, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """A live-but-reassigned window_id must remap by display name.

        Past boot: codex topic (thread 1) bound to @6, whose name was 'codex'.
        After reboot an extra window shifted IDs: @6 is now 'claude', codex
        moved to @7. The codex topic must follow the name to @7.
        """
        self._patch_session_map(monkeypatch, tmp_path)
        mgr.thread_bindings[100] = {1: "@6"}
        mgr.window_display_names["@6"] = "codex"
        mgr.window_states["@6"] = WindowState(
            session_id="old-codex-sid", cwd="/tmp/codex", window_name="codex"
        )
        self._patch_live_windows(
            monkeypatch,
            [
                TmuxWindow(window_id="@6", window_name="claude", cwd="/tmp/claude"),
                TmuxWindow(window_id="@7", window_name="codex", cwd="/tmp/codex"),
            ],
        )

        await mgr.resolve_stale_ids()

        assert mgr.thread_bindings[100][1] == "@7"

    @pytest.mark.asyncio
    async def test_window_state_follows_display_name_when_id_reused(
        self, mgr: SessionManager, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """window_states keyed by a reassigned id must move to the right id."""
        self._patch_session_map(monkeypatch, tmp_path)
        mgr.window_display_names["@4"] = "smoking"
        mgr.window_states["@4"] = WindowState(
            session_id="smoking-sid", cwd="/tmp/smoking", window_name="smoking"
        )
        self._patch_live_windows(
            monkeypatch,
            [
                TmuxWindow(window_id="@3", window_name="insudeal", cwd="/tmp/ins"),
                TmuxWindow(window_id="@4", window_name="scraping", cwd="/tmp/scr"),
                TmuxWindow(window_id="@5", window_name="smoking", cwd="/tmp/smoking"),
            ],
        )

        await mgr.resolve_stale_ids()

        assert "@5" in mgr.window_states
        assert mgr.window_states["@5"].session_id == "smoking-sid"
        # @4 must no longer hold the stale smoking state
        assert mgr.window_states.get("@4") is None or (
            mgr.window_states["@4"].session_id != "smoking-sid"
        )

    @pytest.mark.asyncio
    async def test_unchanged_window_is_left_intact(
        self, mgr: SessionManager, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """When the id still points at the same-named window, keep it as-is.

        Guards against churning a window that was merely renamed-in-place and
        already reconciled (display name matches live name).
        """
        self._patch_session_map(monkeypatch, tmp_path)
        mgr.thread_bindings[100] = {1: "@6"}
        mgr.window_display_names["@6"] = "claude"
        mgr.window_states["@6"] = WindowState(
            session_id="claude-sid", cwd="/tmp/claude", window_name="claude"
        )
        self._patch_live_windows(
            monkeypatch,
            [TmuxWindow(window_id="@6", window_name="claude", cwd="/tmp/claude")],
        )

        await mgr.resolve_stale_ids()

        assert mgr.thread_bindings[100][1] == "@6"
        assert mgr.window_states["@6"].session_id == "claude-sid"

    @pytest.mark.asyncio
    async def test_user_offset_follows_display_name_when_id_reused(
        self, mgr: SessionManager, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """The per-user read offset must travel with the window, not the id."""
        self._patch_session_map(monkeypatch, tmp_path)
        mgr.window_display_names["@4"] = "smoking"
        mgr.window_states["@4"] = WindowState(
            session_id="smoking-sid", cwd="/tmp/smoking", window_name="smoking"
        )
        mgr.user_window_offsets[100] = {"@4": 500}
        self._patch_live_windows(
            monkeypatch,
            [
                TmuxWindow(window_id="@4", window_name="scraping", cwd="/tmp/scr"),
                TmuxWindow(window_id="@5", window_name="smoking", cwd="/tmp/smoking"),
            ],
        )

        await mgr.resolve_stale_ids()

        assert mgr.user_window_offsets[100].get("@5") == 500
        assert "@4" not in mgr.user_window_offsets[100]

    @pytest.mark.asyncio
    async def test_thread_binding_remaps_via_window_state_name_when_display_absent(
        self, mgr: SessionManager, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """Old-format upgrade: window_display_names is empty but WindowState
        still carries the name. The thread binding must remap by that name,
        not get dropped — the window_states loop already uses window_name as
        a fallback, so the thread_bindings loop must stay symmetric.
        """
        self._patch_session_map(monkeypatch, tmp_path)
        mgr.thread_bindings[100] = {1: "@6"}
        mgr.window_states["@6"] = WindowState(
            session_id="codex-sid", cwd="/tmp/codex", window_name="codex"
        )
        # Intentionally do NOT set window_display_names["@6"].
        self._patch_live_windows(
            monkeypatch,
            [
                TmuxWindow(window_id="@6", window_name="claude", cwd="/tmp/claude"),
                TmuxWindow(window_id="@7", window_name="codex", cwd="/tmp/codex"),
            ],
        )

        await mgr.resolve_stale_ids()

        assert mgr.thread_bindings[100][1] == "@7"
