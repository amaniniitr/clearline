"""Postgres access (psycopg 3, direct connection: advisory locks need a session, not a pooler)."""
from __future__ import annotations

import json
from contextlib import contextmanager
from decimal import Decimal
from datetime import date, datetime
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb, set_json_dumps
from psycopg_pool import ConnectionPool

from .config import ROOT, settings


def _default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, set):
        return sorted(o)
    raise TypeError(f"not JSON serialisable: {type(o)}")


def dumps(o: Any) -> str:
    return json.dumps(o, default=_default)


set_json_dumps(dumps)

_pool: ConnectionPool | None = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(settings().database_url, min_size=1, max_size=10,
                               kwargs={"row_factory": dict_row, "autocommit": False}, open=True)
    return _pool


@contextmanager
def tx() -> Iterator[psycopg.Connection]:
    """One transaction. Commits on success, rolls back on error."""
    with pool().connection() as conn:
        with conn.transaction():
            yield conn


def q(sql: str, params: Any = None) -> list[dict]:
    with tx() as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else []


def q1(sql: str, params: Any = None) -> dict | None:
    rows = q(sql, params)
    return rows[0] if rows else None


def J(o: Any) -> Jsonb:
    return Jsonb(o, dumps)


def migrate(reset: bool = False) -> None:
    with psycopg.connect(settings().database_url, autocommit=True) as c:
        if reset:
            c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted((ROOT / "migrations").glob("*.sql")):
            c.execute(f.read_text())
