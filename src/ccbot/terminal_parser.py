"""Terminal output parser — detects Claude Code UI elements in pane text.

Parses captured tmux pane content to detect:
  - Interactive UIs (AskUserQuestion, ExitPlanMode, Permission Prompt,
    RestoreCheckpoint) via regex-based UIPattern matching with top/bottom
    delimiters.
  - Status line (spinner characters + working text) by scanning from bottom up.

All Claude Code text patterns live here. To support a new UI type or
a changed Claude Code version, edit UI_PATTERNS / STATUS_SPINNERS.

Key functions: is_interactive_ui(), extract_interactive_content(),
parse_status_line(), strip_pane_chrome(), extract_bash_output().
"""

import re
from dataclasses import dataclass


@dataclass
class InteractiveUIContent:
    """Content extracted from an interactive UI."""

    content: str  # The extracted display content
    name: str = ""  # Pattern name that matched (e.g. "AskUserQuestion")


@dataclass(frozen=True)
class UIPattern:
    """A text-marker pair that delimits an interactive UI region.

    Extraction scans lines top-down: the first line matching any `top` pattern
    marks the start, the first subsequent line matching any `bottom` pattern
    marks the end.  Both boundary lines are included in the extracted content.

    ``top`` and ``bottom`` are tuples of compiled regexes — any single match
    is sufficient.  This accommodates wording changes across Claude Code
    versions (e.g. a reworded confirmation prompt).
    """

    name: str  # Descriptive label (not used programmatically)
    top: tuple[re.Pattern[str], ...]
    bottom: tuple[re.Pattern[str], ...]
    min_gap: int = 2  # minimum lines between top and bottom (inclusive)


# ── UI pattern definitions (order matters — first match wins) ────────────

UI_PATTERNS: list[UIPattern] = [
    UIPattern(
        # Workspace trust dialog shown on first launch in a directory.
        # Since ~2.1.260 the options are unnumbered and "No, exit" is the
        # default, so an unanswered or blindly-confirmed dialog ends the session.
        name="TrustDialog",
        top=(
            re.compile(r"^\s*Quick safety check"),
            re.compile(r"^\s*Do you trust the files in this folder"),
        ),
        bottom=(re.compile(r"Enter to confirm"),),
    ),
    UIPattern(
        name="ExitPlanMode",
        top=(
            re.compile(r"^\s*Would you like to proceed\?"),
            # v2.1.29+: longer prefix that may wrap across lines
            re.compile(r"^\s*Claude has written up a plan"),
        ),
        bottom=(
            re.compile(r"^\s*ctrl-g to edit in "),
            re.compile(r"^\s*Esc to (cancel|exit)"),
        ),
    ),
    UIPattern(
        name="AskUserQuestion",
        top=(re.compile(r"^\s*←\s+[☐✔☒]"),),  # Multi-tab: no bottom needed
        bottom=(),
        min_gap=1,
    ),
    UIPattern(
        name="AskUserQuestion",
        top=(re.compile(r"^\s*[☐✔☒]"),),  # Single-tab: bottom required
        bottom=(re.compile(r"^\s*Enter to select"),),
        min_gap=1,
    ),
    UIPattern(
        name="PermissionPrompt",
        top=(
            re.compile(r"^\s*Do you want to proceed\?"),
            re.compile(r"^\s*Do you want to make this edit"),
            re.compile(r"^\s*Do you want to create \S"),
            re.compile(r"^\s*Do you want to delete \S"),
            re.compile(r"^\s*Would you like to run the following command\?"),
        ),
        bottom=(
            re.compile(r"^\s*Esc to cancel"),
            re.compile(r"(?i)esc to cancel"),
        ),
    ),
    UIPattern(
        # Permission menu with numbered choices (no "Esc to cancel" line)
        name="PermissionPrompt",
        top=(re.compile(r"^\s*[❯›]\s*1\.\s*Yes"),),
        bottom=(),
        min_gap=2,
    ),
    UIPattern(
        # Bash command approval
        name="BashApproval",
        top=(
            re.compile(r"^\s*Bash command\s*$"),
            re.compile(r"^\s*This command requires approval"),
        ),
        bottom=(re.compile(r"^\s*Esc to cancel"),),
    ),
    UIPattern(
        name="RestoreCheckpoint",
        top=(re.compile(r"^\s*Restore the code"),),
        bottom=(re.compile(r"^\s*Enter to continue"),),
    ),
    UIPattern(
        name="Settings",
        top=(
            re.compile(r"^\s*Settings:.*tab to cycle"),
            re.compile(r"^\s*Select model"),
        ),
        bottom=(
            re.compile(r"Esc to cancel"),
            re.compile(r"Esc to exit"),
            re.compile(r"Enter to confirm"),
            re.compile(r"^\s*Type to filter"),
        ),
    ),
]


