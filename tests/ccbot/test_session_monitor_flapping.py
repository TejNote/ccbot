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
C = "cccccccc-1111-2222-3333-444444444444"
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


@pytest.mark.asyncio
async def test_ignores_subagent_jsonl(setup):
    """agent-*.jsonl 은 더 최신이어도 후보가 아니다(기존 필터가 새 가드 옆에서 유효한지)."""
    mon, pd, smap = setup
    now = time.time()
    _touch(pd / f"{A}.jsonl", now - 100)
    _touch(pd / f"{B}.jsonl", now - 200)
    _touch(pd / "agent-deadbeef.jsonl", now + 500)   # 가장 최신이지만 서브에이전트

    await mon._auto_detect_session_changes()
    assert await mon._auto_detect_session_changes() is False
    assert _sid(smap) == A


@pytest.mark.asyncio
async def test_warns_only_once_per_candidate(setup, caplog):
    """거부 경고는 창·후보 조합당 한 번만. 폴링이 2초라 매번 찍으면 로그가 무한히 쌓인다."""
    mon, pd, smap = setup
    now = time.time()
    _touch(pd / f"{A}.jsonl", now - 100)
    _touch(pd / f"{B}.jsonl", now - 100)
    await mon._auto_detect_session_changes()
    _touch(pd / f"{B}.jsonl", now)
    await mon._auto_detect_session_changes()          # B 채택, A 버림
    await mon._auto_detect_session_changes()          # B mtime 캐시

    _touch(pd / f"{A}.jsonl", now + 10)
    caplog.clear()
    with caplog.at_level("WARNING"):
        for _ in range(5):
            assert await mon._auto_detect_session_changes() is False
    refusals = [r for r in caplog.records if "Refusing to re-adopt" in r.getMessage()]
    assert len(refusals) == 1, f"경고가 {len(refusals)}번 — 조합당 1번이어야 한다"


@pytest.mark.asyncio
async def test_window_isolation(monkeypatch, tmp_path):
    """창별로 격리된다 — 한 창에서 버린 세션이 다른 창의 판정을 막지 않는다.

    ⚠️ 두 창의 «버린 목록» 이 서로 달라야 이 성질이 드러난다. 둘 다 같은 sid 를
       버리게 짜면 전역 set 구현으로도 통과해버린다(2026-08-31 실측).
       그래서 @4 는 A 를 버리고, @5 는 아무것도 버리지 않은 상태에서 A 를 채택하게 한다.
    """
    projects = tmp_path / "projects"
    projects.mkdir()
    cwd4, cwd5 = "/tmp/w4", "/tmp/w5"
    pd4, pd5 = _project_dir(projects, cwd4), _project_dir(projects, cwd5)
    smap = tmp_path / "session_map.json"
    smap.write_text(
        json.dumps(
            {
                "ccbot:@4": {"session_id": A, "cwd": cwd4, "window_name": "four"},
                "ccbot:@5": {"session_id": B, "cwd": cwd5, "window_name": "five"},
            }
        )
    )
    monkeypatch.setattr("ccbot.session_monitor.config.session_map_file", smap)
    monkeypatch.setattr("ccbot.session_monitor.config.tmux_session_name", "ccbot")
    mon = SessionMonitor(projects_path=projects)

    now = time.time()
    for d in (pd4, pd5):
        _touch(d / f"{A}.jsonl", now - 100)
        _touch(d / f"{B}.jsonl", now - 100)

    await mon._auto_detect_session_changes()          # 양쪽 mtime 캐시

    # @4 만 갈아탄다 — @5 는 두 파일 mtime 이 같아 후보가 없다
    _touch(pd4 / f"{B}.jsonl", now)
    assert await mon._auto_detect_session_changes() is True
    m = json.loads(smap.read_text())
    assert m["ccbot:@4"]["session_id"] == B
    assert m["ccbot:@5"]["session_id"] == B
    assert mon._abandoned_sids["ccbot:@4"] == {A}
    assert "ccbot:@5" not in mon._abandoned_sids     # @5 는 아직 버린 게 없다

    await mon._auto_detect_session_changes()          # @4 의 B mtime 캐시

    # @5 에서 A 가 최신이 된다. @5 는 A 를 버린 적이 없으므로 채택해야 한다.
    # 전역 set 구현이면 @4 가 버린 A 에 걸려 거부된다 → 이 단정이 깨진다.
    _touch(pd5 / f"{A}.jsonl", now + 10)
    assert await mon._auto_detect_session_changes() is True
    m = json.loads(smap.read_text())
    assert m["ccbot:@5"]["session_id"] == A, "다른 창이 버린 세션 때문에 거부됐다"
    assert m["ccbot:@4"]["session_id"] == B


@pytest.mark.asyncio
async def test_external_switch_clears_memory(setup):
    """훅·세션 피커가 명시적으로 바꾸면 기억을 비운다.

    없으면: 창 안에서 /resume 했는데 훅이 실패한 경우, 과거에 버려진 세션이면
    영구히 재채택이 거부돼 아무 신호 없이 대화가 멈춘 것처럼 보인다.
    """
    mon, pd, smap = setup
    now = time.time()
    _touch(pd / f"{A}.jsonl", now - 100)
    _touch(pd / f"{B}.jsonl", now - 100)
    await mon._auto_detect_session_changes()
    _touch(pd / f"{B}.jsonl", now)
    assert await mon._auto_detect_session_changes() is True
    assert A in mon._abandoned_sids[KEY]

    # 훅이 새 세션 C 를 등록했다고 가정
    _touch(pd / f"{C}.jsonl", now + 5)
    m = json.loads(smap.read_text()); m[KEY]["session_id"] = C
    smap.write_text(json.dumps(m))

    await mon._auto_detect_session_changes()          # 외부 전환 감지 → 기억 비움
    await mon._auto_detect_session_changes()          # C mtime 캐시

    # 이제 A 가 최신이 되면 채택된다 — 예전 기억이 남아 있었다면 거부됐을 것
    _touch(pd / f"{A}.jsonl", now + 20)
    assert await mon._auto_detect_session_changes() is True
    assert _sid(smap) == A
