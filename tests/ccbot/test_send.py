"""Tests for the ccbot send subcommand routing helpers."""

from __future__ import annotations

import json
from pathlib import Path

from ccbot.send import _resolve_routing


def _write_state(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "state.json"
    p.write_text(json.dumps(payload))
    return p


class TestResolveRouting:
    def test_state_file_missing_returns_none(self, tmp_path: Path) -> None:
        assert _resolve_routing(tmp_path / "nope.json", "", "x") is None

    def test_resolve_routing_by_window_name(self, tmp_path: Path) -> None:
        """codex 브랜치 case — provider 가 박힌 window_state 직접 매칭."""
        p = _write_state(
            tmp_path,
            {
                "window_states": {"@9": {"window_name": "codex", "provider": "codex"}},
                "thread_bindings": {"12345": {"42": "@9"}},
                "group_chat_ids": {"12345:42": -100999},
            },
        )
        assert _resolve_routing(p, "", "codex") == (-100999, 42)

    def test_window_states_match_by_session_id(self, tmp_path: Path) -> None:
        p = _write_state(
            tmp_path,
            {
                "window_states": {
                    "@9": {"session_id": "abc", "cwd": "/x", "window_name": "claude"}
                },
                "thread_bindings": {"100": {"42": "@9"}},
                "group_chat_ids": {"100:42": -1234},
            },
        )
        assert _resolve_routing(p, "abc", "") == (-1234, 42)

    def test_fallback_to_display_names_when_window_states_empty(
        self, tmp_path: Path
    ) -> None:
        """codex provider 등 startup-cleanup 후 첫 메시지 케이스.

        window_states 가 비어있어도 window_display_names + thread_bindings 만으로
        라우팅 가능해야 omx hook 의 `ccbot send --window codex` 가 silent fail 안 함.
        """
        p = _write_state(
            tmp_path,
            {
                "window_states": {},
                "window_display_names": {"@27": "codex"},
                "thread_bindings": {"285987728": {"21357": "@27"}},
                "group_chat_ids": {"285987728:21357": -1003775904155},
            },
        )
        assert _resolve_routing(p, "", "codex") == (-1003775904155, 21357)

    def test_fallback_skips_stale_window_id_not_in_thread_bindings(
        self, tmp_path: Path
    ) -> None:
        """ccbot 재기동 후 옛 window_id 가 display_names 에 잔존해도, 그 wid 가
        thread_bindings 에 실제 매핑돼 있지 않으면 fallback 후보에서 제외 — 새
        window_id 를 잡아야 silent fail 없음 (claude 브랜치 흡수)."""
        p = _write_state(
            tmp_path,
            {
                "window_states": {},
                "window_display_names": {
                    "@27": "codex",  # stale (kickstart 전 cut)
                    "@6": "codex",  # 진짜 활성
                },
                "thread_bindings": {"285987728": {"21357": "@6"}},
                "group_chat_ids": {"285987728:21357": -1003775904155},
            },
        )
        assert _resolve_routing(p, "", "codex") == (-1003775904155, 21357)

    def test_fallback_does_not_apply_for_session_id_only(self, tmp_path: Path) -> None:
        """display_names 에는 session_id 정보가 없으므로 fallback 발동 안 함."""
        p = _write_state(
            tmp_path,
            {
                "window_states": {},
                "window_display_names": {"@27": "codex"},
                "thread_bindings": {"100": {"42": "@27"}},
                "group_chat_ids": {"100:42": -1234},
            },
        )
        assert _resolve_routing(p, "some-session-id", "") is None

    def test_no_match_anywhere_returns_none(self, tmp_path: Path) -> None:
        p = _write_state(
            tmp_path,
            {
                "window_states": {},
                "window_display_names": {"@9": "claude"},
                "thread_bindings": {"100": {"42": "@9"}},
                "group_chat_ids": {"100:42": -1234},
            },
        )
        assert _resolve_routing(p, "", "ghost-window") is None

    def test_no_thread_binding_returns_none(self, tmp_path: Path) -> None:
        p = _write_state(
            tmp_path,
            {
                "window_states": {},
                "window_display_names": {"@27": "codex"},
                "thread_bindings": {},
                "group_chat_ids": {},
            },
        )
        assert _resolve_routing(p, "", "codex") is None
