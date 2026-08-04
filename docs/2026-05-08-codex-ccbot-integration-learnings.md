# ccbot Codex 연동 학습 정리

작성일: 2026-05-08
대상 repo: `/Users/pakjungeol/Documents/Personal/ccbot-src`
관련 브랜치: `codex-connext-by-code`, `codex-connext-by-code-v2`, `codex-connext-by-code-v3`, `codex-connext-by-code-v4`, `ccbot-codex-connect-by-cluade`

## 결론

최종 추천 기준은 `codex-connext-by-code-v4`다.

`v4`는 `v3`와 tree가 동일하고, `v3`의 atomic commit 3개를 최종 제출용 1 commit으로 squash한 형태다. 코드/문서/테스트/fixture 결과물은 `v3`와 동일하며, 검증 결과도 통과했다.

검증 evidence:

```text
v4 commit: 2d00b45 Codex topic routing absorbs claude branch validation assets
v3 tree == v4 tree
ruff check: pass
ruff format --check: pass
pyright src/ccbot/: 0 errors
pytest: 278 passed
```

## 브랜치별 판단

### `codex-connext-by-code`

Codex 연동의 기본 구조를 만든 기준 브랜치다.

주요 내용:

- tmux window provider 감지 (`claude` / `codex`)
- Codex window state 보존
- Telegram → Codex tmux window 입력 라우팅
- Codex status polling / terminal parser 기반 상태 표시
- `ccbot send --window codex` CLI 기반 Telegram push 경로

한계:

- Claude branch가 발견한 stale `window_display_names` fallback 문제 미반영
- 기본 `provider: "claude"` 직렬화로 기존 `state.json` row가 불필요하게 바뀔 수 있음
- plan/fixture/edge test가 상대적으로 부족

### `codex-connext-by-code-v2`

Codex 기준 브랜치에 Claude branch의 핵심 runtime bugfix 하나를 섞은 최소 보강판이다.

주요 개선:

- `ccbot send --window codex` fallback에서 stale window id 제외
- `window_display_names`만 믿지 않고 `thread_bindings`에 실제 묶인 window id만 후보로 사용

장점:

- diff가 작고 hotfix로 안전함
- stale `@old -> codex` 때문에 Telegram push가 실패하는 문제를 해결

한계:

- 테스트가 3개 수준으로 얇음
- `provider: "claude"` 기본값 직렬화 문제 남음
- plan/fixture가 부족
- 하나의 commit으로 뭉쳐 리뷰 단위가 큼

### `codex-connext-by-code-v3`

Claude가 Codex 결과를 다시 검토하며 만든 완성형 보강판이다.

주요 개선:

- `v2`의 stale fallback guard 포함
- `WindowState.to_dict()`에서 기본 `provider == "claude"`는 직렬화 생략
- `WindowState.from_dict()`는 provider 누락 시 `claude`로 복원
- `test_send.py` edge case 확장
- `test_session.py` backward compatibility 테스트 추가
- M1/M2 plan 문서와 Codex TUI live fixture 추가
- 기능/docs/tests를 atomic commit으로 분리

검증:

```text
ruff check: pass
ruff format --check: pass
pyright: 0 errors
pytest: 278 passed
```

장점:

- 유지보수 context가 가장 좋음
- 테스트 커버리지가 가장 촘촘함
- state backward compatibility를 더 잘 지킴
- commit 단위 리뷰가 쉬움

단점:

- fixture가 크고, 현재 테스트에서 직접 import되지는 않음
- 문서/fixture까지 포함되어 diff가 큼

### `codex-connext-by-code-v4`

`v3`와 결과 tree가 동일한 최종 제출용 squash 브랜치다.

장점:

- 결과물은 `v3`와 동일
- 최종 MR/merge용으로 하나의 commit이라 깔끔함
- commit body가 상세한 decision record 역할을 함

단점:

- `v3`의 atomic commit 구조가 사라져 commit 단위 리뷰는 불리함

최종 판단:

- 리뷰를 세밀하게 하려면 `v3`
- 최종 제출/MR 대상으로는 `v4`

## 핵심 runtime 학습

### 1. ccbot 내부 라우팅 키는 window name이 아니라 window id

프로젝트 기본 원칙:

```text
1 Topic = 1 Window = 1 Session
internal routing key = tmux window id (@0, @6, ...)
window name = display only
```

Codex topic도 `codex`라는 이름 자체가 canonical key가 아니다. 실제 라우팅은 `thread_bindings[user_id][thread_id] -> window_id`를 따라야 한다.

### 2. `window_display_names` fallback은 stale window id를 조심해야 함

실측에서 `window_display_names`에 오래된 `@27 -> codex`와 현재 `@6 -> codex`가 같이 남을 수 있었다.

나쁜 방식:

```python
for wid, display_name in window_display_names.items():
    if display_name == window_name:
        window_id = wid
        break
```

문제:

- dict 순서상 stale `@27`이 먼저 잡히면 `thread_bindings`에서 못 찾음
- 결과적으로 `ccbot send --window codex`가 silent fail에 가까운 상태가 됨

좋은 방식:

```python
bound_window_ids = set()
for bindings in state.get("thread_bindings", {}).values():
    bound_window_ids.update(bindings.values())

for wid, display_name in state.get("window_display_names", {}).items():
    if display_name == window_name and wid in bound_window_ids:
        window_id = wid
        break
```

결론:

- display name fallback은 반드시 `thread_bindings`와 교차 검증해야 한다.

