# Changelog

이 fork(`TejNote/ccbot`)가 upstream(`six-ddc/ccbot`) 대비 어떻게 달라졌는지 추적합니다.

포맷은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/), 버전 정책은 [SemVer](https://semver.org/lang/ko/)를 따릅니다.

- **MAJOR** (v2.0.0): 기존 사용자가 영향을 받는 호환성 깨는 변경 (state.json 스키마, `.env` 키 이름, CLI 인자 등)
- **MINOR** (v1.x.0): 기능 추가 — 새 hook, 새 명령어, 새 provider 지원 등
- **PATCH** (v1.0.x): 버그 픽스, 안정성 개선, 문서 보정

## [Unreleased]

(다음 릴리스 준비 중인 변경은 여기에 누적)

---

## [1.1.0] - 2026-09-16

upstream 커뮤니티 PR 9건을 선별 머지한다. **upstream `main` 은 2026-07-08 이후 멈춰 있고
이 fork 는 이미 완전 동기화 상태**라(`git rev-list --count <마지막머지>..upstream/main` = 0),
가져올 게 남아 있던 곳은 **머지되지 않은 커뮤니티 PR** 뿐이었다.

upstream 의 미머지 브랜치 3개(`fix/prevent-window-rename` · `fix/restore-telegramify-markdown-default`
· `fix/scrub-sensitive-env`)는 `git cherry` 로 확인한 결과 **전부 이미 패치 동등하게 적용**돼
있었다(SHA 만 다른 PR 잔해). `copilot/*` 는 main 이전의 폐기된 리팩터링이다.
`codex/codex-remote-agents`(+3,120줄)는 Codex 를 app-server WebSocket 원격 프로토콜로 다시
붙이는 실험 브랜치라 이 fork 의 OMX 로컬 tmux 방식과 정면 충돌해 **제외**했다.

### Fixed

- **일시적 오류에도 평문으로 재전송해 메시지가 두 번 가던 것** (upstream #93)
  - `except Exception` 하나로 전부 잡아 평문 재전송했다. `TimedOut`·`NetworkError` 는
    **이미 도착했을 수도 있는** 모호한 상태라, 재전송하면 서식본 + 평문본 2건이 된다
  - 이제 `BadRequest`(= 텔레그램이 서식을 거부 → 확실히 미전달)일 때만 평문으로 다시 보낸다
- **접기 인용이 깨져 원문이 통째로 쏟아지던 것** (upstream #94)
  - `_render_expandable_quote()` 가 `>line…||` 를 만들었는데, 텔레그램 문법은 첫 줄에
    `**>` 를 요구한다. 없으면 끝의 `||` 가 짝 없는 spoiler 라 `Can't parse entities` 로
    전체가 거부되고, 폴백이 **서식 없는 평문 전문**을 그대로 보낸다
  - thinking · Bash/Grep/Glob/Task/WebFetch 결과 · 여러 줄 에러 · Edit diff 가 전부 이 경로다.
    "N줄, 탭해서 펼치기" 대신 로그 원문이 매번 채팅에 떨어지고 있었다
- **상태줄 아래 tip 블록이 끼면 상태를 놓치던 것** (upstream #97)
  - 구분선 바로 윗줄만 보던 로직이 tip 블록에 막혔다. 위로 더 훑되, 인접하지 않은 위치에서는
    `…` 를 가진 줄만 상태로 인정한다(일반 출력의 `·` 불릿과 완료 표시 `✻ Sautéed for 7s` 제외)
- **타이핑 표시가 긴 구간에서 꺼지던 것** (upstream #98)
  - 텔레그램은 chat action 을 약 5초 뒤 지운다. 상태 메시지를 보낼 때만 한 번 쏘던 방식이라
    상태 텍스트가 안 바뀌는 동안 표시가 사라졌다. 이제 상태폴링이 4초 주기 keepalive 로 몬다
- **포럼 토픽에서 "/" 메뉴가 비어 보이던 것** (upstream #99)
  - 클라이언트는 그 채팅에 맞는 **스코프**를 읽고, 좁은 스코프가 기본을 이긴다. 옛 설정이
    남긴 `all_private_chats` 목록 하나가 진짜 메뉴를 가렸다. 지울 땐 관리자 스코프까지 넓게,
    쓸 땐 실제로 쓰는 스코프 전부에 쓴다
- **워크스페이스 신뢰 대화상자가 새 세션을 막던 것** (upstream #101)
  - `claude` 실행 전 `~/.claude.json` 에 해당 디렉터리를 신뢰 표시한다.
    끄려면 `CCBOT_AUTO_TRUST_DIRS=false`
- **세션 이름이 박힌 테두리를 입력창 구분선으로 못 읽던 것** (upstream #101)
- **죽은 토픽 상태가 쌓이던 것** (upstream #81) — `/kill` 이 메뉴 힌트로만 등록돼 있어 핸들러
  없이 Claude Code 로 흘러들어갔다. 실제 핸들러를 붙이고, 토픽이 `/kill` 밖에서 죽었을 때도
  같은 정리 경로를 타게 했다

### Added

- **보조 메시지 자동 정리** (upstream #94) — thinking · tool_use/tool_result · 로컬 명령 에코 ·
  사용자 메시지 되울림이 영원히 쌓여 **진짜 답변을 묻었다.** 토픽별로 마지막 "보조" 메시지를
  추적해 다음 메시지가 오면 지운다. 최종 답변은 추적도 삭제도 하지 않으므로, 턴이 끝나면
  자연히 답변만 남는다. 병합 태스크·분할 전송은 메시지가 여러 개라 **id 목록**으로 추적한다
- **타이머 상태 갱신 적응형 스로틀** (upstream #52) — 레이트 리밋 회피
- **음성 전사 모델 설정** (upstream #91) — OpenAI 호환 백엔드용

### 이 fork 에 맞춰 손본 것

- #98 — upstream 은 chat action 에 thread 를 안 싣는다. 이 fork 는 토픽 모드가 기본이라
  `_send_kwargs` 로 `message_thread_id` 를 넘긴다. codex 분기도 같은 게이트를 탄다
- #99 — upstream 은 `post_init` 2곳만 고쳤지만 이 fork 는 스킬 즐겨찾기 토글에서 런타임
  재등록을 한 번 더 한다. 거기까지 같은 헬퍼를 태워야 토글 뒤 메뉴가 되돌아가지 않는다
- #94 — upstream 은 콘텐츠 처리 끝에서 `_check_and_send_status` 를 인라인 호출한다.
  이 fork 는 상태 표시를 `status_polling` 에 넘기며 그 호출을 뺐으므로 되살리지 않았다
- #81 + #93 — 죽은 토픽 정리(`_maybe_cleanup_dead_topic`)는 `BadRequest` 분기 안쪽에만 둔다.
  `Topic_id_invalid`·`Message thread not found` 는 `BadRequest` 로 오고, `TimedOut` 으로
  토픽을 지우면 네트워크가 흔들릴 때마다 멀쩡한 바인딩이 날아간다
- #97 — fork 고유의 백그라운드 셸 필터(`_BACKGROUND_SHELL_RE`)를 보존했다
- #99 — `CC_COMMANDS` 에 upstream 이 추가한 5개(agents·context·recap·status·tasks)를 넣되
  설명은 이 fork 의 한국어 스타일로 맞췄다

### 머지 후 리뷰에서 잡아 고친 것

upstream 패치 자체는 맞지만 **이 fork 의 다른 부분과 겹쳐서 깨지는** 것들이다.
upstream 테스트로는 안 잡힌다. 전부 `tests/ccbot/test_upstream_merge_fixes.py` 로 고정했고,
수정을 되돌리는 뮤테이션 4종이 각각 해당 테스트 하나씩을 실패시키는 것까지 확인했다.

- 🚨 **죽은 토픽을 정리해도 타이핑 keepalive 가 살아남아 무한 API 호출** — #81 의
  `cleanup_dead_topic` 은 세션 상태만 풀고 `clear_topic_state` 를 부르지 않는 **유일한**
  해제 경로였다(다른 셋은 전부 부른다). #98 이 붙기 전에는 단순 누수였지만, 붙고 나서는
  `_typing_keepalive` 가 `while True` 라 **텔레그램이 "그 토픽 없다" 고 답한 thread 로
  4초마다 `send_chat_action` 을 영원히** 던지게 됐다. `cleanup_dead_topic` 이 정리한
  `(user_id, thread_id)` 목록을 돌려주도록 바꾸고 호출처에서 `clear_topic_state` 를 돌린다
- **#97 의 8줄 스캔이 `·` 불릿을 상태줄로 읽는 구멍을 비인접 줄에 새로 열었다** — upstream 의
  통과 조건이 `"…" in line` 하나뿐인데 `·` 는 스피너이면서 **평범한 불릿**이다. Claude 의
  답변 본문 `· 첫째 항목은…` 이 상태줄이 되고, #98 이 그 값으로 타이핑을 켜므로 유휴 세션에
  유령 상태 메시지 + 영구 타이핑이 남는다. 비인접 줄은 `…` **그리고** `·` 가 아닐 것을 요구한다.
  ⚠️ 구분선 **바로 위** 불릿은 main 에도 있던 기존 오탐이라 손대지 않았다(실측 대조 확인)
- **퍼미션 프롬프트가 떠 있는 내내 "작업 중" 으로 보이던 것** — #98 의 `set_typing` 이
  `update_status_message` 맨 끝에 한 번뿐인데 그 위에 조기 `return` 이 셋이다. 프롬프트는
  사람이 답할 때까지 몇 시간도 떠 있고, 실제로는 봇이 **사용자를 기다리는 중**이다.
  세 지점에서 타이핑을 끈다(큐가 찬 `skip_status` 는 제외 — 에이전트가 실제로 작업 중이다)
- **secondary 태스크의 이미지가 고아로 남던 것** — #94 가 모으는 id 에 `_send_task_images`
  가 보낸 사진이 빠져 있었다. `tool_result` 는 항상 secondary 라, 다음 메시지가 오면
  **설명 텍스트만 지워지고 스크린샷이 맥락 없이 남는다.** `send_photo` 가 message id 를
  반환하게 하고 전송을 추적보다 먼저 한다
- **콜백 응답이 최대 45초 지연되던 것** — `_create_and_bind_window` 가 `query.answer()` 를
  함수 맨 끝에서 불렀는데, 그 앞에 `wait_for_claude_ready`(30초) + `wait_for_session_map_entry`
  (5~15초)가 있다. 텔레그램은 약 15초를 넘기면 `query is too old` 를 내고, 그 호출엔
  try/except 가 없어 그대로 터진다. 응답을 **맨 앞**으로 옮겼다(결과 ✅/❌ 는 `safe_edit` 가
  메시지 본문으로 보여주므로 잃는 정보가 없다)
- **`TOPIC_CHECK_INTERVAL` docstring 이 60초로 남아 있던 것** — #81 이 5초로 낮췄다.
  ⚠️ 바인딩 1개당 API 호출이 12배가 되어 #52 의 스로틀과 방향이 반대다. **레이트 리밋을
  실제로 맞은 관측이 없어** upstream 값을 유지하되, 근거와 되돌리는 값(15~20초)을 주석에 남겼다

### 검증

- `pytest` **440항 통과** (머지 전 400 → upstream 테스트 32 + 이 fork 의 회귀 8)
- `pyright src/ccbot/` **0 errors**
- `ruff check src/ tests/` 위반 **103 → 102**. 파일·규칙 단위로 대조해 **main 에 없던 조합은 0건**이고,
  유일한 증가는 `bot.py` BLE001 12 → 15(#81 `/kill` 핸들러 + #99 스코프 헬퍼 2개)로 그 파일의 기존 스타일과 같다

---

## [1.0.10] - 2026-09-04

libtmux 의 창·pane 열거를 우회해 `zip() argument 2 is shorter than argument 1` 을 없앤다.

### Fixed

- **libtmux 열거가 간헐적으로 터지던 문제** (`tmux_manager.py`, 8곳)
  - libtmux 는 tmux 에 **126개 필드**를 `␞` 로 이어 달라고 하고 **출력을 줄 단위로** 레코드 하나로 본다. 어떤 필드 값에 개행이 들어가면(예: `pane_current_path` 가 개행 든 디렉터리) 레코드가 두 줄로 쪼개지고, `neo.py` 의 `zip(..., strict=True)` 가 `ValueError: zip() argument 2 is shorter than argument 1` 로 터진다 (운영이 쓰는 **0.55.1 기준 `neo.py:244`**, 최신 0.62.0 에서는 `neo.py:657` — 줄 번호는 버전마다 다르다)
  - ⚠️ **개발 venv 와 운영이 서로 다른 libtmux 를 쓴다.** `pyproject.toml` 이 `libtmux>=0.37.0` 로 열려 있어 새로 만든 venv 는 0.62.0 을 받고, 실제 봇(uv tool)은 0.55.1 이다 — 테스트는 다른 버전 위에서 돈다. 별건으로 남긴다
  - **upstream libtmux #752 로 보고돼 있고 PR 미머지다** — 우리 0.55.1 부터 최신 0.62.0 까지 전부 영향. 그래서 버전 업그레이드로는 안 고쳐진다
  - 간헐적인 이유도 이슈가 설명한다 — 「blast radius moves with the active pane」. 세션·창 행이 `pane_*` 를 활성 pane 기준으로 해석하므로, 어느 pane 이 활성인지에 따라 성공/실패가 오간다. 실측: 09-02~09-04 사흘 **11건**, 창 `@0`~`@5` 에 흩어져 발생
  - 🚨 피해가 상태줄 갱신에서 끝나지 않았다 — **텔레그램 → tmux 주입도 같은 열거를 탄다**(`send_keys`·`_send_via_paste`). 개행 든 값이 지속되면 메시지 전달이 막힌다
  - 수정 — `session.windows.get(...)` → `active_pane` 경로를 쓰던 **8곳** + **`list_windows`** 를 `tmux … -t <window_id>` 직접 호출로 바꿨다. tmux 는 창 ID 로 활성 pane 을 자동 타게팅하므로 애초에 열거가 필요 없다
  - 🚨 **초안은 `list_windows` 를 빼먹어 「전달 경로 해결」 주장이 성립하지 않았다**(리뷰가 Critical 로 잡음). `find_window_by_id` 가 `list_windows` 를 부르고, `session.py` 의 `send_to_window` 와 `bot.py` 십여 곳이 **실제 전송 전에** 그걸 먼저 부른다. 게다가 옛 `for window in session.windows:` 는 `try` **밖**이라 예외가 그대로 전파되고, `bot.py` 핸들러에는 감싸는 `try` 도 전역 `error_handler` 도 없다 — 즉 그 메시지는 조용히 유실된다. 「마지막 액션」만 고치고 「창 찾기」를 남겨두면 아무것도 해결되지 않았다
  - 파싱은 **개행에 안전하게** 짰다(`parse_window_records`) — 모든 필드를 `\x1f` 로 종단하고 **구분자 개수로 재조립**한다. 값에 개행이 몇 개 있든 안전하고, 값이 구분자를 품으면 어긋난 결과 대신 실패로 처리한다(업스트림 #752 의 수정 방식과 같다)
  - `_send_via_paste` 의 **버퍼 누출**도 막았다(리뷰 Critical) — 창 조회를 없애면서 `set-buffer` 가 먼저 실행되게 됐고, `paste-buffer` 가 실패하면 `-d` 가 발동하지 않아 **사용자 메시지 전문이 담긴 버퍼가 남는다.** 실패 시 `delete-buffer` 로 명시 정리한다
  - `_tmux()` 실패 로그는 **WARNING** 이다 — 창을 찾은 뒤 명령을 보내는 사이 사용자가 창을 닫는 정상 레이스가 있고, 예전엔 그 경우 로그가 아예 없었다. ERROR 로 올리면 정상 상황이 소음이 된다(리뷰 지적)
  - ⚠️ `send-keys -l` 과 `rename-window` 에 **`--` 를 붙였다.** 없으면 `-` 로 시작하는 문자열이 플래그로 파싱된다(실측: `-x` → `unknown flag -x`). libtmux 는 안 붙이므로 이 부분은 **원본보다 안전**하다

### Changed

- **`capture_pane` 이 3배 빨라졌다** (실측 **13.7ms → 5.5ms**/회). 캡처 한 번마다 42개 pane 을 126필드(2222자 포맷)로 열거하던 것을 없앴다. 상태 폴링이 1초 주기라 상시 부하였다
- **회귀 테스트 7건 신설** (`tests/ccbot/test_tmux_window_parsing.py`) — 평문 / 개행 든 cwd / 첫·중간·마지막 필드 개행 / 오염 레코드가 정상 사이에 낀 경우 / 빈 값·빈 출력 / 위조된 구분자 탐지 / main 창 제외 유지. 이번 변경 전엔 `tmux_manager` 에 대응 테스트가 전혀 없었다(리뷰 지적)

---

## [1.0.9] - 2026-09-04

`scripts/restart.sh` 가 launchd 배포를 몰라 쓸 수 없던 것을 고친다.

### Fixed

- **`restart.sh` 가 launchd(KeepAlive) 배포에서 동작하지 않던 문제** (`scripts/restart.sh`)
  - 이 스크립트는 upstream 의 「tmux `__main__` 창에서 `uv run ccbot`」을 전제한다. launchd 배포에는 둘 다 없다 — `__main__` 창이 없어 창 검사에서 `exit 1` 로 멈추고(지금은 이 덕에 사고가 안 났다), `pgrep` 패턴이 실제 프로세스(`~/.local/bin/ccbot start`)를 못 잡는다
  - ⚠️ **창 이름만 맞으면 「안 돌고 있다」로 오판해 두 번째 인스턴스를 띄운다.** 같은 토큰으로 봇 둘이 폴링하면 Telegram `getUpdates` 가 충돌한다
  - 수정 — launchd job 을 **자동 탐지**하고(`CCBOT_LAUNCHD_LABEL` 로 덮어쓰기 가능) 그쪽 규약으로 재시작한다. **자식 프로세스에만 SIGTERM** 을 보내고 KeepAlive 가 올리게 둔다. 감독 스크립트를 죽이지 않으므로 uptime 기반 실패 카운터 리셋이 정상 동작한다. **upstream 의 tmux 경로는 그대로 남겼다**
  - `pgrep` 패턴에 `bin/ccbot start` 추가. 🚨 그리고 결과를 `kill` 하므로 **패턴을 인자로 가진 셸**(`bash -c '… bin/ccbot start …'`)을 걸러낸다 — `$$`/`$PPID` 만 빼는 것으로는 조부모 셸이 안 걸러진다(실측 확인)
  - 성공 판정에 **생존 확인 8초** 추가. 「새 PID 존재」만 보면 크래시 루프를 성공으로 보고한다

### Changed

- **`set -euo pipefail` + `| head -1` 의 SIGPIPE 함정 제거** (`scripts/restart.sh`) — `head` 가 파이프를 먼저 닫아 앞 단계가 SIGPIPE 로 죽고 `pipefail` 이 그걸 스크립트 실패로 만든다. 실측으로 당했다: 재기동 대기 중 **아무 메시지 없이 `exit 1`** 이 났는데 정작 봇은 정상 기동해 있었다. `first_line()` 헬퍼로 교체했고, upstream 코드의 같은 자리(`UV_PID=$(pgrep … | head -1)`)도 함께 고쳤다

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
