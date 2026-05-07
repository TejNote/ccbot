# ccbot — Codex/OMX provider 연동 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ccbot이 Telegram 토픽 ↔ tmux 창 양방향 라우팅을 codex/omx 세션에도 동일하게 제공한다. claude `--resume` 창과 동일한 사용감 (토픽에서 메시지 던지면 codex로 들어가고, codex 응답이 토픽으로 돌아오는 폐루프).

**Architecture (B-lite):**
- ccbot 본체는 **WindowState에 `provider: Literal["claude", "codex"]` 필드 1개**만 추가. transcript parser ABC, SessionProvider 추상화 등 큰 리팩터는 하지 않는다 (YAGNI).
- 입력 라우팅(텔레그램 → tmux)은 기존 `tmux_manager.send_keys`가 provider 무관이라 **신규 코드 0**.
- 응답 라우팅(codex → 텔레그램)은 ccbot 본체에 폴링 추가하지 않고, **omx의 native hook(Stop)** 에서 capture-pane 후 `ccbot send --window <name>` CLI를 호출하는 외부 스크립트로 처리. 본체 결합도 0.
- 운영 정책: ccbot tmux 안의 codex 창에서는 항상 `OMX_LAUNCH_POLICY=direct omx`로 부팅. `detached-tmux`(default)는 ccbot 단일 tmux 모델과 충돌하므로 금지.

**Tech Stack:** Python 3.11+ (ccbot), python-telegram-bot, tmux, pytest, dataclasses; bash (ccbot-start-real.sh), Node.js (omx hook plugin .mjs).

**Scope:** M1만 다룬다. M2(codex rollout JSONL 파서, terminal_parser codex 호환) 는 1주 사용 후 별도 plan으로 결정 — 본 plan 끝의 "Backlog" 참조.

**Out-of-repo files (이 레포 밖에서 변경):**
- `~/.local/scripts/ccbot-start-real.sh` — ccbot 부팅 스크립트. plan 안에서 변경 단계를 명시.
- `~/Documents/Claude/.omx/hooks/ccbot-bridge.mjs` — omx hook plugin (신규). plan 안에서 작성 단계 명시. 별도 commit/리포 관리 대상은 아님.

---

## File Structure

| 파일 | 책임 | 동작 |
|---|---|---|
| `src/ccbot/session.py` | `WindowState` dataclass에 `provider` 필드 | 수정 |
| `tests/ccbot/test_session.py` | `WindowState` provider 직렬화/하위호환 검증 | 수정 |
| `~/.local/scripts/ccbot-start-real.sh` | tmux 6창 → 7창(`codex` 추가) + send-keys 명령 분기 | 수정 (외부) |
| `~/Documents/Claude/.omx/hooks/ccbot-bridge.mjs` | omx Stop hook → capture-pane → `ccbot send --window` | 신규 (외부) |
| `plans/2026-05-07-codex-omx-ccbot-연동.md` | 본 plan | 신규 |

분리 근거: ccbot 본체는 데이터 모델 1필드 추가만. 운영 스크립트와 hook bridge는 본체와 책임이 다르고 본체 release cycle과 다른 속도로 변하므로 외부 파일로 분리.

---

## Task 1: WindowState에 `provider` 필드 추가 (TDD)

**Files:**
- Modify: `src/ccbot/session.py:44-73`
- Test: `tests/ccbot/test_session.py` (기존 파일 또는 신규 — 1단계에서 확인)

- [ ] **Step 1: 기존 테스트 위치 확인**

```bash
ls /Users/pakjungeol/Documents/Personal/ccbot-src/tests/ccbot/test_session.py 2>&1
grep -n "WindowState" /Users/pakjungeol/Documents/Personal/ccbot-src/tests/ccbot/*.py 2>&1 | head -10
```

기존 `test_session.py`가 있고 `WindowState` 테스트가 있으면 거기에 case 추가. 없으면 새 파일 `tests/ccbot/test_window_state_provider.py` 생성.

- [ ] **Step 2: failing test 작성**

다음 4개 case를 추가한다 (하위호환이 핵심):

