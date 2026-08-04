# ccbot 작업 지침

이 파일이 이 repository의 AI instruction 정본이다. `CLAUDE.md`는 `@AGENTS.md` 한 줄 셔틀이다.

> ℹ️ Personal 프로젝트 — `~/.claude/CLAUDE.md` 글로벌 + `~/Documents/Personal/CLAUDE.md`만 적용 (Insudeal 회사 컨벤션 무관). 시크릿·push 룰은 글로벌 위임. ccbot 운영 메모리: `reference_ccbot_infra.md`, `reference_ccbot_versioning.md`, `feedback_ccbot_version_bump_required.md`.

설계 문서는 `docs/`에 있다 — 메시지 큐 순서 설계는 `docs/2026-04-09-message-queue-ordering-design.md`, spec은 `docs/specs/`. 아키텍처 상세는 아래 `.claude/rules/` 참조.

ccmux — Telegram bot that bridges Telegram Forum topics to Claude Code sessions via tmux windows. Each topic is bound to one tmux window running one Claude Code instance.

Tech stack: Python, python-telegram-bot, tmux, uv.

## Common Commands

```bash
uv run ruff check src/ tests/         # Lint — MUST pass before committing
uv run ruff format src/ tests/        # Format — auto-fix, then verify with --check
uv run pyright src/ccbot/             # Type check — MUST be 0 errors before committing
./scripts/restart.sh                  # Restart the ccbot service after code changes
ccbot hook --install                  # Auto-install Claude Code SessionStart hook
```

## Core Design Constraints

- **1 Topic = 1 Window = 1 Session** — all internal routing keyed by tmux window ID (`@0`, `@12`), not window name. Window names kept as display names. Same directory can have multiple windows.
- **Topic-only** — no backward-compat for non-topic mode. No `active_sessions`, no `/list`, no General topic routing.
- **No message truncation** at parse layer — splitting only at send layer (`split_message`, 4096 char limit).
- **MarkdownV2 only** — use `safe_reply`/`safe_edit`/`safe_send` helpers (auto fallback to plain text). Internal queue/UI code calls bot API directly with its own fallback.
- **Hook-based session tracking** — `SessionStart` hook writes `session_map.json`; monitor polls it to detect session changes.
- **Message queue per user** — FIFO ordering, message merging (3800 char limit), tool_use/tool_result pairing.
- **Rate limiting** — `AIORateLimiter(max_retries=5)` on the Application (30/s global). On restart, the global bucket is pre-filled to avoid burst against Telegram's server-side counter.

## Code Conventions

- Every `.py` file starts with a module-level docstring: purpose clear within 10 lines, one-sentence summary first line, then core responsibilities and key components.
- Telegram interaction: prefer inline keyboards over reply keyboards; use `edit_message_text` for in-place updates; keep callback data under 64 bytes; use `answer_callback_query` for instant feedback.

## Configuration

- Config directory: `~/.ccbot/` by default, override with `CCBOT_DIR` env var.
- `.env` loading priority: local `.env` > config dir `.env`.
- State files: `state.json` (thread bindings), `session_map.json` (hook-generated), `monitor_state.json` (byte offsets).

## Hook Configuration

Auto-install: `ccbot hook --install`

Or manually in `~/.claude/settings.json`:
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

## Architecture Details

See @.claude/rules/architecture.md for full system diagram and module inventory.
See @.claude/rules/topic-architecture.md for topic→window→session mapping details.
See @.claude/rules/message-handling.md for message queue, merging, and rate limiting.

## 개인 지침

@AGENTS.local.md

repository 루트에 `AGENTS.local.md`가 있으면 작업 시작 전에 읽고 따른다. 없으면 위 import는 무시된다.
