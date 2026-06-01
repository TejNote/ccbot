# CCBot 안정성 개선 설계

**날짜**: 2026-04-07  
**대상 레포**: https://github.com/TejNote/ccbot  
**설치 방식**: editable install (`uv tool install --editable ~/Documents/Claude/ccbot-src`)

---

## 배경 및 문제

텔레그램에서 원격으로 Claude Code 세션을 제어할 때 4가지 문제가 발생:

1. **상태 메시지 재출현**: "생각중..." → 결과 → "생각중..." 순서로 나옴
2. **결과 메시지 누락**: 일부 세션에서 Claude 응답이 텔레그램에 안 옴
3. **명령 씹힘**: 텔레그램에서 보낸 명령이 Claude에 전달되지 않음
4. **메시지 송수신 불가**: 일부 세션에서 메시지가 완전히 안 오고 안 보내짐

---

## 원인 분석

### 버그 2, 4 — 근본 원인: session_map 불일치

`session_monitor.py`와 `session.py`의 `load_session_map`은 `config.tmux_session_name` (= `"ccbot"`) prefix를 가진 키만 처리한다.

`hook.py`는 Claude Code 세션 시작 시 현재 tmux 세션 이름을 `tmux display-message`로 읽어 `{session_name}:{window_id}` 형태로 `session_map.json`에 기록한다.

문제: ccbot은 tmux group session으로 구성되어 복사 세션(`ccbot-15`, `ccbot-12` 등)이 존재한다. 사용자가 복사 세션에서 Claude를 실행하면 hook이 `ccbot-15:@4`로 기록하지만, ccbot 봇은 `ccbot:@4`만 처리하므로 무시된다.

**결과**: `ccbot:@4`가 여전히 구버전 세션 ID를 가리켜 새 세션의 응답을 수신하지 못함.

### 버그 1 — 상태 메시지 재출현

`_process_content_task`(message_queue.py)는 텍스트 응답 전송 후 `_check_and_send_status`를 호출한다. 이 함수는 tmux 터미널을 캡처해서 status_line을 파싱하는데, Claude TUI가 최종 응답 후 즉시 상태를 갱신하지 않아 이전 상태("생각중...")가 새 status 메시지로 전송된다.

### 버그 3 — 명령 씹힘

`send_keys`는 텍스트 전송 → 500ms 대기 → Enter 순서로 동작한다. Claude TUI는 응답 생성 중 키 입력을 처리하지 않으므로, Claude가 작업 중일 때 명령을 보내면 씹힌다. 현재 이에 대한 사전 감지나 경고가 없다.

---

## 설계

### 수정 1 — Hook 세션 이름 정규화 (버그 2, 4 근본 해결)

**파일**: `ccbot/hook.py`

hook 실행 시 현재 tmux 세션 이름 대신 ccbot의 canonical session name을 사용한다.

**방식**: `.ccbot/.env`에서 `CCBOT_TMUX_SESSION` 값을 읽어 session_map 키의 prefix로 사용. 없으면 기존 방식(현재 tmux 세션 이름) 유지.

```
# .ccbot/.env에 추가
CCBOT_TMUX_SESSION=ccbot
```

```python
# hook.py 수정 — canonical session name 결정 로직
from .utils import ccbot_dir

env_file = ccbot_dir() / ".env"
canonical_session = tmux_session_name  # fallback: 현재 tmux 세션 이름
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith("CCBOT_TMUX_SESSION="):
            val = line.split("=", 1)[1].strip()
            if val:
                canonical_session = val
            break

session_window_key = f"{canonical_session}:{window_id}"
```

**효과**: `ccbot-15`, `ccbot-12` 등 어떤 복사 세션에서 Claude가 실행되든 항상 `ccbot:@window_id` 형태로 기록.

---

### 수정 2 — 상태 메시지 재출현 방지 (버그 1)

**파일**: `ccbot/handlers/message_queue.py`

`_check_and_send_status` 호출 전 150ms 딜레이를 추가해 Claude TUI가 상태를 갱신할 시간을 확보한다.

```python
# _process_content_task 마지막 부분
await asyncio.sleep(0.15)  # TUI 상태 갱신 대기
await _check_and_send_status(bot, user_id, wid, task.thread_id)
```

**효과**: 최종 응답 전송 후 이전 상태("생각중...")가 재출현하지 않음.

---

### 수정 3 — 명령 씹힘 방지 (버그 3)

**파일**: `ccbot/tmux_manager.py` (또는 명령 처리 handler)

`send_keys` 전 terminal status를 체크해 Claude가 응답 생성 중이면 명령 전송을 막고 사용자에게 경고한다.

```python
# send_to_window 또는 명령 전송 handler에서
pane_text = await tmux_manager.capture_pane(window_id)
status = parse_status_line(pane_text) if pane_text else ""
if status and "esc to interrupt" in status.lower():
    return False, "Claude가 응답 생성 중입니다. 완료 후 다시 시도해주세요."
```

**효과**: 명령 씹힘 대신 명확한 안내 메시지 제공.

---

## 개발 워크플로

```bash
# 1. 레포 클론
git clone git@github.com:TejNote/ccbot.git ~/Documents/Claude/ccbot-src

# 2. editable install (수정 즉시 반영)
uv tool install --editable ~/Documents/Claude/ccbot-src

# 3. .ccbot/.env에 CCBOT_TMUX_SESSION 추가
echo "CCBOT_TMUX_SESSION=ccbot" >> ~/.ccbot/.env

# 4. 수정 → ccbot 재시작 → 테스트
# 5. git commit/push
```

---

## 수정 범위 요약

| 파일 | 변경 내용 |
|------|-----------|
| `ccbot/hook.py` | `.ccbot/.env`에서 canonical session name 읽기 |
| `ccbot/handlers/message_queue.py` | status 체크 전 150ms 딜레이 추가 |
| `ccbot/tmux_manager.py` 또는 handler | 명령 전송 전 Claude 작업 중 여부 체크 |
| `.ccbot/.env` | `CCBOT_TMUX_SESSION=ccbot` 추가 |

---

## 제외 범위

- ccbot의 다른 기능 변경 없음
- Telegram 토픽 라우팅 로직 변경 없음
- 배칭(message_batcher.py) 로직 변경 없음
