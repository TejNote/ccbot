# 메시지 큐 순서 보장 설계

> Claude 응답과 시간적으로 겹칠 수 있는 직접 전송 메시지를 기존 FIFO 큐로 통일하여 텔레그램 메시지 순서를 보장한다.

## 배경

ccbot의 메시지 전송 경로가 2개로 나뉘어 있음:
- **큐 경로**: JSONL 모니터 → `enqueue_content_message` → FIFO 큐 워커 → Telegram
- **직접 경로**: `safe_reply()` 등으로 즉시 전송 (큐 우회)

이 두 경로가 동시에 같은 토픽에 메시지를 보내면 Telegram 서버 도착 순서가 엇갈림. 대표적으로 `⚡ Sent: /brainstorming` 확인 메시지가 Claude 응답 사이에 끼어드는 문제.

## 핵심 변경

`message_queue.py`에 `DirectMessage` 타입과 `enqueue_direct_message()` 함수를 추가. 큐 워커가 기존 ContentMessage/StatusUpdate와 동일한 FIFO 순서로 DirectMessage도 처리.

## DirectMessage 타입

```python
@dataclass
class DirectMessage:
    chat_id: int
    thread_id: int | None
    text: str
    parse_mode: str | None = None
    reply_markup: InlineKeyboardMarkup | None = None
```

- merging 없음 (독립 메시지)
- `send_with_fallback`으로 전송
- `reply_markup` 지원으로 Interactive UI 메시지도 처리 가능

## enqueue_direct_message API

```python
async def enqueue_direct_message(
    user_id: int,
    chat_id: int,
    thread_id: int | None,
    text: str,
    parse_mode: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
```

- `chat_id`/`thread_id`를 명시적으로 받음 — 큐 워커는 나중에 실행되므로 호출 시점에 추출
- 큐 워커가 없으면 자동 시작 (기존 `enqueue_content_message` 패턴)

## 큐 워커 변경

`_message_queue_worker`의 처리 루프에 DirectMessage 분기 추가:

```python
item = await queue.get()
if isinstance(item, DirectMessage):
    await send_with_fallback(bot, item.chat_id, item.text, 
                             thread_id=item.thread_id,
                             parse_mode=item.parse_mode,
                             reply_markup=item.reply_markup)
elif isinstance(item, ContentMessage):
    # 기존 로직
elif isinstance(item, StatusUpdate):
    # 기존 로직
```

## 큐로 전환할 전송 경로

| 전송 | 파일:위치 | 현재 | 변경 |
|------|-----------|------|------|
| `⚡ Sent: /command` | bot.py forward_command_handler | `safe_reply()` | `enqueue_direct_message()` |
| `📷 Image sent` | bot.py photo_handler | `safe_reply()` | `enqueue_direct_message()` |
| `🎙 Voice forwarded` | bot.py voice_handler | `safe_reply()` | `enqueue_direct_message()` |
| Interactive UI 전송 | interactive_ui.py handle_interactive_ui | `bot.send_message()` | `enqueue_direct_message()` |
| Bash capture 출력 | bot.py _send_bash_capture | `send_with_fallback()` | `enqueue_direct_message()` |

## 직접 전송 유지

| 전송 | 이유 |
|------|------|
| `❌` 에러 메시지 | 즉시 피드백 필요 |
| 디렉토리 브라우저 / 세션 피커 | 인터랙티브 UI, 큐 지연이 UX 해침 |
| 콜백 쿼리 응답 (query.answer) | Telegram이 빠른 응답 요구 |
| /history, /screenshot 표시 | 사용자 요청에 대한 즉시 응답 |
| /favorite 키보드 | 동일 |
| /start 환영 메시지 | 동일 |

## 판단 기준

- **큐**: "이 메시지가 Claude 응답 사이에 끼어들 수 있는가?" → Yes → 큐
- **직접**: "즉시 반응이 필수" 또는 "Claude 비작업 상태에서만 발생" → 직접

## 스코프 외

- 큐 성능 최적화
- 큐 full 시 backpressure
- edit 메시지 순서 보장 (edit는 기존 메시지 수정이므로 순서 무관)