```python
# tests/ccbot/test_window_state_provider.py (신규 파일이면)
# 또는 tests/ccbot/test_session.py에 추가

from ccbot.session import WindowState


def test_window_state_default_provider_is_claude():
    """Default provider는 'claude'여서 기존 동작 보존."""
    ws = WindowState(session_id="abc", cwd="/x", window_name="claude")
    assert ws.provider == "claude"


def test_window_state_can_set_codex_provider():
    """provider='codex' 명시 가능."""
    ws = WindowState(provider="codex", cwd="/x", window_name="codex")
    assert ws.provider == "codex"


def test_window_state_to_dict_includes_provider_when_codex():
    """직렬화 시 codex provider는 포함, claude(기본)는 생략 — state.json 안 부풀리기."""
    codex_ws = WindowState(provider="codex", window_name="codex", cwd="/x")
    assert codex_ws.to_dict()["provider"] == "codex"

    claude_ws = WindowState(window_name="claude", cwd="/x")
    assert "provider" not in claude_ws.to_dict()


def test_window_state_from_dict_legacy_state_defaults_to_claude():
    """기존 state.json (provider 키 없음) 로드 시 claude 기본값 — 하위호환."""
    legacy = {"session_id": "abc", "cwd": "/x", "window_name": "claude"}
    ws = WindowState.from_dict(legacy)
    assert ws.provider == "claude"


def test_window_state_from_dict_with_provider_codex():
    new = {"session_id": "", "cwd": "/x", "window_name": "codex", "provider": "codex"}
    ws = WindowState.from_dict(new)
    assert ws.provider == "codex"
```

- [ ] **Step 3: 테스트 실행 — fail 확인**

```bash
cd ~/Documents/Personal/ccbot-src
uv run pytest tests/ccbot/test_window_state_provider.py -v 2>&1 | tail -20
```

기대: 5개 모두 FAIL. 메시지 예: `AttributeError: 'WindowState' object has no attribute 'provider'`.

- [ ] **Step 4: `WindowState`에 provider 필드 + 직렬화 추가**

`src/ccbot/session.py` line 44-73 변경:

```python
@dataclass
class WindowState:
    """Persistent state for a tmux window.

    Attributes:
        session_id: Associated Claude session ID (empty if not yet detected)
        cwd: Working directory for direct file path construction
        window_name: Display name of the window
        provider: "claude" (default) or "codex". Determines which session
            model the window holds. codex windows do not have a UUID
            session_id and are identified by window_name only.
    """

    session_id: str = ""
    cwd: str = ""
    window_name: str = ""
    provider: str = "claude"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "session_id": self.session_id,
            "cwd": self.cwd,
        }
        if self.window_name:
            d["window_name"] = self.window_name
        if self.provider != "claude":
            d["provider"] = self.provider
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WindowState":
        return cls(
            session_id=data.get("session_id", ""),
            cwd=data.get("cwd", ""),
            window_name=data.get("window_name", ""),
            provider=data.get("provider", "claude"),
        )
```

- [ ] **Step 5: 테스트 실행 — pass 확인**

```bash
cd ~/Documents/Personal/ccbot-src
uv run pytest tests/ccbot/test_window_state_provider.py -v 2>&1 | tail -20
```

기대: 5개 모두 PASS.

- [ ] **Step 6: 전체 테스트로 회귀 확인**

```bash
cd ~/Documents/Personal/ccbot-src
uv run pytest tests/ -x 2>&1 | tail -20
```

기대: 기존 테스트 모두 PASS. WindowState를 사용하는 모든 호출지에서 provider 누락이 default("claude")로 채워져 무동작.

- [ ] **Step 7: commit**

```bash
cd ~/Documents/Personal/ccbot-src
git add src/ccbot/session.py tests/ccbot/test_window_state_provider.py
git commit -m "feat(session): add provider field to WindowState

claude(default)/codex 두 provider 구분을 위한 단일 필드 추가.
- 기본값 'claude'로 하위호환 (기존 state.json 그대로 로드)
- 직렬화는 'codex'일 때만 포함 (state.json 부풀리지 않음)
- session_id는 codex window에서 비어있을 수 있음 (UUID 없음)

ccbot이 codex/omx 세션도 라우팅하는 첫 단추."
```

---

## Task 2: 운영 스크립트에 `codex` window 추가 (외부 파일)

**Files:**
- Modify: `~/.local/scripts/ccbot-start-real.sh:78-84` (WINDOWS 배열), `:104-118` (send-keys 분기)

이 파일은 ccbot-src 레포 밖이지만 본 plan task로 포함. 변경 후 launchd kickstart로 검증.

- [ ] **Step 1: 백업**

