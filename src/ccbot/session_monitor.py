"""Session monitoring service — watches JSONL files for new messages.

Runs an async polling loop that:
  1. Loads the current session_map to know which sessions to watch.
  2. Detects session_map changes (new/changed/deleted windows) and cleans up.
  3. Reads new JSONL lines from each session file using byte-offset tracking.
  4. Parses entries via TranscriptParser and emits NewMessage objects to a callback.

Optimizations: mtime cache skips unchanged files; byte offset avoids re-reading.

Key classes: SessionMonitor, NewMessage, SessionInfo.
"""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Awaitable

import aiofiles

from .config import config
from .monitor_state import MonitorState, TrackedSession
from .tmux_manager import tmux_manager
from .transcript_parser import TranscriptParser
from .utils import read_cwd_from_jsonl

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# --- _auto_detect_session_changes 의 시간 임계값 ---
# 훅·세션 피커가 방금 등록한 세션은 jsonl 이 아직 없다. 그 사이에 «낡았다» 로 보고
# 갈아타면 살아 있는 세션을 버리므로, 파일이 생길 때까지 이만큼 기다린다.
NEW_SESSION_GRACE_SEC = 120.0
# 재채택 거부가 이만큼 이어지면 우리 판단을 의심한다(아래 두 조건이 함께 성립할 때만).
READOPT_FORCE_AFTER_SEC = 600.0
# 추적 중인 세션이 이만큼 안 자라면 죽은 것으로 본다.
CURRENT_DEAD_AFTER_SEC = 300.0
# 후보가 이 안에 자랐으면 살아 있는 것으로 본다.
CANDIDATE_ALIVE_WITHIN_SEC = 180.0
# 같은 경고를 이 주기로만 반복한다. 폴링이 2초라 무제한이면 로그가 쌓이고,
# 「한 번만」이면 반대로 오래 갇힌 상태가 사실상 안 보인다(둘 다 겪었다).
REWARN_SEC = 600.0
# cwd 로 계산한 폴더에 sid 의 jsonl 이 없을 때, projects/ 전체를 되짚는 주기.
# 매 폴링(2초)마다 glob 하면 프로젝트 폴더 수만큼 stat 이 돈다.
SID_RESCAN_SEC = 30.0


@dataclass
class SessionInfo:
    """Information about a Claude Code session."""

    session_id: str
    file_path: Path


@dataclass
class NewMessage:
    """A new message detected by the monitor."""

    session_id: str
    text: str
    is_complete: bool  # True when stop_reason is set (final message)
    content_type: str = "text"  # "text" or "thinking"
    tool_use_id: str | None = None
    role: str = "assistant"  # "user" or "assistant"
    tool_name: str | None = None  # For tool_use messages, the tool name
    image_data: list[tuple[str, bytes]] | None = None  # From tool_result images


