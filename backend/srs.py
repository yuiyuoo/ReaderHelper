"""Spaced repetition: when should a saved word come back? A simplified SM-2 (the idea behind Anki).

Each word has an interval (days until the next review) and an ease (how fast the interval grows).
Answers: again (forgot), hard, good, easy.
"""
from datetime import datetime, timedelta, timezone

GRADES = ("again", "hard", "good", "easy")
MIN_EASE, START_EASE = 1.3, 2.5
AGAIN_MINUTES = 10  # a forgotten word comes back in the same session


def schedule(card: dict, grade: str, now: datetime | None = None) -> dict:
    """Return the new scheduling fields for `card` after answering `grade`."""
    now = now or datetime.now(timezone.utc)
    reps, lapses = card.get("reps") or 0, card.get("lapses") or 0
    interval = card.get("interval_days") or 0.0
    ease = card.get("ease") or START_EASE

    if grade == "again":
        reps, lapses = 0, lapses + 1
        ease = max(MIN_EASE, ease - 0.2)
        interval = AGAIN_MINUTES / (24 * 60)
    elif grade == "hard":
        interval = 1.0 if reps == 0 else max(1.0, interval * 1.2)
        ease = max(MIN_EASE, ease - 0.15)
        reps += 1
    elif grade == "good":
        interval = 1.0 if reps == 0 else 3.0 if reps == 1 else interval * ease
        reps += 1
    elif grade == "easy":
        interval = 4.0 if reps == 0 else max(interval * ease * 1.3, 4.0)
        ease += 0.15
        reps += 1
    else:
        raise ValueError(f"unknown grade {grade!r}")

    return {
        "reps": reps, "lapses": lapses, "ease": round(ease, 3), "interval_days": round(interval, 4),
        "due_at": (now + timedelta(days=interval)).isoformat(timespec="seconds"),
        "reviewed_at": now.isoformat(timespec="seconds"),
    }


def label(days: float) -> str:
    """Short, human label for an interval: "10 min", "1 day", "3 days", "2 mo"."""
    if days < 1 / 24:
        return f"{round(days * 24 * 60)} min"
    if days < 1:
        return f"{round(days * 24)} h"
    if days < 30:
        d = round(days)
        return f"{d} day" if d == 1 else f"{d} days"
    if days < 365:
        return f"{round(days / 30)} mo"
    return f"{days / 365:.1f} yr"


def preview(card: dict) -> dict:
    """What each answer would mean, for the buttons: {"again": "10 min", "good": "3 days", ...}."""
    return {g: label(schedule(card, g)["interval_days"]) for g in GRADES}
