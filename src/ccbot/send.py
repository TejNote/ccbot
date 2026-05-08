"""Send subcommand — delivers a message to the Telegram topic for a Claude session.

Called by Claude Code hooks (Stop, PostToolUse, etc.) to send notifications to the
correct Telegram topic without starting the full bot.

Like hook.py, this module MUST NOT import config.py at module level, because hooks
run inside tmux panes where TELEGRAM_BOT_TOKEN is not set in the environment.
Instead, it reads ~/.ccbot/.env directly.

Usage (as called from hook scripts):
  ccbot send --session-id <uuid> <message>
  ccbot send --window <window_name> <message>

Exit codes: 0 on success, 1 on routing failure or API error (hooks should ignore).
"""

import argparse
import json
import logging
import sys
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


def _load_env(env_file: Path) -> dict[str, str]:
    """Parse key=value lines from a .env file. Strips quotes."""
    env: dict[str, str] = {}
    if not env_file.exists():
        return env
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        env[key.strip()] = val.strip().strip("\"'")
    return env


def _resolve_routing(
    state_file: Path, session_id: str, window_name: str
) -> tuple[int, int] | None:
    """Return (chat_id, thread_id) for the given session_id or window_name.

    Reads state.json which is maintained by the running ccbot process.
    Returns None if no routing is found (session not bound to a topic).
    """
    if not state_file.exists():
        return None
    try:
        state = json.loads(state_file.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to read state file %s: %s", state_file, e)
        return None

    # Step 1: session_id / window_name → window_id
    window_id: str | None = None
    for wid, ws in state.get("window_states", {}).items():
        if session_id and ws.get("session_id") == session_id:
            window_id = wid
            break
        if window_name and ws.get("window_name") == window_name:
            window_id = wid
            break

    # Fallback: window_states가 startup cleanup으로 비어있어도
    # window_display_names에는 매핑이 살아있다. 특히 codex provider는
    # session_monitor의 jsonl-기반 등록 경로가 없으므로 이 fallback이
    # 일반 동작 경로다 (claude도 첫 메시지 직전엔 동일 상태).
    #
    # window_display_names 는 ccbot 재기동 후에도 옛날 window_id 가 잔존할
    # 수 있다 (예: kickstart 로 codex 가 @27 → @6 으로 재 cut 됐는데 옛
    # @27 매핑이 남는 경우). 그 stale 항목을 잡으면 thread_bindings 에서
    # 못 찾아 silent fail. 따라서 thread_bindings 에 실제 매핑이 있는
    # window_id 만 후보로 삼는다.
    if not window_id and window_name:
        bound_window_ids: set[str] = set()
        for bindings in state.get("thread_bindings", {}).values():
            bound_window_ids.update(bindings.values())
        for wid, name in state.get("window_display_names", {}).items():
            if name == window_name and wid in bound_window_ids:
                window_id = wid
                break

    if not window_id:
        logger.debug(
            "No window found for session_id=%r window_name=%r", session_id, window_name
        )
        return None

    # Step 2: window_id → (user_id, thread_id)
    thread_id: int | None = None
    user_id: str | None = None
    for uid, bindings in state.get("thread_bindings", {}).items():
        for tid, wid in bindings.items():
            if wid == window_id:
                thread_id = int(tid)
                user_id = uid
                break
        if thread_id is not None:
            break

    if thread_id is None or user_id is None:
        logger.debug("No thread binding found for window_id=%s", window_id)
        return None

    # Step 3: (user_id, thread_id) → chat_id
    chat_key = f"{user_id}:{thread_id}"
    chat_id = state.get("group_chat_ids", {}).get(chat_key)
    if not chat_id:
        logger.debug("No group_chat_id for key %s", chat_key)
        return None

    return (int(chat_id), thread_id)


def send_main() -> None:
    """Entry point for `ccbot send` subcommand."""
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.DEBUG,
        stream=sys.stderr,
    )

    parser = argparse.ArgumentParser(
        prog="ccbot send",
        description="Send a message to the Telegram topic for a Claude session",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--session-id", metavar="UUID", help="Claude session UUID")
    group.add_argument(
        "--window", metavar="NAME", help="tmux window name (e.g. ceo, smoking)"
    )
    parser.add_argument("message", help="Message text to send")

    args = parser.parse_args(sys.argv[2:])

    from .utils import ccbot_dir

    config_dir = ccbot_dir()

    env = _load_env(config_dir / ".env")
    bot_token = env.get("TELEGRAM_BOT_TOKEN", "")
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
    logger.debug("Sending to chat_id=%d thread_id=%d", chat_id, thread_id)

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
            logger.debug("Rate limited by Telegram API, skipping (hook non-critical)")
            sys.exit(0)
        if not resp.is_success:
            logger.error("Telegram API error %d: %s", resp.status_code, resp.text)
            sys.exit(1)
        logger.debug("Message sent successfully")
    except Exception as e:
        logger.error("Failed to send message: %s", e)
        sys.exit(1)
