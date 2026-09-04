# Changelog

이 fork(`TejNote/ccbot`)가 upstream(`six-ddc/ccbot`) 대비 어떻게 달라졌는지 추적합니다.

포맷은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/), 버전 정책은 [SemVer](https://semver.org/lang/ko/)를 따릅니다.

- **MAJOR** (v2.0.0): 기존 사용자가 영향을 받는 호환성 깨는 변경 (state.json 스키마, `.env` 키 이름, CLI 인자 등)
- **MINOR** (v1.x.0): 기능 추가 — 새 hook, 새 명령어, 새 provider 지원 등
- **PATCH** (v1.0.x): 버그 픽스, 안정성 개선, 문서 보정

## [Unreleased]

(다음 릴리스 준비 중인 변경은 여기에 누적)

---

## [1.0.8] - 2026-09-04

v1.0.6·v1.0.7 이 증상을 막았다면, 이번엔 **근본 원인**을 없앤다.

### Fixed

- **훅이 `transcript_path` 를 남기지 않아 jsonl 위치를 cwd 로 추측하던 문제** (`hook.py` · `session_monitor.py`)
  - 근본 원인 규명(공식 문서 `code.claude.com/docs/en/hooks`) — 훅 페이로드의 `cwd` 는 **"Current working directory when the hook is invoked"** 다. 세션이 **시작한** 폴더가 아니라 훅이 불린 **그 순간**의 폴더다. Bash 의 `cd` 가 유지되므로 긴 세션에서는 계속 떠돈다(실측: 한 세션에서 **cwd 전환 359회**, 폴더 6종). 그런데 jsonl 은 **시작 시점 cwd** 로 만든 폴더에 고정된다
  - `SessionStart` 는 `compact` 에도 발화한다(= 세션 도중). 그래서 하위 폴더에서 자동 압축이 걸리면 둘이 어긋난다. 2026-09-03 17:10:59 `@2` 가 그랬고(jsonl 에 `subtype=compact_boundary` 기록, 초 단위 일치) **metlife 토픽이 17시간 죽었다**
  - 같은 페이로드에 `transcript_path`("Path to conversation JSON")가 들어오는데 **레포 전체에서 한 번도 쓰지 않고 있었다**(`grep transcript_path` = 0건)
  - 수정 — 훅이 `transcript_path` 를 검증해 `session_map` 에 함께 남기고(`_validate_transcript_path`: 절대경로 + stem 이 session_id 와 일치), 모니터가 **그걸 0순위**로 쓴다. 추측이 사라진다
  - `transcript_path` 로 찾았을 때는 **cwd 를 고치지 않고 경고도 내지 않는다** — cwd 가 떠도는 건 정상이고, 고쳐 봐야 다음 compact 에 다시 덮인다. 스캔 기준만 실제 폴더로 옮긴다
  - **호환** — 필드는 «있을 때만» 쓴다. 옛 항목은 v1.0.7 의 되짚기로 그대로 동작한다(추가 필드라 스키마 깨짐 없음). 각 창은 다음 SessionStart 때 필드를 얻는다
  - 회귀 테스트 10건 — `test_session_monitor_transcript_path.py` 6건(사고 재현 / cwd 미변경 / 무경고 / 스캔 기준 이동 / 낡은 경로 무시 / 필드 없을 때 폴백) + `test_hook.py` 4건(검증 함수). **transcript 경로를 무력화하면 3건이 실패**함을 확인했다

---

## [1.0.7] - 2026-09-04

v1.0.6 이 못 막은 **같은 버그의 변종** — cwd 가 어긋나면 살아 있는 세션을 여전히 버렸다.

### Fixed

- **session_map 의 cwd 가 세션의 실제 jsonl 위치와 어긋나면 죽은 세션으로 갈아타던 버그** (`session_monitor.py` `_locate_session_jsonl`)
  - auto-detect 는 「그 sid 의 jsonl 은 cwd 로 계산한 폴더에 있다」를 전제했다. 훅이 그 창의 cwd 를 **하위 폴더**로 갱신하면 전제가 깨진다 — 세션의 jsonl 은 시작 당시 cwd 로 만든 폴더에 그대로 있기 때문이다. 그러면 mtime 0 → 「낡았다」 → 엉뚱한 폴더의 최신 파일로 갈아탄다
  - 실측(2026-09-03 17:10:59, 로그 `27644-27647`): 훅이 `@2` 의 cwd 를 `Metlife/insudeal-x-backend` 로 바꾼 **3ms 뒤** auto-detect 가 살아 있는 `06b58ae5` 를 버리고 그 폴더의 최신 파일(`834296ff`, **2026-08-04**, 한 달 전 죽은 세션)로 갈아탔다. **metlife 토픽이 17시간 동안 출력 0**
  - ⚠️ v1.0.6 의 「새 세션 유예」는 이걸 **120초 늦출 뿐** 막지 못한다 — 그 폴더에 파일이 영영 안 생기기 때문이다. 「후보 없음」 경고도 안 걸린다(후보가 있고 파일도 존재한다)
  - 수정 — `<project_dir>/<sid>.jsonl` 이 없으면 `projects/` 전체를 되짚어 실제 파일을 찾는다. 찾으면 그 mtime 으로 판정하고, **session_map 의 cwd 를 실제 위치로 고친다**(안 고치면 폴더 스캔이 계속 엉뚱한 곳을 뒤져, 추적 세션이 정말 죽는 순간 그 폴더의 아무 오래된 파일로 갈아탄다). 고칠 때 `WARNING` 을 남긴다 — 조용히 고치면 훅이 왜 그랬는지 못 본다
  - 되짚기는 `SID_RESCAN_SEC`(30초) 주기로 제한한다. 매 폴링(2초)마다 glob 하면 프로젝트 폴더 수만큼 stat 이 돈다. 캐시는 추적 중인 sid 만 남긴다
  - **「스캔 기준 이동」과 「session_map cwd 교정」을 분리했다.** 초안은 둘을 하나의 `continue` 에 묶어서, cwd 를 못 고치는 경우(jsonl 에 `cwd` 필드가 없어 빈 문자열이 오거나 읽은 값이 저장된 값과 같은 경우) **매 폴링마다 같은 분기로 돌아와 그 창의 auto-detect 가 통째로, 로그 한 줄 없이 영구 정지**했다. 이 패치가 막으려던 증상을 새 경로에서 되풀이한 셈이라 리뷰에서 Critical 로 잡혔다. 이제 스캔 기준은 **항상** 실제 폴더로 옮기고, cwd 교정은 할 수 있을 때만 하며, **어느 쪽이든 경고를 남긴다**
  - 회귀 테스트 9건 (`tests/ccbot/test_session_monitor_cwd_drift.py`) — 사고 재현 / cwd 교정 / 교정 후 정상 추적 / 경고 1회 / 되짚기 주기 제한 / 캐시 무한증식 방지 / **교정 불가 시 정지하지 않음 · 경고는 남김 · 죽은 세션을 집지 않음**. 되짚기를 무력화하면 5건이, 교정 실패 시 `continue` 로 되돌리면 1건이 실패함을 각각 확인했다

---

## [1.0.6] - 2026-09-03

살아 있는 세션을 버려서 텔레그램 토픽이 조용히 죽는 경로 차단.

### Fixed

- **훅이 방금 등록한 세션을 직전 세션으로 덮어쓰던 버그** (`session_monitor.py` `_auto_detect_session_changes`)
  - 새 세션은 jsonl 이 아직 만들어지지 않아 mtime 이 0 이다. 그걸 「낡았다」로 보고 폴더에서 최신 파일을 찾아 갈아타면서, 훅이 등록한 **살아 있는 세션을 `_abandoned_sids` 에 넣어 영구 거부**했다
  - 실측(2026-09-02 16:41): `@4` 가 훅이 쓴 `e2586825` 대신 `e1923ab1`(직전 세션, 아직 flush 중)에 묶였다. **personal 토픽이 23시간 동안 출력 0.** 수신은 tmux 창 ID 로 라우팅하므로 정상이라 「받기는 되는데 안 나온다」로만 보여 원인이 안 잡혔다
  - 수정 3가지 — ① jsonl 이 없는 새 세션에 `NEW_SESSION_GRACE_SEC`(120초) 유예 ② mtime 캐시 키를 창 키 → `(창, sid)` 로 (창 키만 쓰면 「앞 세션」의 mtime 과 비교해 새 세션의 첫 폴링이 곧바로 「낡았다」가 된다) ③ **자기 치유** — 재채택 거부가 **연속** 10분 넘게 이어지는데 추적 중인 세션은 5분째 안 자라고 후보만 3분 내에 자랐으면 강제로 되돌린다
  - **자기 치유가 왕복을 되살리지 않도록 가드 2겹** (초안의 「두 세션이 둘 다 살아 있으면 이 분기에 도달하지 않는다」는 **거짓이었다** — 리뷰가 실측 재현: 320초 주기로 살아 있던 세션에서 946초 만에 튀었다)
    - `_suspect_abandoned` — 「훅이 등록해 둔 것을 우리가 덮은」 sid 만 자기 치유 대상. 우리가 스스로 채택했다 버린 sid 로는 되돌아가지 않는다(되돌아가면 반대편이 후보가 되어 왕복이 성립한다)
    - `_refused_since` 를 growing 분기에서 리셋 — 「연속 거부 10분」이 되고, 「10분 전에 한 번 거부됐다 + 지금 우연히 조용하다」로는 발동하지 않는다
  - 회귀 테스트 13건 (`tests/ccbot/test_session_monitor_readopt.py`) — 사고 재현 / 유예 중 유지 / 유예 만료 후 정상 교체 / resume 기준선 / 자기 치유 발동 / 살아 있는 세션이 주기적으로 깨어날 때 미발동 / 우리가 버린 sid 로는 미발동 / 후보도 죽었으면 미발동 / 둘 다 계속 자랄 때 미발동 / 거부 경고 주기 재발 / 후보 없음 경고 / stat 실패가 교체를 유발하지 않음 / 상태 무한증식 방지

### Changed

- **조용한 실패 3곳에 관측 수단 추가** (`session_monitor.py`) — 이 사고의 본질이 「출력 0 인데 로그가 1줄」이었으므로 같은 클래스를 함께 막았다
  - 대체 후보가 아예 없는 경우(`newest_sid is None`) 예전에는 **로그 0줄**로 영구 침묵했다. 훅이 틀린 cwd 를 등록하면 같은 증상인데 원인 흔적이 없다 → 주기 `WARNING`
  - 재채택 거부 경고가 「창·후보 조합당 한 번만」이라 오래 갇힌 상태가 사실상 안 보였다 → `_warn_periodically` 로 `REWARN_SEC`(10분) 주기 재경고 + 경과 시간 기재
  - jsonl 유예 대기 구간에 `DEBUG` 로그 추가 — 유예 만료 전에 문제를 포착할 수 있다
- **일시적 `stat` 실패와 「파일 없음」을 분리** (`session_monitor.py`) — 둘 다 `mtime=0` 으로 뭉개면 NFS 순단·권한 일시변경이 「낡았다」로 읽혀 폴더의 **더 오래된** jsonl 조차 「더 최신」으로 오판되고 멀쩡한 세션이 교체된다. 이제 stat 실패는 그 폴링을 건너뛴다

### Added

- **CHANGELOG 누락분 소급 기재** — `c94991d`(2026-08-31) 「두 세션이 한 토픽에 섞이던 session_map 왕복 차단」이 CHANGELOG 없이 머지돼 있었다. 위 수정이 그 가드의 부작용을 고치는 것이라 같은 릴리스에 함께 적는다

---

## [1.0.5] - 2026-07-24

state.json·로그 잔재 누적 정리 (재부팅마다 쌓이던 것들).

### Fixed

- **display_names 고아 항목 startup prune** (`session.py` `resolve_stale_ids`)
  - tmux는 서버 재시작마다 window ID를 다시 매겨, 사라진 창(예: 제거된 `codex`)의 `window_display_names` 항목이 매 재부팅 누적됨 (무해하나 state.json 오염)
  - 수정: `resolve_stale_ids` 끝에서 `window_states`에 대응 없는 display-name 항목 제거. **단 `thread_bindings`에 참조된 window_id(셸 전용 `main` 창처럼 window_state가 없는 경우)는 보존** — 그래야 재부팅 후 이름 기준 재바인딩으로 복구 가능. 회귀 테스트 3건 (`TestOrphanDisplayNamePrune`)

### Changed

- **로그 기본 레벨 DEBUG → INFO** (`main.py`)
  - `ccbot` 로거가 DEBUG여서 `State saved`·`Saved N tracked sessions` 등 per-event 스팸이 `ccbot-autostart.log`를 무한 누적(4개월 67MB 실측)
  - 기본 INFO로 낮춰 누적 속도 대폭 감소. `CCBOT_DEBUG=1` 환경변수로 DEBUG 복원 가능

---

## [1.0.4] - 2026-07-24

재부팅·절전복귀 시 텔레그램 토픽 바인딩이 서서히 전부 지워지던 버그 수정.

### Fixed

- **런타임 stale binding 삭제 → 이름 기준 재매핑으로 전환** (`handlers/status_polling.py`)
  - 증상: 재부팅/절전복귀가 반복되면 `thread_bindings`가 하나씩 비다가 결국 `{}`가 되어, ccbot이 살아있어도 모든 창 출력이 `No active users`로 버려짐 (텔레그램 응답 없음)
  - 원인: `status_poll_loop`의 cleanup이 바인딩된 window_id(`@5`·`@6` 등)를 `find_window_by_id`로만 확인하고, 없으면 즉시 `unbind_thread`로 **영구 삭제**. tmux는 서버 재시작마다 ID를 `@0`부터 다시 매기므로, 같은 창이 살아있어도 ID가 바뀌면 삭제됨. 1.0.3의 재매핑은 **load 시점(`session.py` migrate)에만** 적용돼 런타임 폴러엔 빠져 있었음
  - 수정: `resolve_binding_window()` 헬퍼 신설 — window_id로 못 찾으면 영속화된 창 이름으로 `find_window_by_name` 재조회 후 새 ID로 remap. 이름으로도 없을 때만 unbind (창이 진짜 사라진 경우)
  - 회귀 테스트 3건 추가 (`test_status_polling_rebind.py`): remap·live-passthrough·truly-dead

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
