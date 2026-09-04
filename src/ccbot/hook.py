"""Hook subcommand for Claude Code session tracking.

Called by Claude Code's SessionStart hook to maintain a window↔session
mapping in <CCBOT_DIR>/session_map.json. Also provides `--install` to
auto-configure the hook in ~/.claude/settings.json.

This module must NOT import config.py (which requires TELEGRAM_BOT_TOKEN),
since hooks run inside tmux panes where bot env vars are not set.
Config directory resolution uses utils.ccbot_dir() (shared with config.py).

Key functions: hook_main() (CLI entry), _install_hook().
"""

import argparse
import fcntl
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Validate session_id looks like a UUID
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

_CLAUDE_SETTINGS_FILE = Path.home() / ".claude" / "settings.json"

# The hook command suffix for detection
_HOOK_COMMAND_SUFFIX = "ccbot hook"


def _find_ccbot_path() -> str:
    """Find the full path to the ccbot executable.

    Priority:
    1. shutil.which("ccbot") - if ccbot is in PATH
    2. Same directory as the Python interpreter (for venv installs)
    """
    # Try PATH first
    ccbot_path = shutil.which("ccbot")
    if ccbot_path:
        return ccbot_path

    # Fall back to the directory containing the Python interpreter
    # This handles the case where ccbot is installed in a venv
    python_dir = Path(sys.executable).parent
    ccbot_in_venv = python_dir / "ccbot"
    if ccbot_in_venv.exists():
        return str(ccbot_in_venv)

    # Last resort: assume it will be in PATH
    return "ccbot"


def _is_hook_installed(settings: dict) -> bool:
    """Check if ccbot hook is already installed in the settings.

    Detects both 'ccbot hook' and full paths like '/path/to/ccbot hook'.
    """
    hooks = settings.get("hooks", {})
    session_start = hooks.get("SessionStart", [])

    for entry in session_start:
        if not isinstance(entry, dict):
            continue
        inner_hooks = entry.get("hooks", [])
        for h in inner_hooks:
            if not isinstance(h, dict):
                continue
            cmd = h.get("command", "")
            # Match 'ccbot hook' or paths ending with 'ccbot hook'
            if cmd == _HOOK_COMMAND_SUFFIX or cmd.endswith("/" + _HOOK_COMMAND_SUFFIX):
                return True
    return False


