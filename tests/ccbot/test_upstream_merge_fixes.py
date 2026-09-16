"""upstream 커뮤니티 PR 9건을 이 fork 에 합치면서 **새로 생긴 결함**을 고정한다.

2026-09-16 머지 리뷰가 잡은 것들이다. 전부 "upstream 패치는 맞는데 이 fork 의 다른
부분과 겹쳐서 깨지는" 형태라, upstream 테스트로는 잡히지 않는다.

  1. #81 의 cleanup_dead_topic 이 세션 상태만 풀고 핸들러 쪽 토픽 추적을 남긴다.
     #98 이 붙으면서 이게 단순 누수가 아니라 **무한 API 호출**이 됐다 —
     _typing_keepalive 는 while True 라, 텔레그램이 "그 토픽 없다" 고 답한 thread 로
     4초마다 send_chat_action 을 영원히 던진다.
  2. #97 의 8줄 위 스캔이 `·` 불릿을 상태줄로 읽는 구멍을 **비인접 줄에** 새로 열었다.
     #98 과 겹치면 유휴 세션에 타이핑 표시가 영구히 켜진다.
     (구분선 **바로 위** 불릿은 main 에도 있던 기존 오탐이라 이 테스트의 범위가 아니다)
  3. #98 의 set_typing 이 update_status_message 맨 끝에 한 번뿐인데 그 위에 조기
     return 이 세 개다 — 퍼미션 프롬프트가 떠 있는 내내 "작업 중" 으로 보인다.
  4. #94 가 secondary 메시지 id 를 모으는데 **이미지 id 는 빠져** 있었다.
     설명 텍스트만 지워지고 스크린샷이 맥락 없이 남는다.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccbot.handlers.message_queue import (
    MessageTask,
    _process_content_task,
    _secondary_msg_info,
    _tool_msg_ids,
    _typing_tasks,
    set_typing,
)
from ccbot.terminal_parser import parse_status_line

USER_ID = 7
THREAD_ID = 42
CHAT_ID = 555
CHROME = "─" * 40


@pytest.fixture(autouse=True)
def _clear_state():
    for d in (_secondary_msg_info, _tool_msg_ids):
        d.clear()
    for t in list(_typing_tasks.values()):
        t.cancel()
    _typing_tasks.clear()
    yield
    for t in list(_typing_tasks.values()):
        t.cancel()
    _typing_tasks.clear()
    for d in (_secondary_msg_info, _tool_msg_ids):
        d.clear()


# ── 1. 죽은 토픽을 정리하면 타이핑 태스크도 같이 죽어야 한다 ──────────────────
@pytest.mark.asyncio
class TestDeadTopicClearsHandlerState:
    async def test_cleanup_cancels_typing_task(self) -> None:
        """이게 안 되면 봇이 없는 토픽에 4초마다 chat action 을 영원히 던진다."""
        from ccbot.handlers.message_sender import _maybe_cleanup_dead_topic

        bot = AsyncMock()
        with patch("ccbot.handlers.message_queue.session_manager") as mock_sm:
            mock_sm.resolve_chat_id.return_value = CHAT_ID
            set_typing(bot, USER_ID, THREAD_ID, True)
            assert (USER_ID, THREAD_ID) in _typing_tasks

            with patch("ccbot.session.session_manager") as sess:
                sess.cleanup_dead_topic = AsyncMock(
                    return_value=[(USER_ID, THREAD_ID)]
                )
                await _maybe_cleanup_dead_topic(
                    CHAT_ID,
                    {"message_thread_id": THREAD_ID},
                    Exception("Message thread not found"),
                )

        assert (USER_ID, THREAD_ID) not in _typing_tasks, (
            "죽은 토픽을 정리했는데 타이핑 keepalive 가 살아남았다"
        )

    async def test_unrelated_error_does_not_clean(self) -> None:
        """TimedOut 으로 토픽을 지우면 네트워크가 흔들릴 때마다 바인딩이 날아간다."""
        from ccbot.handlers.message_sender import _maybe_cleanup_dead_topic

        with patch("ccbot.session.session_manager") as sess:
            sess.cleanup_dead_topic = AsyncMock(return_value=[])
            await _maybe_cleanup_dead_topic(
                CHAT_ID, {"message_thread_id": THREAD_ID}, Exception("Timed out")
            )
            sess.cleanup_dead_topic.assert_not_called()


# ── 2. 비인접 `·` 불릿은 상태줄이 아니다 ─────────────────────────────────────
class TestBulletIsNotStatus:
    def test_bullet_three_lines_above_is_not_status(self) -> None:
        pane = f"· 첫째 항목은 이렇고…\n결과 줄\n또 한 줄\n{CHROME}"
        assert parse_status_line(pane) is None

    def test_tip_block_case_still_works(self) -> None:
        """#97 이 고치려던 것 — 이 케이스가 죽으면 수정이 과했다는 뜻이다."""
        pane = f"출력\n✳ Gitifying… (2m 1s)\n  ⎿ Tip: 어쩌고\n\n{CHROME}"
        assert parse_status_line(pane) == "Gitifying… (2m 1s)"

    def test_finished_marker_non_adjacent_is_not_status(self) -> None:
        pane = f"출력\n✻ Sautéed for 7s\n결과 줄\n{CHROME}"
        assert parse_status_line(pane) is None

    def test_background_shell_filter_survives_non_adjacent(self) -> None:
        """fork 고유 필터 — upstream 패치가 이걸 무력화하지 않았는지."""
        pane = (
            f"출력\n✻ Running… (3s · 1 shell still running)\n  ⎿ Tip: x\n{CHROME}"
        )
        assert parse_status_line(pane) is None


