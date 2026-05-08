# codex thinking status 구현 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** codex window 한 turn 동안 텔레그램 토픽에 in-place edit으로 thinking status 메시지 표시. claude의 spinner UX와 동일한 사용감 + 도구 사용 trace 노출.

**Architecture:** ccbot 기존 `status_poll_loop` (1초 capture-pane polling) 인프라를 그대로 재사용하고, `terminal_parser.py`에 codex 전용 `parse_codex_status_line` 함수를 신규로 추가, `status_polling.py:109`에서 provider/display_name 분기 한 줄 추가. claude 흐름 100% 보존.

**Tech Stack:** Python 3.11+, pytest, libtmux. (omx hook 변경 없음.)

**Spec:** `plans/2026-05-08-codex-thinking-status-알림-design.md` (이 plan은 그 spec의 task 분해)

---

## File Structure

| 파일 | 변경 |
|---|---|
| `src/ccbot/terminal_parser.py` | `parse_codex_status_line()` 함수 + `STATUS_SPINNERS_CODEX` / `CODEX_TOOL_RE` 상수 추가. 기존 `parse_status_line` 무수정 |
| `src/ccbot/handlers/status_polling.py:109` | `parse_status_line(pane_text)` 호출 직전에 provider 분기 추가 |
| `tests/ccbot/test_terminal_parser.py` | `TestParseCodexStatusLine` 클래스 5 case 추가 |
| `tests/ccbot/test_status_polling_codex.py` | (신규) provider 분기 통합 테스트 1 case |

---

## Task 1: codex thinking 패턴 실측

코드 작성 전 실측. 이 task의 산출물은 patterns 자료 (cli 출력) — 다음 task의 fixture/상수 입력값.

**Files:** 없음 (실측만)

- [ ] **Step 1: 실측 준비 — codex window 깨끗한 상태 확인**

```bash
tmux capture-pane -t ccbot:codex -p -S -10 | tail -10
```

기대: 마지막 라인이 `gpt-X.Y high · ... · main` 형식의 status bar. 누적 입력 라인이 많으면 사용자에게 codex window에서 `/clear` 한 번 입력 요청.

- [ ] **Step 2: 사용자에게 long-thinking 메시지 보내달라고 요청**

사용자가 텔레그램 codex 토픽에 다음 같은 메시지 보냄 (3종류, 각 turn 마다 1초 단위 capture):

  1. **즉답형**: `안녕`
  2. **단일 도구**: `/Users/pakjungeol/Documents/Claude의 LICENSE 파일 내용 보여줘`
  3. **장시간 + 다중 도구**: `5초 기다린 후 README 첫 5줄을 출력해줘`

- [ ] **Step 3: 시계열 capture 자동화**

다른 터미널에서 1초마다 `tmux capture-pane -t ccbot:codex -p -S -50` 실행하며 stderr로 timestamp 찍기. 30초간:

```bash
for i in $(seq 1 30); do
  echo "=== t=${i}s $(date +%H:%M:%S) ===" >&2
  tmux capture-pane -t ccbot:codex -p -S -50
  echo
  sleep 1
done > /tmp/codex-thinking-trace.txt
```

- [ ] **Step 4: trace 분석 — thinking spinner / tool 라인 패턴 추출**

```bash
# spinner character 또는 thinking text 검출
grep -E "^[^a-zA-Z]" /tmp/codex-thinking-trace.txt | sort -u | head -30
# 도구 사용 라인 (• 시작)
grep -E "^\s*•" /tmp/codex-thinking-trace.txt | sort -u | head -30
```

기대: spinner character (예: `⏳`, `▶`, `·`, `…`) 또는 thinking 텍스트 (예: `Working`, `Thinking for Xs`, `Generating`). 도구 라인은 `• Ran`, `• Read`, `• Edit`, `• Wrote`, `• Explored` 같은 동사로 시작.

