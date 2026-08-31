"""_auto_detect_session_changes 가 두 세션 사이를 왕복하지 않는지.

2026-08-31 실측 사고 — 같은 cwd 에 Claude 세션이 둘 살아 있으면 session_map 의
window_id 가 20~40초마다 두 세션을 교대해서, 두 대화가 한 Telegram 토픽에 섞여 나왔다.
방금 입력한 쪽의 jsonl 이 계속 «최신» 이 되기 때문이다.
"""

import json
import os
import time

import pytest

from ccbot.session_monitor import SessionMonitor

A = "aaaaaaaa-1111-2222-3333-444444444444"
B = "bbbbbbbb-1111-2222-3333-444444444444"
CWD = "/tmp/twin"
KEY = "ccbot:@4"


def _project_dir(projects, cwd):
    d = projects / ("-" + cwd.strip("/").replace("/", "-"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _touch(path, mtime):
    path.write_text("x")
    os.utime(path, (mtime, mtime))


@pytest.fixture
def setup(monkeypatch, tmp_path):
    projects = tmp_path / "projects"
    projects.mkdir()
    pd = _project_dir(projects, CWD)
    smap = tmp_path / "session_map.json"
    smap.write_text(
        json.dumps({KEY: {"session_id": A, "cwd": CWD, "window_name": "personal"}})
    )
    monkeypatch.setattr("ccbot.session_monitor.config.session_map_file", smap)
    monkeypatch.setattr("ccbot.session_monitor.config.tmux_session_name", "ccbot")
    return SessionMonitor(projects_path=projects), pd, smap


def _sid(smap):
    return json.loads(smap.read_text())[KEY]["session_id"]


@pytest.mark.asyncio
async def test_adopts_newer_session_once(setup):
    """/clear 등으로 세션이 갈린 경우는 그대로 한 번 갈아탄다(기존 기능)."""
    mon, pd, smap = setup
    now = time.time()
    _touch(pd / f"{A}.jsonl", now - 100)
    _touch(pd / f"{B}.jsonl", now - 100)

    await mon._auto_detect_session_changes()      # A 의 mtime 을 캐시

    _touch(pd / f"{B}.jsonl", now)                # A 는 멈추고 B 가 자란다
    assert await mon._auto_detect_session_changes() is True
    assert _sid(smap) == B


@pytest.mark.asyncio
async def test_does_not_flap_back_to_abandoned_session(setup):
    """버린 세션으로 되돌아가지 않는다 — 왕복이 원리적으로 불가능해야 한다."""
    mon, pd, smap = setup
    now = time.time()
    _touch(pd / f"{A}.jsonl", now - 100)
    _touch(pd / f"{B}.jsonl", now - 100)

    await mon._auto_detect_session_changes()
    _touch(pd / f"{B}.jsonl", now)
    assert await mon._auto_detect_session_changes() is True
    assert _sid(smap) == B

    await mon._auto_detect_session_changes()      # B 의 mtime 을 캐시

    # 이제 A 쪽에서 사용자가 입력한다 → 예전엔 A 로 되돌아가 왕복이 시작됐다
    _touch(pd / f"{A}.jsonl", now + 10)
    assert await mon._auto_detect_session_changes() is False
    assert _sid(smap) == B

    # 몇 번을 더 돌려도 흔들리지 않는다
    for i in range(3):
        _touch(pd / f"{A}.jsonl", now + 20 + i)
        assert await mon._auto_detect_session_changes() is False
        assert _sid(smap) == B
