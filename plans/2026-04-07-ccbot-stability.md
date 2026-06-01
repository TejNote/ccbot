# CCBot Stability Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ccbot의 텔레그램 원격 제어에서 발생하는 4가지 안정성 문제(상태 메시지 재출현, 세션 누락, 명령 씹힘, 세션 불통)를 수정한다.

**Architecture:** 3개 파일 수정 — hook.py(세션 이름 정규화), message_queue.py(상태 체크 딜레이), session.py(명령 전송 전 busy 감지). 각 수정은 독립적이며 순서대로 진행.

**Tech Stack:** Python 3.13, asyncio, libtmux, python-telegram-bot, uv editable install

---

## File Map

| 파일 (소스 레포 기준) | 변경 내용 |
|---|---|
| `ccbot/hook.py` | `TMUX_SESSION_NAME` env 읽어 canonical session name 사용 |
| `ccbot/handlers/message_queue.py` | `_check_and_send_status` 호출 3곳에 150ms 딜레이 추가 |
| `ccbot/session.py` | `send_to_window`에서 send_keys 전 Claude busy 상태 체크 |

---

## Task 1: 레포 클론 및 editable install 설정

**Files:**
- Create: `~/Documents/Claude/ccbot-src/` (git clone)

- [ ] **Step 1: ccbot 레포 클론**

```bash
git clone git@github.com:TejNote/ccbot.git ~/Documents/Claude/ccbot-src
```

Expected: `Cloning into '/Users/pakjungeol/Documents/Claude/ccbot-src'...` 성공

- [ ] **Step 2: 현재 ccbot 중지**

```bash
ccbot stop
```

Expected: ccbot 프로세스 종료 확인

- [ ] **Step 3: editable install로 재설치**

```bash
uv tool install --editable ~/Documents/Claude/ccbot-src
```

Expected: `Installed 1 package in ...` 또는 기존 설치가 editable로 교체됨

- [ ] **Step 4: 설치 확인**

```bash
ccbot --version
ls -la ~/Documents/Claude/ccbot-src/ccbot/hook.py
```

Expected: 버전 출력, 소스 파일 확인. 이후 소스 수정이 즉시 반영됨.

---

## Task 2: Fix 1 — hook.py 세션 이름 정규화 (버그 2, 4 근본 해결)

**Files:**
- Modify: `~/Documents/Claude/ccbot-src/ccbot/hook.py` (line 218-220 근처)

**배경:** tmux group session의 복사본(ccbot-15, ccbot-12)에서 Claude 실행 시 hook이 `ccbot-15:@4`로 기록하지만 ccbot은 `ccbot:@4`만 처리한다. `.ccbot/.env`의 `TMUX_SESSION_NAME=ccbot`을 읽어 항상 canonical 이름으로 기록하도록 수정.

- [ ] **Step 1: hook.py에서 수정 위치 확인**

`~/Documents/Claude/ccbot-src/ccbot/hook.py` 파일의 218-220행:
```python
tmux_session_name, window_id, window_name = parts
# Key uses window_id for uniqueness
session_window_key = f"{tmux_session_name}:{window_id}"
```

- [ ] **Step 2: canonical session name 결정 로직 삽입**

218행의 `tmux_session_name, window_id, window_name = parts` 다음 줄에 추가:

```python
tmux_session_name, window_id, window_name = parts

# Use canonical session name from .ccbot/.env (TMUX_SESSION_NAME) if set.
# This handles tmux group session copies (ccbot-15, ccbot-12, etc.) which
# would otherwise record keys like "ccbot-15:@4" that the bot ignores.
from .utils import ccbot_dir

_env_file = ccbot_dir() / ".env"
canonical_session = tmux_session_name  # fallback: current tmux session name
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line.startswith("TMUX_SESSION_NAME="):
            _val = _line.split("=", 1)[1].strip()
            if _val:
                canonical_session = _val
            break

# Key uses window_id for uniqueness
session_window_key = f"{canonical_session}:{window_id}"
```

- [ ] **Step 3: 수정 후 문법 확인**

