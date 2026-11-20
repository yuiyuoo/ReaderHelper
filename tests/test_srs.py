from datetime import datetime, timezone

import pytest

from backend import srs

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
NEW = {"reps": 0, "lapses": 0, "interval_days": 0, "ease": 2.5}


def test_new_card_good_comes_back_tomorrow():
    out = srs.schedule(NEW, "good", NOW)
    assert out["interval_days"] == 1.0
    assert out["reps"] == 1
    assert out["due_at"] == "2026-01-02T00:00:00+00:00"


def test_good_intervals_grow_1_3_then_times_ease():
    card = dict(NEW)
    intervals = []
    for _ in range(3):
        card = {**card, **srs.schedule(card, "good", NOW)}
        intervals.append(card["interval_days"])
    assert intervals == [1.0, 3.0, 7.5]


def test_again_resets_and_lowers_ease():
    card = {"reps": 4, "lapses": 0, "interval_days": 20, "ease": 2.5}
    out = srs.schedule(card, "again", NOW)
    assert out["reps"] == 0 and out["lapses"] == 1
    assert out["ease"] == 2.3
    assert srs.label(out["interval_days"]) == "10 min"


def test_ease_never_drops_below_minimum():
    card = {"reps": 1, "lapses": 0, "interval_days": 1, "ease": srs.MIN_EASE}
    assert srs.schedule(card, "again", NOW)["ease"] == srs.MIN_EASE
    assert srs.schedule(card, "hard", NOW)["ease"] == srs.MIN_EASE


def test_unknown_grade_raises():
    with pytest.raises(ValueError):
        srs.schedule(NEW, "perfect", NOW)


@pytest.mark.parametrize("days, text", [
    (10 / 1440, "10 min"), (0.5, "12 h"), (1, "1 day"), (3, "3 days"), (60, "2 mo"), (730, "2.0 yr"),
])
def test_label(days, text):
    assert srs.label(days) == text


def test_preview_has_all_four_answers():
    assert set(srs.preview(NEW)) == set(srs.GRADES)
