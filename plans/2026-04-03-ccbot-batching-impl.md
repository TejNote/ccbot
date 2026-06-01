# ccbot 메시지 배칭 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** tool_use / tool_result / thinking 메시지를 N초 단위로 묶어 Telegram 메시지 폭주를 줄이고, 어떤 에이전트가 일하는지 표시한다.

**Architecture:** `MessageBatcher` 클래스가 (user_id, thread_id) 키별로 메시지 버퍼를 유지하고, 백그라운드 타이머로 주기적으로 flush한다. `handle_new_message`에서 tool_use/tool_result/thinking은 batcher로 라우팅하고, 최종 text 응답 직전에 flush한다.

**Tech Stack:** Python 3.13, python-telegram-bot 22.x, asyncio, `six-ddc/ccbot` fork

---

## 파일 구조

| 파일 | 변경 |
|---|---|
| `ccbot/message_batcher.py` | 신규 — MessageBatcher 클래스 |
| `ccbot/config.py` | 수정 — `CCBOT_BATCH_WINDOW` 설정 추가 |
| `ccbot/bot.py` | 수정 — `post_init`, `handle_new_message` |
| `~/.ccbot/.env` | 수정 — `CCBOT_BATCH_WINDOW=10` 추가 |

---

### Task 1: GitHub fork 및 로컬 준비

**Files:**
- N/A (GitHub 작업)

- [ ] **Step 1: GitHub에서 fork 생성**

  브라우저에서 `https://github.com/six-ddc/ccbot` → Fork → Owner: `TejNote`, Repository name: `ccbot` → Create fork

- [ ] **Step 2: 로컬에 fork clone**

  ```bash
  git clone git@github.com:TejNote/ccbot.git /tmp/ccbot-dev
  cd /tmp/ccbot-dev
  git remote add upstream https://github.com/six-ddc/ccbot.git
  ```

- [ ] **Step 3: 현재 설치된 소스와 동일 버전인지 확인**

  ```bash
  git log --oneline -5
  # upstream 최신 커밋과 비교
  git fetch upstream
  git log --oneline upstream/main -5
  ```

---

### Task 2: config.py — CCBOT_BATCH_WINDOW 설정 추가

**Files:**
- Modify: `ccbot/config.py` (show_tool_calls 블록 다음, show_hidden_dirs 블록 이전)

- [ ] **Step 1: config.py에 batch_window 추가**

  `show_tool_calls` 블록(라인 93~97) 바로 뒤에 아래 코드 삽입:

  ```python
  # Batch tool_use/tool_result/thinking messages into one summary per N seconds
  # Set to 0.0 to disable batching (sends each message individually)
  self.batch_window = float(os.getenv("CCBOT_BATCH_WINDOW", "0.0"))
  ```

  > 기본값 `0.0` = 배칭 비활성 (기존 동작 유지). `.env`에서 `10.0`으로 설정.

- [ ] **Step 2: 커밋**

  ```bash
  cd /tmp/ccbot-dev
  git add ccbot/config.py
  git commit -m "feat: add CCBOT_BATCH_WINDOW config option"
  ```

---

### Task 3: message_batcher.py 신규 작성

**Files:**
- Create: `ccbot/message_batcher.py`

