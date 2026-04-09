# 메시지 큐 순서 보장 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Claude 응답과 시간적으로 겹칠 수 있는 직접 전송 메시지를 기존 FIFO 큐로 통일하여 텔레그램 메시지 순서를 보장한다.

**Architecture:** `message_queue.py`에 `DirectMessage` 타입과 `enqueue_direct_message()` 함수를 추가. 큐 워커의 FIFO 루프에 `direct` 분기를 추가하여 기존 content/status와 동일한 순서 보장. bot.py의 해당 `safe_reply()` 호출들을 `enqueue_direct_message()`로 교체.

**Tech Stack:** Python 3.12, python-telegram-bot, asyncio

**Spec:** `docs/2026-04-09-message-queue-ordering-design.md`

---

### Task 1: DirectMessage 타입 및 enqueue_direct_message 추가

**Files:**
- Modify: `src/ccbot/handlers/message_queue.py`
- Test: `tests/ccbot/test_message_queue_direct.py`

- [ ] **Step 1: 테스트 파일 생성**

```python
# tests/ccbot/test_message_queue_direct.py
"""Tests for DirectMessage queue type."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccbot.handlers.message_queue import (
    DirectMessage,
    MessageTask,
    enqueue_direct_message,
    get_or_create_queue,
)


def test_direct_message_dataclass() -> None:
    """DirectMessage has required fields with defaults."""
    msg = DirectMessage(chat_id=123, thread_id=42, text="hello")
    assert msg.chat_id == 123
    assert msg.thread_id == 42
    assert msg.text == "hello"
    assert msg.parse_mode is None
    assert msg.reply_markup is None


def test_direct_message_with_parse_mode() -> None:
    msg = DirectMessage(chat_id=123, thread_id=None, text="test", parse_mode="HTML")
    assert msg.parse_mode == "HTML"


@pytest.fixture
def mock_bot() -> MagicMock:
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


async def test_enqueue_direct_creates_queue(mock_bot: MagicMock) -> None:
    """enqueue_direct_message creates queue and worker if not exists."""
    with patch(
        "ccbot.handlers.message_queue.get_or_create_queue"
    ) as mock_get:
        mock_queue = asyncio.Queue()
        mock_get.return_value = mock_queue

        await enqueue_direct_message(
            bot=mock_bot,
            user_id=999,
            chat_id=123,
            thread_id=42,
            text="test message",
        )

        mock_get.assert_called_once_with(mock_bot, 999)
        assert not mock_queue.empty()
        item = mock_queue.get_nowait()
        assert isinstance(item, DirectMessage)
        assert item.text == "test message"
        assert item.chat_id == 123
        assert item.thread_id == 42
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `cd /Users/pakjungeol/Documents/Claude/ccbot-src && uv run pytest tests/ccbot/test_message_queue_direct.py -v`
Expected: FAIL — `ImportError: cannot import name 'DirectMessage'`

- [ ] **Step 3: DirectMessage 타입 추가**

`src/ccbot/handlers/message_queue.py`에서 `MessageTask` dataclass 바로 아래 (line 67 이후)에 추가:

```python
@dataclass
class DirectMessage:
    """Direct message to send through the queue for ordering guarantees.

    Unlike ContentMessage (from JSONL monitor) and StatusUpdate (from polling),
    this represents messages that were previously sent via safe_reply() directly,
    bypassing the queue. Routing them through the queue ensures they appear
    in correct order relative to Claude's responses.
    """

    chat_id: int
    thread_id: int | None = None
    text: str = ""
    parse_mode: str | None = None
    reply_markup: object | None = None  # InlineKeyboardMarkup
```

- [ ] **Step 4: enqueue_direct_message 함수 추가**

`src/ccbot/handlers/message_queue.py`의 `enqueue_status_update` 함수 바로 아래에 추가:

```python
async def enqueue_direct_message(
    bot: Bot,
    user_id: int,
    chat_id: int,
    thread_id: int | None,
    text: str,
    parse_mode: str | None = None,
    reply_markup: object | None = None,
) -> None:
    """Enqueue a direct message for ordered delivery.

    Use this instead of safe_reply() for messages that may interleave
    with Claude responses (command confirmations, photo/voice acks, etc.).
    """
    queue = get_or_create_queue(bot, user_id)
    msg = DirectMessage(
        chat_id=chat_id,
        thread_id=thread_id,
        text=text,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
    )
    queue.put_nowait(msg)
```

- [ ] **Step 5: 큐 워커에 DirectMessage 처리 분기 추가**

`_message_queue_worker` 함수 (line ~200)의 `task = await queue.get()` 이후, `if task.task_type == "content":` 분기 앞에 DirectMessage 처리를 추가:

```python
                if isinstance(task, DirectMessage):
                    await _process_direct_message(bot, user_id, task)
                elif task.task_type == "content":