# ── 3. 인터랙티브 UI 가 떠 있으면 타이핑을 끈다 ──────────────────────────────
@pytest.mark.asyncio
class TestTypingOffWhileWaitingForUser:
    async def test_interactive_ui_turns_typing_off(self) -> None:
        from ccbot.handlers import status_polling

        bot = AsyncMock()
        with patch("ccbot.handlers.message_queue.session_manager") as mock_sm:
            mock_sm.resolve_chat_id.return_value = CHAT_ID
            set_typing(bot, USER_ID, THREAD_ID, True)
            assert (USER_ID, THREAD_ID) in _typing_tasks

            with (
                patch.object(status_polling, "get_interactive_window", return_value="@1"),
                patch.object(status_polling, "is_interactive_ui", return_value=True),
                patch.object(status_polling, "session_manager") as sp_sm,
                patch.object(status_polling, "tmux_manager") as sp_tmux,
                patch.object(status_polling, "get_message_queue") as gq,
            ):
                sp_sm.resolve_chat_id.return_value = CHAT_ID
                win = MagicMock()
                win.window_id = "@1"
                sp_tmux.find_window_by_id = AsyncMock(return_value=win)
                sp_tmux.capture_pane = AsyncMock(return_value="퍼미션 프롬프트")
                gq.return_value = MagicMock(empty=MagicMock(return_value=True))
                await status_polling.update_status_message(
                    bot, USER_ID, "@1", thread_id=THREAD_ID
                )

        assert (USER_ID, THREAD_ID) not in _typing_tasks, (
            "사용자 답을 기다리는 동안 '작업 중' 표시가 계속 나간다"
        )


# ── 4. secondary 태스크의 이미지도 같이 지워진다 ─────────────────────────────
@pytest.mark.asyncio
class TestSecondaryImagesTracked:
    async def test_image_ids_are_tracked_with_text(self) -> None:
        bot = AsyncMock()
        text_msg = MagicMock()
        text_msg.message_id = 100
        bot.send_message.return_value = text_msg
        photo_msg = MagicMock()
        photo_msg.message_id = 101
        bot.send_photo.return_value = photo_msg

        task = MessageTask(
            task_type="content",
            window_id="@1",
            parts=["스크린샷 찍었습니다"],
            content_type="tool_result",
            thread_id=THREAD_ID,
            is_secondary=True,
            image_data=[("image/png", b"fake")],
        )

        with (
            patch("ccbot.handlers.message_queue.session_manager") as mock_sm,
            patch("ccbot.handlers.message_queue.tmux_manager") as mock_tmux,
        ):
            mock_sm.resolve_chat_id.return_value = CHAT_ID
            mock_tmux.find_window_by_id = AsyncMock(return_value=None)
            await _process_content_task(bot, USER_ID, task)

        ids, _wid = _secondary_msg_info[(USER_ID, THREAD_ID)]
        assert 101 in ids, (
            "사진 id 가 추적에서 빠지면 설명 텍스트만 지워지고 스크린샷이 고아로 남는다"
        )
        assert ids == [100, 101]