```bash
python3 -c "import ast; ast.parse(open('$HOME/Documents/Claude/ccbot-src/ccbot/hook.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 4: 수동 테스트 — hook 동작 확인**

현재 세션에서 새 Claude 세션 시작 후 session_map.json 확인:
```bash
cat ~/.ccbot/session_map.json | python3 -m json.tool
```

Expected: 새 세션 키가 `ccbot:@N` 형태 (복사 세션 이름이 아닌 canonical 이름)

- [ ] **Step 5: 커밋**

```bash
cd ~/Documents/Claude/ccbot-src
git add ccbot/hook.py
git commit -m "fix: hook - normalize tmux session name using TMUX_SESSION_NAME env

Group session copies (ccbot-15, ccbot-12) were recording session_map keys
with their own names (ccbot-15:@4) instead of the canonical name (ccbot:@4).
Bot only processes ccbot: prefix, so these sessions were invisible.

Fix: read TMUX_SESSION_NAME from .ccbot/.env and use as canonical prefix."
```

---

## Task 3: Fix 2 — message_queue.py 상태 메시지 재출현 방지 (버그 1)

**Files:**
- Modify: `~/Documents/Claude/ccbot-src/ccbot/handlers/message_queue.py`

**배경:** `_process_content_task`는 결과 전송 직후 `_check_and_send_status`를 호출해 tmux 터미널을 캡처한다. Claude TUI가 최종 응답 후 상태를 즉시 갱신하지 않아 이전 상태("생각중...")가 새 status 메시지로 전송된다.

수정 위치 3곳 (모두 `_check_and_send_status` 직전에 150ms 딜레이 추가):

- [ ] **Step 1: message_queue.py에서 수정 위치 확인**

3곳의 `await _check_and_send_status(...)` 호출:
- **위치 A** (line ~324): tool_result 처리 성공 경로
- **위치 B** (line ~339): tool_result 처리 fallback 경로  
- **위치 C** (line ~385): 일반 content 전송 후

- [ ] **Step 2: 위치 A 수정 (tool_result 성공 경로)**

```python
# 변경 전 (line ~323-324):
                await _send_task_images(bot, chat_id, task)
                await _check_and_send_status(bot, user_id, wid, task.thread_id)

# 변경 후:
                await _send_task_images(bot, chat_id, task)
                await asyncio.sleep(0.15)  # Wait for Claude TUI to update status
                await _check_and_send_status(bot, user_id, wid, task.thread_id)
```

- [ ] **Step 3: 위치 B 수정 (tool_result fallback 경로)**

```python
# 변경 전 (line ~338-339):
                    await _send_task_images(bot, chat_id, task)
                    await _check_and_send_status(bot, user_id, wid, task.thread_id)

# 변경 후:
                    await _send_task_images(bot, chat_id, task)
                    await asyncio.sleep(0.15)  # Wait for Claude TUI to update status
                    await _check_and_send_status(bot, user_id, wid, task.thread_id)
```

- [ ] **Step 4: 위치 C 수정 (일반 content 후)**

```python
# 변경 전 (line ~384-385):
    # 5. After content, check and send status
    await _check_and_send_status(bot, user_id, wid, task.thread_id)

# 변경 후:
    # 5. After content, check and send status
    await asyncio.sleep(0.15)  # Wait for Claude TUI to update status after response
    await _check_and_send_status(bot, user_id, wid, task.thread_id)
```

- [ ] **Step 5: 문법 확인**

```bash
python3 -c "import ast; ast.parse(open('$HOME/Documents/Claude/ccbot-src/ccbot/handlers/message_queue.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 6: ccbot 재시작 후 동작 확인**

```bash
ccbot stop && ccbot start
```

텔레그램에서 Claude 응답 후 "생각중..." 메시지가 재출현하지 않는지 확인.

- [ ] **Step 7: 커밋**

```bash
cd ~/Documents/Claude/ccbot-src
git add ccbot/handlers/message_queue.py
git commit -m "fix: add 150ms delay before status check to prevent stale status reappearance

Claude TUI does not update its status line immediately after completing a
response. Reading the terminal right after sending the response captured the
previous 'thinking...' status and re-sent it as a new status message.

Fix: sleep 150ms before _check_and_send_status at all 3 call sites."
```

---

## Task 4: Fix 3 — session.py 명령 씹힘 방지 (버그 3)

**Files:**
- Modify: `~/Documents/Claude/ccbot-src/ccbot/session.py` (send_to_window, line ~814-829)

**배경:** Claude TUI가 응답 생성 중 키 입력을 처리하지 않는다. `send_to_window`는 Claude가 작업 중인지 확인하지 않고 바로 keys를 전송한다. terminal 상태를 먼저 확인해 "esc to interrupt" 텍스트가 있으면 경고를 반환한다.