- [ ] **Step 5: 결과 정리 (다음 task에 박을 상수)**

다음 형식으로 결과를 정리해서 plan에 주석으로 남긴다:

```
STATUS_SPINNERS_CODEX (실측):
  - 검출 character/string: ...
  - working 텍스트 prefix: ...

CODEX_TOOL_RE (실측):
  - 시작 verb 집합: Ran, Read, Edit, Wrote, Explored, ...
```

만약 thinking 패턴을 capture-pane에서 전혀 못 찾으면 **fallback design 활성화** — spec의 "Open question" 섹션 마지막 단락 참조 (omx hook 기반 status_update CLI 신설). 이 경우 본 plan 일시 중단하고 fallback plan 별도 작성.

- [ ] **Step 6: 실측 결과 노트 commit**

```bash
cd ~/Documents/Personal/ccbot-src
mkdir -p tests/ccbot/fixtures
cp /tmp/codex-thinking-trace.txt tests/ccbot/fixtures/codex_thinking_trace.txt
git add tests/ccbot/fixtures/codex_thinking_trace.txt
git commit -m "test(fixtures): codex thinking 패턴 시계열 capture (실측)

다음 task의 parse_codex_status_line fixture / 상수 입력값으로 사용."
```

---

## Task 2: parse_codex_status_line 함수 추가 (TDD)

**Files:**
- Modify: `src/ccbot/terminal_parser.py`
- Test: `tests/ccbot/test_terminal_parser.py`

T1 산출물(`fixtures/codex_thinking_trace.txt`)에서 추출한 패턴을 상수로 박는다.

- [ ] **Step 1: T1 결과로부터 상수 값 결정**

T1 step 5의 분석 결과를 보고 다음 두 상수의 정확한 값을 결정. 본 plan 작성 시점에는 placeholder 값을 두었으니 실측 후 교체.

```python
# 예시 — T1 실측으로 교체할 것
STATUS_SPINNERS_CODEX: frozenset[str] = frozenset(["⏳", "▶", "…"])  # T1 step 5 산출
CODEX_TOOL_RE = re.compile(r"^\s*•\s+(Ran|Read|Edit|Wrote|Explored|Searched)\b")  # T1 step 5
```

- [ ] **Step 2: failing test 작성**

`tests/ccbot/test_terminal_parser.py`에 새 클래스 추가:

```python
# tests/ccbot/test_terminal_parser.py 끝에 추가

from ccbot.terminal_parser import parse_codex_status_line


class TestParseCodexStatusLine:
    def test_thinking_spinner_returns_status(self) -> None:
        """spinner character + working text 라인이 있으면 그 텍스트 반환."""
        # 실측 사례를 fixture로 사용 — T1 트레이스에서 thinking 시점 라인 발췌
        pane = (
            "› 5초 기다린 후 README 출력\n"
            "⏳ Working 3s\n"
            "  gpt-5.5 high · 5h 99% · weekly 73% · Context 94% left · main\n"
        )
        result = parse_codex_status_line(pane)
        assert result is not None
        assert "Working" in result or "⏳" in result

    def test_tool_use_line_returns_status(self) -> None:
        """thinking spinner 없을 때 가장 최근 도구 사용 라인 반환."""
        pane = (
            "› LICENSE 보여줘\n"
            "• Read LICENSE\n"
            "  gpt-5.5 high · 5h 99% · weekly 73% · Context 94% left · main\n"
        )
        result = parse_codex_status_line(pane)
        assert result is not None
        assert "Read" in result

    def test_idle_returns_none(self) -> None:
        """status bar만 있고 thinking/trace 없으면 None."""
        pane = (
            "› Use /skills to list available skills\n"
            "  gpt-5.5 high · 5h 99% · weekly 73% · Context 94% left · main\n"
        )
        assert parse_codex_status_line(pane) is None

    def test_status_bar_filtered_out(self) -> None:
        """status bar 라인이 결과에 포함되지 않는다."""
        pane = (
            "• Ran echo hello\n"
            "  gpt-5.5 high · 5h 99% · weekly 73% · Context 94% left · main\n"
        )
        result = parse_codex_status_line(pane)
        assert result is not None
        assert "gpt-5.5" not in result

    def test_empty_pane_returns_none(self) -> None:
        assert parse_codex_status_line("") is None
        assert parse_codex_status_line("\n\n\n") is None
```

