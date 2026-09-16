"""Safe message sending helpers with MarkdownV2 fallback.

Provides utility functions for sending Telegram messages with automatic
format conversion, falling back to plain text only when Telegram rejects the
formatting (BadRequest) — never on an ambiguous transient error such as
TimedOut, which may already have been delivered and would otherwise produce a
duplicate message.

Uses telegramify-markdown for MarkdownV2 formatting.

Functions:
  - send_with_fallback: Send with formatting → plain text fallback
  - send_photo: Photo sending (single or media group)
  - safe_reply: Reply with formatting, fallback to plain text
  - safe_edit: Edit message with formatting, fallback to plain text
  - safe_send: Send message with formatting, fallback to plain text

Rate limiting is handled globally by AIORateLimiter on the Application.
RetryAfter exceptions are re-raised so callers (queue worker) can handle them.
"""

import io
import logging
from typing import Any

from telegram import Bot, InputMediaPhoto, LinkPreviewOptions, Message
from telegram.error import BadRequest, RetryAfter

from ..markdown_v2 import convert_markdown
from ..transcript_parser import TranscriptParser

logger = logging.getLogger(__name__)


def strip_sentinels(text: str) -> str:
    """Strip expandable quote sentinel markers for plain text fallback."""
    for s in (
        TranscriptParser.EXPANDABLE_QUOTE_START,
        TranscriptParser.EXPANDABLE_QUOTE_END,
    ):
        text = text.replace(s, "")
    return text


def _ensure_formatted(text: str) -> str:
    """Convert markdown to MarkdownV2."""
    return convert_markdown(text)


PARSE_MODE = "MarkdownV2"


# Disable link previews in all messages to reduce visual noise
NO_LINK_PREVIEW = LinkPreviewOptions(is_disabled=True)


# Substrings in Telegram errors that mean the topic/thread is gone
_TOPIC_GONE_MARKERS = ("Topic_id_invalid", "Message thread not found")


async def _maybe_cleanup_dead_topic(
    chat_id: int, kwargs: dict[str, Any], err: BaseException
) -> None:
    """If the error indicates a dead forum topic, tear down the binding.

    Called from send fallbacks after both attempts have failed. Lazy-imports
    session_manager to avoid a circular import at module load.
    """
    msg = str(err)
    if not any(m in msg for m in _TOPIC_GONE_MARKERS):
        return
    thread_id = kwargs.get("message_thread_id")
    if thread_id is None:
        return
    from ..session import session_manager  # lazy: avoid circular import
    from .cleanup import clear_topic_state  # lazy: avoid circular import

    # 🚨 세션 상태만 푸는 것으로 끝나지 않는다. 핸들러 쪽 토픽별 추적(상태 메시지 ·
    #    tool id · secondary id · **타이핑 keepalive 태스크**)은 여기서 안 지우면 그대로
    #    남고, _typing_keepalive 는 while True 라 취소 전엔 죽지 않는다 — 텔레그램이
    #    "그 토픽 없다" 고 답한 thread 로 4초마다 send_chat_action 을 영원히 던지게 된다.
    #    다른 세 해제 경로(status_polling 2곳 · /kill · topic_closed)는 전부 부른다.
    for user_id, t_id in await session_manager.cleanup_dead_topic(
        int(chat_id), int(thread_id)
    ):
        await clear_topic_state(user_id, t_id)


async def send_with_fallback(
    bot: Bot,
    chat_id: int,
    text: str,
    **kwargs: Any,
) -> Message | None:
    """Send message with MarkdownV2, falling back to plain text on failure.

    Returns the sent Message on success, None on failure.
    RetryAfter is re-raised for caller handling.
    """
    kwargs.setdefault("link_preview_options", NO_LINK_PREVIEW)
    try:
        return await bot.send_message(
            chat_id=chat_id,
            text=_ensure_formatted(text),
            parse_mode=PARSE_MODE,
            **kwargs,
        )
    except RetryAfter:
        raise
    except BadRequest:
        # Telegram rejected the formatted message (e.g. invalid MarkdownV2), so
        # it was NOT delivered — retry once as plain text.
        try:
            return await bot.send_message(
                chat_id=chat_id, text=strip_sentinels(text), **kwargs
            )
        except RetryAfter:
            raise
        except Exception as e:
            logger.error(f"Failed to send message to {chat_id}: {e}")
            await _maybe_cleanup_dead_topic(chat_id, kwargs, e)
            return None
    except Exception as e:
        # Any other error (TimedOut, NetworkError, ...) is ambiguous: on a slow
        # or flaky connection the formatted send may already have reached
        # Telegram before the client raised. Retrying would deliver a duplicate
        # (one formatted copy + one plain), so log and give up instead.
        logger.warning(
            f"Send to {chat_id} raised {type(e).__name__}; not retrying to "
            f"avoid a possible duplicate: {e}"
        )
        return None


