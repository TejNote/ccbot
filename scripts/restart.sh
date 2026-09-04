#!/usr/bin/env bash
set -euo pipefail

TMUX_SESSION="ccbot"
TMUX_WINDOW="__main__"
TARGET="${TMUX_SESSION}:${TMUX_WINDOW}"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MAX_WAIT=10  # seconds to wait for process to exit

# launchd(KeepAlive)가 봇을 감독하는 배포에서는 이 스크립트가 직접 띄우면 안 된다.
# 🚨 2026-09-04 실측: 이 스크립트는 upstream 의 `uv run ccbot` + `__main__` 창을
#    전제하는데, launchd 배포에서는 둘 다 없다 —
#      · `__main__` 창이 없어 창 검사에서 exit 1 (지금은 이 덕에 사고가 안 났다)
#      · pgrep 패턴이 `~/.local/bin/ccbot start` 를 못 잡는다
#    창 이름만 맞으면 「안 돌고 있다」로 오판해 **두 번째 인스턴스**를 띄운다.
#    같은 토큰으로 봇 둘이 폴링하면 Telegram getUpdates 가 충돌한다.
#
# 그래서 launchd job 이 등록돼 있으면 그쪽 규약을 따른다 — 자식 프로세스에만
# SIGTERM 을 보내고 KeepAlive 가 다시 올리게 둔다. 감독 스크립트를 죽이지 않으므로
# uptime 기반 실패 카운터(`~/.ccbot/.fail-count`) 리셋도 정상 동작한다.
# 라벨은 자동 탐지하며 CCBOT_LAUNCHD_LABEL 로 덮어쓸 수 있다.
LAUNCHD_LABEL="${CCBOT_LAUNCHD_LABEL:-}"
if [ -z "$LAUNCHD_LABEL" ] && command -v launchctl >/dev/null 2>&1; then
    LAUNCHD_LABEL="$(launchctl list 2>/dev/null \
        | awk '$3 ~ /ccbot/ { print $3; exit }')"
fi
# ThrottleInterval 만큼 기다려야 할 수도 있어 넉넉히 잡는다. 다만 job 이 그보다
# 오래 살아 있었다면 스로틀 창이 이미 지나 **즉시** 다시 뜬다(실측: 정지 12:40:14 →
# 기동 12:40:15). 우리 plist 는 ThrottleInterval=30 이다.
LAUNCHD_WAIT=90
# 새 PID 를 본 뒤 이만큼 살아 있어야 «성공» 으로 친다. 크래시 루프면 새 PID 가
# 계속 생기므로, 존재만 보고 성공이라 하면 조용한 실패를 성공으로 보고하게 된다.
LAUNCHD_SETTLE=8

CCBOT_PATTERN='uv run ccbot|\.venv/bin/ccbot|bin/ccbot start'

ccbot_pids() {
    # `uv run ccbot`(upstream) · `.venv/bin/ccbot` · `bin/ccbot start`(launchd 런처)
    #
    # 🚨 결과를 kill 하므로 «봇이 아닌 것»을 반드시 걸러야 한다. `pgrep -f` 는 명령줄
    #    전체를 보기 때문에, 이 패턴을 **인자로 가진 셸**(`bash -c '... bin/ccbot start ...'`)
    #    도 같이 잡힌다. 2026-09-04 실측으로 확인했다.
    #    `$$`/`$PPID` 만 빼는 것으로는 부족하다 — 조부모 셸은 안 걸러진다.
    #    그래서 「명령줄이 `sh -c` 계열이면 제외」로 판정한다. 실제 봇은
    #    `<python> …/bin/ccbot start` 또는 `uv run ccbot` 이라 여기 걸리지 않는다.
    # ⚠️ 감독 스크립트(ccbot-start-real.sh)는 애초에 패턴에 안 걸린다 —
    #    그건 죽이는 대상이 아니다(죽이면 카운터 로직이 건너뛰어진다).
    local p cmd
    pgrep -f "$CCBOT_PATTERN" 2>/dev/null | while read -r p; do
        [ "$p" = "$$" ] && continue
        cmd="$(ps -o command= -p "$p" 2>/dev/null)"
        case "$cmd" in
            ""|*sh\ -c\ *|*pgrep*) continue ;;
        esac
        echo "$p"
    done
}

is_ccbot_running() {
    [ -n "$(ccbot_pids)" ]
}

# 첫 줄만 꺼낸다.
# 🚨 `cmd | head -1` 를 쓰면 안 된다. 이 스크립트는 `set -euo pipefail` 이라
#    head 가 파이프를 먼저 닫아 앞 단계가 SIGPIPE 로 죽으면 **스크립트 전체가
#    조용히 종료**된다. 2026-09-04 실측으로 당했다 — 재기동 대기 도중 아무 메시지
#    없이 exit 1 이 났고, 정작 봇은 정상 기동했는데 실패로 보고됐다.
first_line() {
    printf '%s' "${1%%$'\n'*}"
}