```

그리고 `_process_direct_message` 함수 추가 (`_process_content_task` 앞):

```python
async def _process_direct_message(
    bot: Bot, user_id: int, msg: DirectMessage
) -> None:
    """Send a direct message through the queue."""
    kwargs = _send_kwargs(msg.thread_id)
    if msg.reply_markup:
        kwargs["reply_markup"] = msg.reply_markup
    try:
        if msg.parse_mode:
            await bot.send_message(
                chat_id=msg.chat_id,
                text=msg.text,
                parse_mode=msg.parse_mode,
                link_preview_options=NO_LINK_PREVIEW,
                **kwargs,
            )
        else:
            await bot.send_message(
                chat_id=msg.chat_id,
                text=msg.text,
                link_preview_options=NO_LINK_PREVIEW,
                **kwargs,
            )
    except Exception:
        # Fallback: try plain text without parse_mode
        try:
            await bot.send_message(
                chat_id=msg.chat_id,
                text=strip_sentinels(msg.text),
                link_preview_options=NO_LINK_PREVIEW,
                **kwargs,
            )
        except Exception as e:
            logger.error("Failed to send direct message: %s", e)
```

- [ ] **Step 6: `__init__.py` export 업데이트 (필요 시)**

`message_queue.py`에서 이미 `enqueue_content_message`과 `enqueue_status_update`가 bot.py에서 직접 import되고 있으므로, 동일 패턴으로 `enqueue_direct_message`와 `DirectMessage`도 import하면 됨. 별도 `__init__.py` 변경 불필요.

- [ ] **Step 7: 테스트 실행 — 통과 확인**

Run: `cd /Users/pakjungeol/Documents/Claude/ccbot-src && uv run pytest tests/ccbot/test_message_queue_direct.py -v`
Expected: 모든 테스트 PASS

- [ ] **Step 8: 린트**

Run: `cd /Users/pakjungeol/Documents/Claude/ccbot-src && uv run ruff check src/ccbot/handlers/message_queue.py tests/ccbot/test_message_queue_direct.py && uv run ruff format --check src/ccbot/handlers/message_queue.py tests/ccbot/test_message_queue_direct.py`
Expected: 에러 없음

- [ ] **Step 9: 커밋**

```bash
cd /Users/pakjungeol/Documents/Claude/ccbot-src
git add src/ccbot/handlers/message_queue.py tests/ccbot/test_message_queue_direct.py
git commit -m "feat: add DirectMessage type and enqueue_direct_message for ordering"
```

---

### Task 2: forward_command_handler를 큐로 전환

**Files:**
- Modify: `src/ccbot/bot.py`

- [ ] **Step 1: import 추가**

`bot.py` 상단 import 블록에서 기존 `message_queue` import에 `enqueue_direct_message` 추가:

```python
from .handlers.message_queue import (
    clear_status_msg_info,
    enqueue_content_message,
    enqueue_direct_message,  # 추가
    enqueue_status_update,
    get_message_queue,
    shutdown_workers,
)
```

- [ ] **Step 2: forward_command_handler 성공 경로 변경**

`forward_command_handler`에서 성공 시 `safe_reply` (현재 `⚡ [{display}] Sent: {cc_slash}`) 를 `enqueue_direct_message`로 교체.

현재 코드 (bot.py의 forward_command_handler, 성공 분기):
```python
    if success:
        await safe_reply(update.message, f"⚡ [{display}] Sent: {cc_slash}")
```

변경:
```python
    if success:
        chat = update.effective_chat
        chat_id = chat.id if chat else user.id
        await enqueue_direct_message(
            bot=context.bot,
            user_id=user.id,
            chat_id=chat_id,
            thread_id=thread_id,
            text=f"⚡ [{display}] Sent: {cc_slash}",
        )