# Dialogs the bot answers itself: name -> text of the option to select.
AUTO_ANSWER_DIALOGS: dict[str, str] = {
    "TrustDialog": "Yes, I trust",
}

_RE_MENU_CURSOR = re.compile(r"^\s*❯\s*\S")


# ── Post-processing ──────────────────────────────────────────────────────

_RE_LONG_DASH = re.compile(r"^─{5,}$")


def _shorten_separators(text: str) -> str:
    """Replace lines of 5+ ─ characters with exactly ─────."""
    return "\n".join(
        "─────" if _RE_LONG_DASH.match(line) else line for line in text.split("\n")
    )


# ── Core extraction ──────────────────────────────────────────────────────


def _try_extract(lines: list[str], pattern: UIPattern) -> InteractiveUIContent | None:
    """Try to extract content matching a single UI pattern.

    When ``pattern.bottom`` is empty, the region extends from the top marker
    to the last non-empty line (used for multi-tab AskUserQuestion where the
    bottom delimiter varies by tab).
    """
    top_idx: int | None = None
    bottom_idx: int | None = None

    for i, line in enumerate(lines):
        if top_idx is None:
            if any(p.search(line) for p in pattern.top):
                top_idx = i
        elif pattern.bottom and any(p.search(line) for p in pattern.bottom):
            bottom_idx = i
            break

    if top_idx is None:
        return None

    # No bottom patterns → use last non-empty line as boundary
    if not pattern.bottom:
        for i in range(len(lines) - 1, top_idx, -1):
            if lines[i].strip():
                bottom_idx = i
                break

    if bottom_idx is None or bottom_idx - top_idx < pattern.min_gap:
        return None

    content = "\n".join(lines[top_idx : bottom_idx + 1]).rstrip()
    return InteractiveUIContent(content=_shorten_separators(content), name=pattern.name)


# ── Public API ───────────────────────────────────────────────────────────


def extract_interactive_content(pane_text: str) -> InteractiveUIContent | None:
    """Extract content from an interactive UI in terminal output.

    Tries each UI pattern in declaration order; first match wins.
    Returns None if no recognizable interactive UI is found.
    """
    if not pane_text:
        return None

    lines = pane_text.strip().split("\n")
    for pattern in UI_PATTERNS:
        result = _try_extract(lines, pattern)
        if result:
            return result
    return None


def is_interactive_ui(pane_text: str) -> bool:
    """Check if terminal currently shows an interactive UI."""
    return extract_interactive_content(pane_text) is not None


def find_menu_option(pane_text: str, needle: str) -> tuple[int, int] | None:
    """Locate the highlighted (``❯``) line and the option containing ``needle``.

    Returns ``(cursor_idx, option_idx)`` line indices; pressing Down
    ``option_idx - cursor_idx`` times moves the cursor onto the option.
    The option is searched from the bottom (dialogs render at the end of the
    pane) and the cursor is the ``❯`` nearest to it, so an earlier shell
    prompt such as ``❯ claude`` is never mistaken for the menu cursor.
    """
    lines = pane_text.split("\n")
    option_idx = next(
        (i for i in range(len(lines) - 1, -1, -1) if needle in lines[i]), None
    )
    if option_idx is None:
        return None
    lo, hi = max(0, option_idx - 10), min(len(lines), option_idx + 11)
    cursor_idx = min(
        (i for i in range(lo, hi) if _RE_MENU_CURSOR.match(lines[i])),
        key=lambda i: abs(i - option_idx),
        default=None,
    )
    if cursor_idx is None:
        return None
    return cursor_idx, option_idx