- [ ] **Step 3: 테스트 실행 — fail 확인**

```bash
cd ~/Documents/Personal/ccbot-src
uv run pytest tests/ccbot/test_terminal_parser.py::TestParseCodexStatusLine -v 2>&1 | tail -15
```

기대: 5개 모두 FAIL (`ImportError: cannot import name 'parse_codex_status_line'` 또는 그 유사).

- [ ] **Step 4: parse_codex_status_line 구현**

`src/ccbot/terminal_parser.py`의 기존 `STATUS_SPINNERS = frozenset([...])` 상수 직후에 추가:

```python
# codex TUI status patterns (실측 fixture: tests/ccbot/fixtures/codex_thinking_trace.txt).
# claude의 STATUS_SPINNERS와 별도로 둔다 — codex의 spinner/tool 표시는 별개 어휘.
STATUS_SPINNERS_CODEX: frozenset[str] = frozenset(["⏳", "▶", "…"])  # T1 실측 결과로 교체
CODEX_TOOL_RE = re.compile(r"^\s*•\s+(Ran|Read|Edit|Wrote|Explored|Searched)\b")
CODEX_STATUS_BAR_RE = re.compile(r"^\s*gpt-[\d.]+(?:\s+\w+)?\s+·")
CODEX_TOOL_LINE_MAX = 100  # status 메시지에 포함할 도구 라인 길이 상한
```

같은 파일의 `parse_status_line` 함수 직후에 신규 함수 추가:

```python
def parse_codex_status_line(pane_text: str) -> str | None:
    """codex window의 capture-pane에서 thinking status 한 줄 추출.

    우선순위:
      1) thinking spinner (STATUS_SPINNERS_CODEX의 문자로 시작) → "⏳ <text>"
      2) 가장 최근 `• <Verb> ...` 도구 사용 라인 → "🔧 <라인>"
      3) 둘 다 없음 → None (idle 상태)

    status bar 라인(`gpt-X.Y ...`)은 결과에서 제외한다.

    Args:
        pane_text: capture_pane(...) 결과 문자열.

    Returns:
        status 한 줄 텍스트 (앞뒤 공백 제거, 최대 CODEX_TOOL_LINE_MAX자) 또는 None.
    """
    if not pane_text:
        return None

    lines = pane_text.split("\n")
    # 마지막에서 역순 스캔. status bar 라인은 건너뛴다.
    last_tool: str | None = None
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if CODEX_STATUS_BAR_RE.match(line):
            continue
        # 우선순위 1: spinner
        if stripped[0] in STATUS_SPINNERS_CODEX:
            return stripped[:CODEX_TOOL_LINE_MAX]
        # 우선순위 2 후보 — 가장 최근(역순 스캔의 첫번째) 도구 라인 보관
        if last_tool is None and CODEX_TOOL_RE.match(line):
            last_tool = stripped[:CODEX_TOOL_LINE_MAX]
            # 계속 스캔 — 위쪽에 spinner가 있으면 우선순위 1 우선
    if last_tool is not None:
        return f"🔧 {last_tool}"
    return None
```

- [ ] **Step 5: 테스트 실행 — pass 확인**

```bash
cd ~/Documents/Personal/ccbot-src
uv run pytest tests/ccbot/test_terminal_parser.py::TestParseCodexStatusLine -v 2>&1 | tail -15
```

기대: 5개 모두 PASS.

- [ ] **Step 6: 전체 회귀 테스트**

```bash
uv run pytest tests/ -q 2>&1 | tail -5
```