async def send_photo(
    bot: Bot,
    chat_id: int,
    image_data: list[tuple[str, bytes]],
    **kwargs: Any,
) -> list[int]:
    """Send photo(s) to chat. Sends as media group if multiple images.

    Rate limiting is handled globally by AIORateLimiter on the Application.

    Returns the message ids actually sent (empty on failure) so callers can
    track them — a tool_result's screenshot belongs to the same "secondary"
    message group as its text, and an untracked photo outlives the text that
    explained it.

    Args:
        bot: Telegram Bot instance
        chat_id: Target chat ID
        image_data: List of (media_type, raw_bytes) tuples
        **kwargs: Extra kwargs passed to send_photo/send_media_group
    """
    if not image_data:
        return []
    try:
        if len(image_data) == 1:
            _media_type, raw_bytes = image_data[0]
            sent = await bot.send_photo(
                chat_id=chat_id,
                photo=io.BytesIO(raw_bytes),
                **kwargs,
            )
            return [sent.message_id]
        media = [
            InputMediaPhoto(media=io.BytesIO(raw_bytes))
            for _media_type, raw_bytes in image_data
        ]
        msgs = await bot.send_media_group(
            chat_id=chat_id,
            media=media,
            **kwargs,
        )
        return [m.message_id for m in msgs]
    except RetryAfter:
        raise
    except Exception as e:
        logger.error("Failed to send photo to %d: %s", chat_id, e)
        return []


async def safe_reply(message: Message, text: str, **kwargs: Any) -> Message:
    """Reply with formatting, falling back to plain text on failure."""
    kwargs.setdefault("link_preview_options", NO_LINK_PREVIEW)
    try:
        return await message.reply_text(
            _ensure_formatted(text),
            parse_mode=PARSE_MODE,
            **kwargs,
        )
    except RetryAfter:
        raise
    except BadRequest:
        # Formatting rejected → not delivered; retry once as plain text.
        try:
            return await message.reply_text(strip_sentinels(text), **kwargs)
        except RetryAfter:
            raise
        except Exception as e:
            logger.error(f"Failed to reply: {e}")
            raise
    except Exception as e:
        # Ambiguous error (e.g. TimedOut): the formatted reply may already have
        # been delivered. Don't retry (avoids a duplicate); propagate instead.
        logger.warning(
            f"Reply raised {type(e).__name__}; not retrying to avoid a "
            f"possible duplicate: {e}"
        )
        raise


async def safe_edit(target: Any, text: str, **kwargs: Any) -> None:
    """Edit message with formatting, falling back to plain text on failure."""
    kwargs.setdefault("link_preview_options", NO_LINK_PREVIEW)
    try:
        await target.edit_message_text(
            _ensure_formatted(text),
            parse_mode=PARSE_MODE,
            **kwargs,
        )
    except RetryAfter:
        raise
    except BadRequest:
        # Formatting rejected → the edit was not applied; retry as plain text.
        try:
            await target.edit_message_text(strip_sentinels(text), **kwargs)
        except RetryAfter:
            raise
        except Exception as e:
            logger.error("Failed to edit message: %s", e)
    except Exception as e:
        # Ambiguous error (e.g. TimedOut): the edit may already have applied.
        # Don't retry — a plain-text retry would drop the formatting.
        logger.warning(
            "Edit raised %s; not retrying to avoid clobbering formatting: %s",
            type(e).__name__,
            e,
        )


async def safe_send(
    bot: Bot,
    chat_id: int,
    text: str,
    message_thread_id: int | None = None,
    **kwargs: Any,
) -> None:
    """Send message with formatting, falling back to plain text on failure."""
    kwargs.setdefault("link_preview_options", NO_LINK_PREVIEW)
    if message_thread_id is not None:
        kwargs.setdefault("message_thread_id", message_thread_id)
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=_ensure_formatted(text),
            parse_mode=PARSE_MODE,
            **kwargs,
        )
    except RetryAfter:
        raise
    except BadRequest:
        # Formatting rejected → not delivered; retry once as plain text.
        try:
            await bot.send_message(
                chat_id=chat_id, text=strip_sentinels(text), **kwargs
            )
        except RetryAfter:
            raise
        except Exception as e:
            logger.error(f"Failed to send message to {chat_id}: {e}")
            await _maybe_cleanup_dead_topic(chat_id, kwargs, e)
    except Exception as e:
        # Ambiguous error (e.g. TimedOut): the formatted send may already have
        # reached Telegram. Retrying would deliver a duplicate, so don't.
        # 🔎 죽은 토픽 정리는 여기서 부르지 않는다 — Topic_id_invalid ·
        #    Message thread not found 는 BadRequest 로 오므로 위 분기에서 잡힌다.
        #    TimedOut·NetworkError 로 토픽을 지우면 네트워크가 흔들릴 때마다
        #    멀쩡한 바인딩이 날아간다.
        logger.warning(
            f"Send to {chat_id} raised {type(e).__name__}; not retrying to "
            f"avoid a possible duplicate: {e}"
        )
