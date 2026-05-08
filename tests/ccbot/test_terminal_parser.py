"""Tests for terminal_parser — regex-based detection of Claude Code UI elements."""

import pytest

from ccbot.terminal_parser import (
    extract_bash_output,
    extract_interactive_content,
    is_interactive_ui,
    parse_status_line,
    strip_pane_chrome,
)

# ── parse_status_line ────────────────────────────────────────────────────


class TestParseStatusLine:
    @pytest.mark.parametrize(
        ("spinner", "rest", "expected"),
        [
            ("·", "Working on task", "Working on task"),
            ("✻", "  Reading file  ", "Reading file"),
            ("✽", "Thinking deeply", "Thinking deeply"),
            ("✶", "Analyzing code", "Analyzing code"),
            ("✳", "Processing input", "Processing input"),
            ("✢", "Building project", "Building project"),
        ],
    )
    def test_spinner_chars(self, spinner: str, rest: str, expected: str, chrome: str):
        pane = f"some output\n{spinner}{rest}\n{chrome}"
        assert parse_status_line(pane) == expected

    @pytest.mark.parametrize(
        "pane",
        [
            pytest.param("just normal text\nno spinners here\n", id="no_spinner"),
            pytest.param("", id="empty"),
        ],
    )
    def test_returns_none(self, pane: str):
        assert parse_status_line(pane) is None

    def test_no_chrome_returns_none(self):
        """Without chrome separator, status can't be determined."""
        pane = "output\n✻ Doing work\nno chrome here\n"
        assert parse_status_line(pane) is None

    def test_blank_line_between_status_and_chrome(self, chrome: str):
        """Status line with blank lines before separator."""
        pane = f"output\n✻ Doing work\n\n{chrome}"
        assert parse_status_line(pane) == "Doing work"

    def test_idle_no_status(self, chrome: str):
        """Idle pane (no status line above chrome) returns None."""
        pane = f"some output\n● Tool result\n{chrome}"
        assert parse_status_line(pane) is None

    def test_false_positive_bullet(self, chrome: str):
        """· in regular output must NOT be detected as status."""
        pane = f"· bullet point one\n· bullet point two\nsome result\n{chrome}"
        assert parse_status_line(pane) is None

    def test_uses_fixture(self, sample_pane_status_line: str):
        assert parse_status_line(sample_pane_status_line) == "Reading file src/main.py"

    @pytest.mark.parametrize(
        "spinner_text",
        [
            "· Sautéed for 3s · 1 shell still running",
            "· Sautéed for 12s · 2 shells still running",
            "✻ Generating… (3s · 1 shell still running)",
        ],
    )
    def test_background_shell_indicator_not_status(
        self, spinner_text: str, chrome: str
    ):
        """Spinner line that only indicates background shells (no active working
        signal like 'esc to interrupt') must not be treated as a working status.

        These lines appear briefly after a turn ends while a backgrounded Bash
        tool is still alive — the user is free to send the next message, so we
        must not enqueue a stale status message that would persist after the
        background shell exits.
        """
        pane = f"some output\n{spinner_text}\n{chrome}"
        assert parse_status_line(pane) is None

    @pytest.mark.parametrize(
        ("spinner_text", "expected"),
        [
            (
                "· Sautéed for 3s · esc to interrupt",
                "Sautéed for 3s · esc to interrupt",
            ),
            (
                "✻ Generating… (12s · ↓ 2k tokens · esc to interrupt)",
                "Generating… (12s · ↓ 2k tokens · esc to interrupt)",
            ),
        ],
    )
    def test_active_working_still_detected(
        self, spinner_text: str, expected: str, chrome: str
    ):
        """Active working spinner ('esc to interrupt' present) must still be
        detected — only background-only indicators are filtered out."""
        pane = f"some output\n{spinner_text}\n{chrome}"
        assert parse_status_line(pane) == expected


# ── extract_interactive_content ──────────────────────────────────────────