기대: 전체 PASS (claude `parse_status_line` 회귀 없음).

- [ ] **Step 7: commit**

```bash
git add src/ccbot/terminal_parser.py tests/ccbot/test_terminal_parser.py
git commit -m "$(cat <<'EOF'
feat(parser): add parse_codex_status_line for codex thinking status

claude의 parse_status_line은 무수정으로 보존하고 codex 전용 함수를
별도로 추가. STATUS_SPINNERS_CODEX/CODEX_TOOL_RE 상수는 T1 실측
fixture 기반.

우선순위:
  1) thinking spinner character → "⏳ <text>"
  2) 가장 최근 도구 사용 라인 → "🔧 <line>"
  3) 둘 다 없으면 None (idle)

status bar 라인은 결과에서 제외.
EOF
)"
```

---

## Task 3: status_polling.py에 provider 분기

**Files:**
- Modify: `src/ccbot/handlers/status_polling.py:71-119`
- Test: `tests/ccbot/test_status_polling_codex.py` (신규)

- [ ] **Step 1: failing 통합 테스트 작성**

신규 파일 `tests/ccbot/test_status_polling_codex.py`:

```python
"""Integration test for codex provider routing in status_polling."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccbot.handlers.status_polling import update_status_message
from ccbot.session import SessionManager, WindowState


@pytest.fixture
def mgr(monkeypatch) -> SessionManager:
    monkeypatch.setattr(SessionManager, "_load_state", lambda self: None)
    monkeypatch.setattr(SessionManager, "_save_state", lambda self: None)
    return SessionManager()


@pytest.mark.asyncio
async def test_update_status_routes_codex_window_to_codex_parser(
    mgr: SessionManager, monkeypatch
) -> None:
    """codex provider window는 parse_codex_status_line으로 분기."""
    # codex provider window 세팅
    ws = WindowState(provider="codex", cwd="/x", window_name="codex")
    mgr.window_states["@27"] = ws
    monkeypatch.setattr("ccbot.handlers.status_polling.session_manager", mgr)

    # tmux_manager mock
    fake_window = MagicMock(window_id="@27")
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.tmux_manager.find_window_by_id",
        AsyncMock(return_value=fake_window),
    )
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.tmux_manager.capture_pane",
        AsyncMock(return_value="› hi\n• Read X\n  gpt-5.5 high · main\n"),
    )

    # parse 함수 둘 다 spy로 wrap — 어느 게 호출됐는지 검증
    claude_parser = MagicMock(return_value=None)
    codex_parser = MagicMock(return_value="🔧 Read X")
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.parse_status_line", claude_parser
    )
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.parse_codex_status_line", codex_parser
    )

    enqueue = AsyncMock()
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.enqueue_status_update", enqueue
    )
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.is_interactive_ui", lambda _t: False
    )
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.get_interactive_window", lambda _u, _t: None
    )

    bot = MagicMock()
    await update_status_message(bot, user_id=1, window_id="@27", thread_id=42)

    codex_parser.assert_called_once()
    claude_parser.assert_not_called()
    enqueue.assert_awaited_once()
    # 4번째 positional arg가 status_line
    args = enqueue.await_args.args
    assert args[3] == "🔧 Read X"


@pytest.mark.asyncio
async def test_update_status_routes_claude_window_to_claude_parser(
    mgr: SessionManager, monkeypatch
) -> None:
    """기본(claude) provider는 기존 parse_status_line 흐름."""
    ws = WindowState(provider="claude", cwd="/x", window_name="claude")
    mgr.window_states["@5"] = ws
    monkeypatch.setattr("ccbot.handlers.status_polling.session_manager", mgr)

    fake_window = MagicMock(window_id="@5")
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.tmux_manager.find_window_by_id",
        AsyncMock(return_value=fake_window),
    )
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.tmux_manager.capture_pane",
        AsyncMock(return_value="✻ Sautéed for 5s · 1 shell still running\n"),
    )

    claude_parser = MagicMock(return_value="✻ Sautéed for 5s")
    codex_parser = MagicMock(return_value=None)
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.parse_status_line", claude_parser
    )
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.parse_codex_status_line", codex_parser
    )

    enqueue = AsyncMock()
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.enqueue_status_update", enqueue
    )
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.is_interactive_ui", lambda _t: False
    )
    monkeypatch.setattr(
        "ccbot.handlers.status_polling.get_interactive_window", lambda _u, _t: None
    )

    bot = MagicMock()
    await update_status_message(bot, user_id=1, window_id="@5", thread_id=42)

    claude_parser.assert_called_once()
    codex_parser.assert_not_called()
```