```bash
cp ~/.local/scripts/ccbot-start-real.sh ~/.local/scripts/ccbot-start-real.sh.bak.$(date +%Y%m%d)
```

- [ ] **Step 2: WINDOWS 배열에 codex 추가**

`~/.local/scripts/ccbot-start-real.sh` line 78-84:

```bash
WINDOWS=(
    "main::$HOME"
    "ceo::$HOME/Documents/Insudeal/CeoReport"
    "metlife::$HOME/Documents/Insudeal/Metlife"
    "scraping::$HOME/Documents/Insudeal/Scraping"
    "smoking::$HOME/Documents/Personal/smoking-place"
    "claude::$HOME/Documents/Claude"
    "codex::$HOME/Documents/Claude"
)
```

- [ ] **Step 3: send-keys 분기 — codex window는 omx --direct로 부팅**

기존 line 104-118 (`# 각 창에서 claude --resume 자동 시작` 블록):

```bash
echo "$(date): claude --resume 시작"
for entry in "${WINDOWS[@]}"; do
    name="${entry%%::*}"
    [ "$name" = "main" ] && continue
    "$TMUX_BIN" send-keys -t "ccbot:$name" 'claude --resume' Enter
done
```

다음으로 변경 (codex window는 다른 명령 송신):

```bash
echo "$(date): per-window startup commands 시작"
for entry in "${WINDOWS[@]}"; do
    name="${entry%%::*}"
    case "$name" in
        main)
            # 일반 셸 유지 — 명령 송신 안 함
            ;;
        codex)
            # OMX_LAUNCH_POLICY=direct: ccbot 단일 tmux session 안에서
            # omx가 자체 tmux/HUD를 만들지 않고 현재 창에 codex를 직접 부팅.
            # detached-tmux(default)는 ccbot 모델과 충돌하므로 금지.
            "$TMUX_BIN" send-keys -t "ccbot:$name" \
                'OMX_LAUNCH_POLICY=direct omx' Enter
            ;;
        *)
            "$TMUX_BIN" send-keys -t "ccbot:$name" 'claude --resume' Enter
            ;;
    esac
done
```

- [ ] **Step 4: bash -n 문법 검증**

```bash
bash -n ~/.local/scripts/ccbot-start-real.sh && echo "syntax OK"
```

기대: `syntax OK`.

- [ ] **Step 5: 기존 ccbot 종료 후 launchd 재시작 — 7창으로 재기동**

```bash
launchctl kickstart -k gui/$UID/com.pakjungeol.ccbot-start
sleep 5
tmux list-windows -t ccbot
```

기대: 출력에 `codex` window가 포함된 7개 창. 부팅 명령이 정상 송신됐는지 확인.

```bash
tmux capture-pane -t ccbot:codex -p | tail -10
```

기대: codex/omx 부팅 메시지(`OpenAI Codex v...`).

- [ ] **Step 6: 안정 운영 확인 (5분)**

`~/Documents/Claude/logs/ccbot-autostart.log` 마지막 100줄 확인. supervisor가 5분 안에 비정상 종료 카운터를 올리지 않으면 OK.

```bash
sleep 300
tail -30 ~/Documents/Claude/logs/ccbot-autostart.log
cat ~/.ccbot/.fail-count 2>/dev/null || echo "(no count)"
```

기대: 카운터 0 또는 reset 로그.

- [ ] **Step 7: 외부 스크립트 변경은 dotfiles 레포에 별도 커밋 (해당 시) — 본 plan에선 ccbot-src commit 없음.**

`~/.local/scripts/`가 다른 dotfiles 레포에 있으면 거기서 커밋. 없으면 변경된 스크립트 위치만 plan에 기록 (이 단계는 정보 단계, 실행 명령 없음).

---

## Task 3: omx hook plugin — Stop hook → capture-pane → ccbot send

**Files:**
- Create: `~/Documents/Claude/.omx/hooks/ccbot-bridge.mjs`

omx의 hook plugin 시스템(`omx hooks init/status/validate`)을 이용. plugin은 자동 로드되며 `OMX_HOOK_PLUGINS=0`로 비활성 가능.

- [ ] **Step 1: 디렉토리 확인**

```bash
ls -la ~/Documents/Claude/.omx/hooks/ 2>&1
omx hooks status 2>&1 | head -20
```

기대: 디렉토리 존재 및 `Discovered plugins: 0`.