class TestExtractInteractiveContent:
    def test_exit_plan_mode(self, sample_pane_exit_plan: str):
        result = extract_interactive_content(sample_pane_exit_plan)
        assert result is not None
        assert result.name == "ExitPlanMode"
        assert "Would you like to proceed?" in result.content
        assert "ctrl-g to edit in" in result.content

    def test_exit_plan_mode_variant(self):
        pane = (
            "  Claude has written up a plan\n  ─────\n  Details here\n  Esc to cancel\n"
        )
        result = extract_interactive_content(pane)
        assert result is not None
        assert result.name == "ExitPlanMode"
        assert "Claude has written up a plan" in result.content

    def test_ask_user_multi_tab(self, sample_pane_ask_user_multi_tab: str):
        result = extract_interactive_content(sample_pane_ask_user_multi_tab)
        assert result is not None
        assert result.name == "AskUserQuestion"
        assert "←" in result.content

    def test_ask_user_single_tab(self, sample_pane_ask_user_single_tab: str):
        result = extract_interactive_content(sample_pane_ask_user_single_tab)
        assert result is not None
        assert result.name == "AskUserQuestion"
        assert "Enter to select" in result.content

    def test_permission_prompt(self, sample_pane_permission: str):
        result = extract_interactive_content(sample_pane_permission)
        assert result is not None
        assert result.name == "PermissionPrompt"
        assert "Do you want to proceed?" in result.content

    def test_codex_command_permission_prompt(self):
        pane = (
            "  Would you like to run the following command?\n"
            "\n"
            "  Reason: Telegram 메시지 수신 여부를 확인합니다.\n"
            "\n"
            "  $ printf '%s\\n' '--- process ---'\n"
            "\n"
            "› 1. Yes, proceed (y)\n"
            "  2. No, and tell Codex what to do differently (esc)\n"
            "\n"
            "  Press enter to confirm or esc to cancel\n"
        )

        result = extract_interactive_content(pane)

        assert result is not None
        assert result.name == "PermissionPrompt"
        assert "Would you like to run the following command?" in result.content
        assert "Press enter to confirm or esc to cancel" in result.content

    def test_restore_checkpoint(self):
        pane = (
            "  Restore the code to a previous state?\n"
            "  ─────\n"
            "  Some details\n"
            "  Enter to continue\n"
        )
        result = extract_interactive_content(pane)
        assert result is not None
        assert result.name == "RestoreCheckpoint"
        assert "Restore the code" in result.content

    def test_settings(self):
        pane = "  Settings: press tab to cycle\n  ─────\n  Option 1\n  Esc to cancel\n"
        result = extract_interactive_content(pane)
        assert result is not None
        assert result.name == "Settings"
        assert "Settings:" in result.content

    def test_settings_model_picker(self, sample_pane_settings: str):
        result = extract_interactive_content(sample_pane_settings)
        assert result is not None
        assert result.name == "Settings"
        assert "Select model" in result.content
        assert "Sonnet" in result.content
        assert "Enter to confirm" in result.content

    def test_settings_esc_to_cancel_bottom(self):
        pane = (
            "  Settings: press tab to cycle\n"
            "  ─────\n"
            "  Model\n"
            "  ─────\n"
            "  ● claude-sonnet-4-20250514\n"
            "  ○ claude-opus-4-20250514\n"
            "  Esc to cancel\n"
        )
        result = extract_interactive_content(pane)
        assert result is not None
        assert result.name == "Settings"
        assert "Esc to cancel" in result.content

    def test_settings_esc_to_exit_bottom(self):
        pane = (
            "  Settings: press tab to cycle\n"
            "  ─────\n"
            "  Model\n"
            "  ─────\n"
            "  ● Default (Opus 4.6)\n"
            "  ○ claude-sonnet-4-20250514\n"
            "\n"
            "  Enter to confirm · Esc to exit\n"
        )
        result = extract_interactive_content(pane)
        assert result is not None
        assert result.name == "Settings"
        assert "Enter to confirm" in result.content

    @pytest.mark.parametrize(
        "pane",
        [
            pytest.param("$ echo hello\nhello\n$\n", id="no_ui"),
            pytest.param("", id="empty"),
        ],
    )
    def test_returns_none(self, pane: str):
        assert extract_interactive_content(pane) is None

    def test_min_gap_too_small_returns_none(self):
        pane = "  Do you want to proceed?\n  Esc to cancel\n"
        assert extract_interactive_content(pane) is None


# ── is_interactive_ui ────────────────────────────────────────────────────


class TestIsInteractiveUI:
    def test_true_when_ui_present(self, sample_pane_exit_plan: str):
        assert is_interactive_ui(sample_pane_exit_plan) is True

    def test_false_when_no_ui(self, sample_pane_no_ui: str):
        assert is_interactive_ui(sample_pane_no_ui) is False

    def test_settings_is_interactive(self, sample_pane_settings: str):
        assert is_interactive_ui(sample_pane_settings) is True

    def test_false_for_empty_string(self):
        assert is_interactive_ui("") is False


# ── strip_pane_chrome ───────────────────────────────────────────────────


