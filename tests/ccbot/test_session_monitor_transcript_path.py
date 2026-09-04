"""훅이 남긴 `transcript_path` 를 쓰면 jsonl 위치를 추측할 필요가 없다.

근본 원인 (2026-09-04 규명, 공식 문서 code.claude.com/docs/en/hooks) —

    cwd              "Current working directory when the hook is invoked"
    transcript_path  "Path to conversation JSON"

`cwd` 는 세션이 **시작한** 폴더가 아니라 훅이 불린 **그 순간**의 폴더다. Bash 의 `cd`
가 유지되므로 긴 세션에서는 계속 떠돈다(실측: 한 세션에서 359회 전환, 폴더 6종).
그런데 jsonl 은 시작 시점 cwd 로 만든 폴더에 고정된다. SessionStart 는 `compact`
에도 발화하므로, 하위 폴더에서 자동 압축이 걸리면 둘이 어긋난다 —
2026-09-03 17:10:59 `@2` 가 그랬고(jsonl 에 `subtype=compact_boundary` 기록됨)
metlife 토픽이 17시간 죽었다.
"""

import json
import os

import pytest

from ccbot.session_monitor import SessionMonitor

LIVE = "06b58ae5-6aba-4e0b-93b6-38721e72bab8"
DEAD = "834296ff-534c-4d08-a38f-46c4bfc51b68"
PARENT = "/tmp/Metlife"
SUBDIR = "/tmp/Metlife/insudeal-x-backend"
KEY = "ccbot:@2"
BASE = 1_700_000_000.0