- [ ] **Step 2: plugin 파일 작성**

> SDK 형태 (실측 확인): `omx hooks init` scaffold + `dist/hooks/extensibility/types.d.ts:HookEventName` 기준.
> - **export**: `export async function onHookEvent(event, sdk)` (default export 아님)
> - **이벤트 분기**: `event.event === 'turn-complete'` 등. 사용 가능 이벤트:
>   `'session-start' | 'stop' | 'session-end' | 'session-idle' | 'turn-complete' | 'blocked' | 'finished' | 'failed' | 'pre-tool-use' | 'post-tool-use' ...`
> - **SDK**: `sdk.log.info(msg, payload?)`, `sdk.state.read(key)/write(key, value)` 가용.
> - **TMUX_PANE**: plugin runner는 자식 프로세스라 부모 omx의 환경변수가 그대로 전달됨 → `process.env.TMUX_PANE` 사용 가능.

```javascript
// ~/Documents/Claude/.omx/hooks/ccbot-bridge.mjs
//
// omx hook plugin: turn-complete → tmux capture-pane → 마지막 turn 추출 → ccbot send
// codex window의 한 턴이 끝나면 그 turn의 응답만 Telegram 토픽으로 push.
//
// 전제: ccbot tmux session 안에서 OMX_LAUNCH_POLICY=direct로 부팅된 codex.
// 비활성: OMX_HOOK_PLUGINS=0 또는 파일 삭제.

import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";

const ANSI_RE = /\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07/g;
const MAX_TG = 3500;       // Telegram 4096 - 마진
const TAIL_LINES = 200;    // 넓게 캡처. anchor로 자름
const MIN_CONTENT_LINES = 2;

// codex TUI 마커:
// - 사용자 prompt 시작: `›`
// - status bar (매 capture마다 바뀜): `gpt-{ver} {effort} · ...`
const PROMPT_RE = /^\s*›\s/;
const STATUS_BAR_RE = /^\s*gpt-[\d.]+(?:\s+\w+)?\s+·/;

function stripAnsi(s) { return s.replace(ANSI_RE, ""); }

function fingerprint(s) {
  return createHash("sha256").update(s).digest("hex").slice(0, 16);
}

function capturePaneTail() {
  const pane = process.env.TMUX_PANE;
  if (!pane) return "";
  try {
    const raw = execFileSync(
      "tmux",
      ["capture-pane", "-t", pane, "-p", "-S", `-${TAIL_LINES}`],
      { encoding: "utf8" },
    );
    return stripAnsi(raw).trimEnd();
  } catch { return ""; }
}

function tmuxWindowName() {
  const pane = process.env.TMUX_PANE;
  if (!pane) return null;
  try {
    return execFileSync(
      "tmux",
      ["display-message", "-p", "-t", pane, "#{window_name}"],
      { encoding: "utf8" },
    ).trim();
  } catch { return null; }
}

// codex 화면에서 "마지막 사용자 prompt + 그에 대한 응답"만 슬라이스.
// 못 찾으면 마지막 30라인 fallback. status bar 라인은 잘라낸다.
export function extractLastTurn(tail) {
  const lines = tail.split("\n");

  let promptIdx = -1;
  for (let i = lines.length - 1; i >= 0; i--) {
    if (PROMPT_RE.test(lines[i])) { promptIdx = i; break; }
  }
  if (promptIdx === -1) return lines.slice(-30).join("\n").trimEnd();

  let endIdx = lines.length;
  for (let i = lines.length - 1; i > promptIdx; i--) {
    if (STATUS_BAR_RE.test(lines[i])) { endIdx = i; break; }
  }
  return lines.slice(promptIdx, endIdx).join("\n").trimEnd();
}

function ccbotSend(windowName, message) {
  if (!windowName || !message) return;
  const text = message.length > MAX_TG ? message.slice(-MAX_TG) : message;
  try {
    execFileSync("ccbot", ["send", "--window", windowName, text], {
      stdio: "ignore",
      timeout: 10_000,
    });
  } catch { /* best-effort */ }
}

export async function onHookEvent(event, sdk) {
  if (event.event !== "turn-complete") return;

  const windowName = tmuxWindowName();
  if (!windowName) { await sdk.log.info?.("ccbot-bridge: no TMUX_PANE, skip"); return; }

  const tail = capturePaneTail();
  if (!tail) return;

  const turn = extractLastTurn(tail);
  if (!turn) return;
  if (turn.split("\n").filter((l) => l.trim()).length < MIN_CONTENT_LINES) return;

  const fp = fingerprint(turn);
  const stateKey = `last-fp:${windowName}`;
  const lastFp = await sdk.state.read(stateKey);
  if (fp === lastFp) {
    await sdk.log.info?.("ccbot-bridge: duplicate turn, skip", { window: windowName });
    return;
  }

  ccbotSend(windowName, `📟 [${windowName}]\n\`\`\`\n${turn}\n\`\`\``);
  await sdk.state.write(stateKey, fp);

  await sdk.log.info?.("ccbot-bridge: pushed last turn", {
    window: windowName, lines: turn.split("\n").length, fp,
  });
}
```

- [ ] **Step 3: omx hook validate**

```bash
omx hooks validate 2>&1 | tail -20
```

기대: `ccbot-bridge` plugin이 export 검증을 통과.

- [ ] **Step 4: synthetic 이벤트 테스트**

```bash
omx hooks test 2>&1 | tail -20
```

기대: turn-complete 이벤트가 plugin에 dispatch되며 에러 없음. (실제로 ccbot send 호출이 일어나도 무시 가능 — 토픽 매핑이 없으면 send.py가 grace fail.)

- [ ] **Step 5: e2e smoke test (수동)**

1. cmux 또는 직접 tmux로 `cctmux codex` 진입.
2. omx 부팅 확인 후 텔레그램 codex 토픽에서 첫 메시지로 `ls` 같은 짧은 명령 입력.
3. 첫 입력으로 thread_bindings 자동 매핑 + send-keys로 codex에 전달됨.
4. codex 응답이 끝난 후 Stop hook이 발화 → 토픽에 마지막 capture-pane이 push 되는지 확인.

기대: 토픽에 `📟 [codex] ...` 메시지가 도착.

- [ ] **Step 6: state.json에 codex window의 provider 필드가 박히는지 확인**

```bash
python3 -c "
import json
s = json.load(open('/Users/pakjungeol/.ccbot/state.json'))
for wid, ws in s.get('window_states', {}).items():
    if ws.get('window_name') == 'codex':
        print(wid, ws)
