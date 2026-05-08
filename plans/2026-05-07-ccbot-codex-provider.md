# ccbot Codex Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Telegram forum topic에서 기존 Claude window뿐 아니라 ccbot tmux 안의 Codex/OMX direct window로 메시지를 보내고, Codex 상태는 polling으로 표시하며 최종 응답은 외부 Codex/OMX turn-complete bridge가 `ccbot send`로 Telegram topic에 push한다.

**Architecture:** 큰 provider ABC 리팩터는 보류하고 `WindowState.provider`만 추가한다. `provider="codex"` window는 Claude JSONL/session hook에 의존하지 않고 `tmux paste-buffer`로 입력을 전송한다. 작업중 표시는 codex TUI status parser로 처리하고, 최종 응답 push는 Codex/OMX hook bridge가 `ccbot send --window codex`를 호출한다. OMX는 ccbot 단일 tmux와 충돌하지 않도록 운영에서 `OMX_LAUNCH_POLICY=direct omx` 또는 `omx --direct`로 실행한다.

**Tech Stack:** Python 3.12, python-telegram-bot, libtmux, pytest, ruff, pyright.

---

## Files

- Modify: `src/ccbot/session.py`
  - `WindowState.provider` 추가
  - window name 기반 `claude|codex` provider 감지
  - Codex window가 Claude `session_map.json` cleanup에서 삭제되지 않도록 보호
- Modify: `src/ccbot/terminal_parser.py`
  - ANSI/control sequence strip helper 추가
  - Codex permission prompt/status line parser 추가
- Modify: `src/ccbot/bot.py`
  - `provider=codex` window는 snapshot 전송 없이 입력/interactive UI만 처리
- Modify: `src/ccbot/handlers/status_polling.py`
  - `provider=codex` window는 Codex status parser로 작업중 메시지 갱신
- Modify: `src/ccbot/tmux_manager.py`
  - Codex composer 입력 안정화를 위해 paste-buffer 전송 지원
- Add: `src/ccbot/send.py`
  - 현재 `main.py`가 이미 import하는 `ccbot send` subcommand 구현 누락 보완
  - `--window <name>` 기반 topic routing 유지
- Add/Modify tests:
  - `tests/ccbot/test_session.py`
  - `tests/ccbot/test_terminal_parser.py`
  - `tests/ccbot/test_bot_codex.py`
  - `tests/ccbot/test_send.py`

## Task 1: Provider state model

- [x] RED: `WindowState`가 `provider="codex"`를 저장/복원하고, `bind_thread(..., window_name="codex")`가 codex provider를 감지하는 테스트 추가.
- [x] RED: `load_session_map()`이 codex window state를 session_map 미등재 stale로 삭제하지 않는 테스트 추가.
- [x] GREEN: `WindowState.provider`, `detect_window_provider`, `get_window_provider`, `set_window_provider` 구현.
- [x] VERIFY: `uv run pytest tests/ccbot/test_session.py -q` 통과.

## Task 2: Codex terminal parser

- [x] RED: Codex permission prompt/status line parser 테스트 추가.
- [x] GREEN: `strip_ansi_control_sequences`, `parse_codex_status_line` 구현.
- [x] VERIFY: `uv run pytest tests/ccbot/test_terminal_parser.py -q` 통과.

## Task 3: Codex send/status loop

- [x] RED: `text_handler`가 codex provider window에 메시지만 전송하고 snapshot queue를 만들지 않는 테스트 추가.
- [x] GREEN: codex provider 입력 분기, status polling parser 분기, paste-buffer 전송 구현.
- [x] VERIFY: `uv run pytest tests/ccbot/test_bot_codex.py tests/ccbot/test_status_polling_codex.py -q` 통과.

## Task 4: ccbot send subcommand 보완

- [x] RED: `ccbot send --window codex` routing이 `state.json`의 `window_states` + `thread_bindings` + `group_chat_ids`로 chat/thread를 찾는 테스트 추가.
- [x] GREEN: `src/ccbot/send.py` 구현.
- [x] VERIFY: `uv run pytest tests/ccbot/test_send.py -q` 통과.

## Task 5: 전체 검증

- [x] `uv run ruff format src/ tests/ --check`
- [x] `uv run ruff check src/ tests/`
- [x] `uv run pyright src/ccbot/`
- [x] `uv run pytest tests/ccbot/test_session.py tests/ccbot/test_terminal_parser.py tests/ccbot/test_bot_codex.py tests/ccbot/test_send.py -q`

## Operating note

ccbot 안 Codex/OMX window는 detached tmux를 만들지 않게 아래 중 하나로 시작한다.

```bash
OMX_LAUNCH_POLICY=direct omx
# or
omx --direct
```