class _Clock:
    def __init__(self, t):
        self.t = t

    def time(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def _pdir(projects, cwd):
    d = projects / ("-" + cwd.strip("/").replace("/", "-"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write(path, cwd, mtime):
    path.write_text(json.dumps({"cwd": cwd, "type": "user"}) + "\n")
    os.utime(path, (mtime, mtime))


@pytest.fixture
def setup(monkeypatch, tmp_path):
    projects = tmp_path / "projects"
    projects.mkdir()
    parent_dir = _pdir(projects, PARENT)
    sub_dir = _pdir(projects, SUBDIR)
    smap = tmp_path / "session_map.json"
    monkeypatch.setattr("ccbot.session_monitor.config.session_map_file", smap)
    monkeypatch.setattr("ccbot.session_monitor.config.tmux_session_name", "ccbot")
    clock = _Clock(BASE)
    monkeypatch.setattr("ccbot.session_monitor.time", clock)
    return SessionMonitor(projects_path=projects), parent_dir, sub_dir, smap, clock


def _put(smap, **kw):
    e = {"session_id": LIVE, "cwd": SUBDIR, "window_name": "metlife"}
    e.update(kw)
    smap.write_text(json.dumps({KEY: e}))


def _entry(smap):
    return json.loads(smap.read_text())[KEY]


@pytest.mark.asyncio
async def test_transcript_path_wins_over_wandering_cwd(setup):
    """🚨 사고 재현 — transcript_path 가 있으면 cwd 가 떠돌아도 흔들리지 않는다."""
    mon, parent_dir, sub_dir, smap, clock = setup
    live_jsonl = parent_dir / f"{LIVE}.jsonl"
    _write(live_jsonl, PARENT, BASE)
    _write(sub_dir / f"{DEAD}.jsonl", SUBDIR, BASE - 2_600_000)  # 한 달 전
    _put(smap, transcript_path=str(live_jsonl))

    for _ in range(20):
        clock.advance(60)
        assert await mon._auto_detect_session_changes() is False
        assert _entry(smap)["session_id"] == LIVE, "죽은 세션으로 갈아탔다"


@pytest.mark.asyncio
async def test_transcript_path_does_not_rewrite_cwd(setup):
    """cwd 는 고치지 않는다 — 떠도는 게 정상이고, 고쳐도 다음 compact 에 덮인다."""
    mon, parent_dir, sub_dir, smap, clock = setup
    live_jsonl = parent_dir / f"{LIVE}.jsonl"
    _write(live_jsonl, PARENT, BASE)
    _write(sub_dir / f"{DEAD}.jsonl", SUBDIR, BASE - 2_600_000)
    _put(smap, transcript_path=str(live_jsonl))

    clock.advance(2)
    await mon._auto_detect_session_changes()
    assert _entry(smap)["cwd"] == SUBDIR, "cwd 를 건드렸다"


@pytest.mark.asyncio
async def test_transcript_path_path_is_silent(setup, caplog):
    """정상 동작이므로 WARNING 을 내지 않는다 — 매 10분 경고는 소음이다."""
    mon, parent_dir, sub_dir, smap, clock = setup
    live_jsonl = parent_dir / f"{LIVE}.jsonl"
    _write(live_jsonl, PARENT, BASE)
    _write(sub_dir / f"{DEAD}.jsonl", SUBDIR, BASE - 2_600_000)
    _put(smap, transcript_path=str(live_jsonl))

    clock.advance(2)
    with caplog.at_level("WARNING"):
        await mon._auto_detect_session_changes()
    assert not [r for r in caplog.records if "스캔 기준" in r.getMessage()]


@pytest.mark.asyncio
async def test_scan_dir_follows_transcript(setup):
    """스캔 기준이 실제 폴더로 옮겨진다 — 거기서 새 세션이 자라면 감지한다."""
    mon, parent_dir, sub_dir, smap, clock = setup
    live_jsonl = parent_dir / f"{LIVE}.jsonl"
    _write(live_jsonl, PARENT, BASE)
    _write(sub_dir / f"{DEAD}.jsonl", SUBDIR, BASE - 2_600_000)
    _put(smap, transcript_path=str(live_jsonl))

    clock.advance(2)
    await mon._auto_detect_session_changes()  # 기준선

    newer = "11111111-2222-3333-4444-555555555555"
    clock.advance(2)
    _write(parent_dir / f"{newer}.jsonl", PARENT, clock.time())
    assert await mon._auto_detect_session_changes() is True
    assert _entry(smap)["session_id"] == newer


@pytest.mark.asyncio
async def test_stale_transcript_path_is_ignored(setup):
    """sid 와 이름이 안 맞는 transcript_path 는 믿지 않고 폴백한다."""
    mon, parent_dir, sub_dir, smap, clock = setup
    live_jsonl = parent_dir / f"{LIVE}.jsonl"
    _write(live_jsonl, PARENT, BASE)
    _write(sub_dir / f"{DEAD}.jsonl", SUBDIR, BASE - 2_600_000)
    # 다른 세션의 경로가 남아 있는 상태
    _put(smap, transcript_path=str(sub_dir / f"{DEAD}.jsonl"))

    clock.advance(2)
    await mon._auto_detect_session_changes()
    # 되짚기로 진짜 위치를 찾아 LIVE 를 유지해야 한다
    for _ in range(5):
        clock.advance(60)
        await mon._auto_detect_session_changes()
        assert _entry(smap)["session_id"] == LIVE


@pytest.mark.asyncio
async def test_missing_transcript_path_falls_back(setup):
    """옛 항목(필드 없음)은 v1.0.7 되짚기로 그대로 동작한다 — 호환 유지."""
    mon, parent_dir, sub_dir, smap, clock = setup
    _write(parent_dir / f"{LIVE}.jsonl", PARENT, BASE)
    _write(sub_dir / f"{DEAD}.jsonl", SUBDIR, BASE - 2_600_000)
    _put(smap)  # transcript_path 없음

    clock.advance(2)
    assert await mon._auto_detect_session_changes() is True  # cwd 교정(v1.0.7)
    assert _entry(smap)["cwd"] == PARENT
    assert _entry(smap)["session_id"] == LIVE
