"""session_map 의 cwd 가 세션의 실제 jsonl 위치와 어긋날 때.

2026-09-03 17:10:59 실측 사고 (`~/.local/logs/ccbot-autostart.log:27644-27647`) —

    17:10:59,187  hook:        @2 → 06b58ae5, cwd=.../Metlife/insudeal-x-backend
    17:10:59,189  auto-detect: @2: 06b58ae5 → 834296ff
    17:11:01,313  session_map: @2 → 834296ff

훅이 그 창의 cwd 를 **하위 폴더**로 갱신했는데, 세션의 jsonl 은 시작 당시 cwd 로 만든
폴더(`-…-Metlife`)에 그대로 있었다. auto-detect 는 cwd 로 폴더를 계산해 거기서
`<sid>.jsonl` 을 찾으므로 **없다 → mtime 0 → 「낡았다」** 로 읽었고, 그 폴더의 최신
파일인 `834296ff`(2026-08-04, 한 달 전 죽은 세션)로 갈아탔다.
metlife 토픽이 17시간 동안 죽은 세션을 보며 출력 0 이었다.

v1.0.6 의 「새 세션 유예」는 이걸 **120초 늦출 뿐** 막지 못한다 — 그 폴더에 파일이
영영 안 생기기 때문이다.
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
    # 훅이 이미 cwd 를 하위 폴더로 써 둔 상태 (사고 직전)
    smap.write_text(
        json.dumps({KEY: {"session_id": LIVE, "cwd": SUBDIR, "window_name": "metlife"}})
    )
    monkeypatch.setattr("ccbot.session_monitor.config.session_map_file", smap)
    monkeypatch.setattr("ccbot.session_monitor.config.tmux_session_name", "ccbot")
    clock = _Clock(BASE)
    monkeypatch.setattr("ccbot.session_monitor.time", clock)
    return SessionMonitor(projects_path=projects), parent_dir, sub_dir, smap, clock


def _entry(smap):
    return json.loads(smap.read_text())[KEY]


@pytest.mark.asyncio
async def test_does_not_switch_to_dead_session_in_wrong_dir(setup):
    """🚨 사고 재현 — 살아 있는 세션을 한 달 전 죽은 세션으로 바꾸지 않는다."""
    mon, parent_dir, sub_dir, smap, clock = setup
    _write(parent_dir / f"{LIVE}.jsonl", PARENT, BASE)  # 살아 있는 쪽
    _write(sub_dir / f"{DEAD}.jsonl", SUBDIR, BASE - 2_600_000)  # 한 달 전

    for _ in range(10):
        clock.advance(2)
        await mon._auto_detect_session_changes()
        assert _entry(smap)["session_id"] == LIVE, "죽은 세션으로 갈아탔다"

    # 유예(120초)를 한참 넘겨도 마찬가지다 — v1.0.6 은 여기서 무너졌다
    for _ in range(10):
        clock.advance(60)
        await mon._auto_detect_session_changes()
        assert _entry(smap)["session_id"] == LIVE, "유예 만료 후 죽은 세션으로 갈아탔다"


@pytest.mark.asyncio
async def test_corrects_cwd_to_actual_jsonl_location(setup):
    """어긋난 cwd 를 실제 위치로 고친다 — 안 고치면 폴더 스캔이 계속 헛돈다."""
    mon, parent_dir, sub_dir, smap, clock = setup
    _write(parent_dir / f"{LIVE}.jsonl", PARENT, BASE)
    _write(sub_dir / f"{DEAD}.jsonl", SUBDIR, BASE - 2_600_000)

    clock.advance(2)
    assert await mon._auto_detect_session_changes() is True  # cwd 를 고쳐 썼다
    assert _entry(smap)["cwd"] == PARENT
    assert _entry(smap)["session_id"] == LIVE


@pytest.mark.asyncio
async def test_tracks_normally_after_cwd_corrected(setup):
    """cwd 가 고쳐진 뒤에는 평소대로 동작한다 — 자라면 그대로 두고, 죽으면 갈아탄다."""
    mon, parent_dir, sub_dir, smap, clock = setup
    _write(parent_dir / f"{LIVE}.jsonl", PARENT, BASE)
    _write(sub_dir / f"{DEAD}.jsonl", SUBDIR, BASE - 2_600_000)

    clock.advance(2)
    await mon._auto_detect_session_changes()  # cwd 교정
    clock.advance(2)
    await mon._auto_detect_session_changes()  # 기준선
    assert _entry(smap)["session_id"] == LIVE

    # 같은 (진짜) 폴더에 새 세션이 생겨 자라면 그때는 갈아탄다
    newer = "11111111-2222-3333-4444-555555555555"
    clock.advance(2)
    _write(parent_dir / f"{newer}.jsonl", PARENT, clock.time())
    assert await mon._auto_detect_session_changes() is True
    assert _entry(smap)["session_id"] == newer


@pytest.mark.asyncio
async def test_warns_about_cwd_mismatch(setup, caplog):
    """cwd 불일치는 조용히 고치지 않는다 — 훅이 왜 그랬는지 봐야 한다."""
    mon, parent_dir, sub_dir, _smap, clock = setup
    _write(parent_dir / f"{LIVE}.jsonl", PARENT, BASE)
    _write(sub_dir / f"{DEAD}.jsonl", SUBDIR, BASE - 2_600_000)

    clock.advance(2)
    with caplog.at_level("WARNING"):
        await mon._auto_detect_session_changes()
    hits = [r for r in caplog.records if "cwd 를 실제 위치로 고치고" in r.getMessage()]
    assert len(hits) == 1, f"경고가 {len(hits)}번 — 1번이어야 한다"


@pytest.mark.asyncio
async def test_rescan_is_rate_limited(setup, monkeypatch):
    """되짚기는 SID_RESCAN_SEC 주기로만 — 매 폴링(2초)마다 glob 하면 낭비다."""
    mon, _parent_dir, sub_dir, _smap, clock = setup
    _write(sub_dir / f"{DEAD}.jsonl", SUBDIR, BASE - 2_600_000)
    # LIVE 의 jsonl 은 어디에도 없다 → 매번 되짚기를 시도하게 되는 최악 조건

    calls = []
    real_glob = type(mon.projects_path).glob

    def counting_glob(self, pattern):
        if pattern.endswith(".jsonl") and "/" in pattern:
            calls.append(pattern)
        return real_glob(self, pattern)

    monkeypatch.setattr(type(mon.projects_path), "glob", counting_glob)

    for _ in range(30):  # 60초치 폴링
        clock.advance(2)
        await mon._auto_detect_session_changes()

    assert len(calls) <= 3, f"60초에 되짚기 {len(calls)}회 — 주기 제한이 안 걸렸다"
    assert len(calls) >= 2, f"되짚기가 {len(calls)}회 — 아예 안 돈다"


@pytest.mark.asyncio
async def test_sid_cache_stays_bounded(setup):
    """캐시는 sid 로 키를 잡으므로, 추적하지 않는 sid 는 남지 않는다."""
    mon, _parent_dir, sub_dir, smap, clock = setup
    _write(sub_dir / f"{DEAD}.jsonl", SUBDIR, BASE - 2_600_000)

    for i in range(5):
        sid = f"{i}{'0' * 7}-2222-3333-4444-555555555555"
        m = json.loads(smap.read_text())
        m[KEY] = {"session_id": sid, "cwd": SUBDIR, "window_name": "metlife"}
        smap.write_text(json.dumps(m))
        clock.advance(60)
        await mon._auto_detect_session_changes()

    live = {_entry(smap)["session_id"]}
    assert set(mon._sid_jsonl_cache) <= live, "옛 sid 캐시가 남았다"
    assert set(mon._sid_scan_at) <= live, "옛 sid 스캔기록이 남았다"


# ─────────────────────────────────────────────────────────────────────────────
# cwd 를 «고칠 수 없는» 경우 — 초안이 여기서 조용히 영구 정지했다 (2026-09-04 리뷰)
# ─────────────────────────────────────────────────────────────────────────────


def _write_no_cwd(path, mtime):
    """cwd 필드가 없는 jsonl. `read_cwd_from_jsonl` 이 "" 를 돌려준다."""
    path.write_text(json.dumps({"type": "user", "message": {"role": "user"}}) + "\n")
    os.utime(path, (mtime, mtime))


@pytest.mark.asyncio
async def test_uncorrectable_cwd_does_not_stall_auto_detect(setup):
    """🚨 cwd 를 못 고쳐도 auto-detect 가 멈추면 안 된다.

    초안은 교정 실패 시에도 `continue` 해서, 매 폴링마다 같은 분기로 돌아와
    이 창의 감지 로직 전체가 **로그 한 줄 없이 영구 정지**했다. 리뷰가 재현했다.
    스캔 기준은 실제 폴더로 옮기고 판정은 계속해야 한다.
    """
    mon, parent_dir, sub_dir, smap, clock = setup
    _write_no_cwd(parent_dir / f"{LIVE}.jsonl", BASE)  # cwd 필드 없음
    _write(sub_dir / f"{DEAD}.jsonl", SUBDIR, BASE - 2_600_000)

    clock.advance(2)
    await mon._auto_detect_session_changes()  # 기준선
    assert _entry(smap)["session_id"] == LIVE
    assert _entry(smap)["cwd"] == SUBDIR, "고칠 수 없으므로 cwd 는 그대로여야 한다"

    # 진짜 폴더에 새 세션이 자라면 감지된다 = 정지하지 않았다는 증거
    newer = "11111111-2222-3333-4444-555555555555"
    clock.advance(2)
    _write(parent_dir / f"{newer}.jsonl", PARENT, clock.time())
    assert await mon._auto_detect_session_changes() is True, "auto-detect 가 멈춰 있다"
    assert _entry(smap)["session_id"] == newer


@pytest.mark.asyncio
async def test_uncorrectable_cwd_still_warns(setup, caplog):
    """고칠 수 없어도 경고는 남는다 — 조용하면 원인 흔적이 사라진다."""
    mon, parent_dir, sub_dir, _smap, clock = setup
    _write_no_cwd(parent_dir / f"{LIVE}.jsonl", BASE)
    _write(sub_dir / f"{DEAD}.jsonl", SUBDIR, BASE - 2_600_000)

    clock.advance(2)
    with caplog.at_level("WARNING"):
        await mon._auto_detect_session_changes()
    hits = [r for r in caplog.records if "실제 cwd 를 못 읽어" in r.getMessage()]
    assert len(hits) == 1, f"경고가 {len(hits)}번 — 조용히 넘어가면 안 된다"


@pytest.mark.asyncio
async def test_uncorrectable_cwd_does_not_pick_dead_session(setup):
    """스캔 기준이 실제 폴더로 옮겨졌으므로, 옛 폴더의 죽은 세션은 후보가 아니다."""
    mon, parent_dir, sub_dir, smap, clock = setup
    _write_no_cwd(parent_dir / f"{LIVE}.jsonl", BASE)
    _write(sub_dir / f"{DEAD}.jsonl", SUBDIR, BASE - 2_600_000)

    for _ in range(20):
        clock.advance(60)
        assert await mon._auto_detect_session_changes() is False
        assert _entry(smap)["session_id"] == LIVE, "죽은 세션으로 갈아탔다"