"
```

자동으로 박히지는 않는다 (Task 1은 모델 추가, 자동 분류 로직은 없음). 필요하면 수동 패치:

```bash
python3 - <<'PY'
import json, pathlib
p = pathlib.Path.home() / ".ccbot/state.json"
s = json.loads(p.read_text())
for wid, ws in s.get("window_states", {}).items():
    if ws.get("window_name") == "codex":
        ws["provider"] = "codex"
p.write_text(json.dumps(s, indent=2, ensure_ascii=False))
print("patched")
PY
```

- [ ] **Step 7 (옵션): hook plugin 변경분 commit**

`~/Documents/Claude/.omx/` 가 git 추적 대상이면 commit. 아니면 plan에 위치만 명시. 본 plan의 ccbot-src 커밋과는 무관.

---

## Task 4: 통합 검증 + plan 종료 commit

- [ ] **Step 1: 폐루프 검증 시나리오**

| 단계 | 입력/조건 | 기대 결과 |
|---|---|---|
| 1 | `cctmux codex` 진입 | omx가 direct 모드로 부팅, 7번째 창 활성 |
| 2 | 토픽에 `현재 시각 알려줘` 입력 | send-keys로 codex에 전달, codex 응답 생성 |
| 3 | codex 응답 종료 | Stop hook → 토픽에 `📟 [codex] ...` push |
| 4 | 다시 토픽에 `pwd` 입력 | 동일 흐름 — 양방향 폐루프 안정 |
| 5 | claude 토픽 (`claude`/`ceo` 등)에서 평소대로 메시지 | 기존 동작 회귀 없음 |

- [ ] **Step 2: 회귀 없음 확인 — pytest 전체 한 번 더**

```bash
cd ~/Documents/Personal/ccbot-src
uv run pytest tests/ 2>&1 | tail -10
```

기대: PASS.

- [ ] **Step 3: ccbot supervisor 카운터가 0인지 마지막 확인**

```bash
cat ~/.ccbot/.fail-count 2>/dev/null || echo 0
tail -20 ~/Documents/Claude/logs/ccbot-autostart.log
```

기대: count 0, 5분+ 운영 로그.

- [ ] **Step 4: README 또는 CHANGELOG 업데이트는 yagni — 생략**

본 plan은 surgical change. 사용자 컨벤션상 README 강제 업데이트 없음.

- [ ] **Step 5: feature 브랜치 push (공유 브랜치 직접 push 금지)**

```bash
cd ~/Documents/Personal/ccbot-src
git branch -vv  # upstream이 main/dev/prod이면 중단
git push origin HEAD:ccbot-codex-connect-by-cluade
```

- [ ] **Step 6: MR 작성 (squash merge)**

GitHub UI 또는 `gh pr create`로 PR 생성. 제목: `feat(session): codex/omx provider 연동 (window provider B-lite)`. 본문: 변경 요약, 운영 정책(`OMX_LAUNCH_POLICY=direct`), 외부 파일 변경 위치(`~/.local/scripts/ccbot-start-real.sh`, `~/Documents/Claude/.omx/hooks/`), 검증 시나리오. 머지는 squash.

---

## Backlog (M2 — 1주 사용 후 결정)

본 plan에서 **의도적으로 제외**한 항목. 실사용 후 fundamentally needed인지 판단:

1. **Codex rollout JSONL parser** (`~/.codex/sessions/**/rollout-*.jsonl` 모니터링)
   - capture-pane이 긴 코드블록·다단 출력을 자르는 경우에만 필요.
   - 추가하면 `session_monitor.py`에 provider 분기.
2. **terminal_parser.py codex 호환**
   - 현재 STATUS_SPINNERS / UI_PATTERNS는 Claude Code 전용 문자.
   - codex window에서 false-trigger가 보이면 provider == codex일 때 우회.
3. **codex hook 자동 등록 (provider 자동 감지)**
   - 현재는 codex window의 `provider="codex"` 박는 게 수동 패치.
   - omx hook의 SessionStart에서 `state.json`에 자동 시드하는 옵션.
4. **양방향 응답에서 turn 단위 dedup**
   - capture-pane 기반은 동일 turn 중복 push 가능성. 1주 사용 후 빈도 보고 결정.
5. **provider 추상화 ABC (`SessionProvider`)**
   - M2의 1~3 중 둘 이상이 필요해지는 시점에 도입. 그 전까진 `if provider == "codex"` 분기 1~2개로 충분 (YAGNI).

---

## Self-Review

- [x] **Spec coverage**: B-lite 핵심 4요소 모두 task로 매핑 — provider 필드(T1), 운영 정책 direct(T2), 응답 라우팅 hook(T3), e2e 검증(T4).
- [x] **Placeholder 없음**: 모든 step에 실제 코드/명령어/기대 출력 포함.
- [x] **Type 일관성**: `provider` 필드 타입(str), default(`"claude"`), 직렬화 조건(`!= "claude"` 시만 포함)을 모든 task에서 동일하게 사용.
- [x] **외부 파일 명시**: `~/.local/scripts/ccbot-start-real.sh`, `~/Documents/Claude/.omx/hooks/ccbot-bridge.mjs`는 이 레포 밖이라는 점, 별도 커밋 정책을 plan에 명시.
- [x] **사용자 컨벤션 준수**: 파일명 `2026-05-07-한글주제.md`, 코드 폴더 plans/ 저장, 한글 주제 + 영문 OK 단어 혼용, 하이픈만 구분자.
- [x] **karpathy 4원칙**:
  - Think Before Coding: B-lite vs B-full vs A 토론 후 가정 표면화 (단일 tmux 모델, hook 중심).
  - Simplicity First: ABC 추상화 회피, 외부 hook으로 본체 결합도 0.
  - Surgical Changes: ccbot 본체 수정은 dataclass 1필드.
  - Goal-Driven Execution: 검증 시나리오(T4 Step 1)로 성공 기준 사전 정의.

---

## Related

- 메모리: `reference_ccbot_infra.md` (ccbot 인프라, supervisor, tmux 그룹 모델)
- 외부 스크립트: `~/.local/scripts/ccbot-start-real.sh`
- omx hook plugin 디렉토리: `~/Documents/Claude/.omx/hooks/`
- codex 평행 작업 브랜치: ccbot-src의 다른 브랜치(사용자가 codex CLI에 동시 지시)와는 변경 영역이 겹칠 수 있으므로 머지 시 충돌 검토 필요.
