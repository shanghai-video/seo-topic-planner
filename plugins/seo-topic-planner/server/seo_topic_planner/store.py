"""SQLite persistence: JSON documents, a paid-request ledger and a response cache.

Documents are small JSON objects keyed by ``(project_id, kind, id)``.  The
ledger is what keeps spending honest: a paid request is reserved against the
project's cap before it is sent, settled with the provider's reported cost, and
never silently re-sent when its outcome is unknown.  Successful responses are
cached (compressed) so a resume or a second project reuses them for free.
"""

from __future__ import annotations

import contextlib
import gzip
import json
import os
import sqlite3
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from .errors import BudgetError, StateError

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    project_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    id TEXT NOT NULL,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, kind, id)
);
CREATE TABLE IF NOT EXISTS requests (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    reserved_usd TEXT NOT NULL,
    actual_usd TEXT,
    created_at TEXT NOT NULL,
    settled_at TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_requests_project ON requests(project_id, request_hash);
CREATE TABLE IF NOT EXISTS cache (
    request_hash TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    response BLOB NOT NULL,
    cost_usd TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""

# A reservation older than this was interrupted mid-flight: it may have been
# billed, so it is treated as UNKNOWN_COST rather than released.
STALE_RESERVATION = timedelta(minutes=15)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime | None = None) -> str:
    return (moment or utcnow()).isoformat(timespec="seconds")


class Store:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self.data_dir, 0o700)
        self.path = self.data_dir / "planner-v2.sqlite3"
        with self._connect() as connection:
            connection.executescript(SCHEMA)
        with contextlib.suppress(OSError):
            os.chmod(self.path, 0o600)

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    @contextlib.contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")

    # --- documents --------------------------------------------------------------

    def put(self, project_id: str, kind: str, doc_id: str, data: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO docs(project_id, kind, id, data, updated_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(project_id, kind, id) DO UPDATE SET data=excluded.data, "
                "updated_at=excluded.updated_at",
                (project_id, kind, doc_id, json.dumps(data, ensure_ascii=False), iso()),
            )
        return data

    def put_many(self, project_id: str, kind: str, items: dict[str, dict[str, Any]]) -> None:
        stamp = iso()
        with self._transaction() as connection:
            connection.executemany(
                "INSERT INTO docs(project_id, kind, id, data, updated_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(project_id, kind, id) DO UPDATE SET data=excluded.data, "
                "updated_at=excluded.updated_at",
                [
                    (project_id, kind, doc_id, json.dumps(data, ensure_ascii=False), stamp)
                    for doc_id, data in items.items()
                ],
            )

    def get(self, project_id: str, kind: str, doc_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT data FROM docs WHERE project_id=? AND kind=? AND id=?", (project_id, kind, doc_id)
            ).fetchone()
        return json.loads(row["data"]) if row else None

    def list(self, project_id: str, kind: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT data FROM docs WHERE project_id=? AND kind=? ORDER BY rowid", (project_id, kind)
            ).fetchall()
        return [json.loads(row["data"]) for row in rows]

    def delete_kind(self, project_id: str, kind: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM docs WHERE project_id=? AND kind=?", (project_id, kind))

    # --- ledger -------------------------------------------------------------------

    def _expire_stale(self, connection: sqlite3.Connection, project_id: str) -> None:
        cutoff = iso(utcnow() - STALE_RESERVATION)
        connection.execute(
            "UPDATE requests SET status='UNKNOWN_COST', error='interrupted after reservation' "
            "WHERE project_id=? AND status='RESERVED' AND created_at < ?",
            (project_id, cutoff),
        )

    @staticmethod
    def _committed(connection: sqlite3.Connection, project_id: str) -> Decimal:
        total = Decimal("0")
        for row in connection.execute(
            "SELECT status, reserved_usd, actual_usd FROM requests WHERE project_id=?", (project_id,)
        ):
            if row["status"] == "COMPLETED":
                total += Decimal(row["actual_usd"] or "0")
            elif row["status"] in {"RESERVED", "UNKNOWN_COST"}:
                total += Decimal(row["reserved_usd"])
        return total

    def committed_usd(self, project_id: str) -> Decimal:
        with self._connect() as connection:
            return self._committed(connection, project_id)

    def reserve(
        self, *, project_id: str, operation: str, request_hash: str, ceiling: Decimal, cap: Decimal
    ) -> str:
        """Atomically check the cap and reserve ``ceiling`` for one request."""

        with self._transaction() as connection:
            self._expire_stale(connection, project_id)
            blocking = connection.execute(
                "SELECT status FROM requests WHERE project_id=? AND request_hash=? "
                "AND status IN ('RESERVED','UNKNOWN_COST')",
                (project_id, request_hash),
            ).fetchone()
            if blocking:
                raise StateError(
                    f"an identical {operation} request is {blocking['status']}; it is not re-sent "
                    "automatically because it may already have been billed"
                )
            committed = self._committed(connection, project_id)
            if committed + ceiling > cap:
                raise BudgetError(
                    f"budget cap ${cap} reached (committed ${committed:.4f}, next {operation} "
                    f"reserves ${ceiling})"
                )
            request_id = f"req_{uuid.uuid4().hex[:16]}"
            connection.execute(
                "INSERT INTO requests(id, project_id, operation, request_hash, status, reserved_usd, "
                "created_at) VALUES (?,?,?,?, 'RESERVED', ?, ?)",
                (request_id, project_id, operation, request_hash, str(ceiling), iso()),
            )
        return request_id

    def settle(self, request_id: str, actual: Decimal) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE requests SET status='COMPLETED', actual_usd=?, settled_at=? WHERE id=?",
                (str(actual), iso(), request_id),
            )

    def fail(self, request_id: str, *, cost_uncertain: bool, error: str) -> None:
        status = "UNKNOWN_COST" if cost_uncertain else "FAILED"
        with self._connect() as connection:
            connection.execute(
                "UPDATE requests SET status=?, error=?, settled_at=? WHERE id=?",
                (status, error[:300], iso(), request_id),
            )

    def requests(self, project_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            self._expire_stale(connection, project_id)
            rows = connection.execute(
                "SELECT * FROM requests WHERE project_id=? ORDER BY created_at", (project_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    # --- cache --------------------------------------------------------------------

    def cache_get(self, request_hash: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT response, fetched_at, expires_at FROM cache WHERE request_hash=?", (request_hash,)
            ).fetchone()
        if not row or row["expires_at"] <= iso():
            return None
        payload = json.loads(gzip.decompress(row["response"]).decode("utf-8"))
        return {"payload": payload, "fetched_at": row["fetched_at"]}

    def cache_put(
        self, *, request_hash: str, operation: str, payload: dict[str, Any], cost: Decimal, ttl_days: int
    ) -> str:
        fetched = utcnow()
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO cache(request_hash, operation, response, cost_usd, fetched_at, "
                "expires_at) VALUES (?,?,?,?,?,?)",
                (
                    request_hash,
                    operation,
                    gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
                    str(cost),
                    iso(fetched),
                    iso(fetched + timedelta(days=ttl_days)),
                ),
            )
        return iso(fetched)
