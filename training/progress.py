"""
Progress reporter — throttled writes to ops.training_progress.

Max 1 DB write per second, except on stage changes (which flush
immediately). Prevents a tight training loop from hammering PG with
status updates while keeping the app's Training Monitor responsive.

`new_run` context manager creates the ops.training_runs row, yields a
reporter, and marks the run 'completed' or 'failed' on exit.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from psycopg.types.json import Json

from common.db import transaction
from config.logging_setup import get_logger

log = get_logger(__name__)


class Reporter:
    def __init__(self, run_id: uuid.UUID):
        self.run_id = run_id
        self.generation = 0
        self.stage = "idle"
        self.stage_progress = 0.0
        self._fields: dict = {}
        self._last_flush = 0.0

    def stage_set(self, generation: int, stage: str, progress: float = 0.0, **kw) -> None:
        self.generation = generation
        self.stage = stage
        self.stage_progress = progress
        self._fields.update(kw)
        self._flush()

    def stage_update(self, progress: float | None = None, **kw) -> None:
        if progress is not None:
            self.stage_progress = progress
        self._fields.update(kw)
        if time.time() - self._last_flush >= 1.0:
            self._flush()

    def message(self, msg: str) -> None:
        self._fields["message"] = msg
        self._flush()

    def _flush(self) -> None:
        cols = ["run_id", "generation", "stage", "stage_progress"]
        vals: list = [self.run_id, self.generation, self.stage, self.stage_progress]
        for k, v in self._fields.items():
            cols.append(k)
            vals.append(v)
        with transaction() as cur:
            cur.execute(
                f"INSERT INTO ops.training_progress ({','.join(cols)}) "
                f"VALUES ({','.join(['%s'] * len(vals))})",
                vals,
            )
            cur.execute(
                "UPDATE ops.training_runs SET current_generation=%s WHERE run_id=%s",
                (self.generation, self.run_id),
            )
        self._last_flush = time.time()


@contextmanager
def new_run(config: dict) -> Iterator[Reporter]:
    run_id = uuid.uuid4()
    with transaction() as cur:
        cur.execute(
            "INSERT INTO ops.training_runs (run_id, config, status) VALUES (%s, %s, 'running')",
            (run_id, Json(config)),
        )
    rep = Reporter(run_id)
    rep.stage_set(0, "idle", 1.0, message="run started")
    try:
        yield rep
    except Exception as e:
        log.exception("run_failed", error=str(e))
        with transaction() as cur:
            cur.execute(
                "UPDATE ops.training_runs SET status='failed', ended_at=NOW() WHERE run_id=%s",
                (run_id,),
            )
        raise
    else:
        with transaction() as cur:
            cur.execute(
                "UPDATE ops.training_runs SET status='completed', ended_at=NOW() WHERE run_id=%s",
                (run_id,),
            )