- [ ] **Step 1: message_batcher.py 작성**

  ```python
  """Message batching — groups tool_use/tool_result/thinking into timed summaries.

  When CCBOT_BATCH_WINDOW > 0, tool calls and thinking messages are buffered
  per (user_id, thread_id) and flushed as a single summary after N seconds,
  or immediately before a final text response.

  Format:
      ⚙️ 작업 중 (10초간 6건)
      • Bash × 3
      • Thinking × 2
      • Task(frontend-developer: 컴포넌트 구현) × 1
  """

  import asyncio
  import json
  import logging
  import time
  from collections import defaultdict
  from dataclasses import dataclass, field

  from telegram import Bot

  from .handlers.message_sender import safe_send
  from .session import session_manager

  logger = logging.getLogger(__name__)


  @dataclass
  class _Entry:
      tool_name: str | None
      content_type: str
      text: str


  class MessageBatcher:
      """Buffers tool_use/tool_result/thinking messages and flushes as summaries."""

      def __init__(self) -> None:
          self._buffers: dict[tuple[int, int | None], list[_Entry]] = defaultdict(list)
          self._start_times: dict[tuple[int, int | None], float] = {}
          self._bot: Bot | None = None
          self._window: float = 0.0
          self._task: asyncio.Task | None = None

      def start(self, bot: Bot, window: float) -> None:
          """Start background flush timer."""
          self._bot = bot
          self._window = window
          self._task = asyncio.create_task(self._timer_loop())

      def stop(self) -> None:
          if self._task:
              self._task.cancel()
              self._task = None

      def add(
          self,
          user_id: int,
          thread_id: int | None,
          tool_name: str | None,
          content_type: str,
          text: str,
      ) -> None:
          """Add a message to the buffer."""
          key = (user_id, thread_id)
          if key not in self._start_times:
              self._start_times[key] = time.monotonic()
          self._buffers[key].append(_Entry(tool_name, content_type, text))

      async def flush_and_send(
          self, bot: Bot, user_id: int, thread_id: int | None
      ) -> None:
          """Flush buffer and send summary. Called before a final text response."""
          key = (user_id, thread_id)
          entries = self._buffers.pop(key, [])
          elapsed = time.monotonic() - self._start_times.pop(key, time.monotonic())
          if not entries:
              return
          text = _format_batch(entries, elapsed)
          chat_id = session_manager.resolve_chat_id(user_id, thread_id)
          await safe_send(bot, chat_id, text, message_thread_id=thread_id)

      async def _timer_loop(self) -> None:
          """Periodically flush all non-empty buffers."""
          while True:
              await asyncio.sleep(self._window)
              if not self._bot:
                  continue
              keys = list(self._buffers.keys())
              for key in keys:
                  entries = self._buffers.pop(key, [])
                  elapsed = time.monotonic() - self._start_times.pop(key, time.monotonic())
                  if not entries:
                      continue
                  user_id, thread_id = key
                  text = _format_batch(entries, elapsed)
                  try:
                      chat_id = session_manager.resolve_chat_id(user_id, thread_id)
                      await safe_send(
                          self._bot, chat_id, text, message_thread_id=thread_id
                      )
                  except Exception as e:
                      logger.error("Batcher flush error for key %s: %s", key, e)


  def _extract_task_desc(text: str) -> str | None:
      """Extract description from Task tool input JSON (first 50 chars)."""
      try:
          data = json.loads(text)
          desc = data.get("description") or data.get("prompt", "")
          return str(desc)[:50] if desc else None
      except (json.JSONDecodeError, AttributeError, TypeError):
          return None


  def _format_batch(entries: list[_Entry], elapsed: float) -> str:
      """Format buffered entries into a human-readable summary."""
      counts: dict[str, int] = {}
      for e in entries:
          if e.content_type == "thinking":
              key = "Thinking"
          elif e.tool_name in ("Task", "Agent"):
              desc = _extract_task_desc(e.text)
              key = f"Task({desc})" if desc else "Task"
          else:
              key = e.tool_name or e.content_type
          counts[key] = counts.get(key, 0) + 1

      lines = [f"⚙️ 작업 중 ({int(elapsed)}초간 {len(entries)}건)"]
      for name, count in counts.items():
          lines.append(f"• {name} × {count}")
      return "\n".join(lines)


  # Module-level singleton — imported by bot.py
  batcher = MessageBatcher()
  ```

- [ ] **Step 2: 커밋**

  ```bash
  git add ccbot/message_batcher.py
  git commit -m "feat: add MessageBatcher for timed message grouping"
  ```

---

### Task 4: bot.py — 배칭 레이어 삽입

**Files:**
- Modify: `ccbot/bot.py`

`handle_new_message` 함수(라인 1738)와 `post_init` 함수(라인 1830)를 수정한다.

