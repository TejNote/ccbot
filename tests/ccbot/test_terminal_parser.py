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


class TestParseCodexStatusLine:
    """codex provider 의 thinking/tool status 추출 — 실측 fixture 기반."""

    def test_working_line_returns_thinking_status(self) -> None:
        """• Working (Xs ...) 라인이 있으면 ⏳ prefix 로 반환."""
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

    def test_working_line_with_bg_info_preserved(self) -> None:
        from ccbot.terminal_parser import parse_codex_status_line

        pane = (
            "› 작업\n"
            "• Working (12s • esc to interrupt) · 1 background terminal running · /ps to view · /stop to close\n"
            "  gpt-5.5 high · main\n"
        )
        result = parse_codex_status_line(pane)
        assert result is not None
        assert "Working" in result
        assert "background terminal" in result

    def test_tool_use_line_returns_tool_status(self) -> None:
        """thinking 없을 때 가장 최근 • <Verb> 도구 사용 라인 반환."""
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

    def test_thinking_takes_priority_over_tool(self) -> None:
        """Working 라인이 있으면 그게 우선, 도구 라인은 무시."""
        from ccbot.terminal_parser import parse_codex_status_line

        pane = (
            "› 작업\n"
            "• Ran sleep 5\n"
            "• Working (5s • esc to interrupt)\n"
            "  gpt-5.5 high · main\n"
        )
        result = parse_codex_status_line(pane)
        assert result is not None
        assert result.startswith("⏳")
        assert "Working" in result

    def test_idle_returns_none(self) -> None:
        """status bar + placeholder만 있으면 None."""
        from ccbot.terminal_parser import parse_codex_status_line

        pane = (
            "› Implement {feature}\n"
            "  gpt-5.5 high · 5h 99% · weekly 73% · Context 94% left · main\n"
        )
        assert parse_codex_status_line(pane) is None

    def test_response_text_returns_none(self) -> None:
        """일반 응답 본문(• 안녕하세요...)은 thinking/tool 패턴에 매칭 안 됨 → None."""
        from ccbot.terminal_parser import parse_codex_status_line

        pane = (
            "› 안녕\n"
            "• 안녕하세요! 무엇을 도와드릴까요?\n"
            "  gpt-5.5 high · main\n"
        )
        assert parse_codex_status_line(pane) is None

    def test_hook_meta_lines_filtered(self) -> None:
        """• SessionStart hook (completed) 같은 메타 라인은 무시."""
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

    def test_empty_pane_returns_none(self) -> None:
        from ccbot.terminal_parser import parse_codex_status_line

        assert parse_codex_status_line("") is None
        assert parse_codex_status_line("\n\n\n") is None

    def test_status_bar_filtered_from_result(self) -> None:
        """status bar 자체는 결과에 포함되지 않는다."""
        from ccbot.terminal_parser import parse_codex_status_line

        pane = (
            "• Ran echo hello\n"
            "  gpt-5.5 high · 5h 99% · weekly 73% · Context 94% left · main\n"
        )
        result = parse_codex_status_line(pane)
        assert result is not None
        assert "gpt-5.5" not in result
