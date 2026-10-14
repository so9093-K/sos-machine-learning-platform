"""SQLite-backed metadata for local, single-worker development.

The store preserves submitted work and execution attempts across API restarts.
It is intentionally not a distributed task queue.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, home: Path):
        self.home = Path(home).resolve()
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "files").mkdir(exist_ok=True)
        (self.home / "results").mkdir(exist_ok=True)
        self.database = self.home / "metadata.sqlite3"

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.database, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout = 30000")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def initialize(self):
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS datasets (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    kind TEXT NOT NULL, location TEXT NOT NULL,
                    version TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL,
                    dataset_id TEXT NOT NULL, spec TEXT NOT NULL,
                    status TEXT NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0,
                    result TEXT, error TEXT, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(dataset_id) REFERENCES datasets(id)
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL,
                    number INTEGER NOT NULL, status TEXT NOT NULL,
                    started_at TEXT NOT NULL, finished_at TEXT, error TEXT,
                    FOREIGN KEY(job_id) REFERENCES jobs(id),
                    UNIQUE(job_id, number)
                );
                """
            )

    def register_dataset(self, *, name: str, kind: str, location: str, version: str) -> dict:
        item = dict(
            id=str(uuid4()),
            name=name,
            kind=kind,
            location=location,
            version=str(version),
            created_at=now(),
        )
        with self.connection() as db:
            db.execute(
                "INSERT INTO datasets(id,name,kind,location,version,created_at) "
                "VALUES(:id,:name,:kind,:location,:version,:created_at)",
                item,
            )
        return item

    def get_dataset(self, dataset_id: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM datasets WHERE id=?", (dataset_id,)).fetchone()
            return dict(row) if row else None

    def datasets(self) -> list[dict]:
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM datasets ORDER BY created_at DESC")]

    def create_job(self, *, kind: str, dataset_id: str, spec: dict) -> dict:
        item = dict(
            id=str(uuid4()),
            kind=kind,
            dataset_id=dataset_id,
            spec=json.dumps(spec),
            status="Queued",
            created_at=now(),
            updated_at=now(),
        )
        with self.connection() as db:
            db.execute(
                "INSERT INTO jobs(id,kind,dataset_id,spec,status,created_at,updated_at) "
                "VALUES(:id,:kind,:dataset_id,:spec,:status,:created_at,:updated_at)",
                item,
            )
        return self.get_job(item["id"])

    def get_job(self, job_id: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                return None
            item = dict(row)
            item["spec"] = json.loads(item["spec"])
            item["result"] = json.loads(item["result"]) if item["result"] else None
            item["cancel_requested"] = bool(item["cancel_requested"])
            item["attempts"] = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM attempts WHERE job_id=? ORDER BY number", (job_id,)
                )
            ]
            return item

    def jobs(self) -> list[dict]:
        with self.connection() as db:
            ids = [r["id"] for r in db.execute("SELECT id FROM jobs ORDER BY created_at DESC")]
        return [self.get_job(job_id) for job_id in ids]

    def request_cancel(self, job_id: str) -> dict | None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row and row["status"] == "Queued":
                db.execute(
                    "UPDATE jobs SET status='Cancelled',updated_at=? WHERE id=?", (now(), job_id)
                )
            elif row and row["status"] == "Running":
                db.execute(
                    "UPDATE jobs SET cancel_requested=1,updated_at=? WHERE id=?", (now(), job_id)
                )
        return self.get_job(job_id)

    def claim(self) -> dict | None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id FROM jobs WHERE status='Queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            job_id = row["id"]
            number = (
                db.execute(
                    "SELECT COUNT(*) AS n FROM attempts WHERE job_id=?", (job_id,)
                ).fetchone()["n"]
                + 1
            )
            db.execute("UPDATE jobs SET status='Running',updated_at=? WHERE id=?", (now(), job_id))
            db.execute(
                "INSERT INTO attempts(id,job_id,number,status,started_at) VALUES(?,?,?,?,?)",
                (str(uuid4()), job_id, number, "Running", now()),
            )
        return self.get_job(job_id)

    def finish(
        self,
        job_id: str,
        status: str,
        *,
        result: dict | None = None,
        error: str | None = None,
        publish: dict | None = None,
    ) -> str:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status,cancel_requested FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            if not row or row["status"] != "Running":
                raise RuntimeError("Job is not running")
            if row["cancel_requested"]:
                status, result, publish = "Cancelled", None, None
            if status == "Succeeded" and publish:
                item = dict(
                    id=str(uuid4()),
                    name=publish["name"],
                    kind="file",
                    location=publish["location"],
                    version=publish["version"],
                    created_at=now(),
                )
                db.execute(
                    "INSERT INTO datasets(id,name,kind,location,version,created_at) "
                    "VALUES(:id,:name,:kind,:location,:version,:created_at)",
                    item,
                )
                result = {**(result or {}), "dataset_id": item["id"]}
            db.execute(
                "UPDATE jobs SET status=?,result=?,error=?,updated_at=? WHERE id=?",
                (status, json.dumps(result, default=str) if result else None, error, now(), job_id),
            )
            db.execute(
                "UPDATE attempts SET status=?,finished_at=?,error=? WHERE job_id=? "
                "AND status='Running'",
                (status, now(), error, job_id),
            )
        return status

    def recover(self):
        """A stopped local worker cannot leave a job marked successful."""
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            pending = [
                dict(r)
                for r in db.execute("SELECT id,cancel_requested FROM jobs WHERE status='Running'")
            ]
            for item in pending:
                status = "Cancelled" if item["cancel_requested"] else "Queued"
                db.execute(
                    "UPDATE jobs SET status=?,updated_at=? WHERE id=?", (status, now(), item["id"])
                )
                db.execute(
                    "UPDATE attempts SET status='Interrupted',finished_at=?,error=? "
                    "WHERE job_id=? AND status='Running'",
                    (now(), "Worker stopped before completion", item["id"]),
                )