- [ ] **Step 2: 테스트 실행 — fail 확인**

```bash
cd ~/Documents/Personal/ccbot-src
uv run pytest tests/ccbot/test_status_polling_codex.py -v 2>&1 | tail -15
```

기대: 첫 번째 case FAIL — `parse_codex_status_line` import 실패 또는 분기 안 됨. 두 번째 case는 PASS 가능 (claude 흐름은 기존 그대로라).

- [ ] **Step 3: status_polling.py에 provider 분기 추가**

`src/ccbot/handlers/status_polling.py` 변경 두 군데:

(a) import 추가 (파일 상단의 기존 import 블록):

```python
from ..terminal_parser import (
    is_interactive_ui,
    parse_codex_status_line,
    parse_status_line,
)
```

(b) `update_status_message` 함수 안 line 109 부근 — `status_line = parse_status_line(pane_text)` 한 줄을 다음으로 교체:

```python
    # provider 분기: codex window는 별도 status 추출 함수.
    # WindowState.provider 가 authoritative; 없으면 display_name == "codex" fallback
    # (codex window는 SessionStart hook 자동 등록 경로가 없어 window_states 가 빌 수 있음).
    ws = session_manager.window_states.get(window_id)
    display = session_manager.get_display_name(window_id)
    is_codex = (ws and ws.provider == "codex") or display == "codex"
    status_line = (
        parse_codex_status_line(pane_text)
        if is_codex
        else parse_status_line(pane_text)
    )
```

- [ ] **Step 4: 테스트 실행 — pass 확인**

```bash
uv run pytest tests/ccbot/test_status_polling_codex.py -v 2>&1 | tail -15
```

기대: 2개 모두 PASS.

- [ ] **Step 5: 전체 회귀 테스트**

```bash
uv run pytest tests/ -q 2>&1 | tail -5
```

기대: 전체 PASS.

- [ ] **Step 6: commit**

```bash
git add src/ccbot/handlers/status_polling.py tests/ccbot/test_status_polling_codex.py
git commit -m "$(cat <<'EOF'
feat(status): codex provider routing in update_status_message

WindowState.provider == 'codex' (또는 display_name == 'codex' fallback)
일 때 parse_codex_status_line 으로 분기. claude 흐름은 기존 그대로.

provider 결정 로직은 SessionManager.send_to_window 와 동일 (M1 의 paste
경로 분기와 일관).
EOF
)"
```

---

## Task 4: 통합 검증 + push

**Files:** 없음 (검증만 + push)

- [ ] **Step 1: ccbot 프로세스 reload**

editable install이라 코드 변경은 즉시 반영되지만, 이미 import된 모듈은 메모리에 옛 버전. ccbot 재시작:

```bash
pkill -HUP -f "ccbot start"
sleep 3
ps aux | grep "ccbot start" | grep -v grep | head -2
```

기대: 새 PID로 ccbot 재시작.

- [ ] **Step 2: e2e — 텔레그램 in-place edit 동작 확인**

사용자가 텔레그램 codex 토픽에 다음 메시지 입력:

```
5초 기다린 후 README 첫 5줄을 보여줘
```