def _install_hook() -> int:
    """Install the ccbot hook into Claude's settings.json.

    Returns 0 on success, 1 on error.
    """
    settings_file = _CLAUDE_SETTINGS_FILE
    settings_file.parent.mkdir(parents=True, exist_ok=True)

    # Read existing settings
    settings: dict = {}
    if settings_file.exists():
        try:
            settings = json.loads(settings_file.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Error reading %s: %s", settings_file, e)
            print(f"Error reading {settings_file}: {e}", file=sys.stderr)
            return 1

    # Check if already installed
    if _is_hook_installed(settings):
        logger.info("Hook already installed in %s", settings_file)
        print(f"Hook already installed in {settings_file}")
        return 0

    # Find the full path to ccbot
    ccbot_path = _find_ccbot_path()
    hook_command = f"{ccbot_path} hook"
    hook_config = {"type": "command", "command": hook_command, "timeout": 5}
    logger.info("Installing hook command: %s", hook_command)

    # Install the hook
    if "hooks" not in settings:
        settings["hooks"] = {}
    if "SessionStart" not in settings["hooks"]:
        settings["hooks"]["SessionStart"] = []

    settings["hooks"]["SessionStart"].append({"hooks": [hook_config]})

    # Write back
    try:
        settings_file.write_text(
            json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
        )
    except OSError as e:
        logger.error("Error writing %s: %s", settings_file, e)
        print(f"Error writing {settings_file}: {e}", file=sys.stderr)
        return 1

    logger.info("Hook installed successfully in %s", settings_file)
    print(f"Hook installed successfully in {settings_file}")
    return 0


def _validate_transcript_path(transcript_path: str, session_id: str) -> str:
    """훅 페이로드의 `transcript_path` 를 검증한다. 못 믿으면 빈 문자열.

    🚨 왜 이 필드가 필요한가 — `cwd` 로는 그 세션의 jsonl 위치를 알 수 없다.
       공식 문서상 `cwd` 는 "Current working directory when the hook is invoked" 다.
       세션이 시작한 폴더가 아니라 **훅이 불린 그 순간의 폴더**이고, Bash 의 `cd` 가
       유지되므로 긴 세션에서는 계속 떠돈다(실측: 한 세션에서 359회 전환, 폴더 6종).
       그런데 jsonl 은 **시작 시점 cwd** 로 만든 폴더에 고정된다.

       SessionStart 는 `compact` 에도 발화한다(= 세션 도중이다). 그래서 하위 폴더에서
       자동 압축이 걸리면 둘이 어긋난다 — 2026-09-03 17:10:59 `@2` 가 그랬고
       (jsonl 에 `subtype=compact_boundary` 기록) metlife 토픽이 17시간 죽었다.

       `transcript_path` 는 페이로드가 주는 authoritative 경로다. 추측이 사라진다.
    """
    if not transcript_path:
        return ""
    if not os.path.isabs(transcript_path):
        logger.warning("transcript_path is not absolute: %s", transcript_path)
        return ""
    if Path(transcript_path).stem != session_id:
        # 이름이 session_id 와 다르면 우리가 아는 규칙 밖이다 — 믿지 않는다.
        logger.warning(
            "transcript_path stem != session_id (%s vs %s), ignoring",
            Path(transcript_path).stem,
            session_id,
        )
        return ""
    return transcript_path


def hook_main() -> None:
    """Process a Claude Code hook event from stdin, or install the hook."""
    # Configure logging for the hook subprocess (main.py logging doesn't apply here)
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.DEBUG,
        stream=sys.stderr,
    )

    parser = argparse.ArgumentParser(
        prog="ccbot hook",
        description="Claude Code session tracking hook",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Install the hook into ~/.claude/settings.json",
    )
    # Parse only known args to avoid conflicts with stdin JSON
    args, _ = parser.parse_known_args(sys.argv[2:])

    if args.install:
        logger.info("Hook install requested")
        sys.exit(_install_hook())

    # Normal hook processing: read JSON from stdin
    logger.debug("Processing hook event from stdin")
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("Failed to parse stdin JSON: %s", e)
        return

    session_id = payload.get("session_id", "")
    cwd = payload.get("cwd", "")
    transcript_path = payload.get("transcript_path", "")
    event = payload.get("hook_event_name", "")

    if not session_id or not event:
        logger.debug("Empty session_id or event, ignoring")
        return

    # Validate session_id format
    if not _UUID_RE.match(session_id):
        logger.warning("Invalid session_id format: %s", session_id)
        return

    # Validate cwd is an absolute path (if provided)
    if cwd and not os.path.isabs(cwd):
        logger.warning("cwd is not absolute: %s", cwd)
        return

    transcript_path = _validate_transcript_path(transcript_path, session_id)

    if event != "SessionStart":
        logger.debug("Ignoring non-SessionStart event: %s", event)
        return

    # Skip non-interactive sessions (claude -p / --print).
    # These are one-shot commands (e.g. daily-news-digest.sh) that inherit
    # TMUX_PANE from the parent shell but should not overwrite session_map.
    try:
        ppid = os.getppid()
        cmdline = Path(f"/proc/{ppid}/cmdline").read_bytes().decode(errors="ignore")
    except (OSError, FileNotFoundError):
        # macOS: no /proc, use ps instead
        try:
            ps_out = subprocess.run(
                ["ps", "-o", "args=", "-p", str(os.getppid())],
                capture_output=True,
                text=True,
            ).stdout.strip()
            cmdline = ps_out
        except Exception:
            cmdline = ""
    if any(flag in cmdline for flag in [" -p ", " --print ", " -p\x00", "\x00-p\x00"]):
        logger.debug("Skipping non-interactive session (parent has -p/--print flag)")
        return

    # Get tmux session:window key for the pane running this hook.
    # TMUX_PANE is set by tmux for every process inside a pane.
    pane_id = os.environ.get("TMUX_PANE", "")
    if not pane_id:
        logger.warning("TMUX_PANE not set, cannot determine window")
        return

    result = subprocess.run(
        [
            "tmux",
            "display-message",
            "-t",
            pane_id,
            "-p",
            "#{session_name}:#{window_id}:#{window_name}",
        ],
        capture_output=True,
        text=True,
    )
    raw_output = result.stdout.strip()
    # Expected format: "session_name:@id:window_name"
    parts = raw_output.split(":", 2)
    if len(parts) < 3:
        logger.warning(
            "Failed to parse session:window_id:window_name from tmux (pane=%s, output=%s)",
            pane_id,
            raw_output,
        )
        return
    tmux_session_name, window_id, window_name = parts

    # Use canonical session name from .ccbot/.env (TMUX_SESSION_NAME) if set.
    # This handles tmux group session copies (ccbot-15, ccbot-12, etc.) which
    # would otherwise record keys like "ccbot-15:@4" that the bot ignores.
    from .utils import ccbot_dir

    _env_file = ccbot_dir() / ".env"
    canonical_session = tmux_session_name  # fallback: current tmux session name
    if _env_file.exists():
        for _line in _env_file.read_text().splitlines():
            _line = _line.strip()
            if _line.startswith("TMUX_SESSION_NAME="):
                _val = _line.split("=", 1)[1].strip().strip("\"'")
                if _val:
                    canonical_session = _val
                break

    # Key uses window_id for uniqueness
    session_window_key = f"{canonical_session}:{window_id}"

    logger.debug(
        "tmux key=%s, window_name=%s, session_id=%s, cwd=%s",
        session_window_key,
        window_name,
        session_id,
        cwd,
    )

    # Read-modify-write with file locking to prevent concurrent hook races
    map_file = ccbot_dir() / "session_map.json"
    map_file.parent.mkdir(parents=True, exist_ok=True)

    lock_path = map_file.with_suffix(".lock")
    try:
        with open(lock_path, "w") as lock_f:
            fcntl.flock(lock_f, fcntl.LOCK_EX)
            logger.debug("Acquired lock on %s", lock_path)
            try:
                session_map: dict[str, dict[str, str]] = {}
                if map_file.exists():
                    try:
                        session_map = json.loads(map_file.read_text())
                    except (json.JSONDecodeError, OSError):
                        logger.warning(
                            "Failed to read existing session_map, starting fresh"
                        )

                entry = {
                    "session_id": session_id,
                    "cwd": cwd,
                    "window_name": window_name,
                }
                # 옛 항목과 섞이므로 «있을 때만» 넣는다. 읽는 쪽은 없으면 폴백한다.
                if transcript_path:
                    entry["transcript_path"] = transcript_path
                session_map[session_window_key] = entry

                # Clean up old-format key ("session:window_name") if it exists.
                # Previous versions keyed by window_name instead of window_id.
                old_key = f"{tmux_session_name}:{window_name}"
                if old_key != session_window_key and old_key in session_map:
                    del session_map[old_key]
                    logger.info("Removed old-format session_map key: %s", old_key)

                from .utils import atomic_write_json

                atomic_write_json(map_file, session_map)
                logger.info(
                    "Updated session_map: %s -> session_id=%s, cwd=%s, transcript=%s",
                    session_window_key,
                    session_id,
                    cwd,
                    transcript_path or "(없음)",
                )
            finally:
                fcntl.flock(lock_f, fcntl.LOCK_UN)
    except OSError as e:
        logger.error("Failed to write session_map: %s", e)