restart_via_launchd() {
    echo "launchd job 감지: $LAUNCHD_LABEL — KeepAlive 규약으로 재시작한다"
    local pids old_first
    pids="$(ccbot_pids)"
    old_first="$(first_line "$pids")"
    if [ -z "$pids" ]; then
        echo "  실행 중인 ccbot 프로세스가 없다. launchd 가 곧 올린다(kickstart)."
        launchctl kickstart "gui/$(id -u)/${LAUNCHD_LABEL}" 2>/dev/null || true
    else
        echo "  SIGTERM → $pids"
        # shellcheck disable=SC2086
        kill $pids 2>/dev/null || true
    fi

    echo "  재기동 대기 (최대 ${LAUNCHD_WAIT}s)..."
    local waited=0 newpid=""
    # shellcheck disable=SC2034
    while [ "$waited" -lt "$LAUNCHD_WAIT" ]; do
        sleep 2
        waited=$((waited + 2))
        newpid="$(first_line "$(ccbot_pids)")"
        if [ -n "$newpid" ] && [ "$newpid" != "$old_first" ]; then
            # 존재만으로 성공이라 하지 않는다 — 같은 PID 가 계속 살아 있어야 한다
            sleep "$LAUNCHD_SETTLE"
            if ! kill -0 "$newpid" 2>/dev/null; then
                echo "  ⚠️ PID $newpid 가 ${LAUNCHD_SETTLE}s 안에 사라졌다 — 계속 기다린다"
                old_first="$newpid"   # 다음 회차의 «옛 PID» 기준을 갱신
                waited=$((waited + LAUNCHD_SETTLE))
                continue
            fi
            echo "  ✔ 재기동 완료 (PID $newpid, ${waited}s 후 감지 · ${LAUNCHD_SETTLE}s 생존 확인)"
            echo "  fail-count: $(cat "${HOME}/.ccbot/.fail-count" 2>/dev/null || echo "-")"
            return 0
        fi
    done

    echo "  🚨 ${LAUNCHD_WAIT}s 안에 안 올라왔다. 아래를 확인한다 —" >&2
    echo "     launchctl print gui/$(id -u)/${LAUNCHD_LABEL} | grep -E 'state|last exit'" >&2
    echo "     tail -30 ~/.local/logs/ccbot-autostart.log" >&2
    return 1
}

# launchd 가 감독하는 배포면 그쪽으로 빠진다 (tmux 창 검사 전에 판정한다 —
# 그 배포엔 `__main__` 창 자체가 없어서 아래 검사가 무조건 exit 1 이다)
if [ -n "$LAUNCHD_LABEL" ]; then
    restart_via_launchd
    exit $?
fi

# ── 여기부터는 upstream 방식(tmux 창에서 `uv run ccbot`) ──
# Check if tmux session and window exist
if ! tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
    echo "Error: tmux session '$TMUX_SESSION' does not exist"
    exit 1
fi

if ! tmux list-windows -t "$TMUX_SESSION" -F '#{window_name}' 2>/dev/null | grep -qx "$TMUX_WINDOW"; then
    echo "Error: window '$TMUX_WINDOW' not found in session '$TMUX_SESSION'"
    echo "  (launchd 로 돌리는 배포라면 CCBOT_LAUNCHD_LABEL 을 지정한다)"
    exit 1
fi

# Check if uv run ccbot is running. Uses pgrep (portable across macOS/Linux)
# instead of pstree, which is not installed on macOS by default.
# Stop existing process if running
if is_ccbot_running; then
    echo "Found running ccbot process, sending Ctrl-C..."
    tmux send-keys -t "$TARGET" C-c

    # Wait for process to exit
    waited=0
    while is_ccbot_running && [ "$waited" -lt "$MAX_WAIT" ]; do
        sleep 1
        waited=$((waited + 1))
        echo "  Waiting for process to exit... (${waited}s/${MAX_WAIT}s)"
    done

    if is_ccbot_running; then
        echo "Process did not exit after ${MAX_WAIT}s, sending SIGTERM..."
        # Kill the uv wrapper directly (pgrep is portable across macOS/Linux)
        # `| head -1` 은 set -o pipefail 에서 SIGPIPE 로 스크립트를 죽인다(위 참고)
        UV_PID="$(first_line "$(pgrep -f 'uv run ccbot' || true)")"
        if [ -n "$UV_PID" ]; then
            kill "$UV_PID" 2>/dev/null || true
            sleep 2
        fi
        if is_ccbot_running; then
            echo "Process still running, sending SIGKILL..."
            kill -9 "$UV_PID" 2>/dev/null || true
            sleep 1
        fi
    fi

    echo "Process stopped."
else
    echo "No ccbot process running in $TARGET"
fi

# Brief pause to let the shell settle
sleep 1

# Start ccbot
echo "Starting ccbot in $TARGET..."
tmux send-keys -t "$TARGET" "cd ${PROJECT_DIR} && uv run ccbot" Enter

# Verify startup and show logs
sleep 3
if is_ccbot_running; then
    echo "ccbot restarted successfully. Recent logs:"
    echo "----------------------------------------"
    tmux capture-pane -t "$TARGET" -p | tail -20
    echo "----------------------------------------"
else
    echo "Warning: ccbot may not have started. Pane output:"
    echo "----------------------------------------"
    tmux capture-pane -t "$TARGET" -p | tail -30
    echo "----------------------------------------"
    exit 1
fi