기대 흐름:
1. ccbot 토픽에 새 status 메시지 push (`⏳ Working ...` 또는 `🔧 Ran sleep 5`).
2. 1초 간격으로 in-place edit (메시지 ID 유지, 본문 갱신 — `🔧 Read README.md` 등으로 진행).
3. codex 응답 완료 시 status 메시지 사라지고 응답 본문이 별도 메시지로 도착 (M1 omx hook 흐름).

육안 확인:
- status 메시지가 새로 N개 쌓이지 않고 1개만 in-place edit 되는지
- 응답 도착 후 status 메시지가 깔끔히 사라지는지
- 도구 사용 라인이 trace로 보이는지

- [ ] **Step 3: claude 흐름 회귀 — 변화 없음 확인**

사용자가 텔레그램 claude 토픽 (또는 ceo/metlife 등)에 평소대로 메시지 입력. claude 흐름은 기존 그대로 (✻ Sautéed for Xs · ... 형식 in-place edit) 작동해야 함.

기대: claude window에 회귀 없음, 평소와 동일한 spinner status.

- [ ] **Step 4: ccbot 로그 sanity check**

```bash
tail -30 ~/Documents/Claude/logs/ccbot-autostart.log | grep -iE "error|traceback|exception" || echo "no errors"
```

기대: `no errors`.

- [ ] **Step 5: feature 브랜치 push (공유 브랜치 직접 push 금지 — push-guard)**

```bash
cd ~/Documents/Personal/ccbot-src
git branch -vv  # upstream이 main/dev/prod이면 중단
git push origin HEAD:ccbot-codex-connect-by-cluade
```

기대: PR #3 자동 갱신 (Task 1~3 commits 추가).

- [ ] **Step 6: PR 본문 backlog 항목 — 본 plan 머지 표시**

PR #3 본문의 Backlog 섹션에 본 plan 완료 표시:

```markdown
- [x] codex thinking status in-place 알림 (plans/2026-05-08-codex-thinking-status-구현.md)
```

`gh pr edit 3 --repo TejNote/ccbot --body-file <updated_body>` 또는 GitHub UI에서 직접 편집.

---

## Self-Review

- [x] **Spec coverage**: spec의 4개 핵심 결정 — Architecture(B-lite + status_polling), parse_codex_status_line 우선순위, Data Flow, File Structure — 각각 Task 2/3에 매핑됨. spec의 "Open question(실측)"은 Task 1에 명시.
- [x] **Placeholder 없음**: 모든 step에 실제 코드 / 명령 / 기대 출력 포함. T1의 `STATUS_SPINNERS_CODEX` 값은 placeholder가 아니라 의도적 실측 입력값 — T2 step 1에서 교체 명시.
- [x] **Type 일관성**: `parse_codex_status_line(pane_text: str) -> str | None`, `STATUS_SPINNERS_CODEX: frozenset[str]`, `CODEX_TOOL_RE: re.Pattern` — Task 2/3 전체에서 동일 사용.
- [x] **Provider 분기 일관**: `(ws and ws.provider == "codex") or display == "codex"` 패턴이 spec / Task 3 / 기존 `session.py:send_to_window` (M1)와 동일.
- [x] **회귀 보호**: claude `parse_status_line` 무수정 + 분기 테스트로 회귀 케이스(`test_update_status_routes_claude_window_to_claude_parser`) 명시.

---

## Related

- spec: `plans/2026-05-08-codex-thinking-status-알림-design.md`
- M1 plan: `plans/2026-05-07-codex-omx-ccbot-연동.md` (양방향 폐루프)
- 참조 코드:
  - `src/ccbot/handlers/status_polling.py:46-119` (`update_status_message` 흐름)
  - `src/ccbot/terminal_parser.py:199` (`STATUS_SPINNERS`, `parse_status_line`)
  - `src/ccbot/session.py:854-887` (`send_to_window` provider 분기 — Task 3과 동일 패턴)
