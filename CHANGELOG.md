# Changelog

이 fork(`TejNote/ccbot`)가 upstream(`six-ddc/ccbot`) 대비 어떻게 달라졌는지 추적합니다.

포맷은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/), 버전 정책은 [SemVer](https://semver.org/lang/ko/)를 따릅니다.

- **MAJOR** (v2.0.0): 기존 사용자가 영향을 받는 호환성 깨는 변경 (state.json 스키마, `.env` 키 이름, CLI 인자 등)
- **MINOR** (v1.x.0): 기능 추가 — 새 hook, 새 명령어, 새 provider 지원 등
- **PATCH** (v1.0.x): 버그 픽스, 안정성 개선, 문서 보정

## [Unreleased]

(다음 릴리스 준비 중인 변경은 여기에 누적)

---

## [1.0.3] - 2026-06-01

재부팅 후 텔레그램 토픽이 엉뚱한 창으로 라우팅되던 버그 수정.

### Fixed

- **window_id 재사용으로 인한 토픽 오라우팅** (`session.py` `resolve_stale_ids`)
  - tmux는 서버 재시작마다 window ID를 `@0`부터 다시 매겨서, 재부팅 전후로 `@6` 같은 ID가 **그대로 존재하지만 다른 창을 가리킬** 수 있음 (예: 창 추가로 ID가 한 칸씩 밀림 → 과거 codex(`@6`)에 바인딩된 토픽이 재부팅 후 claude(`@6`)로 연결)
  - 기존 로직은 "window_id가 live하면 무조건 신뢰"해서 ID가 가리키는 창이 바뀐 걸 감지 못함
  - 수정: live window의 실제 이름과 영속화된 display name을 대조(`is_trustworthy`)해 불일치 시 display name 기준으로 재매핑. `window_states`·`thread_bindings`·`user_window_offsets` 3곳 모두 적용
  - display name 스냅샷(`orig_display`)으로 세 루프 간 in-place 변경 순서 의존성 제거
  - 회귀 테스트 4건 추가 (`TestResolveStaleIds`)

---

## [1.0.2] - 2026-05-18

문서 보정. 코드 변경 없음.

### Changed

- `CLAUDE.md` 상단에 자동 로드 체인 + 상위 위임 안내 추가 (Personal 프로젝트, `~/.claude/CLAUDE.md` 글로벌만 자동 로드)
- 관련 운영 메모리 명시: `reference_ccbot_infra.md`, `reference_ccbot_versioning.md`, `feedback_ccbot_version_bump_required.md`

---

## [1.0.1] - 2026-05-14

upstream `six-ddc/ccbot` pending merge 3건을 cherry-pick. 버그픽스 only.

### Fixed

- **Interactive UI 버튼 누를 때 중복 메시지 생성 수정** (upstream [`865ab89`](https://github.com/six-ddc/ccbot/commit/865ab89), #67)
  - "Message is not modified" BadRequest를 별도 처리: 기존 메시지 유지하고 early return
  - 다른 edit 실패 시에는 교체 메시지를 먼저 보내고 원본 삭제
- **bind 시 사용자가 만든 Telegram 토픽 이름 rename 안 함** (upstream [`350c653`](https://github.com/six-ddc/ccbot/commit/350c653), #73)
  - 사용자가 직접 만든 토픽 이름을 ccbot이 자동 변경하지 않음
- **Write tool result의 line count 정확히 표시** (upstream [`f5ddd7f`](https://github.com/six-ddc/ccbot/commit/f5ddd7f))
  - 기존: Write의 tool_result는 `File created successfully at: ...` 같은 확인 메시지라 line count가 항상 1이었음
  - 변경: 원본 `tool_use.input.content`에서 line count 계산 (trailing newline 보정 포함)
  - `_format_tool_result_text`에 `tool_input_data` 인자 추가 (시그니처 변경, 기본값 `None`이라 fork 내부 호출과 호환)

### Tests

- `tests/ccbot/test_transcript_parser.py::TestFormatToolResultText` 갱신
  - parametrize에 `tool_input_data` 컬럼 추가, Write 케이스를 새 동작에 맞춰 수정
  - 전체 283/283 통과

---

## [1.0.0] - 2026-05-14

TejNote fork의 첫 공식 버전. 2026-04-27 이후 누적된 fork 전용 추가 사항을 한 번에 v1.0.0으로 정리합니다 (이전 내부 버전 `0.1.0`).

### Added (새 기능)

- **Codex / OMX provider 양방향 라우팅** ([#4](https://github.com/TejNote/ccbot/pull/4))
  - `codex` / `codex-*` tmux 창을 자동 감지해 텔레그램 토픽과 양방향 연결
  - Codex composer 전용 입력 경로: tmux `set-buffer` + `paste-buffer -d` + `Enter`로 single bracketed-paste 이벤트 전달 (직접 send-keys 시 newline 누적 문제 우회)
  - 별도 status 파서 `parse_codex_status_line`: `⏳ Working`, `🔧 <tool>` 라인 인식
  - state.json 하위 호환: 기본값 `provider=claude`는 직렬화 생략
  - OMX hook plugin (`ccbot-bridge.mjs`): `turn-complete` 이벤트 → `ccbot send`로 텔레그램 푸시
- **플러그인 스킬 메뉴**
  - 설치된 Claude Code 플러그인 스킬(superpowers, pr-review-toolkit, octo 등) 부팅 시 자동 스캔
  - `/` 명령어로 텔레그램에 자동 등록, 한글 description 지원
  - `/favorite` 즐겨찾기 핀, 프로젝트별 사용 빈도 기준 자동 정렬
  - `commands/` 디렉터리도 스캔 (`/octo:octo` 등 모든 CLI slash command 포함)
- **MessageBatcher**
  - tool-use / thinking 이벤트를 주기적 요약(`⚙️ 작업 중 N건`)으로 묶음 처리
  - `CCBOT_BATCH_WINDOW` 환경 변수로 주기 설정 (기본 10초)
- **DirectMessage 큐**
  - 명령어/사진/음성 확인 메시지를 사용자별 큐로 직렬화
  - assistant 응답 사이에 ack 메시지가 끼어드는 현상 제거
- **`ccbot send` CLI 서브커맨드**
  - `ccbot send --session-id <uuid> "메시지"` / `ccbot send --window <창이름> "메시지"`
  - 외부 hook(Stop, PostToolUse 등)에서 텔레그램 API 안 거치고 토픽에 직접 푸시 가능
  - stale window_id guard: `thread_bindings`에 매핑된 wid만 fallback 후보

### Changed (기존 동작 변경)

- README에 fork 차이점 명시 + Changelog 섹션 추가 ([#6](https://github.com/TejNote/ccbot/pull/6))

### Fixed (버그 수정)

- **상태 메시지 좀비 청소** ([#2](https://github.com/TejNote/ccbot/pull/2))
  - `state.json`에 live status message IDs 저장
  - 재시작 시 orphaned `⏳ Working` 메시지 자동 삭제
- **status polling 안정화** ([#5](https://github.com/TejNote/ccbot/pull/5))
  - background-shell-only 스피너(`Sautéed for 3s · 1 shell still running` 같은 `esc to interrupt` 신호 없는 라인)를 status update로 enqueue하지 않음
  - 턴 종료 후 답변이 마지막 메시지로 안정적으로 남음
- **status 업데이트 경로 정리**
  - content task가 즉시 status를 re-enqueue하지 않고, status polling에 위임
- **send_keys busy-state guard**
  - 수신 pane이 idle인지 먼저 확인하고 전송 → 입력 silent drop 방지
- **/clear 후 session_map 갱신**
  - `/clear` 직후 다음 메시지가 새 세션으로 정상 매핑
- **batch summary 큐 순회 수정** ([#1](https://github.com/TejNote/ccbot/pull/1))
  - batch summary가 message queue를 정상 통과
- **hook .env 파싱 보정**
  - `.env` 값의 quote 제거, `TMUX_SESSION_NAME` 정규화

### Telegram API 제약 대응

- 전체 bot command 수를 100개로 cap (Telegram API limit)
- 스킬 description 전체 길이를 Telegram ~5000자 한도 내로 budget

### Pending upstream merges

> ✅ 아래 3건은 모두 [1.0.1]에서 reconcile 완료.

`six-ddc/ccbot:main`에는 있지만 v1.0.0 시점에는 아직 fork에 reconcile 안 된 commit이었음:

| Upstream commit                                                    | 설명                                                                 |
| ------------------------------------------------------------------ | -------------------------------------------------------------------- |
| [`865ab89`](https://github.com/six-ddc/ccbot/commit/865ab89) (#67) | Interactive UI 버튼 누를 때 중복 메시지 생성되는 문제 수정          |
| [`350c653`](https://github.com/six-ddc/ccbot/commit/350c653) (#73) | bind 시 사용자가 만든 Telegram 토픽 이름을 rename하지 않도록 수정    |
| [`f5ddd7f`](https://github.com/six-ddc/ccbot/commit/f5ddd7f)       | Write tool 결과의 line count 정확히 표시                            |

[Unreleased]: https://github.com/TejNote/ccbot/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/TejNote/ccbot/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/TejNote/ccbot/releases/tag/v1.0.0
