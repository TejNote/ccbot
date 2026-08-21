"""Application entry point — CLI dispatcher and bot bootstrap.

Handles two execution modes:
  1. `ccbot hook` — delegates to hook.hook_main() for Claude Code hook processing.
  2. Default — configures logging, initializes tmux session, and starts the
     Telegram bot polling loop via bot.create_bot().
"""

import logging
import os
import sys


def main() -> None:
    """Main entry point."""
    if len(sys.argv) > 1:
        if sys.argv[1] == "hook":
            from .hook import hook_main

            hook_main()
            return
        # `send` 는 이 fork 고유 서브커맨드다(upstream 에 없다).
        # ~/.local/bin/claude-stop-notify.sh 가 `ccbot send --session-id ...` 로 부른다.
        # upstream 의 "미지 argv 거부" 를 이 dispatch 앞에 두면 send 가 exit(2) 로 죽는다.
        if sys.argv[1] == "send":
            from .send import send_main

            send_main()
            return
        # Reject anything else: silently falling through to "start the bot"
        # means a typo (or `ccbot --help`) launches a second bot instance
        # that races the real one for Telegram updates.
        usage = (
            "Usage: ccbot [start]  start the Telegram bot\n"
            "       ccbot hook     run as Claude Code SessionStart hook\n"
            "       ccbot send     send a message to a session (this fork)"
        )
        if sys.argv[1] in ("-h", "--help"):
            print(usage)
            return
        # `start` 는 이 fork 런처의 명시적 별칭이다 — ccbot-start-real.sh:57 이
        # `"$CCBOT_BIN" start` 로 부른다. upstream 은 bare `ccbot` 만 상정해 이걸
        # "미지의 인자" 로 거부했고, 2026-08-21 upstream 머지 직후 봇이 exit 2 로
        # 기동 실패해 launchd 가 재시도 루프에 들어갔다. 거부에서 예외로 둔다.
        if sys.argv[1] != "start":
            print(f"Unknown argument: {sys.argv[1]}\n{usage}", file=sys.stderr)
            sys.exit(2)

    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.WARNING,
    )

    # Import config before enabling DEBUG — avoid leaking debug logs on config errors
    try:
        from .config import config
    except ValueError as e:
        from .utils import ccbot_dir

        config_dir = ccbot_dir()
        env_path = config_dir / ".env"
        print(f"Error: {e}\n")
        print(f"Create {env_path} with the following content:\n")
        print("  TELEGRAM_BOT_TOKEN=your_bot_token_here")
        print("  ALLOWED_USERS=your_telegram_user_id")
        print()
        print("Get your bot token from @BotFather on Telegram.")
        print("Get your user ID from @userinfobot on Telegram.")
        sys.exit(1)

    # Default INFO to keep the autostart log from ballooning with per-event
    # DEBUG spam ("State saved", "Saved N tracked sessions"). Set CCBOT_DEBUG=1
    # in the environment to restore DEBUG for troubleshooting.
    _log_level = logging.DEBUG if os.environ.get("CCBOT_DEBUG") else logging.INFO
    logging.getLogger("ccbot").setLevel(_log_level)
    # AIORateLimiter (max_retries=5) handles retries itself; keep INFO for visibility
    logging.getLogger("telegram.ext.AIORateLimiter").setLevel(logging.INFO)
    logger = logging.getLogger(__name__)

    from .tmux_manager import tmux_manager

    logger.info("Allowed users: %s", config.allowed_users)
    logger.info("Claude projects path: %s", config.claude_projects_path)

    # Ensure tmux session exists
    session = tmux_manager.get_or_create_session()
    logger.info("Tmux session '%s' ready", session.session_name)

    logger.info("Starting Telegram bot...")
    from .bot import create_bot

    application = create_bot()
    application.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
