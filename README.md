# CCBot (TejNote fork)

[中文文档](README_CN.md)
[Русская документация](README_RU.md)

> 🔱 **This is a fork** of [six-ddc/ccbot](https://github.com/six-ddc/ccbot) maintained at [TejNote/ccbot](https://github.com/TejNote/ccbot).
> Adds Codex/OMX provider routing, a plugin skill menu, message batching/ordering, and several reliability fixes for the Telegram ↔ tmux bridge. See [Fork additions](#fork-additions) and [Changelog (fork)](#changelog-fork) below for details.

Control Claude Code (and Codex/OMX) sessions remotely via Telegram — monitor, interact, and manage AI coding sessions running in tmux.

https://github.com/user-attachments/assets/15ffb38e-5eb9-4720-93b9-412e4961dc93

## Why CCBot?

Claude Code runs in your terminal. When you step away from your computer — commuting, on the couch, or just away from your desk — the session keeps working, but you lose visibility and control.

CCBot solves this by letting you **seamlessly continue the same session from Telegram**. The key insight is that it operates on **tmux**, not the Claude Code SDK. Your Claude Code process stays exactly where it is, in a tmux window on your machine. CCBot simply reads its output and sends keystrokes to it. This means:

- **Switch from desktop to phone mid-conversation** — Claude is working on a refactor? Walk away, keep monitoring and responding from Telegram.
- **Switch back to desktop anytime** — Since the tmux session was never interrupted, just `tmux attach` and you're back in the terminal with full scrollback and context.
- **Run multiple sessions in parallel** — Each Telegram topic maps to a separate tmux window, so you can juggle multiple projects from one chat group.

Other Telegram bots for Claude Code typically wrap the Claude Code SDK to create separate API sessions. Those sessions are isolated — you can't resume them in your terminal. CCBot takes a different approach: it's just a thin control layer over tmux, so the terminal remains the source of truth and you never lose the ability to switch back.

In fact, CCBot itself was built this way — iterating on itself through Claude Code sessions monitored and driven from Telegram via CCBot.

## Features

### Upstream (shared with [six-ddc/ccbot](https://github.com/six-ddc/ccbot))

- **Topic-based sessions** — Each Telegram topic maps 1:1 to a tmux window and Claude session
- **Real-time notifications** — Assistant responses, thinking content, tool use/result, local command output
- **Interactive UI** — Navigate AskUserQuestion, ExitPlanMode, and Permission Prompts via inline keyboard
- **Voice messages** — Voice messages are transcribed via OpenAI and forwarded as text
- **Slash command forwarding** — Send any `/command` directly to Claude Code (e.g. `/clear`, `/compact`, `/cost`)
- **Create / resume / kill sessions** — Start fresh or pick up an existing Claude session via directory browser; close a topic to auto-kill the window
- **Message history** — Browse conversation history with pagination
- **Hook-based session tracking** — Auto-associates tmux windows with Claude sessions via `SessionStart` hook
- **Persistent state** — Thread bindings and read offsets survive restarts

### 🔱 Fork additions

- **Codex / OMX provider routing** — `codex` / `codex-*` windows are auto-detected and routed bidirectionally. Uses tmux paste-buffer (vs. plain send-keys) so the Codex composer receives a single bracketed-paste event. A separate status parser (`parse_codex_status_line`) reports `⏳ Working` and `🔧 <tool>` lines from Codex output. State serialization stays backward-compatible (default `provider=claude` is omitted).
- **Plugin skill menu with usage sorting** — Installed Claude Code plugin skills (superpowers, pr-review-toolkit, octo, etc.) are auto-discovered at startup and registered as Telegram `/` commands. Skills with Korean descriptions show localized text. Use `/favorite` to pin frequently-used skills; per-project usage frequency sorts the rest.
- **MessageBatcher** — Tool-use and thinking events are grouped into a periodic summary (`⚙️ 작업 중 N건`) instead of flooding the chat. Configurable via `CCBOT_BATCH_WINDOW`.
- **DirectMessage queue** — Confirmation messages (commands, photo/voice acks) are routed through the per-user message queue so they never interleave with assistant output.
- **`ccbot send` CLI subcommand** — `ccbot send --session-id <uuid> <text>` and `ccbot send --window <name> <text>` let external hooks (e.g. `Stop`, `PostToolUse`) push messages to a topic without going through Telegram.
- **Persistent status message IDs** — `state.json` now tracks live status message IDs and the bot deletes orphans on next startup, so a crash-and-restart no longer leaves dangling `⏳ Working` messages on the chat.
- **Status polling reliability fixes** — `parse_status_line` ignores background-shell-only spinners (`Sautéed for 3s · 1 shell still running`) so the answer remains the last visible message after a turn ends. Status update is delegated entirely to the polling loop (no immediate enqueue from the content task path).
- **Claude busy-state guard** — `send_keys` checks the receiving pane is idle before transmitting, preventing silent command drops.
- **Hook hardening** — Tmux session name normalization via `TMUX_SESSION_NAME`; `.env` value quoting stripped; `/clear` resets `session_map` correctly.

## Prerequisites

- **tmux** — must be installed and available in PATH
- **Claude Code** — the CLI tool (`claude`) must be installed
- **Codex / OMX** *(optional)* — required only if you want Codex windows routed; install [`omx`](https://github.com/) and the bundled `~/Documents/Claude/.omx/hooks/ccbot-bridge.mjs` plugin

## Installation

### Option 1: Install from this fork (Recommended)

```bash
# Using uv (recommended)
uv tool install git+https://github.com/TejNote/ccbot.git

# Or using pipx
pipx install git+https://github.com/TejNote/ccbot.git
```

### Option 2: Install from source

```bash
git clone https://github.com/TejNote/ccbot.git ccbot-src
cd ccbot-src
uv sync
```

## Configuration

**1. Create a Telegram bot and enable Threaded Mode:**

1. Chat with [@BotFather](https://t.me/BotFather) to create a new bot and get your bot token
2. Open @BotFather's profile page, tap **Open App** to launch the mini app
3. Select your bot, then go to **Settings** > **Bot Settings**
4. Enable **Threaded Mode**

**2. Configure environment variables:**

Create `~/.ccbot/.env`:

```ini
TELEGRAM_BOT_TOKEN=your_bot_token_here
ALLOWED_USERS=your_telegram_user_id
```

**Required:**

| Variable             | Description                       |
| -------------------- | --------------------------------- |
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather         |
| `ALLOWED_USERS`      | Comma-separated Telegram user IDs |

**Optional:**

| Variable                   | Default                     | Description                                                                       |
| -------------------------- | --------------------------- | --------------------------------------------------------------------------------- |
| `CCBOT_DIR`                | `~/.ccbot`                  | Config/state directory (`.env` loaded from here)                                  |
| `TMUX_SESSION_NAME`        | `ccbot`                     | Tmux session name                                                                 |
| `CLAUDE_COMMAND`           | `claude`                    | Command to run in new windows                                                     |
| `MONITOR_POLL_INTERVAL`    | `2.0`                       | Polling interval in seconds                                                       |
| `CCBOT_SHOW_HIDDEN_DIRS`   | `false`                     | Show hidden (dot) directories in directory browser                                |
| `OPENAI_API_KEY`           | _(none)_                    | OpenAI API key for voice message transcription                                    |
| `OPENAI_BASE_URL`          | `https://api.openai.com/v1` | OpenAI API base URL (for proxies or compatible APIs)                              |
| `CCBOT_BATCH_WINDOW`       | `10`                        | 🔱 Seconds before MessageBatcher emits a summary (`0` to disable batching)        |
| `CCBOT_SHOW_USER_MESSAGES` | `true`                      | 🔱 Echo the user's own message back to the topic (set `false` to suppress)       |
| `CCBOT_SHOW_TOOL_CALLS`    | `true`                      | 🔱 Forward `tool_use` / `tool_result` events (set `false` to keep only summaries) |

🔱 = fork-specific.

> If running on a VPS where there's no interactive terminal to approve permissions, consider:
>
> ```
> CLAUDE_COMMAND=IS_SANDBOX=1 claude --dangerously-skip-permissions
> ```

## Hook Setup (Recommended)

Auto-install via CLI:

```bash
ccbot hook --install
```

Or manually add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [{ "type": "command", "command": "ccbot hook", "timeout": 5 }]
      }
    ]
  }
}
```

This writes window-session mappings to `$CCBOT_DIR/session_map.json` (`~/.ccbot/` by default), so the bot automatically tracks which Claude session is running in each tmux window — even after `/clear` or session restarts.

### `Stop` hook bridge (fork)

Pair with the `ccbot send` subcommand to push per-window summaries from arbitrary hooks. Example: `~/.local/bin/claude-stop-notify.sh` runs on `Stop`, computes a `git diff --shortstat`, and calls `ccbot send --session-id "$SESSION_ID" "📊 [<window>] N개 파일 변경, M줄 추가"`.

## Usage

```bash
# If installed via uv tool / pipx
ccbot