class TestStripPaneChrome:
    def test_strips_from_separator(self):
        lines = [
            "some output",
            "more output",
            "─" * 30,
            "❯",
            "─" * 30,
            "  [Opus 4.6] Context: 34%",
        ]
        assert strip_pane_chrome(lines) == ["some output", "more output"]

    def test_no_separator_returns_all(self):
        lines = ["line 1", "line 2", "line 3"]
        assert strip_pane_chrome(lines) == lines

    def test_short_separator_not_triggered(self):
        lines = ["output", "─" * 10, "more output"]
        assert strip_pane_chrome(lines) == lines

    def test_only_searches_last_10_lines(self):
        # Separator at line 0 with 15 lines total — outside the last-10 window
        lines = ["─" * 30] + [f"line {i}" for i in range(14)]
        assert strip_pane_chrome(lines) == lines


# ── extract_bash_output ─────────────────────────────────────────────────


class TestExtractBashOutput:
    def test_extracts_command_output(self):
        pane = "some context\n! echo hello\n⎿ hello\n"
        result = extract_bash_output(pane, "echo hello")
        assert result is not None
        assert "! echo hello" in result
        assert "hello" in result

    def test_command_not_found_returns_none(self):
        pane = "some context\njust normal output\n"
        assert extract_bash_output(pane, "echo hello") is None

    def test_chrome_stripped(self):
        pane = (
            "some context\n"
            "! ls\n"
            "⎿ file.txt\n"
            + "─" * 30
            + "\n"
            + "❯\n"
            + "─" * 30
            + "\n"
            + "  [Opus 4.6] Context: 34%\n"
        )
        result = extract_bash_output(pane, "ls")
        assert result is not None
        assert "file.txt" in result
        assert "Opus" not in result

    def test_prefix_match_long_command(self):
        pane = "! long_comma…\n⎿ output\n"
        result = extract_bash_output(pane, "long_command_that_gets_truncated")
        assert result is not None
        assert "output" in result

    def test_trailing_blank_lines_stripped(self):
        pane = "! echo hi\n⎿ hi\n\n\n"
        result = extract_bash_output(pane, "echo hi")
        assert result is not None
        assert not result.endswith("\n")


class TestPaneSnapshotFormatting:
    def test_strip_ansi_control_sequences(self):
        from ccbot.terminal_parser import strip_ansi_control_sequences

        assert strip_ansi_control_sequences("\x1b[31mred\x1b[0m\r\n") == "red\n"

    def test_format_pane_snapshot_strips_chrome_and_limits_blank_lines(self):
        from ccbot.terminal_parser import format_pane_snapshot

        pane = (
            "\x1b[32mAnswer line\x1b[0m\n"
            "\n"
            "\n"
            "More detail\n"
            "──────────────────────────────────────\n"
            "❯ prompt\n"
            "──────────────────────────────────────\n"
            "model · context\n"
        )

        assert format_pane_snapshot(pane) == "Answer line\n\nMore detail"


class TestParseCodexStatusLine:
    """Codex provider thinking/tool status extraction."""

    def test_working_line_returns_thinking_status(self) -> None:
        from ccbot.terminal_parser import parse_codex_status_line

        pane = (
            "› 5초 기다린 후 README 출력\n"
            "• Working (3s • esc to interrupt)\n"
            "  gpt-5.5 high · 5h 99% · weekly 73% · Context 94% left · main\n"
        )

        result = parse_codex_status_line(pane)

        assert result is not None
        assert result.startswith("⏳")
        assert "Working" in result
        assert "(3s" in result

    def test_tool_use_line_returns_tool_status(self) -> None:
        from ccbot.terminal_parser import parse_codex_status_line

        pane = (
            "› LICENSE 보여줘\n"
            "• Read LICENSE\n"
            "  gpt-5.5 high · 5h 99% · weekly 73% · Context 94% left · main\n"
        )

        result = parse_codex_status_line(pane)

        assert result is not None
        assert result.startswith("🔧")
        assert "Read" in result

    def test_response_text_returns_none(self) -> None:
        from ccbot.terminal_parser import parse_codex_status_line

        pane = "› 안녕\n• 안녕하세요! 무엇을 도와드릴까요?\n  gpt-5.5 high · main\n"

        assert parse_codex_status_line(pane) is None

    def test_hook_meta_lines_filtered(self) -> None:
        from ccbot.terminal_parser import parse_codex_status_line

        pane = (
            "› 안녕\n"
            "• SessionStart hook (completed)\n"
            "• UserPromptSubmit hook (completed)\n"
            "• Working (1s • esc to interrupt)\n"
            "  gpt-5.5 high · main\n"
        )

        result = parse_codex_status_line(pane)

        assert result is not None
        assert "Working" in result

    def test_idle_returns_none(self) -> None:
        from ccbot.terminal_parser import parse_codex_status_line

        assert parse_codex_status_line("") is None
        assert parse_codex_status_line("\n\n\n") is None
        assert (
            parse_codex_status_line("› Implement {feature}\n  gpt-5.5 high · main\n")
            is None
        )
