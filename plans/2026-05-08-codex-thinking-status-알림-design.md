# codex thinking status 텔레그램 in-place 알림 — Design Spec

**Goal:** codex window 한 turn (사용자 입력 → 응답 완료) 동안 텔레그램 토픽에 "생각중" status 메시지를 in-place edit으로 표시. claude window의 spinner UX (`✻ Sautéed for 5s · 1 shell still running`)와 동일한 사용감을 codex에도 제공. 도구 사용(Read X, Ran ...) trace까지 단계별로 노출.

**Context:** 본 spec은 `2026-05-07-codex-omx-ccbot-연동.md` plan(양방향 폐루프 = M1)의 후속 M2 단계. M1에서 의도적으로 backlog로 둔 "thinking 알림 push" 항목을 deep dive.

**Non-goals (M3+):**
- codex 외 다른 provider(gemini/qwen) 일반화 — `SessionProvider` ABC 도입은 셋 이상 provider 필요해질 때.
- 실시간 streaming partial response — omx native streaming 활용 시 별도 plan.
- claude의 status_msg와 codex status_msg 동작 차이 — 본 spec은 "claude UX와 동일"이 목표.

---

## Architecture (B-lite + status_polling 확장)

기존 인프라를 최대한 재사용하는 surgical 변경:

| 기존 인프라 | 재사용 방식 |
|---|---|
| `status_poll_loop` (1초 polling) | thread-bound window 전체를 이미 iterate 중 → codex window도 자동 포함 |
| `update_status_message` | provider 분기 추가만 — codex면 다른 parse 함수 호출 |
| `enqueue_status_update` / `_do_clear_status_message` | 그대로 사용 (in-place edit, 정리 메커니즘 동일) |
| `status_msg_ids` 영속화 | 그대로 사용 (재시작 시 orphan 정리 자동) |
| `tmux_manager.capture_pane` | 그대로 사용 (codex window도 동일 capture 가능) |
| `parse_status_line` (claude 전용) | 그대로 두고 옆에 `parse_codex_status_line` 신규 |

**핵심 결정**: `terminal_parser.py`에 codex 전용 함수를 신규로 추가 (claude의 `parse_status_line`은 무수정). `status_polling.py`에서 `WindowState.provider == "codex"` 또는 `display_name == "codex"`로 분기. 이렇게 하면 claude 흐름은 100% 보존.

---

## parse_codex_status_line 동작

신규 함수 시그니처:

```python
def parse_codex_status_line(pane_text: str) -> str | None:
    """codex window의 capture-pane에서 thinking status 한 줄 추출.

    Returns:
        - "⏳ Working 3s" 같은 string: 텔레그램 status 메시지로 표시
        - None: 패턴 못 찾음 (idle 상태) — status 표시 안 함, 기존 status 메시지 있으면 cleanup 트리거
    """
```

알고리즘 (claude의 `parse_status_line`과 대칭 구조):

```
1) capture-pane을 라인 단위로 split, 마지막에서 역순 스캔.
2) status bar 라인 (`gpt-X.Y high · ...`) 직전까지가 검사 영역.
3) 검사 영역에서 다음 우선순위로 status 추출:
   a) thinking spinner 패턴 (예: `⏳ Working 5s` / `▶ Generating ...`)
      → "⏳ <text>" 반환. 해당 패턴 상수는 plan 단계에서 실측 후 STATUS_SPINNERS_CODEX에 박는다.
   b) 가장 최근 `• Ran X` / `• Read Y` / `• Explored` 도구 사용 라인
      → "🔧 <라인 1줄>" 반환 (한 줄 trim, 100자 제한)
   c) 둘 다 없으면 None (turn 끝났거나 prompt placeholder만 있는 idle 상태)
```

상수:
- `STATUS_SPINNERS_CODEX: frozenset[str]` — codex spinner character 집합 (실측). 빈 세트로 시작해도 (b) trace 표시는 동작.
- `CODEX_TOOL_RE: re.Pattern` — `^\s*•\s+(Ran|Read|Edit|Wrote|Explored|...)` 매칭 (실측 후 정리).

---

## Data Flow

```
사용자 → 텔레그램 codex 토픽 입력
   ↓
ccbot text_handler → session.send_to_window (paste 경로 — 이미 동작 중)
   ↓
codex가 응답 생성 시작 (응답 들어가는 사이 capture-pane 변화)

[병행] status_poll_loop 1초 tick
   ├─ thread_bindings의 codex window 발견
   ├─ capture_pane(window)
   ├─ provider 분기: codex
   ├─ parse_codex_status_line(pane_text)
   │
   ├─ → "⏳ Working 2s"  (1번째 tick)  → enqueue_status_update → 토픽에 status 메시지 생성
   ├─ → "🔧 Read SKILL.md" (3번째 tick) → enqueue_status_update → in-place edit (같은 메시지 갱신)
   ├─ → "🔧 Ran sleep 10"  (5번째 tick) → enqueue_status_update → in-place edit
   ├─ → ...                                                       (status 메시지 1개가 매 tick 내용 업데이트)
   │
turn-complete (omx hook firing)
   └─ ccbot-bridge.mjs → ccbot send (응답 push, M1 그대로)

[병행] status_poll_loop 그 다음 tick
   ├─ capture_pane (이제 turn 끝나서 codex idle)
   ├─ parse_codex_status_line → None
   └─ _do_clear_status_message → status 메시지 삭제

결과: 토픽에 status 메시지 1개가 turn 동안 in-place로 진행 표시 → turn 끝나면 사라지고 응답 메시지가 별도 push됨.
```

claude 흐름과 정확히 동일 — 차이는 parse 함수 한 개뿐.

---

## File Structure

