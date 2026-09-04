"""`list-windows -F` 출력 파싱 — 값에 개행이 들어와도 레코드가 갈라지지 않는지.

libtmux 는 tmux 출력을 **줄 단위로** 레코드 하나로 본다. 그래서 어떤 필드 값에 개행이
들어가면(개행 든 디렉터리의 `pane_current_path`) 레코드가 두 줄로 쪼개지고
`neo.py` 의 `zip(..., strict=True)` 가 터진다 —
`ValueError: zip() argument 2 is shorter than argument 1`.
upstream libtmux #752 (open, PR 미머지) 이고 0.55.1~0.62.0 전부 영향이다.

2026-09-04 실측 재현 — 별도 tmux 서버에 개행 든 디렉터리로 창을 띄우니
구 libtmux 경로는 그 ValueError 로 터지고, 신규 파싱은 3창을 모두 열거하며
값이 `'/private/tmp/evil\\ndir'` 로 정확히 왕복했다.

이 파싱이 delivery 의 핫패스다 — `find_window_by_id` 가 이걸 쓰고,
`send_to_window` 와 `bot.py` 십여 곳이 실제 전송 «전에» 그걸 부른다.
"""

from ccbot.tmux_manager import _FS, _WIN_FIELDS, parse_window_records

N = len(_WIN_FIELDS)


def _rec(*vals: str) -> str:
    """한 레코드: 모든 필드를 _FS 로 종단하고 개행으로 끝낸다(tmux 출력 형태)."""
    assert len(vals) == N
    return "".join(v + _FS for v in vals) + "\n"


def test_plain_records():
    out = _rec("@1", "ceo", "/tmp/a", "bash") + _rec("@2", "metlife", "/tmp/b", "node")
    ws = parse_window_records(out)
    assert ws is not None
    assert [
        (w.window_id, w.window_name, w.cwd, w.pane_current_command) for w in ws
    ] == [
        ("@1", "ceo", "/tmp/a", "bash"),
        ("@2", "metlife", "/tmp/b", "node"),
    ]


def test_newline_in_path_does_not_split_record():
    """🚨 사고 재현 — 개행 든 cwd 가 레코드를 갈라놓지 않는다."""
    out = _rec("@1", "ceo", "/tmp/a", "bash") + _rec(
        "@2", "evil", "/private/tmp/evil\ndir", "bash"
    )
    ws = parse_window_records(out)
    assert ws is not None
    assert len(ws) == 2, "레코드가 갈라졌다"
    assert ws[1].cwd == "/private/tmp/evil\ndir", "값이 왕복하지 않았다"
    assert ws[1].window_id == "@2"


def test_newline_in_first_middle_last_field():
    """어느 필드에 있어도, 몇 개가 있어도 안전하다."""
    for idx in range(N):
        vals = ["@9", "w", "/tmp/x", "bash"]
        vals[idx] = vals[idx] + "\n\nmore"
        ws = parse_window_records(_rec("@1", "a", "/tmp/a", "bash") + _rec(*vals))
        assert ws is not None, f"필드 {idx} 에서 파싱 실패"
        assert len(ws) == 2, f"필드 {idx} 에서 레코드가 갈라졌다"
        got = (
            ws[1].window_id,
            ws[1].window_name,
            ws[1].cwd,
            ws[1].pane_current_command,
        )
        assert got[idx] == vals[idx], f"필드 {idx} 값이 깨졌다: {got[idx]!r}"


def test_poisoned_record_between_clean_ones():
    out = (
        _rec("@1", "a", "/tmp/a", "bash")
        + _rec("@2", "evil", "/tmp/e\nvil", "bash")
        + _rec("@3", "c", "/tmp/c", "bash")
    )
    ws = parse_window_records(out)
    assert ws is not None
    assert [w.window_id for w in ws] == ["@1", "@2", "@3"]


def test_empty_values_and_empty_output():
    assert parse_window_records("") == []
    ws = parse_window_records(_rec("@1", "a", "", ""))
    assert ws is not None
    assert ws[0].cwd == "" and ws[0].pane_current_command == ""


def test_forged_separator_is_detected_not_silently_wrong():
    """값이 구분자를 품으면 어긋난 결과 대신 None 을 준다 — 조용한 오류가 최악이다."""
    out = "".join(v + _FS for v in ("@1", "a" + _FS + "b", "/tmp", "bash")) + "\n"
    assert parse_window_records(out) is None


def test_main_window_is_skipped():
    """자리표시자 main 창 제외는 기존 동작이다 — 회귀하지 않는지 본다."""
    from ccbot.config import config

    out = _rec("@0", config.tmux_main_window_name, "/tmp", "bash") + _rec(
        "@1", "ceo", "/tmp/a", "bash"
    )
    ws = parse_window_records(out)
    assert ws is not None
    assert [w.window_id for w in ws] == ["@1"]