# If installed from source
uv run ccbot

# Hook helper / inter-process messaging (fork)
ccbot hook --install
ccbot send --session-id <uuid> "<text>"
ccbot send --window <name> "<text>"
```

### Commands

**Bot commands:**

| Command       | Description                       |
| ------------- | --------------------------------- |
| `/start`      | Show welcome message              |
| `/history`    | Message history for this topic    |
| `/screenshot` | Capture terminal screenshot       |
| `/esc`        | Send Escape to interrupt Claude   |
| `/kill`       | Kill session and delete topic     |
| `/unbind`     | Unbind topic from session         |
| `/usage`      | Show Claude Code usage remaining  |
| `/favorite`   | 🔱 Toggle skill favorites         |

**Claude Code commands (forwarded via tmux):**

| Command    | Description                  |
| ---------- | ---------------------------- |
| `/clear`   | Clear conversation history   |
| `/compact` | Compact conversation context |
| `/cost`    | Show token/cost usage        |
| `/help`    | Show Claude Code help        |
| `/memory`  | Edit CLAUDE.md               |
| `/model`   | Switch AI model              |

Any unrecognized `/command` is also forwarded to Claude Code as-is (e.g. `/review`, `/doctor`, `/init`).

**Plugin skills (auto-discovered, fork):**

Installed Claude Code plugins are scanned at startup. Their skills appear in the Telegram `/` command menu alongside built-in commands. Skills with Korean translations show localized descriptions. For example:

| Command                    | Description                          |
| -------------------------- | ------------------------------------ |
| `/brainstorming`           | ↗ 브레인스토밍 — 기능 설계 전 아이디어 구체화 |
| `/systematic_debugging`    | ↗ 체계적 디버깅                       |
| `/writing_plans`           | ↗ 구현 계획 작성                      |
| `/test_driven_development` | ↗ TDD — 테스트 주도 개발              |
| `/skill_debug`             | ↗ Octo 디버깅                        |
| ...                        | (all installed plugin skills)        |

Use `/favorite` to pin your most-used skills to the top of the menu. Per-project usage counts surface the rest by frequency.

### Topic Workflow

**1 Topic = 1 Window = 1 Session.** The bot runs in Telegram Forum (topics) mode.

**Creating a new session:**

1. Create a new topic in the Telegram group
2. Send any message in the topic
3. A directory browser appears — select the project directory
4. If the directory has existing Claude sessions, a session picker appears — choose one to resume or start fresh
5. A tmux window is created, `claude` starts (with `--resume` if resuming), and your pending message is forwarded

**Sending messages:**

Once a topic is bound to a session, just send text or voice messages in that topic — text gets forwarded to Claude Code via tmux keystrokes, and voice messages are automatically transcribed and forwarded as text.

**Codex / OMX windows (fork):**

Windows named `codex` or `codex-*` are routed to OMX in `direct` mode. Set this in your launcher (e.g. ccbot's bootstrap script):

```bash
OMX_LAUNCH_POLICY=direct omx
```

Status updates from Codex (`Working`, `Ran`, `Read`, `Edit`, etc.) flow through the same Telegram pipeline as Claude. The `ccbot-bridge.mjs` OMX hook plugin (at `~/Documents/Claude/.omx/hooks/`) emits assistant responses back to the topic via `ccbot send`.

**Killing a session:**

Close (or delete) the topic in Telegram. The associated tmux window is automatically killed and the binding is removed.

### Message History

Navigate with inline buttons:

```
📋 [project-name] Messages (42 total)