| 파일 | 변경 | 책임 |
|---|---|---|
| `src/ccbot/terminal_parser.py` | 추가 ~30~50라인 | `parse_codex_status_line()` 함수 + 패턴 상수. 기존 `parse_status_line` 무수정 |
| `src/ccbot/handlers/status_polling.py` | 추가 ~5~10라인 | `update_status_message`에서 provider/display_name 분기 |
| `tests/ccbot/test_terminal_parser.py` | 추가 ~30라인 | 단위 테스트 — 실측 capture fixture로 thinking/trace/idle 시나리오 |
| `~/Documents/Claude/.omx/hooks/ccbot-bridge.mjs` | 무변경 | 기존 turn-complete push 흐름 그대로 유지 |
| `plans/2026-05-08-codex-thinking-status-알림-design.md` | 신규 (이 문서) | spec |

분리 근거: claude의 `parse_status_line`을 건드리면 회귀 위험. 별도 함수로 두면 claude 사용자 환경 100% 보존 + codex 분기는 명확한 if 한 줄.

---

## Error Handling

| 시나리오 | 처리 |
|---|---|
| `capture_pane`이 None/빈 문자열 | claude와 동일 — silent skip (`update_status_message` 기존 분기) |
| `parse_codex_status_line` → None | status 표시 안 함. 기존 status 메시지 있으면 `_do_clear_status_message`로 정리 (즉 idle 진입 시 자동 cleanup) |
| codex가 응답 중 에러로 죽음 | turn-complete hook 안 옴 → status 메시지 cleanup만 polling으로 처리 (timeout 같은 추가 로직 없음 — claude도 동일) |
| 사용자가 codex window를 직접 detach 후 재attach | tmux pane id 변경 가능. 기존 ccbot stale pane 처리 로직(`Removing stale window_state`) 그대로 동작 |
| `STATUS_SPINNERS_CODEX` 패턴 미스매치 | 도구 trace로 fallback. 둘 다 미스매치면 None — 일시적 빈 status는 허용 (다음 tick에 catch up) |

---

## Testing

**단위 테스트** (`tests/ccbot/test_terminal_parser.py`):

1. `test_parse_codex_status_thinking` — capture fixture에 spinner 텍스트 있을 때 "⏳ Working Xs" 반환.
2. `test_parse_codex_status_tool_use` — 마지막 `•` 라인이 도구 사용일 때 "🔧 Read X" 반환.
3. `test_parse_codex_status_idle` — status bar만 있고 thinking/trace 없을 때 None 반환.
4. `test_parse_codex_status_status_bar_filtered` — status bar 라인이 결과에 포함되지 않음 (filter 검증).
5. `test_parse_status_line_unchanged_for_claude` — 기존 claude `parse_status_line` 호출 그대로 → 회귀 없음.

**통합 검증** (수동 — plan 단계):
- 사용자가 codex 토픽에 다양한 메시지 입력 (즉답형, 도구 사용, 긴 thinking 등)
- 텔레그램에서 status 메시지가 in-place edit되며 trace가 갱신되는지 육안 확인
- turn 완료 후 status 메시지가 사라지고 응답이 별도로 도착하는지 확인

---

## Open question (plan 단계 실측)

**codex thinking 패턴이 정확히 무엇인지** — 본 spec은 "spinner 또는 도구 trace를 status에 표시"까지만 정한다. 실제 spinner character / 텍스트 형식은 plan T1 첫 step에서 실측:

```
사용자가 codex 토픽에 "10초 기다린 후 안녕이라고 답해줘" 같은 메시지 입력
  → 그 동안 1초 간격으로 capture-pane 시계열 캡처
  → 어떤 라인에 어떤 문자가 보이는지 fixture로 추출
  → STATUS_SPINNERS_CODEX, CODEX_TOOL_RE 채우기
```

이 실측이 spec의 "구현 가능성"을 깨면 (= codex가 thinking 표시를 화면에 안 그림) → fallback design을 쓴다:
- omx hook의 `pre-tool-use` / `post-tool-use` 이벤트로 ccbot에 status_update 신호를 직접 보냄 (`ccbot status-update --window codex --text "..."` CLI 신규)
- 즉 capture-pane 의존을 omx hook 의존으로 교체

이 fallback은 폼 좀 큰 작업이므로 plan 단계 실측 결과 본 후 결정.

---

## Self-Review

- **Placeholder:** "TBD"/"TODO" 없음. 단 "Open question — 실측" 섹션은 의도적인 plan 단계 작업으로 명시.
- **Internal consistency:** Architecture 표 / Data Flow 다이어그램 / File Structure가 모두 같은 결정을 표현 (provider 분기는 status_polling.py, parse는 terminal_parser.py).
- **Scope:** 단일 plan 분량 (parse 함수 + 분기 + 테스트). codex 외 provider, streaming, 별도 hook fallback은 모두 Out-of-scope 또는 conditional fallback.
- **Ambiguity:** `parse_codex_status_line` 우선순위 (a > b > c) 명시. STATUS_SPINNERS_CODEX 빈 세트로 시작해도 (b) trace path만으로 동작 보장 — 점진적 정확도 향상 path 명확.

---

## Related

- M1 plan: `plans/2026-05-07-codex-omx-ccbot-연동.md` (양방향 폐루프, PR #3에서 머지 대기)
- claude 참조 코드:
  - `src/ccbot/handlers/status_polling.py:46-100` (`update_status_message` 흐름)
  - `src/ccbot/terminal_parser.py:199` (`STATUS_SPINNERS`, `parse_status_line`)
  - `src/ccbot/handlers/message_queue.py` (`_do_clear_status_message`, `enqueue_status_update`)
- 메모리: `reference_ccbot_infra.md` (ccbot 인프라 / status_msg_ids 영속화)
