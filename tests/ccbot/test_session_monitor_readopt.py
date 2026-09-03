"""살아 있는 세션을 버려서 토픽이 영구히 조용해지는 경로를 막는지.

2026-09-02 16:41 실측 사고 — 훅이 `@4 → e2586825`(막 시작한 세션)를 썼는데 2ms 뒤
`_auto_detect_session_changes` 가 그걸 «낡았다» 로 보고 `e1923ab1`(직전 세션, 아직
flush 중)로 덮었다. 새 세션은 jsonl 이 아직 없어 mtime 0 이었기 때문이다. 그 과정에서
살아 있던 e2586825 가 `_abandoned_sids` 에 들어가 **영구 거부**됐고, personal 토픽은
23시간 동안 죽은 세션을 보고 있었다 — 출력 0. 수신은 창 ID 로 라우팅하므로 정상이라
「받기는 되는데 안 나온다」로만 보였다.
"""

import json
import os

import pytest

from ccbot import session_monitor as sm
from ccbot.session_monitor import SessionMonitor

A = "aaaaaaaa-1111-2222-3333-444444444444"
B = "bbbbbbbb-1111-2222-3333-444444444444"
CWD = "/tmp/readopt"
KEY = "ccbot:@4"

BASE = 1_700_000_000.0


class _Clock:
    """모듈의 `time` 을 대체한다 — session_monitor 는 `time.time()` 만 쓴다."""

    def __init__(self, t: float) -> None:
        self.t = t

    def time(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _touch(path, mtime):
    path.write_text("x")
    os.utime(path, (mtime, mtime))


@pytest.fixture
def setup(monkeypatch, tmp_path):
    projects = tmp_path / "projects"
    projects.mkdir()
    pd = projects / ("-" + CWD.strip("/").replace("/", "-"))
    pd.mkdir(parents=True)
    smap = tmp_path / "session_map.json"
    smap.write_text(
        json.dumps({KEY: {"session_id": A, "cwd": CWD, "window_name": "personal"}})
    )
    monkeypatch.setattr("ccbot.session_monitor.config.session_map_file", smap)
    monkeypatch.setattr("ccbot.session_monitor.config.tmux_session_name", "ccbot")
    clock = _Clock(BASE)
    monkeypatch.setattr("ccbot.session_monitor.time", clock)
    return SessionMonitor(projects_path=projects), pd, smap, clock


def _sid(smap):
    return json.loads(smap.read_text())[KEY]["session_id"]


@pytest.mark.asyncio
async def test_new_session_without_jsonl_is_not_replaced(setup):
    """🚨 사고 재현 — jsonl 이 아직 없는 새 세션(A)을 직전 세션(B)으로 덮지 않는다."""
    mon, pd, smap, clock = setup
    # A 는 훅이 방금 등록했고 파일이 없다. B 는 직전 세션이고 아직 flush 중이다.
    _touch(pd / f"{B}.jsonl", BASE - 1)
    assert not (pd / f"{A}.jsonl").exists()

    for _ in range(5):
        clock.advance(2)  # 폴링 간격
        assert await mon._auto_detect_session_changes() is False
        assert _sid(smap) == A, "살아 있는 새 세션이 직전 세션으로 덮였다"

    assert A not in mon._abandoned_sids.get(KEY, set())


@pytest.mark.asyncio
async def test_new_session_kept_once_its_jsonl_appears(setup):
    """유예 중에 파일이 생기면 그대로 A 를 계속 추적한다(정상 경로)."""
    mon, pd, smap, clock = setup
    _touch(pd / f"{B}.jsonl", BASE - 1)
    clock.advance(2)
    await mon._auto_detect_session_changes()

    # Claude 가 첫 메시지를 쓰면 파일이 생긴다
    clock.advance(3)
    _touch(pd / f"{A}.jsonl", clock.time())
    assert await mon._auto_detect_session_changes() is False
    assert _sid(smap) == A
    assert mon._auto_detect_mtimes[(KEY, A)] == clock.time()


@pytest.mark.asyncio
async def test_missing_jsonl_replaced_after_grace(setup):
    """유예가 끝나도 파일이 안 생기면 갈아탄다 — 영구히 붙잡고 있지 않는다."""
    mon, pd, smap, clock = setup
    _touch(pd / f"{B}.jsonl", BASE - 1)
    clock.advance(2)
    assert await mon._auto_detect_session_changes() is False

    clock.advance(sm.NEW_SESSION_GRACE_SEC + 1)
    _touch(pd / f"{B}.jsonl", clock.time())
    assert await mon._auto_detect_session_changes() is True
    assert _sid(smap) == B


@pytest.mark.asyncio
async def test_resumed_session_baselines_before_scanning(setup):
    """이미 파일이 있는 세션으로 훅이 바꿔도 첫 폴링에 곧바로 갈아타지 않는다.

    mtime 캐시가 창 키였을 때는 «앞 세션» 의 mtime 과 비교해서, 새로 등록된 세션이
    그보다 오래된 파일이면 첫 폴링에 바로 «낡았다» 로 판정됐다.
    """
    mon, pd, smap, clock = setup
    # A 를 한동안 추적해 캐시를 키운다
    _touch(pd / f"{A}.jsonl", BASE)
    clock.advance(2)
    await mon._auto_detect_session_changes()
    clock.advance(2)
    _touch(pd / f"{A}.jsonl", clock.time())
    await mon._auto_detect_session_changes()

    # 훅이 B 로 바꿨다. B 의 파일은 A 보다 오래됐다(resume 직후라 아직 안 자랐다).
    _touch(pd / f"{B}.jsonl", BASE - 50)
    m = json.loads(smap.read_text())
    m[KEY]["session_id"] = B
    smap.write_text(json.dumps(m))

    clock.advance(2)
    assert await mon._auto_detect_session_changes() is False
    assert _sid(smap) == B, "훅이 등록한 세션이 첫 폴링에 덮였다"


@pytest.mark.asyncio
async def test_force_readopt_when_current_dead_and_candidate_alive(setup):
    """🩹 자기 치유 — 추적 중인 쪽이 죽고 버린 쪽만 자라면 시간을 두고 되돌린다."""
    mon, pd, smap, clock = setup
    _touch(pd / f"{A}.jsonl", BASE)
    _touch(pd / f"{B}.jsonl", BASE)
    clock.advance(2)
    await mon._auto_detect_session_changes()  # A 기준선

    # B 가 자라 갈아탄다 → A 가 abandoned 에 들어간다
    clock.advance(2)
    _touch(pd / f"{B}.jsonl", clock.time())
    assert await mon._auto_detect_session_changes() is True
    assert _sid(smap) == B
    assert A in mon._abandoned_sids[KEY]
    clock.advance(2)
    await mon._auto_detect_session_changes()  # B 기준선

    # 이제 B 는 멈추고 A 만 자란다 = A 가 살아 있는 세션이었다.
    clock.advance(sm.CURRENT_DEAD_AFTER_SEC + 10)
    _touch(pd / f"{A}.jsonl", clock.time())
    assert await mon._auto_detect_session_changes() is False, (
        "즉시 되돌리면 왕복이 된다"
    )
    assert _sid(smap) == B

    # 거부가 이어지는 동안 A 는 계속 자란다.
    # ⚠️ 기준점은 «B 가 죽은 시각» 이 아니라 «처음 거부한 시각» 이다.
    refused_at = mon._refused_since[(KEY, A)]
    changed = False
    while clock.time() - refused_at < sm.READOPT_FORCE_AFTER_SEC + 120:
        clock.advance(60)
        _touch(pd / f"{A}.jsonl", clock.time())
        changed = await mon._auto_detect_session_changes()
        if changed:
            break

    assert changed is True, "자기 치유가 발동하지 않았다 — 토픽이 영구히 조용해진다"
    assert _sid(smap) == A
    assert A not in mon._abandoned_sids.get(KEY, set())


@pytest.mark.asyncio
async def test_no_force_readopt_while_current_still_alive(setup):
    """둘 다 살아 있으면 되돌리지 않는다 — 왕복 가드가 그대로 유효해야 한다."""
    mon, pd, smap, clock = setup
    _touch(pd / f"{A}.jsonl", BASE)
    _touch(pd / f"{B}.jsonl", BASE)
    clock.advance(2)
    await mon._auto_detect_session_changes()
    clock.advance(2)
    _touch(pd / f"{B}.jsonl", clock.time())
    assert await mon._auto_detect_session_changes() is True
    assert _sid(smap) == B

    # A·B 가 «둘 다» 계속 자란다. 추적 중인 B 가 살아 있으니 되돌아가면 안 된다.
    for _ in range(30):
        clock.advance(60)
        _touch(pd / f"{B}.jsonl", clock.time())
        _touch(pd / f"{A}.jsonl", clock.time() + 1)
        assert await mon._auto_detect_session_changes() is False
        assert _sid(smap) == B, "두 세션이 다 살아 있는데 왕복했다"


@pytest.mark.asyncio
async def test_sid_keyed_state_stays_bounded(setup):
    """세션이 여러 번 갈려도 (창, sid) 키 dict 가 창당 1칸을 넘지 않는다.

    캐시 키에 sid 를 넣었으므로, 안 버리면 `/clear` 마다 한 칸씩 쌓인다.
    """
    mon, pd, smap, clock = setup
    sid = A
    for i in range(6):
        new_sid = f"{i}{'0' * 7}-1111-2222-3333-444444444444"
        clock.advance(10)
        _touch(pd / f"{new_sid}.jsonl", clock.time())
        m = json.loads(smap.read_text())
        m[KEY]["session_id"] = new_sid
        smap.write_text(json.dumps(m))
        await mon._auto_detect_session_changes()
        sid = new_sid

    for name, d in (
        ("_auto_detect_mtimes", mon._auto_detect_mtimes),
        ("_sid_first_seen", mon._sid_first_seen),
    ):
        keys = [k for k in d if k[0] == KEY]
        assert keys == [(KEY, sid)], f"{name} 에 {len(keys)}칸 남았다 — 누적된다"


# ─────────────────────────────────────────────────────────────────────────────
# 자기 치유가 왕복을 되살리지 않는지 — 2026-09-03 리뷰 2건이 독립적으로 지적한 결함
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_force_readopt_when_current_wakes_periodically(setup):
    """🚨 리뷰 실측 재현 — 추적 세션이 살아 있지만 5분 넘게 조용한 구간이 있으면?

    `_refused_since` 가 「최초 거부 이후의 달력 시간」이면, 그 사이 추적 세션이 몇 번이나
    정상으로 자랐어도 누적돼 자기 치유가 발동한다. 리뷰어가 실제 모듈을 돌려 재현했다 —
    320초 주기로 살아 있던 세션에서 946초 만에 튀었다. growing 분기에서 시계를
    리셋해야 「연속 거부」가 된다.
    """
    mon, pd, smap, clock = setup
    _touch(pd / f"{A}.jsonl", BASE)
    _touch(pd / f"{B}.jsonl", BASE)
    clock.advance(2)
    await mon._auto_detect_session_changes()
    clock.advance(2)
    _touch(pd / f"{B}.jsonl", clock.time())
    assert await mon._auto_detect_session_changes() is True
    assert _sid(smap) == B

    # B 는 320초 주기로 «살아 있다». A 는 60초마다 자란다. 2초 폴링으로 1시간 돌린다.
    next_b = clock.time() + 320
    for _ in range(1800):
        clock.advance(2)
        if clock.time() >= next_b:
            _touch(pd / f"{B}.jsonl", clock.time())
            next_b = clock.time() + 320
        _touch(pd / f"{A}.jsonl", clock.time())
        assert await mon._auto_detect_session_changes() is False
        assert _sid(smap) == B, "살아 있는 세션에서 튀었다 — 왕복이 되살아났다"


@pytest.mark.asyncio
async def test_no_force_readopt_for_self_adopted_abandon(setup):
    """우리가 스스로 채택했다 버린 sid 로는 되돌아가지 않는다.

    되돌아가면 그 다음엔 반대편이 후보가 되어 왕복이 성립한다. 자기 치유는
    「훅이 등록해 둔 것을 우리가 덮은」 경우(`_suspect_abandoned`)만 대상이다.
    """
    mon, pd, smap, clock = setup
    C = "cccccccc-1111-2222-3333-444444444444"
    for sid in (A, B, C):
        _touch(pd / f"{sid}.jsonl", BASE)
    clock.advance(2)
    await mon._auto_detect_session_changes()

    # A(훅이 넣은 값) → B : A 는 의심 대상이 된다
    clock.advance(2)
    _touch(pd / f"{B}.jsonl", clock.time())
    assert await mon._auto_detect_session_changes() is True
    assert mon._suspect_abandoned[KEY] == {A}

    # B(우리가 채택) → C : B 는 의심 대상이 아니다
    clock.advance(2)
    await mon._auto_detect_session_changes()
    clock.advance(2)
    _touch(pd / f"{C}.jsonl", clock.time())
    assert await mon._auto_detect_session_changes() is True
    assert _sid(smap) == C
    assert B not in mon._suspect_abandoned.get(KEY, set())

    # C 가 죽고 B 만 자란다. B 는 의심 대상이 아니므로 아무리 기다려도 안 되돌린다.
    clock.advance(2)
    await mon._auto_detect_session_changes()
    for _ in range(60):
        clock.advance(60)
        _touch(pd / f"{B}.jsonl", clock.time())
        assert await mon._auto_detect_session_changes() is False
        assert _sid(smap) == C, "우리가 버린 세션으로 되돌아갔다 — 왕복 경로다"


@pytest.mark.asyncio
async def test_no_force_readopt_when_candidate_also_dead(setup):
    """후보도 죽었으면 되돌리지 않는다 — 사용자가 그 대화를 완전히 떠난 경우다."""
    mon, pd, smap, clock = setup
    _touch(pd / f"{A}.jsonl", BASE)
    _touch(pd / f"{B}.jsonl", BASE)
    clock.advance(2)
    await mon._auto_detect_session_changes()
    clock.advance(2)
    _touch(pd / f"{B}.jsonl", clock.time())
    assert await mon._auto_detect_session_changes() is True
    clock.advance(2)
    await mon._auto_detect_session_changes()

    # A 를 한 번 자라게 해 거부를 시작시킨 뒤, 둘 다 방치한다
    clock.advance(sm.CURRENT_DEAD_AFTER_SEC + 10)
    _touch(pd / f"{A}.jsonl", clock.time())
    await mon._auto_detect_session_changes()

    clock.advance(sm.READOPT_FORCE_AFTER_SEC + sm.CANDIDATE_ALIVE_WITHIN_SEC + 60)
    for _ in range(5):
        clock.advance(60)
        assert await mon._auto_detect_session_changes() is False
        assert _sid(smap) == B, "후보도 죽었는데 되돌렸다"


@pytest.mark.asyncio
async def test_refusal_warning_repeats_periodically(setup, caplog):
    """거부 경고는 주기적으로 재발한다 — 「한 번만」이면 갇힌 상태가 안 보인다."""
    mon, pd, _smap, clock = setup
    _touch(pd / f"{A}.jsonl", BASE)
    _touch(pd / f"{B}.jsonl", BASE)
    clock.advance(2)
    await mon._auto_detect_session_changes()
    clock.advance(2)
    _touch(pd / f"{B}.jsonl", clock.time())
    await mon._auto_detect_session_changes()
    clock.advance(2)
    await mon._auto_detect_session_changes()

    # A 를 한 번만 자라게 해 거부를 시작시킨다. 그 뒤로는 건드리지 않는다 —
    # 계속 자라게 두면 600초 뒤 자기 치유가 발동해 재경고 대신 교체가 일어난다.
    clock.advance(2)
    _touch(pd / f"{A}.jsonl", clock.time())

    caplog.clear()
    with caplog.at_level("WARNING"):
        # 같은 주기 안에서는 여러 번 폴링해도 한 번만
        for _ in range(5):
            clock.advance(2)
            await mon._auto_detect_session_changes()
        first = [r for r in caplog.records if "Refusing to re-adopt" in r.getMessage()]
        assert len(first) == 1, f"주기 안에서 {len(first)}번 — 1번이어야 한다"

        # 주기가 지나면 다시 찍힌다
        clock.advance(sm.REWARN_SEC + 1)
        assert await mon._auto_detect_session_changes() is False
        again = [r for r in caplog.records if "Refusing to re-adopt" in r.getMessage()]
        assert len(again) == 2, f"재경고가 {len(again) - 1}번 — 주기마다 나와야 한다"


@pytest.mark.asyncio
async def test_warns_when_jsonl_missing_and_no_candidate(setup, caplog):
    """🚨 후보가 아예 없는 경우 — 예전엔 로그 0줄로 영구 침묵이었다.

    훅이 틀린 cwd 를 등록하거나 jsonl 이 끝내 안 생기면 토픽 출력이 0 인데,
    `newest_sid` 가 None 이라 어느 분기도 타지 않아 경고조차 남지 않았다.
    """
    mon, _pd, _smap, clock = setup  # A.jsonl 없음, 다른 후보도 없음
    clock.advance(2)
    with caplog.at_level("WARNING"):
        assert await mon._auto_detect_session_changes() is False
        # 유예 중에는 조용하다
        assert not [r for r in caplog.records if "대체 후보도 없다" in r.getMessage()]

        clock.advance(sm.NEW_SESSION_GRACE_SEC + 1)
        assert await mon._auto_detect_session_changes() is False
        warns = [r for r in caplog.records if "대체 후보도 없다" in r.getMessage()]
        assert len(warns) == 1, "유예가 끝났는데도 경고가 없다 — 조용한 실패다"


@pytest.mark.asyncio
async def test_stat_failure_does_not_replace_session(setup, monkeypatch):
    """일시적 stat 실패를 「파일 없음」으로 뭉개면 멀쩡한 세션이 교체된다."""
    from pathlib import Path as _Path

    mon, pd, smap, clock = setup
    _touch(pd / f"{A}.jsonl", BASE)
    _touch(pd / f"{B}.jsonl", BASE - 500)  # B 는 A 보다 오래됐다
    clock.advance(2)
    await mon._auto_detect_session_changes()

    real_stat, real_exists = _Path.stat, _Path.exists

    def flaky_stat(self, *a, **kw):
        if self.name == f"{A}.jsonl":
            raise OSError(5, "Input/output error")
        return real_stat(self, *a, **kw)

    def always_exists(self):
        return True if self.name == f"{A}.jsonl" else real_exists(self)

    monkeypatch.setattr(_Path, "stat", flaky_stat)
    monkeypatch.setattr(_Path, "exists", always_exists)

    for _ in range(5):
        clock.advance(2)
        assert await mon._auto_detect_session_changes() is False
        assert _sid(smap) == A, "stat 실패가 세션 교체를 유발했다"
