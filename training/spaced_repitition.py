"""
SM-2 algorithm — used by both opening study and puzzle scheduling.

Duplicated in Rust (app/src-tauri/src/training_tools.rs) so both Python
and Rust produce identical scheduling. Any change here must be mirrored
there — the Rust side is what the UI actually calls.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def update_sm2(ef: float, interval: int, reps: int, quality: int) -> tuple[float, int, int]:
    """
    Update SM-2 state.

    quality: 0-5 (0=blackout, 3=correct w/ effort, 5=perfect)
    Returns: (new_ef, new_interval, new_reps)
    """
    q = max(0, min(5, quality))
    if q < 3:
        return ef, 1, 0
    new_ef = ef + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
    new_ef = max(1.3, new_ef)
    if reps == 0:
        new_interval = 1
    elif reps == 1:
        new_interval = 6
    else:
        new_interval = max(1, round(interval * new_ef))
    return new_ef, int(new_interval), reps + 1


def next_due(interval_days: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=interval_days)
