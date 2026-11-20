from leadbeast.social.reachable import QUIET, _sort_key


def _row(verdict, days, new=False):
    return {"verdict": verdict, "days_since_last_post": days, "new": new}


def test_strongest_reason_first_then_new_then_longest_silence():
    rows = [_row("irregular", 40), _row("inactive", 100), _row("inactive", 900),
            _row("unavailable", None), _row("inactive", 50, new=True), _row("inactive", None)]
    got = [(r["verdict"], r["days_since_last_post"], r["new"]) for r in sorted(rows, key=_sort_key)]
    assert got == [("unavailable", None, False), ("inactive", 50, True), ("inactive", None, False),
                   ("inactive", 900, False), ("inactive", 100, False), ("irregular", 40, False)]


def test_quiet_verdicts_exclude_active():
    assert "active" not in QUIET