def _is_separator(line: str) -> bool:
    """A chrome border line.

    Named sessions (--resume <name>, --name, /rename) put the name in the
    input box's top border: "──────── my-session ─" (one trailing dash).
    """
    stripped = line.strip()
    if len(stripped) < 20 or not stripped.startswith("────"):
        return False
    return stripped.endswith("─") and stripped.count("─") / len(stripped) >= 0.6


def is_prompt_ready(pane_text: str) -> bool:
    """True when Claude Code shows its idle input box.

    Layout at the bottom of the pane::

        ────────────────  (separator)
        ❯ …               (input line)
        ────────────────  (separator)
          ⏵⏵ … (footer)
    """
    if not pane_text:
        return False
    tail = pane_text.rstrip().split("\n")[-8:]
    for i in range(len(tail) - 2):
        if not _is_separator(tail[i]) or not tail[i + 1].lstrip().startswith("❯"):
            continue
        if any(_is_separator(tail[j]) for j in range(i + 2, min(i + 5, len(tail)))):
            return True
    return False


# ── Status line parsing ─────────────────────────────────────────────────

# Spinner characters Claude Code uses in its status line
STATUS_SPINNERS = frozenset(["·", "✻", "✽", "✶", "✳", "✢"])

# ── codex status line patterns ─────────────────────────────────────────
#
# Claude status uses a spinner line near the bottom chrome. Codex TUI exposes
# progress as text lines such as `• Working (3s • esc to interrupt)` and tool
# lines such as `• Ran ...`. We only surface these status/tool lines; regular
# response text must not become a status update.

CODEX_THINKING_RE = re.compile(r"^\s*•\s+Working\s+\(\d+s\b")
CODEX_TOOL_VERBS = (
    "Ran",
    "Read",
    "Edit",
    "Wrote",
    "Explored",
    "Searched",
    "Bash",
    "Code",
    "Patch",
    "Diff",
)
CODEX_TOOL_RE = re.compile(rf"^\s*•\s+(?:{'|'.join(CODEX_TOOL_VERBS)})\b")
CODEX_HOOK_RE = re.compile(
    r"^\s*•\s+(?:SessionStart|UserPromptSubmit|PreToolUse|PostToolUse|Stop)\s+hook\b"
)
CODEX_STATUS_BAR_RE = re.compile(r"^\s*gpt-[\d.]+(?:\s+\w+)?\s+·")
CODEX_TOOL_LINE_MAX = 100


def parse_codex_status_line(pane_text: str) -> str | None:
    """Extract Codex thinking/tool status from a captured pane.

    Returns a short status for in-place Telegram status updates, or None for
    idle/regular response text. Final Codex replies are expected to be pushed by
    the external OMX/codex hook bridge, not by pane snapshots.
    """
    if not pane_text:
        return None

    lines = pane_text.split("\n")
    last_tool: str | None = None

    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if CODEX_STATUS_BAR_RE.match(line):
            continue
        if CODEX_HOOK_RE.match(line):
            continue
        if CODEX_THINKING_RE.match(line):
            return f"⏳ {stripped[:CODEX_TOOL_LINE_MAX]}"
        if last_tool is None and CODEX_TOOL_RE.match(line):
            last_tool = stripped[:CODEX_TOOL_LINE_MAX]

    if last_tool is not None:
        return f"🔧 {last_tool}"
    return None


_BACKGROUND_SHELL_RE = re.compile(r"\b\d+\s+shells?\s+still\s+running\b")


