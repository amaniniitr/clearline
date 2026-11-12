"""Append-only run event log (P7). Drives the live view, history and audit."""
from __future__ import annotations

import time
from contextlib import contextmanager

from .. import db
from ..config import settings


def emit(run_id: str, stage: str, type_: str, status: str | None = None, summary: str | None = None,
         data: dict | None = None, duration_ms: int | None = None) -> None:
    db.q("""INSERT INTO run_events (run_id, stage, type, status, summary, data, duration_ms)
            VALUES (%s,%s,%s,%s,%s,%s,%s)""", (run_id, stage, type_, status, summary, db.J(data or {}), duration_ms))


class Stage:
    """Context manager: emits stage.started, then stage.finished with status/summary/duration."""

    def __init__(self, run_id: str, stage: str, pace_ms: int = 0):
        self.run_id, self.stage, self.pace_ms = run_id, stage, pace_ms
        self.status, self.summary, self.data = "pass", "", {}

    def __enter__(self):
        self.t0 = time.monotonic()
        emit(self.run_id, self.stage, "stage.started", "running")
        return self

    def done(self, status: str, summary: str, **data):
        self.status, self.summary, self.data = status, summary, {**self.data, **data}

    def __exit__(self, exc_type, exc, tb):
        if self.pace_ms:
            time.sleep(self.pace_ms / 1000)
        ms = int((time.monotonic() - self.t0) * 1000)
        if exc is not None:
            emit(self.run_id, self.stage, "stage.finished", "error", f"{type(exc).__name__}: {exc}"[:300], self.data, ms)
            return False
        emit(self.run_id, self.stage, "stage.finished", self.status, self.summary, self.data, ms)
        return False


def pace() -> int:
    return int(getattr(settings(), "demo_pace_ms", 0) or 0)
