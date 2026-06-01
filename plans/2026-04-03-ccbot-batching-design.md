# ccbot 메시지 배칭 설계

**날짜**: 2026-04-03  
**상태**: 승인됨  

---

## 문제

scraping 등 장시간 작업 세션에서 tool call(Bash, ToolSearch, MCP 등)과 Thinking 메시지가 건별로 Telegram에 전송되어 메시지 폭주 및 딜레이 발생.

사용자 요구: "Claude가 열심히 일하고 있다는 걸 알고 싶다" — 완전 무음은 불안, 폭주는 불편.

---

## 해결 방향

`CCBOT_BATCH_WINDOW` 초(기본 10초) 동안 쌓인 tool call / thinking 메시지를 하나로 묶어 전송. Claude 최종 텍스트 응답은 즉시 flush + 전송.

---

## 아키텍처

### 메시지 분류

| content_type | is_complete | 처리 방식 |
|---|---|---|
| `tool_use` | True | 배치 버퍼에 추가 |
| `tool_result` | True | 배치 버퍼에 추가 |
| `thinking` | any | 배치 버퍼에 추가 |
| `text` (assistant) | True | 버퍼 즉시 flush → 원문 전송 |

### MessageBatcher 컴포넌트

**위치**: `ccbot/message_batcher.py` (신규)

**책임**:
- 세션(user_id, thread_id)별 메시지 버퍼 유지
- `add(msg_summary)` — 버퍼에 메시지 요약 추가
- `flush(user_id, thread_id)` → `str | None` — 버퍼 내용을 포맷된 문자열로 반환 후 클리어
- 백그라운드 타이머: `CCBOT_BATCH_WINDOW`초마다 비어있지 않은 버퍼 자동 flush

### bot.py 수정

`handle_new_message` 내 기존 skip 로직 이후에 배칭 레이어 삽입:

```
tool_use / tool_result / thinking
  → batcher.add(요약)
  → (타이머가 N초 후 flush해서 전송)

text + is_complete
  → batcher.flush() → 배치 메시지 전송 (있으면)
  → 원문 텍스트 전송 (기존 enqueue_content_message)
```

### 설정 추가 (config.py)

```python
self.batch_window = float(os.getenv("CCBOT_BATCH_WINDOW", "10.0"))
# 0.0 이면 배칭 비활성화 (기존 동작)
```

### 배칭 메시지 포맷

Task(Agent) 호출은 description을 파싱해서 어떤 에이전트가 일하는지 표시:

```
⚙️ 작업 중 (10초간 8건)
• Bash × 3
• Thinking × 2
• Task(frontend-developer: 컴포넌트 구현) × 1
• ToolSearch × 1
• Read × 1
```

Task 외 도구는 tool_name만, Task/Agent는 input의 `description` 필드 파싱.

---

## 구현 방식: GitHub Fork

- `six-ddc/ccbot` → fork → `TejNote/ccbot`
- `uv tool install git+https://github.com/TejNote/ccbot.git` 으로 재설치
- 이후 `uv tool upgrade ccbot` 이 TejNote/ccbot 기준으로 동작
- 업스트림 업데이트는 TejNote/ccbot에서 `git merge upstream/main`

## 구현 범위

1. `TejNote/ccbot` fork 생성
2. `ccbot/message_batcher.py` — MessageBatcher 클래스 신규 작성
3. `ccbot/config.py` — `CCBOT_BATCH_WINDOW` 설정 추가
4. `ccbot/bot.py` — `handle_new_message`에 배칭 레이어 삽입
5. `uv tool install git+https://github.com/TejNote/ccbot.git` 재설치
6. `~/.ccbot/.env` — `CCBOT_BATCH_WINDOW=10` 추가
7. ccbot 재시작

## 제외 범위

- 이미지/voice 메시지 배칭 없음
- Interactive UI 메시지(프롬프트 등) 배칭 없음
- 세션 간 버퍼 공유 없음
