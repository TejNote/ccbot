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
from dataclasses import dataclass

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
        """Stop background flush timer."""
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
        if not entries or elapsed < 5.0:
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
