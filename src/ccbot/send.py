"""Send subcommand — delivers one message to a bound Telegram topic.

Called by lightweight hook/bridge scripts when the full Telegram bot is already
running and owns `~/.ccbot/state.json`. Routing can target a Claude session ID or
a tmux window display name such as `codex`.

This module intentionally avoids importing config.py at module import time: hook
processes may not have the bot environment loaded. It reads `~/.ccbot/.env` only
inside `send_main()`.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


def _load_env(env_file: Path) -> dict[str, str]:
    """Parse simple KEY=VALUE lines from a dotenv file."""
    env: dict[str, str] = {}
    if not env_file.exists():
        return env
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip("\"'")
    return env


def _resolve_routing(
    state_file: Path, session_id: str, window_name: str
) -> tuple[int, int] | None:
    """Resolve `(chat_id, thread_id)` from a session ID or window name."""
    if not state_file.exists():
        return None
    try:
        state = json.loads(state_file.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to read state file %s: %s", state_file, e)
        return None

    window_id: str | None = None
    for wid, ws in state.get("window_states", {}).items():
        if session_id and ws.get("session_id") == session_id:
            window_id = wid
            break
        if window_name and ws.get("window_name") == window_name:
            window_id = wid
            break
    if not window_id and window_name:
        # window_display_names 는 ccbot 재기동 후에도 옛 window_id 가
        # 잔존할 수 있다 (예: kickstart 로 codex 가 @27 → @6 으로 재 cut
        # 됐는데 옛 @27 매핑이 남는 경우). 그 stale 항목을 잡으면
        # thread_bindings 에서 못 찾아 silent fail. thread_bindings 에
        # 실제 매핑된 window_id 만 fallback 후보로 삼는다 (claude 브랜치 흡수).
        bound_window_ids: set[str] = set()
        for bindings in state.get("thread_bindings", {}).values():
            bound_window_ids.update(bindings.values())
        for wid, display_name in state.get("window_display_names", {}).items():
            if display_name == window_name and wid in bound_window_ids:
                window_id = wid
                break

    if not window_id:
        logger.debug(
            "No window found for session_id=%r window_name=%r", session_id, window_name
        )
        return None

    user_id: str | None = None
    thread_id: int | None = None
    for uid, bindings in state.get("thread_bindings", {}).items():
        for tid, wid in bindings.items():
            if wid == window_id:
                user_id = uid
                thread_id = int(tid)
                break
        if thread_id is not None:
            break

    if user_id is None or thread_id is None:
        logger.debug("No thread binding found for window_id=%s", window_id)
        return None

    chat_id = state.get("group_chat_ids", {}).get(f"{user_id}:{thread_id}")
    if chat_id is None:
        logger.debug("No group_chat_id for user=%s thread=%s", user_id, thread_id)
        return None

    return int(chat_id), thread_id


def send_main() -> None:
    """Entry point for `ccbot send`."""
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.INFO,
        stream=sys.stderr,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    parser = argparse.ArgumentParser(
        prog="ccbot send",
        description="Send a message to the Telegram topic for a session/window",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--session-id", metavar="ID", help="Claude/Codex session ID")
    group.add_argument("--window", metavar="NAME", help="tmux window name")
    parser.add_argument("message", help="Message text to send")
    args = parser.parse_args(sys.argv[2:])

    from .utils import ccbot_dir

    config_dir = ccbot_dir()
    bot_token = _load_env(config_dir / ".env").get("TELEGRAM_BOT_TOKEN", "")
    if not bot_token:
        logger.error("TELEGRAM_BOT_TOKEN not found in %s/.env", config_dir)
        sys.exit(1)

    routing = _resolve_routing(
        config_dir / "state.json",
        session_id=args.session_id or "",
        window_name=args.window or "",
    )
    if not routing:
        logger.error(
            "Could not resolve Telegram routing for session_id=%r window=%r",
            args.session_id,
            args.window,
        )
        sys.exit(1)

    chat_id, thread_id = routing
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                data={
                    "chat_id": str(chat_id),
                    "message_thread_id": str(thread_id),
                    "text": args.message,
                },
            )
        if resp.status_code == 429:
            logger.debug("Telegram rate limited ccbot send; skipping non-critical send")
            sys.exit(0)
        if not resp.is_success:
            logger.error("Telegram API error %d: %s", resp.status_code, resp.text)
            sys.exit(1)
    except Exception as e:
        logger.error("Failed to send message: %s", e)
        sys.exit(1)