def parse_status_line(pane_text: str) -> str | None:
    """Extract the Claude Code status line from terminal output.

    The status line (spinner + working text) appears above the chrome
    separator (a full line of ``─`` characters).  We locate the separator
    first, then scan upward.  The line *directly* above it counts as a status
    line on the spinner alone; further up, Claude Code may have rendered a tip
    block in between, so a spinner line only counts when it carries the ``…``
    of a live status **and** is not a plain ``·`` bullet.  That pair of
    conditions applies to non-adjacent lines only — directly above the
    separator, ``✻ Sautéed for 7s`` is still returned as status (unchanged
    behaviour).

    Returns the text after the spinner, or None if no status line found.

    Background-only indicator filter: when the spinner text only reflects a
    surviving backgrounded Bash tool (e.g. ``Sautéed for 3s · 1 shell still
    running``) and contains no active working signal (``esc to interrupt``),
    the turn is effectively over — return None so a new status message is not
    enqueued and left stale once the background shell exits.
    """
    if not pane_text:
        return None

    lines = pane_text.split("\n")

    # Find the chrome separator: topmost ──── line in the last 10 lines
    chrome_idx: int | None = None
    search_start = max(0, len(lines) - 10)
    for i in range(search_start, len(lines)):
        stripped = lines[i].strip()
        if len(stripped) >= 20 and all(c == "─" for c in stripped):
            chrome_idx = i
            break

    if chrome_idx is None:
        return None  # No chrome visible — can't determine status

    # Scan upward for the spinner line. Claude Code can render a tip block
    # between the status line and the chrome, so keep looking past non-spinner
    # lines (upstream six-ddc/ccbot#97).
    #
    # 🚨 Past the line directly above the separator the bar is higher, because
    #    prose lives up there. Two conditions, and both are needed:
    #      - "…" — the finished marker ("✻ Sautéed for 7s") has no ellipsis
    #      - not "·" — this is the one spinner glyph that is also an ordinary
    #        bullet. Claude's own answers are full of "· item one…" lines, and
    #        upstream's ellipsis-only rule let them through: a bullet three
    #        lines above the separator became the status text. That misfire is
    #        not cosmetic — status_polling lights the typing keepalive off this
    #        return value, so a stray bullet in an idle pane leaves a ghost
    #        status message and a typing indicator that never turns off.
    #        tests/ccbot/test_terminal_parser.py has guarded this since before
    #        the merge ("· in regular output must NOT be detected as status").
    adjacent = True
    for i in range(chrome_idx - 1, max(chrome_idx - 9, -1), -1):
        line = lines[i].strip()
        if not line:
            continue
        live = "…" in line and line[0] != "·"
        if line[0] in STATUS_SPINNERS and (adjacent or live):
            rest = line[1:].strip()
            if (
                _BACKGROUND_SHELL_RE.search(rest)
                and "esc to interrupt" not in rest.lower()
            ):
                return None
            return rest
        adjacent = False
    return None


# ── Pane chrome stripping & bash output extraction ─────────────────────


def strip_pane_chrome(lines: list[str]) -> list[str]:
    """Strip Claude Code's bottom chrome (prompt area + status bar).

    The bottom of the pane looks like::

        ────────────────────────  (separator)
        ❯                        (prompt)
        ────────────────────────  (separator)
          [Opus 4.6] Context: 34%
          ⏵⏵ bypass permissions…

    This function finds the topmost ``────`` separator in the last 10 lines
    and strips everything from there down.
    """
    search_start = max(0, len(lines) - 10)
    for i in range(search_start, len(lines)):
        stripped = lines[i].strip()
        if len(stripped) >= 20 and all(c == "─" for c in stripped):
            return lines[:i]
    return lines


ANSI_CONTROL_RE = re.compile(
    r"(?:\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b[@-_])"
)


