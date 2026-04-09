"""Tests for DirectMessage type and enqueue_direct_message."""

from unittest.mock import AsyncMock, patch

import pytest

from ccbot.handlers.message_queue import (
    DirectMessage,
    _message_queues,
    _queue_locks,
    _queue_workers,
    enqueue_direct_message,
)


@pytest.fixture(autouse=True)
def _clean_queues():
    """Clean up global queue state before/after each test."""
    _message_queues.clear()
    _queue_locks.clear()
    for w in _queue_workers.values():
        w.cancel()
    _queue_workers.clear()
    yield
    _message_queues.clear()
    _queue_locks.clear()
    for w in _queue_workers.values():
        w.cancel()
    _queue_workers.clear()


class TestDirectMessageDataclass:
    def test_direct_message_defaults(self):
        msg = DirectMessage(chat_id=123)
        assert msg.chat_id == 123
        assert msg.thread_id is None
        assert msg.text == ""
        assert msg.parse_mode is None
        assert msg.reply_markup is None

    def test_direct_message_with_parse_mode(self):
        markup = {"inline_keyboard": [[{"text": "OK"}]]}
        msg = DirectMessage(
            chat_id=123,
            thread_id=42,
            text="hello",
            parse_mode="MarkdownV2",
            reply_markup=markup,
        )
        assert msg.chat_id == 123
        assert msg.thread_id == 42
        assert msg.text == "hello"
        assert msg.parse_mode == "MarkdownV2"
        assert msg.reply_markup == markup


class TestEnqueueDirectMessage:
    @pytest.mark.asyncio
    async def test_enqueue_direct_creates_queue(self):
        bot = AsyncMock()
        user_id = 999

        with patch(
            "ccbot.handlers.message_queue._message_queue_worker",
            new_callable=AsyncMock,
        ):
            await enqueue_direct_message(
                bot=bot,
                user_id=user_id,
                chat_id=123,
                thread_id=42,
                text="test message",
                parse_mode="MarkdownV2",
            )

        assert user_id in _message_queues
        queue = _message_queues[user_id]
        assert not queue.empty()

        item = queue.get_nowait()
        assert isinstance(item, DirectMessage)
        assert item.chat_id == 123
        assert item.thread_id == 42
        assert item.text == "test message"
        assert item.parse_mode == "MarkdownV2"
        assert item.reply_markup is None
