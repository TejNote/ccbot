"""Tests for the ccbot send subcommand routing helpers."""

import json


def test_resolve_routing_by_window_name(tmp_path):
    from ccbot.send import _resolve_routing

    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps(
            {
                "window_states": {"@9": {"window_name": "codex", "provider": "codex"}},
                "thread_bindings": {"12345": {"42": "@9"}},
                "group_chat_ids": {"12345:42": -100999},
            }
        )
    )

    assert _resolve_routing(state_file, session_id="", window_name="codex") == (
        -100999,
        42,
    )


def test_resolve_routing_by_window_display_name_without_window_state(tmp_path):
    from ccbot.send import _resolve_routing

    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps(
            {
                "window_states": {},
                "window_display_names": {"@9": "codex"},
                "thread_bindings": {"12345": {"42": "@9"}},
                "group_chat_ids": {"12345:42": -100999},
            }
        )
    )

    assert _resolve_routing(state_file, session_id="", window_name="codex") == (
        -100999,
        42,
    )