def strip_ansi_control_sequences(text: str) -> str:
    """Remove ANSI escape/control sequences from captured terminal text."""
    text = ANSI_CONTROL_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Keep normal newlines/tabs; remove other C0 controls that Telegram may show.
    return "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 32)


def format_pane_snapshot(pane_text: str) -> str:
    """Return a Telegram-friendly snapshot from captured tmux pane text.

    The first Codex bridge intentionally uses terminal capture instead of Codex
    rollout JSONL. This helper keeps that output readable by stripping ANSI
    sequences, removing known bottom chrome, and collapsing excessive blank
    lines without truncating content at the parser layer.
    """
    cleaned = strip_ansi_control_sequences(pane_text)
    lines = strip_pane_chrome(cleaned.splitlines())

    compact: list[str] = []
    blank_seen = False
    for line in lines:
        if line.strip():
            compact.append(line.rstrip())
            blank_seen = False
        elif not blank_seen and compact:
            compact.append("")
            blank_seen = True

    while compact and not compact[-1].strip():
        compact.pop()

    return "\n".join(compact).strip()


def extract_bash_output(pane_text: str, command: str) -> str | None:
    """Extract ``!`` command output from a captured tmux pane.

    Searches from the bottom for the ``! <command>`` echo line, then
    returns that line and everything below it (including the ``⎿`` output).
    Returns *None* if the command echo wasn't found.
    """
    lines = strip_pane_chrome(pane_text.splitlines())

    # Find the last "! <command>" echo line (search from bottom).
    # Match on the first 10 chars of the command in case the line is truncated.
    cmd_idx: int | None = None
    match_prefix = command[:10]
    for i in range(len(lines) - 1, -1, -1):
        stripped = lines[i].strip()
        if stripped.startswith(f"! {match_prefix}") or stripped.startswith(
            f"!{match_prefix}"
        ):
            cmd_idx = i
            break

    if cmd_idx is None:
        return None

    # Include the command echo line and everything after it
    raw_output = lines[cmd_idx:]

    # Strip trailing empty lines
    while raw_output and not raw_output[-1].strip():
        raw_output.pop()

    if not raw_output:
        return None

    return "\n".join(raw_output).strip()


# ── Usage modal parsing ──────────────────────────────────────────────────────────


@dataclass
class UsageInfo:
    """Parsed output from Claude Code's /usage modal."""

    raw_text: str  # Full captured pane text
    parsed_lines: list[str]  # Cleaned content lines from the modal


def parse_usage_output(pane_text: str) -> UsageInfo | None:
    """Extract usage information from Claude Code's /usage settings tab.

    The /usage modal shows a Settings overlay with a "Usage" tab containing
    progress bars and reset times.  This parser looks for the Settings header
    line, then collects all content until "Esc to cancel".

    Returns UsageInfo with cleaned lines, or None if not detected.
    """
    if not pane_text:
        return None

    lines = pane_text.strip().split("\n")

    # Find the Settings header that indicates we're in the usage modal
    start_idx: int | None = None
    end_idx: int | None = None

    for i, line in enumerate(lines):
        stripped = line.strip()
        if start_idx is None:
            # The usage tab header line
            if "Settings:" in stripped and "Usage" in stripped:
                start_idx = i + 1  # skip the header itself
        else:
            if stripped.startswith("Esc to"):
                end_idx = i
                break

    if start_idx is None:
        return None
    if end_idx is None:
        end_idx = len(lines)

    # Collect content lines, stripping progress bar characters and whitespace
    cleaned: list[str] = []
    for line in lines[start_idx:end_idx]:
        # Strip the line but preserve meaningful content
        stripped = line.strip()
        if not stripped:
            continue
        # Remove progress bar block characters but keep the rest
        # Progress bars are like: █████▋   38% used
        # Strip leading block chars, keep the percentage
        stripped = re.sub(r"^[\u2580-\u259f\s]+", "", stripped).strip()
        if stripped:
            cleaned.append(stripped)

    if cleaned:
        return UsageInfo(raw_text=pane_text, parsed_lines=cleaned)

    return None