- [ ] **Step 1: import 추가**

  bot.py 상단 import 블록(`.config` import 근처)에 추가:

  ```python
  from .message_batcher import batcher
  ```

- [ ] **Step 2: post_init에서 batcher 시작**

  `post_init` 함수 내 `monitor.start()` 호출 바로 뒤에 추가:

  ```python
  if config.batch_window > 0:
      batcher.start(application.bot, config.batch_window)
  ```

- [ ] **Step 3: post_shutdown에서 batcher 종료**

  `post_shutdown` 함수를 찾아 batcher 정리 추가 (없으면 아래 위치에 추가):

  ```python
  batcher.stop()
  ```

- [ ] **Step 4: handle_new_message에 배칭 로직 삽입**

  기존 라인 1789~1791 (show_tool_calls skip 블록) 바로 뒤에 아래 코드 삽입:

  ```python
  # --- Message batching ---
  if config.batch_window > 0:
      if msg.content_type in ("tool_use", "tool_result", "thinking"):
          batcher.add(user_id, thread_id, msg.tool_name, msg.content_type, msg.text)
          continue
      if msg.content_type == "text" and msg.is_complete and msg.role == "assistant":
          await batcher.flush_and_send(application_bot, user_id, thread_id)
  # --- End batching ---
  ```

  > `application_bot`은 이 함수 스코프에서 `bot` 파라미터. 그대로 `bot` 사용.

  실제 삽입 위치와 변수명 확인 후 적용:

  ```python
  # Skip tool call notifications when CCBOT_SHOW_TOOL_CALLS=false
  if not config.show_tool_calls and msg.content_type in ("tool_use", "tool_result"):
      continue

  # Batch tool_use/tool_result/thinking when CCBOT_BATCH_WINDOW > 0
  if config.batch_window > 0:
      if msg.content_type in ("tool_use", "tool_result", "thinking"):
          batcher.add(user_id, thread_id, msg.tool_name, msg.content_type, msg.text)
          continue
      if msg.content_type == "text" and msg.is_complete and msg.role == "assistant":
          await batcher.flush_and_send(bot, user_id, thread_id)

  parts = build_response_parts(...)
  ```

- [ ] **Step 5: 커밋**

  ```bash
  git add ccbot/bot.py
  git commit -m "feat: route tool/thinking messages through MessageBatcher in handle_new_message"
  ```

- [ ] **Step 6: fork에 push**

  ```bash
  git push origin main
  ```

---

### Task 5: 설치 및 활성화

**Files:**
- `~/.ccbot/.env`

- [ ] **Step 1: fork에서 재설치**

  ```bash
  uv tool install git+https://github.com/TejNote/ccbot.git --force
  ```

  예상 출력:
  ```
  Installed 1 package in ...
  + ccbot==0.1.0 (from git+https://github.com/TejNote/ccbot.git@...)
  ```

- [ ] **Step 2: .env에 배칭 설정 추가**

  `~/.ccbot/.env` 파일에 추가:

  ```
  CCBOT_BATCH_WINDOW=10
  ```

- [ ] **Step 3: ccbot 재시작**

  ```bash
  tmux send-keys -t ccbot:__main__ C-c Enter
  ~/.local/bin/ccbot start
  ```

  또는 launchd 재시작:

  ```bash
  launchctl stop com.user.ccbot-start
  launchctl start com.user.ccbot-start
  ```

- [ ] **Step 4: 동작 확인**

  scraping 창에서 Claude에게 간단한 작업 요청 후 Telegram에서 확인:
  - tool call마다 메시지가 오지 않고 10초 후 배치 요약이 오는지 확인
  - 최종 텍스트 응답 전 배치 메시지가 먼저 오는지 확인
  - 예상 메시지:
    ```
    ⚙️ 작업 중 (10초간 5건)
    • Bash × 2
    • Thinking × 2
    • Read × 1
    ```

---

## 업스트림 업데이트 방법

```bash
cd /tmp/ccbot-dev
git fetch upstream
git merge upstream/main
git push origin main
uv tool upgrade ccbot   # TejNote/ccbot 기준으로 업그레이드
```