```

실패 경로 (`❌`)는 즉시 피드백이 필요하므로 `safe_reply` 유지.

- [ ] **Step 3: 린트**

Run: `cd /Users/pakjungeol/Documents/Claude/ccbot-src && uv run ruff check src/ccbot/bot.py`
Expected: 에러 없음

- [ ] **Step 4: 전체 테스트**

Run: `cd /Users/pakjungeol/Documents/Claude/ccbot-src && uv run pytest tests/ -v --tb=short`
Expected: 모든 테스트 PASS

- [ ] **Step 5: 커밋**

```bash
cd /Users/pakjungeol/Documents/Claude/ccbot-src
git add src/ccbot/bot.py
git commit -m "feat: route forward_command_handler confirmations through message queue"
```

---

### Task 3: photo/voice 확인 메시지를 큐로 전환

**Files:**
- Modify: `src/ccbot/bot.py`

- [ ] **Step 1: photo_handler 변경**

사진 전달 성공 후 확인 메시지를 큐로:

현재:
```python
await safe_reply(update.message, f"📷 Image sent to {display}")
```

변경:
```python
chat = update.effective_chat
chat_id = chat.id if chat else user.id
await enqueue_direct_message(
    bot=context.bot,
    user_id=user.id,
    chat_id=chat_id,
    thread_id=thread_id,
    text=f"📷 Image sent to {display}",
)
```

실패/에러 경로는 `safe_reply` 유지.

- [ ] **Step 2: voice_handler 변경**

음성 전사 후 전달 확인 메시지를 큐로:

현재:
```python
await safe_reply(update.message, f"🎙 Voice forwarded to {display}: {transcript[:100]}")
```

변경:
```python
chat = update.effective_chat
chat_id = chat.id if chat else user.id
await enqueue_direct_message(
    bot=context.bot,
    user_id=user.id,
    chat_id=chat_id,
    thread_id=thread_id,
    text=f"🎙 Voice forwarded to {display}: {transcript[:100]}",
)
```

- [ ] **Step 3: 린트 및 테스트**

Run: `cd /Users/pakjungeol/Documents/Claude/ccbot-src && uv run ruff check src/ccbot/bot.py && uv run pytest tests/ -v --tb=short`
Expected: 에러 없음, 모든 테스트 PASS

- [ ] **Step 4: 커밋**

```bash
cd /Users/pakjungeol/Documents/Claude/ccbot-src
git add src/ccbot/bot.py
git commit -m "feat: route photo/voice confirmations through message queue"
```

---

### Task 4: Interactive UI 메시지를 큐로 전환

**Files:**
- Modify: `src/ccbot/handlers/interactive_ui.py`

- [ ] **Step 1: Interactive UI의 새 메시지 전송을 큐로 전환**

`handle_interactive_ui` 함수에서 새 Interactive UI 메시지를 `bot.send_message()`로 직접 보내는 부분을 `enqueue_direct_message`로 변경.

현재 (`interactive_ui.py`의 새 메시지 전송 부분):
```python
msg = await bot.send_message(
    chat_id=chat_id,
    text=formatted,
    parse_mode=PARSE_MODE,
    reply_markup=keyboard,
    message_thread_id=thread_id,
)
```

변경:
```python
from .message_queue import enqueue_direct_message

await enqueue_direct_message(
    bot=bot,
    user_id=user_id,
    chat_id=chat_id,
    thread_id=thread_id,
    text=formatted,
    parse_mode=PARSE_MODE,
    reply_markup=keyboard,
)
```

주의: 기존 코드는 `send_message`의 반환값으로 `msg.message_id`를 저장하여 나중에 edit할 때 사용. `enqueue_direct_message`는 반환값이 없으므로, Interactive UI의 경우 **edit가 필요한 메시지는 직접 전송을 유지**하고, 새 메시지 전송만 큐로 돌리는 것이 적절.

실제로 `handle_interactive_ui`에서 `set_interactive_msg(user_id, msg.message_id)`를 호출하므로, message_id 추적이 필요한 경우는 직접 전송 유지가 맞음.

**수정**: Interactive UI는 message_id 추적이 필수이므로 **직접 전송 유지**. 이 Task는 스킵.

- [ ] **Step 2: 커밋 (변경 없음 → 스킵)**

---

### Task 5: Bash capture 출력을 큐로 전환

**Files:**
- Modify: `src/ccbot/bot.py`

- [ ] **Step 1: bash capture 첫 전송을 큐로 전환**

`_send_bash_capture` (또는 해당 함수)에서 `send_with_fallback()`로 직접 보내는 부분을 `enqueue_direct_message`로 변경.

먼저 정확한 함수명과 위치를 확인하여 변경. bash capture는 background task에서 실행되므로, `bot` 인스턴스와 `user_id`를 전달받는 구조인지 확인 필요.

bash capture에서 `send_with_fallback`로 보내는 **첫 메시지**는 `enqueue_direct_message`로 변경. 이후 **edit** (`bot.edit_message_text`)는 기존 메시지를 수정하는 것이므로 순서 무관 — 직접 유지.

주의: bash capture도 message_id를 저장하여 후속 edit에 사용. 첫 전송을 큐로 넣으면 message_id를 받을 수 없음.

**수정**: Bash capture도 message_id 추적이 필수이므로 **직접 전송 유지**. 이 Task는 스킵.

---

### Task 6: 전체 테스트 및 수동 검증

**Files:**
- Test: manual verification

- [ ] **Step 1: 전체 테스트**

Run: `cd /Users/pakjungeol/Documents/Claude/ccbot-src && uv run pytest tests/ -v`
Expected: 모든 테스트 PASS

- [ ] **Step 2: 린트 + 타입체크**

Run: `cd /Users/pakjungeol/Documents/Claude/ccbot-src && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/`
Expected: 에러 없음

- [ ] **Step 3: ccbot 재시작 및 수동 검증**

검증 항목:
1. 텔레그램에서 `/brainstorming` 실행 → `⚡ Sent:` 메시지가 Claude 응답 이전에 순서대로 표시되는지
2. 사진 전송 → `📷` 확인 메시지가 Claude 응답과 올바른 순서로 표시되는지
3. 음성 전송 → `🎙` 확인 메시지 순서 확인
4. 에러 메시지 (`❌`)는 여전히 즉시 표시되는지
5. 디렉토리 브라우저, /history, /screenshot 등은 여전히 즉시 반응하는지

- [ ] **Step 4: 커밋 (필요 시)**

수동 검증 중 발견된 수정사항이 있으면 커밋.