- [ ] **Step 1: session.py에서 send_to_window 확인**

`~/Documents/Claude/ccbot-src/ccbot/session.py` 에서 `send_to_window` 함수 (line ~814-829):

```python
async def send_to_window(self, window_id: str, text: str) -> tuple[bool, str]:
    """Send text to a tmux window by ID."""
    display = self.get_display_name(window_id)
    ...
    window = await tmux_manager.find_window_by_id(window_id)
    if not window:
        return False, "Window not found (may have been closed)"
    success = await tmux_manager.send_keys(window.window_id, text)
    if success:
        return True, f"Sent to {display}"
    return False, "Failed to send keys"
```

- [ ] **Step 2: terminal_parser import 확인**

`session.py` 상단에 `from .terminal_parser import parse_status_line` import가 있는지 확인:

```bash
head -30 ~/Documents/Claude/ccbot-src/ccbot/session.py | grep terminal_parser
```

없으면 import 추가.

- [ ] **Step 3: send_to_window 수정**

`window = await tmux_manager.find_window_by_id(window_id)` 다음, `send_keys` 호출 전에 busy 체크 추가:

```python
async def send_to_window(self, window_id: str, text: str) -> tuple[bool, str]:
    """Send text to a tmux window by ID."""
    display = self.get_display_name(window_id)
    logger.debug(
        "send_to_window: window_id=%s (%s), text_len=%d",
        window_id,
        display,
        len(text),
    )
    window = await tmux_manager.find_window_by_id(window_id)
    if not window:
        return False, "Window not found (may have been closed)"

    # Check if Claude is currently generating a response.
    # Claude TUI ignores key input while working, causing commands to be silently dropped.
    pane_text = await tmux_manager.capture_pane(window.window_id)
    if pane_text:
        status = parse_status_line(pane_text)
        if status and "esc to interrupt" in status.lower():
            return False, "Claude가 응답 생성 중입니다. 완료 후 다시 시도해주세요."

    success = await tmux_manager.send_keys(window.window_id, text)
    if success:
        return True, f"Sent to {display}"
    return False, "Failed to send keys"
```

- [ ] **Step 4: terminal_parser import 추가 (필요한 경우)**

`session.py` import 섹션에 없으면 추가:
```python
from .terminal_parser import parse_status_line
```

- [ ] **Step 5: 문법 확인**

```bash
python3 -c "import ast; ast.parse(open('$HOME/Documents/Claude/ccbot-src/ccbot/session.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 6: ccbot 재시작 및 동작 확인**

```bash
ccbot stop && ccbot start
```

Claude가 응답 생성 중 텔레그램에서 명령 전송 시 "Claude가 응답 생성 중입니다" 메시지 반환 확인.

- [ ] **Step 7: 커밋**

```bash
cd ~/Documents/Claude/ccbot-src
git add ccbot/session.py
git commit -m "fix: check Claude busy state before send_keys to prevent silent command drops

Claude TUI does not process key input during response generation. Commands
sent while Claude is working were silently dropped with no feedback to user.

Fix: capture pane and parse status line in send_to_window. If status contains
'esc to interrupt', return an error message instead of sending keys."
```

---

## Task 5: GitHub push 및 최종 검증

- [ ] **Step 1: 전체 변경사항 확인**

```bash
cd ~/Documents/Claude/ccbot-src
git log --oneline -5
git diff origin/main..HEAD --stat
```

Expected: Task 1~4의 커밋 3개 확인

- [ ] **Step 2: GitHub push**

```bash
cd ~/Documents/Claude/ccbot-src
git push origin main
```

- [ ] **Step 3: ccbot 최종 재시작**

```bash
ccbot stop && ccbot start
```

- [ ] **Step 4: 세션 상태 확인**

```bash
cat ~/.ccbot/session_map.json | python3 -m json.tool
```

Expected: 모든 활성 세션이 `ccbot:@N` 형태 키로 등록됨

- [ ] **Step 5: 통합 동작 테스트**

텔레그램에서 각 ccbot 세션에 간단한 명령 전송 후 확인:
1. 응답이 텔레그램에 정상 수신되는지
2. 응답 후 "생각중..." 상태가 재출현하지 않는지
3. Claude 작업 중 명령 전송 시 경고 메시지가 오는지