───── 14:32 ─────

👤 fix the login bug

───── 14:33 ─────

I'll look into the login bug...

[◀ Older]    [2/9]    [Newer ▶]
```

### Notifications

The monitor polls session JSONL files every 2 seconds and sends notifications for:

- **Assistant responses** — Claude's text replies
- **Thinking content** — Shown as expandable blockquotes
- **Tool use/result** — Summarized with stats (e.g. "Read 42 lines", "Found 5 matches"); on this fork, repeated tool-use events within `CCBOT_BATCH_WINDOW` collapse into `⚙️ 작업 중 N건`
- **Local command output** — stdout from commands like `git status`, prefixed with `❯ command_name`

Notifications are delivered to the topic bound to the session's window.

Formatting note:
- Telegram messages are rendered with parse mode `HTML` using `chatgpt-md-converter`
- Long messages are split with HTML tag awareness to preserve code blocks and formatting

## Running Claude Code in tmux

### Option 1: Create via Telegram (Recommended)

1. Create a new topic in the Telegram group
2. Send any message
3. Select the project directory from the browser

### Option 2: Create Manually

```bash
tmux attach -t ccbot
tmux new-window -n myproject -c ~/Code/myproject
# Then start Claude Code in the new window
claude
```

The window must be in the `ccbot` tmux session (configurable via `TMUX_SESSION_NAME`). The hook will automatically register it in `session_map.json` when Claude starts.

## Data Storage

| Path                            | Description                                                                                 |
| ------------------------------- | ------------------------------------------------------------------------------------------- |
| `$CCBOT_DIR/state.json`         | Thread bindings, window states (incl. `provider`), display names, read offsets, **status_msg_ids** |
| `$CCBOT_DIR/session_map.json`   | Hook-generated `{tmux_session:window_id: {session_id, cwd, window_name}}` mappings          |
| `$CCBOT_DIR/monitor_state.json` | Monitor byte offsets per session (prevents duplicate notifications)                         |
| `$CCBOT_DIR/skill_state.json`   | 🔱 Skill favorites and per-project usage counts                                              |
| `~/.claude/projects/`           | Claude Code session data (read-only)                                                        |

## File Structure

```
src/ccbot/
├── __init__.py            # Package entry point
├── main.py                # CLI dispatcher (hook subcommand + bot bootstrap)
├── hook.py                # Hook subcommand for session tracking (+ --install)
├── send.py                # 🔱 ccbot send subcommand (--session-id / --window)
├── config.py              # Configuration from environment variables
├── bot.py                 # Telegram bot setup, command handlers, topic routing
├── session.py             # Session management, state persistence, message history
├── session_monitor.py     # JSONL file monitoring (polling + change detection)
├── monitor_state.py       # Monitor state persistence (byte offsets)
├── transcript_parser.py   # Claude Code JSONL transcript parsing
├── terminal_parser.py     # Terminal pane parsing (interactive UI + status line + 🔱 Codex parser)
├── markdown_v2.py         # Markdown → Telegram HTML conversion + HTML-aware splitting
├── screenshot.py          # Terminal text → PNG image with ANSI color support
├── transcribe.py          # Voice-to-text transcription via OpenAI API
├── skill_registry.py      # 🔱 Plugin skill discovery and Telegram command registration
├── message_batcher.py     # 🔱 Batch tool_use/thinking messages into summaries
├── utils.py               # Shared utilities (atomic JSON writes, JSONL helpers)
├── tmux_manager.py        # Tmux window management (incl. 🔱 paste-buffer path for Codex)
├── telegram_sender.py     # Telegram message splitting (4096 char limit)
├── fonts/                 # Bundled fonts for screenshot rendering
└── handlers/
    ├── __init__.py
    ├── callback_data.py
    ├── cleanup.py
    ├── directory_browser.py
    ├── history.py
    ├── interactive_ui.py
    ├── message_queue.py     # Per-user queue + worker (🔱 DirectMessage, status convert)
    ├── message_sender.py
    ├── response_builder.py
    └── status_polling.py    # Terminal status polling (1s interval)
```

🔱 = file or section added/extended in this fork.

## Changelog (fork)

상세 변경 이력은 [`CHANGELOG.md`](./CHANGELOG.md) 참고. 버전 정책은 [SemVer](https://semver.org/lang/ko/)를 따르고, 포맷은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/) 기준입니다.

현재 버전: **v1.0.1** (upstream pending merge 3건 reconcile, 2026-05-14).

## Contributing back upstream

Bug fixes that aren't fork-specific (e.g. anything not touching Codex routing, the skill menu, or the `ccbot send` subcommand) are welcome upstream — open the PR against [`six-ddc/ccbot`](https://github.com/six-ddc/ccbot) directly. For fork-specific work, target this repository's `main`.

## Contributors

Thanks to all the people who contribute! We encourage using Claude Code to collaborate on contributions.

<a href="https://github.com/six-ddc/ccmux/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=six-ddc/ccmux" />
</a>