class SessionMonitor:
    """Monitors Claude Code sessions for new assistant messages.

    Uses simple async polling with aiofiles for non-blocking I/O.
    Emits both intermediate and complete assistant messages.
    """

    def __init__(
        self,
        projects_path: Path | None = None,
        poll_interval: float | None = None,
        state_file: Path | None = None,
    ):
        self.projects_path = (
            projects_path if projects_path is not None else config.claude_projects_path
        )
        self.poll_interval = (
            poll_interval if poll_interval is not None else config.monitor_poll_interval
        )

        self.state = MonitorState(state_file=state_file or config.monitor_state_file)
        self.state.load()

        self._running = False
        self._task: asyncio.Task | None = None
        self._message_callback: Callable[[NewMessage], Awaitable[None]] | None = None
        # Per-session pending tool_use state carried across poll cycles
        self._pending_tools: dict[str, dict[str, Any]] = {}  # session_id -> pending
        # Track last known session_map for detecting changes
        # Keys may be window_id (@12) or window_name (old format) during transition
        self._last_session_map: dict[str, str] = {}  # window_key -> session_id
        # In-memory mtime cache for quick file change detection (not persisted)
        self._file_mtimes: dict[str, float] = {}  # session_id -> last_seen_mtime
        # Cache for auto-detect: skip dir scan when tracked JSONL is actively growing
        # 🚨 키는 (window_key, sid) 다. 창 키만 쓰면 «앞 세션» 의 mtime 과 비교하게 되고,
        #    훅이 방금 등록한 새 세션의 첫 폴링이 곧바로 «낡았다» 로 판정된다.
        self._auto_detect_mtimes: dict[tuple[str, str], float] = {}
        # 한 번 갈아타며 버린 session_id 를 창별로 기억한다 — 되돌아가지 않기 위해서다.
        # 왜: _auto_detect_session_changes 는 cwd 하나에 세션 하나를 전제한다. 같은 cwd 에
        #     살아 있는 세션이 둘이면 방금 입력한 쪽이 계속 «최신» 이 되어 session_map 이
        #     영원히 왕복한다(2026-08-31 실측: 20~40초마다 교대, 두 세션 출력이 한 토픽에 섞였다).
        #     시각 기반 판정으로는 「/clear 직후」와 「두 세션 동시 생존」을 구분할 수 없다 —
        #     둘 다 «옛 파일이 방금까지 자랐고 새 파일이 자란다» 로 똑같이 보인다.
        #     그래서 시각이 아니라 **되돌아감 금지**로 막는다. /clear 는 한 번만 갈아타면
        #     되므로 그 기능은 그대로 살고, 왕복은 원리적으로 불가능해진다.
        # ⚠️ 단 예외가 하나 있다 — `_suspect_abandoned` 의 자기 치유다. 「우리가 버린 게
        #    아니라 훅이 등록해 둔 세션을 버린」 경우에 한해, 연속 거부가 이어지면 되돌아간다.
        #    그 좁은 구멍이 없으면 살아 있는 세션을 버린 실수가 영구화된다(2026-09-02 사고).
        self._abandoned_sids: dict[str, set[str]] = {}  # window_key -> 버린 sid 들
        # 우리가 auto-detect 로 채택한 sid. 현재 값이 이것과 다르면 훅·세션 피커가
        # 명시적으로 바꾼 것이므로, 그 창의 «버린 세션» 기억을 비운다(의도된 전환은 늘 통한다).
        self._adopted_sids: dict[str, str] = {}  # window_key -> 우리가 채택한 sid
        # 재채택 거부 경고를 창·후보 조합당 한 번만 낸다 — 폴링이 2초라 그냥 두면
        # 유휴 상태에서도 영원히 같은 경고가 찍힌다(2026-08-31 리뷰 지적).
        # «우리가 채택한 적 없는» sid 를 버린 경우만 여기 담는다 = 훅·세션 피커가
        # 등록해 둔 것을 우리가 덮은 것이므로, 우리 판단이 틀렸을 수 있는 후보다.
        # 🚨 자기 치유는 이 집합만 대상으로 한다. 우리가 스스로 채택했다가 버린 sid 로는
        #    절대 되돌아가지 않는다 — 그러면 왕복이 되살아난다(리뷰 지적, 실측 재현됨:
        #    살아 있지만 5분 조용한 세션에서 946초 만에 튀었다).
        self._suspect_abandoned: dict[str, set[str]] = {}
        # 경고를 마지막으로 낸 시각(창·후보 조합별). 값이 시각인 이유는 위 REWARN_SEC 참고.
        self._warned_readopt: dict[tuple[str, str], float] = {}
        # (창, sid) 를 session_map 에서 처음 본 시각. jsonl 이 아직 없는 새 세션에
        # 유예를 주기 위한 기준점이다.
        self._sid_first_seen: dict[tuple[str, str], float] = {}
        # 재채택을 처음 거부한 시각(창·후보 조합별). 자기 치유의 기준점이다.
        self._refused_since: dict[tuple[str, str], float] = {}
        # sid → 실제 jsonl 경로. cwd 로 계산한 폴더에 없을 때 되짚은 결과를 캐시한다.
        self._sid_jsonl_cache: dict[str, Path] = {}
        # sid → 마지막으로 projects/ 전체를 훑은 시각(못 찾은 경우 포함).
        self._sid_scan_at: dict[str, float] = {}

    def set_message_callback(
        self, callback: Callable[[NewMessage], Awaitable[None]]
    ) -> None:
        self._message_callback = callback

    async def _get_active_cwds(self) -> set[str]:
        """Get normalized cwds of all active tmux windows."""
        cwds = set()
        windows = await tmux_manager.list_windows()
        for w in windows:
            try:
                cwds.add(str(Path(w.cwd).resolve()))
            except (OSError, ValueError):
                cwds.add(w.cwd)
        return cwds

    async def scan_projects(self) -> list[SessionInfo]:
        """Scan projects that have active tmux windows."""
        active_cwds = await self._get_active_cwds()
        if not active_cwds:
            return []

        sessions = []

        if not self.projects_path.exists():
            return sessions

        for project_dir in self.projects_path.iterdir():
            if not project_dir.is_dir():
                continue

            index_file = project_dir / "sessions-index.json"
            original_path = ""
            indexed_ids: set[str] = set()

            if index_file.exists():
                try:
                    async with aiofiles.open(index_file, "r") as f:
                        content = await f.read()
                    index_data = json.loads(content)
                    entries = index_data.get("entries", [])
                    original_path = index_data.get("originalPath", "")

                    for entry in entries:
                        session_id = entry.get("sessionId", "")
                        full_path = entry.get("fullPath", "")
                        project_path = entry.get("projectPath", original_path)

                        if not session_id or not full_path:
                            continue

                        try:
                            norm_pp = str(Path(project_path).resolve())
                        except (OSError, ValueError):
                            norm_pp = project_path
                        if norm_pp not in active_cwds:
                            continue

                        indexed_ids.add(session_id)
                        file_path = Path(full_path)
                        if file_path.exists():
                            sessions.append(
                                SessionInfo(
                                    session_id=session_id,
                                    file_path=file_path,
                                )
                            )

                except (json.JSONDecodeError, OSError) as e:
                    logger.debug(f"Error reading index {index_file}: {e}")

            # Pick up un-indexed .jsonl files
            try:
                for jsonl_file in project_dir.glob("*.jsonl"):
                    session_id = jsonl_file.stem
                    if session_id in indexed_ids:
                        continue

                    # Determine project_path for this file
                    file_project_path = original_path
                    if not file_project_path:
                        file_project_path = await asyncio.to_thread(
                            read_cwd_from_jsonl, jsonl_file
                        )
                    if not file_project_path:
                        dir_name = project_dir.name
                        if dir_name.startswith("-"):
                            file_project_path = dir_name.replace("-", "/")

                    try:
                        norm_fp = str(Path(file_project_path).resolve())
                    except (OSError, ValueError):
                        norm_fp = file_project_path

                    if norm_fp not in active_cwds:
                        continue

                    sessions.append(
                        SessionInfo(
                            session_id=session_id,
                            file_path=jsonl_file,
                        )
                    )
            except OSError as e:
                logger.debug(f"Error scanning jsonl files in {project_dir}: {e}")

        return sessions

    async def _read_new_lines(
        self, session: TrackedSession, file_path: Path
    ) -> list[dict]:
        """Read new lines from a session file using byte offset for efficiency.

        Detects file truncation (e.g. after /clear) and resets offset.
        Recovers from corrupted offsets (mid-line) by scanning to next line.
        """
        new_entries = []
        try:
            async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                # Get file size to detect truncation
                await f.seek(0, 2)  # Seek to end
                file_size = await f.tell()

                # Detect file truncation: if offset is beyond file size, reset
                if session.last_byte_offset > file_size:
                    logger.info(
                        "File truncated for session %s "
                        "(offset %d > size %d). Resetting.",
                        session.session_id,
                        session.last_byte_offset,
                        file_size,
                    )
                    session.last_byte_offset = 0

                # Seek to last read position for incremental reading
                await f.seek(session.last_byte_offset)

                # Detect corrupted offset: if we're mid-line (not at '{'),
                # scan forward to the next line start. This can happen if
                # the state file was manually edited or corrupted.
                if session.last_byte_offset > 0:
                    first_char = await f.read(1)
                    if first_char and first_char != "{":
                        logger.warning(
                            "Corrupted offset %d in session %s (mid-line), "
                            "scanning to next line",
                            session.last_byte_offset,
                            session.session_id,
                        )
                        await f.readline()  # Skip rest of partial line
                        session.last_byte_offset = await f.tell()
                        return []
                    await f.seek(session.last_byte_offset)  # Reset for normal read

                # Read only new lines from the offset.
                # Track safe_offset: only advance past lines that parsed
                # successfully. A non-empty line without a trailing newline is
                # a partial write at EOF — retry next cycle. A COMPLETE line
                # (newline-terminated) that fails to parse is permanently
                # corrupt; skip it, or it would stall this session forever.
                safe_offset = session.last_byte_offset
                async for line in f:
                    data = TranscriptParser.parse_line(line)
                    if data:
                        new_entries.append(data)
                        safe_offset = await f.tell()
                    elif line.strip():
                        if line.endswith("\n"):
                            logger.warning(
                                "Skipping unparseable JSONL line in session %s "
                                "(%d bytes)",
                                session.session_id,
                                len(line),
                            )
                            safe_offset = await f.tell()
                        else:
                            # Partial write at EOF — don't advance offset
                            logger.debug(
                                "Partial JSONL line in session %s, "
                                "will retry next cycle",
                                session.session_id,
                            )
                            break
                    else:
                        # Empty line — safe to skip
                        safe_offset = await f.tell()

                session.last_byte_offset = safe_offset

        except OSError as e:
            logger.error("Error reading session file %s: %s", file_path, e)
        return new_entries

    async def check_for_updates(self, active_session_ids: set[str]) -> list[NewMessage]:
        """Check all sessions for new assistant messages.

        Reads from last byte offset. Emits both intermediate
        (stop_reason=null) and complete messages.

        Args:
            active_session_ids: Set of session IDs currently in session_map
        """
        new_messages = []

        # Scan projects to get available session files
        sessions = await self.scan_projects()

        # Only process sessions that are in session_map
        for session_info in sessions:
            if session_info.session_id not in active_session_ids:
                continue
            try:
                tracked = self.state.get_session(session_info.session_id)

                if tracked is None:
                    # For new sessions, initialize offset to end of file
                    # to avoid re-processing old messages
                    try:
                        file_size = session_info.file_path.stat().st_size
                        current_mtime = session_info.file_path.stat().st_mtime
                    except OSError:
                        file_size = 0
                        current_mtime = 0.0
                    tracked = TrackedSession(
                        session_id=session_info.session_id,
                        file_path=str(session_info.file_path),
                        last_byte_offset=file_size,
                    )
                    self.state.update_session(tracked)
                    self._file_mtimes[session_info.session_id] = current_mtime
                    logger.info(f"Started tracking session: {session_info.session_id}")
                    continue

                # Check mtime + file size to see if file has changed
                try:
                    st = session_info.file_path.stat()
                    current_mtime = st.st_mtime
                    current_size = st.st_size
                except OSError:
                    continue

                last_mtime = self._file_mtimes.get(session_info.session_id, 0.0)
                if (
                    current_mtime <= last_mtime
                    and current_size <= tracked.last_byte_offset
                ):
                    # File hasn't changed, skip reading
                    continue

                # File changed, read new content from last offset
                new_entries = await self._read_new_lines(
                    tracked, session_info.file_path
                )
                self._file_mtimes[session_info.session_id] = current_mtime

                if new_entries:
                    logger.debug(
                        f"Read {len(new_entries)} new entries for "
                        f"session {session_info.session_id}"
                    )

                # Parse new entries using the shared logic, carrying over pending tools
                carry = self._pending_tools.get(session_info.session_id, {})
                parsed_entries, remaining = TranscriptParser.parse_entries(
                    new_entries,
                    pending_tools=carry,
                )
                if remaining:
                    self._pending_tools[session_info.session_id] = remaining
                else:
                    self._pending_tools.pop(session_info.session_id, None)

                for entry in parsed_entries:
                    if not entry.text and not entry.image_data:
                        continue
                    # Skip user messages unless show_user_messages is enabled
                    if entry.role == "user" and not config.show_user_messages:
                        continue
                    new_messages.append(
                        NewMessage(
                            session_id=session_info.session_id,
                            text=entry.text,
                            is_complete=True,
                            content_type=entry.content_type,
                            tool_use_id=entry.tool_use_id,
                            role=entry.role,
                            tool_name=entry.tool_name,
                            image_data=entry.image_data,
                        )
                    )

                self.state.update_session(tracked)

            except OSError as e:
                logger.debug(f"Error processing session {session_info.session_id}: {e}")

        self.state.save_if_dirty()
        return new_messages

    async def _load_current_session_map(self) -> dict[str, str]:
        """Load current session_map and return window_key -> session_id mapping.

        Keys in session_map are formatted as "tmux_session:window_id"
        (e.g. "ccbot:@12"). Old-format keys ("ccbot:window_name") are also
        accepted so that sessions running before a code upgrade continue
        to be monitored until the hook re-fires with new format.
        Only entries matching our tmux_session_name are processed.
        """
        window_to_session: dict[str, str] = {}
        if config.session_map_file.exists():
            try:
                async with aiofiles.open(config.session_map_file, "r") as f:
                    content = await f.read()
                session_map = json.loads(content)
                prefix = f"{config.tmux_session_name}:"
                for key, info in session_map.items():
                    # Only process entries for our tmux session
                    if not key.startswith(prefix):
                        continue
                    window_key = key[len(prefix) :]
                    session_id = info.get("session_id", "")
                    if session_id:
                        window_to_session[window_key] = session_id
            except (json.JSONDecodeError, OSError):
                pass
        return window_to_session

    async def _cleanup_all_stale_sessions(self) -> None:
        """Clean up all tracked sessions not in current session_map (used on startup)."""
        current_map = await self._load_current_session_map()
        active_session_ids = set(current_map.values())

        stale_sessions = []
        for session_id in self.state.tracked_sessions.keys():
            if session_id not in active_session_ids:
                stale_sessions.append(session_id)

        if stale_sessions:
            logger.info(
                f"[Startup cleanup] Removing {len(stale_sessions)} stale sessions"
            )
            for session_id in stale_sessions:
                self.state.remove_session(session_id)
                self._file_mtimes.pop(session_id, None)
            self.state.save_if_dirty()

    async def _detect_and_cleanup_changes(self) -> dict[str, str]:
        """Detect session_map changes and cleanup replaced/removed sessions.

        Returns current session_map for further processing.
        """
        current_map = await self._load_current_session_map()

        sessions_to_remove: set[str] = set()

        # Check for window session changes (window exists in both, but session_id changed)
        for window_id, old_session_id in self._last_session_map.items():
            new_session_id = current_map.get(window_id)
            if new_session_id and new_session_id != old_session_id:
                logger.info(
                    "Window '%s' session changed: %s -> %s",
                    window_id,
                    old_session_id,
                    new_session_id,
                )
                sessions_to_remove.add(old_session_id)

        # Check for deleted windows (window in old map but not in current)
        old_windows = set(self._last_session_map.keys())
        current_windows = set(current_map.keys())
        deleted_windows = old_windows - current_windows

        for window_id in deleted_windows:
            old_session_id = self._last_session_map[window_id]
            logger.info(
                "Window '%s' deleted, removing session %s",
                window_id,
                old_session_id,
            )
            sessions_to_remove.add(old_session_id)
            # auto-detect 쪽 창별 상태도 같이 버린다. tmux 창 ID(@N)는 재사용되므로
            # 남겨두면 새 창이 옛 창의 «버린 세션» 기억을 물려받는다.
            # ⚠️ 이 dict 들의 키는 «ccbot:@N» 전체다(_load_current_session_map 은
            #    prefix 를 떼고 @N 만 준다). 재구성하지 않으면 pop 이 조용히 no-op 이 된다.
            full_key = f"{config.tmux_session_name}:{window_id}"
            self._abandoned_sids.pop(full_key, None)
            self._suspect_abandoned.pop(full_key, None)
            self._adopted_sids.pop(full_key, None)
            # ⚠️ 아래 셋은 키가 (창, sid) 튜플이다 — pop 이 아니라 창 기준으로 걸러낸다.
            self._auto_detect_mtimes = {
                k: v for k, v in self._auto_detect_mtimes.items() if k[0] != full_key
            }
            self._sid_first_seen = {
                k: v for k, v in self._sid_first_seen.items() if k[0] != full_key
            }
            self._refused_since = {
                k: v for k, v in self._refused_since.items() if k[0] != full_key
            }
            self._warned_readopt = {
                k: v for k, v in self._warned_readopt.items() if k[0] != full_key
            }

        # Perform cleanup
        if sessions_to_remove:
            for session_id in sessions_to_remove:
                self.state.remove_session(session_id)
                self._file_mtimes.pop(session_id, None)
            self.state.save_if_dirty()

        # Update last known map
        self._last_session_map = current_map

        return current_map

    def _locate_session_jsonl(
        self, project_dir: Path, sid: str, now: float
    ) -> Path | None:
        """추적 중인 세션의 jsonl 을 찾는다. cwd 로 계산한 폴더가 1순위다.

        🚨 auto-detect 는 오래 「그 sid 의 jsonl 은 cwd 로 계산한 폴더에 있다」를
           전제했다. 훅이 그 창의 cwd 를 **하위 폴더로** 갱신하면 전제가 깨진다 —
           세션의 jsonl 은 시작 당시 cwd 로 만든 폴더에 그대로 있기 때문이다.
           그러면 mtime 0 → 「낡았다」 → 엉뚱한 폴더의 최신 파일로 갈아탄다.

           2026-09-03 17:10:59 @2 가 정확히 그랬다(로그 27644-27647) —
           훅이 cwd 를 `Metlife/insudeal-x-backend` 로 바꾼 3ms 뒤 auto-detect 가
           살아 있는 06b58ae5 를 버리고 그 폴더의 최신 파일(834296ff, **08-04**,
           한 달 전 죽은 세션)로 갈아탔다. metlife 토픽이 17시간 출력 0 이었다.

        그래서 못 찾으면 `projects/` 전체를 되짚는다. 매 폴링(2초)마다 훑으면
        프로젝트 폴더 수만큼 stat 이 도므로 `SID_RESCAN_SEC` 주기로 제한한다.
        """
        direct = project_dir / f"{sid}.jsonl"
        if direct.exists():
            return direct

        cached = self._sid_jsonl_cache.get(sid)
        if cached is not None and cached.exists():
            return cached

        if now - self._sid_scan_at.get(sid, 0.0) < SID_RESCAN_SEC:
            return None
        self._sid_scan_at[sid] = now  # 못 찾아도 기록한다 — 그래야 주기가 걸린다
        self._sid_jsonl_cache.pop(sid, None)
        for cand in self.projects_path.glob(f"*/{sid}.jsonl"):
            self._sid_jsonl_cache[sid] = cand
            return cand
        return None

    def _warn_periodically(
        self, wkey: tuple[str, str], now: float, msg: str, *args: object
    ) -> None:
        """같은 경고를 `REWARN_SEC` 주기로만 낸다.

        이 함수가 있는 이유는 두 번의 과잉 교정 때문이다 — 폴링이 2초라 무제한으로
        찍으면 유휴 상태에서도 로그가 무한히 쌓이고(2026-08-31), 「조합당 한 번만」
        찍으면 오래 갇힌 상태가 사실상 안 보인다(2026-09-03). 주기 재경고가 답이다.
        """
        last = self._warned_readopt.get(wkey, 0.0)
        if now - last < REWARN_SEC:
            return
        self._warned_readopt[wkey] = now
        logger.warning(msg, *args)

    def _prune_window_sid_state(self, key: str, keep_sid: str) -> None:
        """창 하나에 대해 «지금 추적하는 sid» 것만 남긴다.

        `_auto_detect_mtimes`·`_sid_first_seen` 은 키가 (창, sid) 다. 창은 그대로인데
        sid 는 `/clear` 마다 바뀌므로, 안 버리면 세션 교체마다 한 칸씩 쌓인다.
        읽을 때는 언제나 현재 sid 것만 보므로 나머지는 버려도 안전하다.
        """
        self._auto_detect_mtimes = {
            k: v
            for k, v in self._auto_detect_mtimes.items()
            if k[0] != key or k[1] == keep_sid
        }
        self._sid_first_seen = {
            k: v
            for k, v in self._sid_first_seen.items()
            if k[0] != key or k[1] == keep_sid
        }

    async def _auto_detect_session_changes(self) -> bool:
        """Detect session_id changes not caught by hook (e.g., /clear).

        For each window in session_map, check if a newer main JSONL exists
        in the project directory. If found, update session_map.json so the
        monitor picks up the new session automatically.

        갈아타기에는 가드가 셋 있다.
          1. **새 세션 유예** — 훅이 방금 등록해 jsonl 이 아직 없는 세션은 건드리지 않는다.
          2. **되돌아가기 금지** — 한 번 버린 sid 로는 돌아가지 않는다(왕복 방지).
          3. **자기 치유** — 단, 2번 때문에 살아 있는 세션을 영구히 놓친 상태가 감지되면
             (추적 중인 쪽은 죽었고 후보만 자란다) 시간을 두고 강제로 되돌린다.
             3번이 2번을 무력화하지 않도록 가드가 둘이다 — 「훅이 등록해 둔 것을 우리가
             덮은」 후보만 대상으로 하고(`_suspect_abandoned`), 거부 시계는 추적 파일이
             자랄 때마다 리셋해 「연속」 거부만 센다.
        """
        if not config.session_map_file.exists():
            return False

        try:
            async with aiofiles.open(config.session_map_file, "r") as f:
                raw = await f.read()
            session_map = json.loads(raw)
        except (json.JSONDecodeError, OSError):
            return False

        prefix = f"{config.tmux_session_name}:"
        changed = False

        for key, info in session_map.items():
            if not key.startswith(prefix):
                continue

            cwd = info.get("cwd", "")
            current_sid = info.get("session_id", "")
            if not cwd or not current_sid:
                continue

            # 현재 값이 «우리가 채택한 것» 과 다르면 훅이나 세션 피커가 명시적으로 바꿨다.
            # 그건 의도된 전환이므로 이 창의 되돌아가기-금지 기억을 비운다.
            # 없으면: 창 안에서 직접 /resume 했는데 훅이 실패한 경우, 과거에 한 번 버려진
            # 세션이면 영구히 재채택이 거부돼 **아무 신호 없이 대화가 멈춘 것처럼 보인다.**
            adopted = self._adopted_sids.get(key)
            if adopted is not None and current_sid != adopted:
                self._abandoned_sids.pop(key, None)
                self._suspect_abandoned.pop(key, None)
                self._adopted_sids.pop(key, None)
                self._warned_readopt = {
                    k: v for k, v in self._warned_readopt.items() if k[0] != key
                }
                self._refused_since = {
                    k: v for k, v in self._refused_since.items() if k[0] != key
                }

            # cwd → project dir (same convention as ~/.claude/projects/)
            project_dir = self.projects_path / ("-" + cwd.strip("/").replace("/", "-"))
            if not project_dir.exists():
                continue

            now = time.time()
            mkey = (key, current_sid)
            self._prune_window_sid_state(key, current_sid)
            first_seen = self._sid_first_seen.setdefault(mkey, now)

            # Current tracked JSONL mtime.
            # ⚠️ «파일이 없다» 와 «stat 이 실패했다» 를 구분한다. 둘 다 0 으로 뭉개면
            #    NFS 순단·권한 일시변경 같은 일시적 실패가 「낡았다」로 읽혀, 폴더의 더
            #    오래된 jsonl 조차 «더 최신» 으로 오판돼 **멀쩡한 세션이 교체된다.**
            #    그러면 정상 교체 INFO 만 남아 원인 흔적이 안 남는다(리뷰 지적).
            current_jsonl = self._locate_session_jsonl(project_dir, current_sid, now)
            if current_jsonl is None:
                current_mtime = 0.0
            else:
                try:
                    current_mtime = current_jsonl.stat().st_mtime
                except OSError as e:
                    logger.debug(
                        "stat 실패로 이번 폴링을 건너뛴다: %s (%s) — %s",
                        current_jsonl,
                        key,
                        e,
                    )
                    continue

                # jsonl 이 cwd 로 계산한 폴더 밖에 있다 = session_map 의 cwd 가 틀렸다.
                #
                # 🚨 여기서 `continue` 하면 안 된다. cwd 를 못 고치는 경우(jsonl 에 cwd
                #    필드가 없어 빈 문자열이 오거나, 읽은 값이 저장된 값과 같은 경우)
                #    매 폴링마다 같은 분기로 돌아와 **이 창의 auto-detect 가 통째로,
                #    로그 한 줄 없이 영구 정지**한다. 초안이 그랬고 리뷰가 재현했다 —
                #    이 패치가 막으려던 「증상은 같은데 원인 흔적이 없다」를 새 경로에서
                #    그대로 되풀이하는 셈이었다.
                #
                # 그래서 둘을 분리한다 —
                #   ① 스캔 기준(project_dir)은 **언제나** 실제 폴더로 옮긴다. 그래야
                #      후보 스캔이 엉뚱한 곳을 뒤져 죽은 세션을 집지 않는다
                #   ② session_map 의 cwd 교정은 «할 수 있으면» 한다(부가 효과)
                # 어느 쪽이든 경고는 남긴다. 조용히 넘기면 훅이 왜 그랬는지 못 본다.
                if current_jsonl.parent != project_dir:
                    real_cwd = await asyncio.to_thread(
                        read_cwd_from_jsonl, current_jsonl
                    )
                    wkey = (key, current_sid + "#cwd")
                    if real_cwd and real_cwd != cwd:
                        self._warn_periodically(
                            wkey,
                            now,
                            "%s: session_map 의 cwd 가 %s 인데 %s 의 jsonl 은 %s 에 "
                            "있다. cwd 를 실제 위치로 고치고 스캔 기준을 옮긴다.",
                            key,
                            cwd,
                            current_sid,
                            current_jsonl.parent,
                        )
                        info["cwd"] = real_cwd
                        changed = True
                    else:
                        self._warn_periodically(
                            wkey,
                            now,
                            "%s: session_map 의 cwd 가 %s 인데 %s 의 jsonl 은 %s 에 "
                            "있다. jsonl 에서 실제 cwd 를 못 읽어 session_map 은 그대로 "
                            "두고 스캔 기준만 옮긴다 — cwd 가 계속 어긋나 있다.",
                            key,
                            cwd,
                            current_sid,
                            current_jsonl.parent,
                        )
                    # ① 은 교정 성공 여부와 무관하게 이번 폴링부터 바로 적용한다
                    project_dir = current_jsonl.parent

            # 🚨 훅·세션 피커가 방금 등록한 세션은 jsonl 이 아직 만들어지지 않았다(mtime 0).
            #    그걸 «낡았다» 로 보고 폴더 최신 파일로 갈아타면 **살아 있는 세션을 버린다.**
            #    2026-09-02 16:41 @4 가 정확히 그랬다 — 훅이 쓴 e2586825 를 2ms 뒤 auto-detect
            #    가 e1923ab1(직전 세션, 아직 flush 중)로 덮고 e2586825 를 abandoned 에 넣었다.
            #    그 뒤 영구 거부돼 personal 토픽이 **23시간 동안 출력 0** 이었다. 수신은 창 ID
            #    로 라우팅하니 정상이라, 「받기는 되는데 안 나온다」로만 보여 더 안 잡혔다.
            if current_mtime == 0 and now - first_seen < NEW_SESSION_GRACE_SEC:
                logger.debug(
                    "%s: %s 의 jsonl 을 기다린다 (%.0f/%.0f초)",
                    key,
                    current_sid,
                    now - first_seen,
                    NEW_SESSION_GRACE_SEC,
                )
                continue

            # Skip dir scan if tracked JSONL is still actively growing
            last_seen = self._auto_detect_mtimes.get(mkey, 0)
            if current_mtime > last_seen:
                # File is growing → no need to scan for replacements
                self._auto_detect_mtimes[mkey] = current_mtime
                # 🚨 자란다 = 살아 있다. 「연속 거부」 시계를 여기서 리셋한다.
                #    안 하면 `_refused_since` 가 «최초 거부 이후의 달력 시간» 이 되어,
                #    그 사이 추적 세션이 몇 번이나 정상으로 자랐어도 누적된다. 그러면
                #    자기 치유가 「10분간 계속 죽어 있었다」가 아니라 「10분 전에 한 번
                #    거부된 적이 있고 지금 우연히 조용하다」로 발동한다 — 리뷰가 실측으로
                #    재현했다(320초 주기로 살아 있던 세션에서 946초 만에 튐).
                self._refused_since = {
                    k: v for k, v in self._refused_since.items() if k[0] != key
                }
                continue
            # mtime unchanged → file is stale, scan for a newer session

            # Find a newer main session JSONL
            newest_sid = None
            newest_mtime = current_mtime

            for jsonl_file in project_dir.glob("*.jsonl"):
                stem = jsonl_file.stem
                if stem.startswith("agent-") or not _UUID_RE.match(stem):
                    continue
                try:
                    file_mtime = jsonl_file.stat().st_mtime
                except OSError:
                    continue
                if file_mtime > newest_mtime:
                    newest_mtime = file_mtime
                    newest_sid = stem

            if not newest_sid or newest_sid == current_sid:
                # 🚨 대체할 후보가 아예 없다. 추적 중인 세션의 jsonl 이 유예를 넘겨서도
                #    안 생겼다면(훅이 틀린 cwd 를 등록·권한 문제 등) 이 창은 영원히
                #    출력 0 인데, 예전에는 여기서 **로그 한 줄도 남지 않았다.** 사고와
                #    같은 증상인데 원인 흔적이 없다 — 그래서 주기적으로 남긴다.
                if current_mtime == 0:
                    self._warn_periodically(
                        (key, current_sid),
                        now,
                        "%s: 추적 중인 %s 의 jsonl 이 %.0f초째 없고 대체 후보도 없다 "
                        "(cwd=%s). 이 토픽은 출력이 나가지 않는다 — 훅이 등록한 cwd 가 "
                        "맞는지 확인한다.",
                        key,
                        current_sid,
                        now - first_seen,
                        cwd,
                    )
                continue

            if newest_sid in self._abandoned_sids.get(key, set()):
                # 이 창에서 이미 버린 세션이다. 되돌아가면 왕복이 시작된다 —
                # 같은 cwd 에 세션이 둘 이상 살아 있다는 신호이므로 사유를 남긴다.
                rkey = (key, newest_sid)
                since = self._refused_since.setdefault(rkey, now)

                # 🩹 자기 치유 — 「우리가 채택한 적 없는」 세션을 버렸고(= 훅이 등록해 둔
                #    것을 우리가 덮었다), 그 뒤 **연속으로** 거부가 이어지는데 추적 중인
                #    쪽은 죽었고 후보만 자란다면, 살아 있는 세션을 버렸던 것이다.
                #    그냥 두면 그 토픽은 영원히 출력 0 이 된다(사고가 23시간 그 상태였다).
                #
                #    가드가 둘이다. 하나만으로는 왕복이 되살아난다 —
                #    ① `_suspect_abandoned` — 우리가 스스로 채택했다 버린 sid 로는 절대
                #       되돌아가지 않는다. 되돌아가면 그 다음엔 반대편이 후보가 되어
                #       왕복이 성립한다
                #    ② `_refused_since` 는 growing 분기에서 리셋된다(위 참고). 그래서
                #       「연속 거부 10분」이고, 「10분 전에 한 번 거부됐다」가 아니다.
                #       ②가 없으면 살아 있지만 5분 조용한 세션에서 튄다(실측 946초)
                if (
                    newest_sid in self._suspect_abandoned.get(key, set())
                    and now - since >= READOPT_FORCE_AFTER_SEC
                    and now - current_mtime >= CURRENT_DEAD_AFTER_SEC
                    and now - newest_mtime <= CANDIDATE_ALIVE_WITHIN_SEC
                ):
                    logger.warning(
                        "Force re-adopting %s for %s (cwd=%s) — 연속 거부 %.0f초, "
                        "추적 중인 %s 는 %.0f초째 안 자라는데 후보만 자란다. "
                        "훅이 등록해 둔 살아 있는 세션을 버렸던 것으로 판단해 되돌린다.",
                        newest_sid,
                        key,
                        cwd,
                        now - since,
                        current_sid,
                        now - current_mtime,
                    )
                    self._abandoned_sids[key].discard(newest_sid)
                    self._suspect_abandoned.get(key, set()).discard(newest_sid)
                    self._refused_since.pop(rkey, None)
                    self._warned_readopt.pop(rkey, None)
                    # 아래 정상 채택 경로로 떨어진다
                else:
                    # ⚠️ 매 폴링(2초)마다 여기 도달하므로 그냥 두면 경고가 무한히 쌓인다
                    #    (2026-08-31 리뷰). 반대로 「조합당 한 번만」은 오래 갇힌 상태를
                    #    사실상 안 보이게 만든다(2026-09-03 리뷰). 그래서 주기 재경고다.
                    self._warn_periodically(
                        rkey,
                        now,
                        "Refusing to re-adopt abandoned session for %s: %s (cwd=%s). "
                        "연속 거부 %.0f초. 같은 cwd 에 살아 있는 세션이 둘 이상이다 — "
                        "코드 작업은 워크트리처럼 cwd 를 분리해서 띄운다.",
                        key,
                        newest_sid,
                        cwd,
                        now - since,
                    )
                    continue

            logger.info(
                "Auto-detected session change for %s: %s -> %s",
                key,
                current_sid,
                newest_sid,
            )
            self._abandoned_sids.setdefault(key, set()).add(current_sid)
            # «우리가 채택한 적 없는» 것을 버렸다면 우리 판단이 틀렸을 수 있다.
            # 자기 치유는 이 경우만 되돌린다.
            if self._adopted_sids.get(key) != current_sid:
                self._suspect_abandoned.setdefault(key, set()).add(current_sid)
            self._adopted_sids[key] = newest_sid
            info["session_id"] = newest_sid
            changed = True
            # 창의 세션이 바뀌었으니 이 창의 거부 시계는 전부 무효다.
            self._refused_since = {
                k: v for k, v in self._refused_since.items() if k[0] != key
            }

        # sid 캐시는 창이 아니라 sid 로 키를 잡으므로, 지금 추적하지 않는 sid 는 버린다.
        # 안 버리면 세션이 바뀔 때마다 한 칸씩 쌓인다.
        live_sids = {
            v.get("session_id", "")
            for k, v in session_map.items()
            if k.startswith(prefix)
        }
        self._sid_jsonl_cache = {
            k: v for k, v in self._sid_jsonl_cache.items() if k in live_sids
        }
        self._sid_scan_at = {
            k: v for k, v in self._sid_scan_at.items() if k in live_sids
        }

        if changed:
            try:
                async with aiofiles.open(config.session_map_file, "w") as f:
                    await f.write(json.dumps(session_map, indent=2))
            except OSError as e:
                logger.error("Failed to update session_map.json: %s", e)
                return False

        return changed

    async def _monitor_loop(self) -> None:
        """Background loop for checking session updates.

        Uses simple async polling with aiofiles for non-blocking I/O.
        """
        logger.info("Session monitor started, polling every %ss", self.poll_interval)

        # Deferred import to avoid circular dependency (cached once)
        from .session import session_manager

        # Clean up all stale sessions on startup
        await self._cleanup_all_stale_sessions()
        # Initialize last known session_map
        self._last_session_map = await self._load_current_session_map()

        while self._running:
            try:
                # Load hook-based session map updates
                await session_manager.load_session_map()

                # Auto-detect session changes not caught by hook (/clear, etc.)
                await self._auto_detect_session_changes()

                # Detect session_map changes and cleanup replaced/removed sessions
                current_map = await self._detect_and_cleanup_changes()
                active_session_ids = set(current_map.values())

                # Check for new messages (all I/O is async)
                new_messages = await self.check_for_updates(active_session_ids)

                for msg in new_messages:
                    status = "complete" if msg.is_complete else "streaming"
                    preview = msg.text[:80] + ("..." if len(msg.text) > 80 else "")
                    logger.info("[%s] session=%s: %s", status, msg.session_id, preview)
                    if self._message_callback:
                        try:
                            await self._message_callback(msg)
                        except Exception as e:
                            logger.error(f"Message callback error: {e}")

            except Exception as e:
                logger.error(f"Monitor loop error: {e}")

            await asyncio.sleep(self.poll_interval)

        logger.info("Session monitor stopped")

    def start(self) -> None:
        if self._running:
            logger.warning("Monitor already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        self.state.save()
        logger.info("Session monitor stopped and state saved")
