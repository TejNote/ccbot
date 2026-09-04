"""Tmux session/window management via libtmux.

Wraps libtmux to provide async-friendly operations on a single tmux session:
  - list_windows / find_window_by_name: discover Claude Code windows.
  - capture_pane: read terminal content (plain or with ANSI colors).
  - send_keys: forward user input or control keys to a window.
  - create_window / kill_window: lifecycle management.

All blocking libtmux calls are wrapped in asyncio.to_thread().

Key class: TmuxManager (singleton instantiated as `tmux_manager`).
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import subprocess
from dataclasses import dataclass
from pathlib import Path

import libtmux

from .config import SENSITIVE_ENV_VARS, config

logger = logging.getLogger(__name__)

# Claude session IDs are UUIDs (JSONL filename stems)
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


# `list_windows` 가 tmux 에서 직접 읽는 필드. 순서가 파싱 순서다.
_WIN_FIELDS = ("window_id", "window_name", "pane_current_path", "pane_current_command")
# 필드 구분자. 🚨 개행을 레코드 구분자로 쓰면 안 된다 — 값 안에 개행이 들어올 수 있고
#    (개행 든 디렉터리의 `pane_current_path`) 그게 libtmux #752 의 원인이다.
#    모든 필드를 이 문자로 «종단» 하고, 개수로 재조립한다(업스트림 수정과 같은 방식).
_FS = "\x1f"


@dataclass
class TmuxWindow:
    """Information about a tmux window."""

    window_id: str
    window_name: str
    cwd: str  # Current working directory
    pane_current_command: str = ""  # Process running in active pane


def parse_window_records(stdout: str) -> list[TmuxWindow] | None:
    """`list-windows -F` 출력을 TmuxWindow 목록으로 파싱한다.

    🚨 개행을 레코드 구분자로 쓰지 않는다. 값 안에 개행이 들어올 수 있고
       (개행 든 디렉터리의 `pane_current_path`) 줄 단위로 쪼개면 레코드가 갈라진다 —
       그게 libtmux #752 의 원인이다. 모든 필드가 `_FS` 로 종단되므로 한 레코드는
       정확히 `len(_WIN_FIELDS)` 개의 구분자를 갖는다. 개수로 재조립하면 값에 개행이
       몇 개 있든, 어디에 있든 안전하다.

    Returns:
        TmuxWindow 목록. 값 개수가 레코드 배수가 아니면(= 어떤 값이 구분자를 품었다)
        조용히 어긋난 결과를 내놓지 않고 None 을 준다.
    """
    vals = stdout.split(_FS)
    # 마지막 필드 뒤 종단 구분자가 남긴 꼬리(마지막 레코드의 개행)를 떼어낸다
    if vals and vals[-1].strip("\n") == "":
        vals.pop()
    if not vals:
        return []
    n = len(_WIN_FIELDS)
    if len(vals) % n:
        return None

    windows: list[TmuxWindow] = []
    for i in range(0, len(vals), n):
        rec = vals[i : i + n]
        # 앞 레코드를 끝낸 개행이 다음 레코드 첫 값에 붙어 온다 — 구분자로서 뗀다
        if i and rec[0].startswith("\n"):
            rec[0] = rec[0][1:]
        wid, name, cwd, pane_cmd = rec
        if name == config.tmux_main_window_name:
            continue  # 자리표시자 main 창은 제외
        windows.append(
            TmuxWindow(
                window_id=wid, window_name=name, cwd=cwd, pane_current_command=pane_cmd
            )
        )
    return windows


class TmuxManager:
    """Manages tmux windows for Claude Code sessions."""

    def __init__(self, session_name: str | None = None):
        """Initialize tmux manager.

        Args:
            session_name: Name of the tmux session to use (default from config)
        """
        self.session_name = session_name or config.tmux_session_name
        self._server: libtmux.Server | None = None

    @property
    def server(self) -> libtmux.Server:
        """Get or create tmux server connection."""
        if self._server is None:
            self._server = libtmux.Server()
        return self._server

    # 🚨 libtmux 의 창·pane 열거를 타지 않는 직접 호출 경로.
    #
    #    왜 필요한가 — libtmux 는 tmux 에 126개 필드를 `␞` 로 이어 달라고 하고
    #    **출력을 줄 단위로** 레코드 하나로 본다. 그런데 어떤 필드 값에 개행이 들어가면
    #    (예: `pane_current_path` 가 개행 든 디렉터리) 레코드가 두 줄로 쪼개져
    #    `neo.py` 의 `zip(..., strict=True)` 가 터진다 —
    #    `ValueError: zip() argument 2 is shorter than argument 1`.
    #    upstream libtmux #752 로 보고돼 있고 **PR 미머지**다(0.55.1~0.62.0 전부 영향).
    #    실측: 2026-09-02~09-04 사흘간 11건, 창 @0~@5 에 흩어져 발생.
    #
    #    피해가 상태줄 갱신에서 끝나지 않는다 — 텔레그램 → tmux 주입도 같은 열거를
    #    타므로, 개행 든 값이 지속되면 **메시지 전달이 막힌다**.
    #    tmux 명령은 `-t @N` 으로 창을 직접 받고 활성 pane 을 자동 타게팅하므로
    #    애초에 열거가 필요 없다. 부수 효과로 3배 빠르다(실측 13.7ms → 4.5ms).
    #    ⚠️ `send-keys -l` 은 `--` 를 붙인다. 없으면 `-` 로 시작하는 문자열이
    #       플래그로 파싱된다(실측: `-x` → `unknown flag -x`). libtmux 는 안 붙인다.
    def _tmux(self, *args: str, timeout: float = 5.0) -> bool:
        """tmux 명령을 직접 실행한다. 성공 여부만 돌려준다."""
        try:
            subprocess.run(["tmux", *args], check=True, timeout=timeout)
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
            # ⚠️ ERROR 가 아니라 WARNING 이다. 창을 찾은 뒤 실제 명령을 보내는 사이에
            #    사용자가 그 창을 닫는 정상 레이스가 있고, 예전 구현은 그 경우 로그를
            #    아예 남기지 않았다. ERROR 로 올리면 정상 상황이 소음이 된다(리뷰 지적).
            logger.warning("tmux %s 실패: %s", " ".join(args[:3]), e)
            return False

    def get_session(self) -> libtmux.Session | None:
        """Get the tmux session if it exists."""
        try:
            return self.server.sessions.get(session_name=self.session_name)
        except Exception:
            return None

    def get_or_create_session(self) -> libtmux.Session:
        """Get existing session or create a new one."""
        session = self.get_session()
        if session:
            self._scrub_session_env(session)
            return session

        # Create new session with main window named specifically
        session = self.server.new_session(
            session_name=self.session_name,
            start_directory=str(Path.home()),
        )
        # Rename the default window to the main window name
        if session.windows:
            session.windows[0].rename_window(config.tmux_main_window_name)
        self._scrub_session_env(session)
        return session

    @staticmethod
    def _scrub_session_env(session: libtmux.Session) -> None:
        """Remove sensitive env vars from the tmux session environment.

        Prevents new windows (and their child processes like Claude Code)
        from inheriting secrets such as TELEGRAM_BOT_TOKEN.
        """
        for var in SENSITIVE_ENV_VARS:
            try:
                session.unset_environment(var)
            except Exception:
                pass  # var not set in session env — nothing to remove

    async def list_windows(self) -> list[TmuxWindow]:
        """List all windows in the session with their working directories.

        Returns:
            List of TmuxWindow with window info and cwd
        """

        # 🚨 이 함수가 delivery 의 진짜 핫패스다 — `find_window_by_id` 가 이걸 부르고,
        #    `session.py` 의 `send_to_window` 와 `bot.py` 의 십여 곳이 실제 전송 «전에»
        #    그걸 먼저 부른다. 예전 구현은 `for window in session.windows:` 가 try 밖에
        #    있어서, libtmux #752 가 터지면 예외가 여기서 그대로 전파됐다.
        #    `bot.py` 핸들러에는 이걸 감싸는 try 도, 전역 error_handler 도 없다 —
        #    즉 그 메시지는 조용히 유실된다. 그래서 열거를 통째로 걷어낸다.
        #    (2026-09-04 리뷰 지적. 초안은 «마지막 액션» 만 고쳐 놓고 전달 경로가
        #     해결됐다고 적었는데, 창을 찾는 단계가 그대로 노출돼 있었다.)
        fmt = "".join("#{" + f + "}" + _FS for f in _WIN_FIELDS)
        try:
            proc = await asyncio.create_subprocess_exec(
                "tmux",
                "list-windows",
                "-t",
                self.session_name,
                "-F",
                fmt,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
        except OSError as e:
            logger.error("list-windows 실행 실패: %s", e)
            return []
        if proc.returncode != 0:
            logger.debug(
                "list-windows 실패: %s",
                stderr.decode("utf-8", errors="replace").strip(),
            )
            return []

        parsed = parse_window_records(stdout.decode("utf-8", errors="replace"))
        if parsed is None:
            logger.error(
                "list-windows 파싱 실패: 값 개수가 레코드 배수가 아니다 "
                "(어떤 필드가 구분자를 포함한다)"
            )
            return []
        return parsed

    async def find_window_by_name(self, window_name: str) -> TmuxWindow | None:
        """Find a window by its name.

        Args:
            window_name: The window name to match

        Returns:
            TmuxWindow if found, None otherwise
        """
        windows = await self.list_windows()
        for window in windows:
            if window.window_name == window_name:
                return window
        logger.debug("Window not found by name: %s", window_name)
        return None

    async def find_window_by_id(self, window_id: str) -> TmuxWindow | None:
        """Find a window by its tmux window ID (e.g. '@0', '@12').

        Args:
            window_id: The tmux window ID to match

        Returns:
            TmuxWindow if found, None otherwise
        """
        windows = await self.list_windows()
        for window in windows:
            if window.window_id == window_id:
                return window
        logger.debug("Window not found by id: %s", window_id)
        return None

    async def capture_pane(self, window_id: str, with_ansi: bool = False) -> str | None:
        """Capture the visible text content of a window's active pane.

        Args:
            window_id: The window ID to capture
            with_ansi: If True, capture with ANSI color codes

        Returns:
            The captured text, or None on failure.
        """
        if with_ansi:
            # Use async subprocess to call tmux capture-pane -e for ANSI colors
            try:
                proc = await asyncio.create_subprocess_exec(
                    "tmux",
                    "capture-pane",
                    "-e",
                    "-p",
                    "-t",
                    window_id,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                if proc.returncode == 0:
                    return stdout.decode("utf-8")
                logger.error(
                    f"Failed to capture pane {window_id}: {stderr.decode('utf-8')}"
                )
                return None
            except Exception as e:
                logger.error(f"Unexpected error capturing pane {window_id}: {e}")
                return None

        # 평문도 ANSI 분기와 같이 직접 호출한다 (libtmux 열거 우회 — 위 _tmux 주석 참고)
        try:
            proc = await asyncio.create_subprocess_exec(
                "tmux",
                "capture-pane",
                "-p",
                "-t",
                window_id,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                # libtmux 의 capture_pane() 은 줄 리스트를 주고 우리가 join 했다.
                # tmux 는 끝에 개행을 붙이므로 그 하나만 떼어 같은 문자열이 되게 한다.
                return stdout.decode("utf-8", errors="replace").rstrip("\n")
            logger.error(
                f"Failed to capture pane {window_id}: {stderr.decode('utf-8', errors='replace')}"
            )
            return None
        except Exception as e:
            logger.error(f"Failed to capture pane {window_id}: {e}")
            return None

    async def _send_via_paste(self, window_id: str, text: str) -> bool:
        """Deliver text through tmux paste-buffer, then fire Enter.

        Codex's composer handles bracketed paste more reliably than direct
        send-keys for full Telegram messages. The trailing Enter submits after
        the paste has been processed.
        """

        def _do_paste() -> bool:
            # `-t <window_id>` 가 그 창의 활성 pane 을 타게팅한다 — pane 조회 불필요
            target = window_id
            try:
                buf_name = f"ccbot-{secrets.token_hex(4)}"
                subprocess.run(
                    ["tmux", "set-buffer", "-b", buf_name, "--", text],
                    check=True,
                    timeout=5,
                )
                try:
                    # `-d` 는 paste 가 성공했을 때만 버퍼를 지운다. 창이 이미 닫혔으면
                    # paste 가 실패하고 **사용자 메시지 전문이 담긴 버퍼가 남는다** —
                    # 예전 구현은 창·pane 조회가 먼저 실패해 이 경로 자체가 없었다.
                    # (2026-09-04 리뷰 지적) 그래서 실패 시 명시적으로 지운다.
                    subprocess.run(
                        ["tmux", "paste-buffer", "-b", buf_name, "-t", target, "-d"],
                        check=True,
                        timeout=5,
                    )
                except Exception:
                    subprocess.run(
                        ["tmux", "delete-buffer", "-b", buf_name],
                        check=False,
                        timeout=5,
                    )
                    raise
                return True
            except subprocess.CalledProcessError as e:
                logger.error(f"tmux paste failed for {window_id}: {e}")
                return False
            except Exception as e:
                logger.error(f"Failed to paste to window {window_id}: {e}")
                return False

        def _send_enter() -> bool:
            return self._tmux("send-keys", "-t", window_id, "Enter")

        if not await asyncio.to_thread(_do_paste):
            return False
        await asyncio.sleep(0.5)
        return await asyncio.to_thread(_send_enter)

    async def send_keys(
        self,
        window_id: str,
        text: str,
        enter: bool = True,
        literal: bool = True,
        use_paste: bool = False,
    ) -> bool:
        """Send keys to a specific window.

        Args:
            window_id: The window ID to send to
            text: Text to send
            enter: Whether to press enter after the text
            literal: If True, send text literally. If False, interpret special keys
                     like "Up", "Down", "Left", "Right", "Escape", "Enter".
            use_paste: When True, route literal text through tmux paste-buffer.

        Returns:
            True if successful, False otherwise
        """
        if literal and enter and use_paste:
            return await self._send_via_paste(window_id, text)

        if literal and enter:
            # Split into text + delay + Enter via libtmux.
            # Claude Code's TUI sometimes interprets a rapid-fire Enter
            # (arriving in the same input batch as the text) as a newline
            # rather than submit.  A 500ms gap lets the TUI process the
            # text before receiving Enter.
            def _send_literal(chars: str) -> bool:
                # `--` 필수 — 없으면 `-` 로 시작하는 문자열이 플래그로 파싱된다
                return self._tmux("send-keys", "-t", window_id, "-l", "--", chars)

            def _send_enter() -> bool:
                return self._tmux("send-keys", "-t", window_id, "Enter")

            # Claude Code's ! command mode: send "!" first so the TUI
            # switches to bash mode, wait 1s, then send the rest.
            if text.startswith("!"):
                if not await asyncio.to_thread(_send_literal, "!"):
                    return False
                rest = text[1:]
                if rest:
                    await asyncio.sleep(1.0)
                    if not await asyncio.to_thread(_send_literal, rest):
                        return False
            else:
                if not await asyncio.to_thread(_send_literal, text):
                    return False
            await asyncio.sleep(0.5)
            return await asyncio.to_thread(_send_enter)

        # Other cases: special keys (literal=False) or no-enter
        def _sync_send_keys() -> bool:
            # libtmux 의 send_keys(literal=…) 를 그대로 옮긴 것이다 —
            #   literal=True  → `send-keys -l -- <text>` (우리는 `--` 를 더 붙인다)
            #   literal=False → `send-keys <text>`        (Up·Escape 같은 키 이름 해석)
            # 그리고 enter=True 면 별도 `Enter` 를 보낸다(libtmux 의 .enter() 와 동일).
            if text:
                args = (
                    ("send-keys", "-t", window_id, "-l", "--", text)
                    if literal
                    else ("send-keys", "-t", window_id, text)
                )
                if not self._tmux(*args):
                    return False
            if enter:
                return self._tmux("send-keys", "-t", window_id, "Enter")
            return True

        return await asyncio.to_thread(_sync_send_keys)

    async def rename_window(self, window_id: str, new_name: str) -> bool:
        """Rename a tmux window by its ID."""

        def _sync_rename() -> bool:
            # `--` 로 옵션 파싱을 끊는다 — 창 이름이 `-` 로 시작할 수 있다
            if not self._tmux("rename-window", "-t", window_id, "--", new_name):
                return False
            logger.info("Renamed window %s to '%s'", window_id, new_name)
            return True

        return await asyncio.to_thread(_sync_rename)

    async def kill_window(self, window_id: str) -> bool:
        """Kill a tmux window by its ID."""

        def _sync_kill() -> bool:
            if not self._tmux("kill-window", "-t", window_id):
                return False
            logger.info("Killed window %s", window_id)
            return True

        return await asyncio.to_thread(_sync_kill)

    async def create_window(
        self,
        work_dir: str,
        window_name: str | None = None,
        start_claude: bool = True,
        resume_session_id: str | None = None,
    ) -> tuple[bool, str, str, str]:
        """Create a new tmux window and optionally start Claude Code.

        Args:
            work_dir: Working directory for the new window
            window_name: Optional window name (defaults to directory name)
            start_claude: Whether to start claude command
            resume_session_id: If set, append --resume <id> to claude command

        Returns:
            Tuple of (success, message, window_name, window_id)
        """
        # Validate directory first
        path = Path(work_dir).expanduser().resolve()
        if not path.exists():
            return False, f"Directory does not exist: {work_dir}", "", ""
        if not path.is_dir():
            return False, f"Not a directory: {work_dir}", "", ""

        # resume_session_id is interpolated into a shell command line below;
        # it comes from JSONL filenames on disk, but validate defensively —
        # Claude session IDs are always UUIDs.
        if resume_session_id and not _UUID_RE.fullmatch(resume_session_id):
            logger.error("Rejecting non-UUID resume_session_id: %r", resume_session_id)
            return False, "Invalid session ID for resume", "", ""

        # Create window name, adding suffix if name already exists
        final_window_name = window_name if window_name else path.name

        # Check for existing window name
        base_name = final_window_name
        counter = 2
        while await self.find_window_by_name(final_window_name):
            final_window_name = f"{base_name}-{counter}"
            counter += 1

        # Create window in thread
        def _create_and_start() -> tuple[bool, str, str, str]:
            session = self.get_or_create_session()
            try:
                # Create new window
                window = session.new_window(
                    window_name=final_window_name,
                    start_directory=str(path),
                )

                wid = window.window_id or ""

                # Prevent Claude Code from overriding window name
                window.set_window_option("allow-rename", "off")

                # Start Claude Code if requested
                if start_claude:
                    pane = window.active_pane
                    if pane:
                        cmd = config.claude_command
                        if resume_session_id:
                            cmd = f"{cmd} --resume {resume_session_id}"
                        pane.send_keys(cmd, enter=True)

                logger.info(
                    "Created window '%s' (id=%s) at %s",
                    final_window_name,
                    wid,
                    path,
                )
                return (
                    True,
                    f"Created window '{final_window_name}' at {path}",
                    final_window_name,
                    wid,
                )

            except Exception as e:
                logger.error(f"Failed to create window: {e}")
                return False, f"Failed to create window: {e}", "", ""

        return await asyncio.to_thread(_create_and_start)


# Global instance with default session name
tmux_manager = TmuxManager()