### 3. `provider`는 `codex`일 때만 저장하는 것이 좋음

`WindowState.provider` 기본값은 `claude`다.

`to_dict()`가 항상 `provider`를 저장하면 기존 `state.json`의 모든 Claude row에 `"provider": "claude"`가 새로 주입된다.

문제:

- 불필요한 storage churn
- 기존 grep/jq 기반 운영 스크립트와 diff noise 가능성
- backward compatibility 측면에서 불리

좋은 정책:

- `provider == "claude"`: 직렬화 생략
- `provider == "codex"`: 명시 저장
- `from_dict()`는 provider 누락 시 `claude`로 복원

### 4. Codex 응답 push는 ccbot 본체만으로 끝나지 않음

Codex는 Claude처럼 JSONL final response를 ccbot이 직접 읽는 구조가 아니다.

현재 실운영 구조:

```text
Telegram topic → ccbot → tmux codex window
Codex turn complete → OMX hook bridge → ccbot send --window codex → Telegram topic
```

외부 hook 파일:

```text
/Users/pakjungeol/Documents/Claude/.omx/hooks/ccbot-bridge.mjs
```

중요:

- 이 파일은 ccbot repo 외부 파일이다.
- 브랜치 diff에 포함되지 않는다.
- Codex 최종 답변 Telegram push UX 문제는 이 hook도 같이 확인해야 한다.

### 5. Codex TUI footer는 Telegram용으로 정리 필요

Codex pane에는 다음 같은 footer가 나온다.

```text
─ Worked for 1m 33s ─────────────────────────
```

Telegram에 그대로 보내면 촌스럽고 의미 전달이 약하다.

현재 hook에서 정리한 표현:

```text
⏱️ 총 소요 시간: 1분 33초
```

관련 파일:

```text
/Users/pakjungeol/Documents/Claude/.omx/hooks/ccbot-bridge.mjs
```

### 6. Codex 권한 프롬프트는 interactive UI로 봐야 함

Codex CLI는 command 실행 전 다음 UI를 띄운다.

```text
Would you like to run the following command?
› 1. Yes, proceed
```

이걸 일반 pane snapshot으로 Telegram에 보내면 UX가 깨진다.

필요한 방향:

- 권한 프롬프트는 interactive UI로 감지
- Telegram에는 snapshot 전체가 아니라 버튼/상태 중심으로 표시
- Codex approval 정책이 `never`이면 빈번한 confirm은 줄어듦

## 테스트/문서화 교훈

### Plan 문서는 task list가 아니라 context document여야 함

빈약한 plan은 후속 개발자가 왜 그런 결정을 했는지 알 수 없다.

필수 포함 항목:

- goal
- current-state evidence
- constraints
- impacted files/modules
- design decisions
- rejected alternatives
- test/verification plan
- risks
- rollback/follow-up notes
- 실측 로그/fixture reference

이 내용은 전역 지침에도 반영했다.

```text
/Users/pakjungeol/.codex/AGENTS.md
```

### Commit은 atomic하게 나누는 것이 좋음

`v3`는 다음처럼 나뉘어 리뷰가 쉬웠다.

```text
docs/fixture import
WindowState provider backward compatibility
send fallback stale window guard
```

`v4`는 최종 제출용 squash로는 좋지만, 리뷰 과정에서는 `v3`처럼 나뉜 commit이 더 좋다.

전역 지침에 반영한 원칙:

- behavior/test/docs/runtime config 변경은 가능하면 분리
- 큰 작업을 하나의 commit에 숨기지 않기
- squash가 필요하면 commit body에 판단 근거와 검증 evidence 남기기

## 운영 체크리스트

ccbot Codex 연동을 다시 점검할 때는 다음 순서가 안전하다.

```bash
# 현재 branch/commit 확인
git branch --show-current
git log -1 --oneline

# 품질 검증
uv run --extra dev ruff check src/ tests/
uv run --extra dev ruff format --check src/ tests/
uv run --extra dev pyright src/ccbot/
uv run --extra dev pytest

# editable install source 확인
cat ~/.local/share/uv/tools/ccbot/lib/python*/site-packages/ccbot-0.1.0.dist-info/direct_url.json

# launchd restart
launchctl kickstart -k gui/$(id -u)/com.pakjungeol.ccbot-start

# process 확인
ps -axo pid,ppid,lstart,command | grep -E '/\.local/bin/ccbot start|ccbot-start-real|uv run ccbot|ccbot-src/.venv/bin/ccbot' | grep -v grep

# codex routing smoke
~/.local/bin/ccbot send --window codex '[ccbot smoke] codex routing test'
```

주의:

- Telegram Bot token은 절대 로그/문서/commit에 남기지 않는다.
- `httpx` DEBUG 로그는 Bot API URL에 token이 노출될 수 있으므로 `WARNING` 이상으로 제한한다.
- 실제 Telegram 수신 테스트 시 `Conflict: terminated by other getUpdates request`가 뜨면 중복 bot process를 먼저 확인한다.

## 남은 개선 후보

1. `tests/ccbot/fixtures/codex_thinking_trace.txt`를 실제 테스트에서 일부라도 읽게 연결하기
2. repo 외부 hook인 `ccbot-bridge.mjs`를 별도 versioned 위치로 관리할지 결정하기
3. Codex final response를 tmux capture 대신 더 안정적인 event/JSONL 소스로 받을 수 있는지 조사하기
4. `ccbot send --window` fallback에서 동일 display name이 여러 active topic에 묶이는 경우 정책 명확화하기
