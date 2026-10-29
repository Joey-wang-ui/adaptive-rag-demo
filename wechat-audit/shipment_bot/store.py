"""Scoped local records and revision-bound manual confirmations."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


def scope(account_id: str, sender_id: str) -> str:
    return hashlib.sha256(json.dumps([account_id, sender_id]).encode()).hexdigest()


def now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    scope TEXT PRIMARY KEY, case_id TEXT, pending_role TEXT
                );
                CREATE TABLE IF NOT EXISTS cases (
                    scope TEXT NOT NULL, case_id TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
                    order_json TEXT, delivery_json TEXT, report_json TEXT,
                    approved_revision INTEGER, approved_at TEXT,
                    PRIMARY KEY (scope, case_id)
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, scope TEXT NOT NULL, case_id TEXT,
                    action TEXT NOT NULL, revision INTEGER, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    scope TEXT NOT NULL, message_id TEXT NOT NULL, reply TEXT NOT NULL,
                    PRIMARY KEY(scope, message_id)
                );
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def cached(self, peer: str, message_id: str) -> str | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT reply FROM messages WHERE scope=? AND message_id=?", (peer, message_id)
            ).fetchone()
            return row["reply"] if row else None

    def cache(self, peer: str, message_id: str, reply: str) -> str:
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO messages VALUES (?,?,?)", (peer, message_id, reply))
        return reply

    def session(self, peer: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM sessions WHERE scope=?", (peer,)).fetchone()
            return dict(row) if row else {}

    def new_case(self, peer: str, case_id: str) -> None:
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO cases(scope, case_id) VALUES (?,?)", (peer, case_id))
            db.execute(
                "INSERT INTO sessions VALUES (?,?,NULL) ON CONFLICT(scope) DO UPDATE SET case_id=excluded.case_id,pending_role=NULL",
                (peer, case_id),
            )

    def pending(self, peer: str, role: str | None) -> None:
        with self.connection() as db:
            db.execute("UPDATE sessions SET pending_role=? WHERE scope=?", (role, peer))
            if role:
                db.execute(
                    "UPDATE cases SET report_json=NULL,approved_revision=NULL,approved_at=NULL WHERE scope=? AND case_id=(SELECT case_id FROM sessions WHERE scope=?)",
                    (peer, peer),
                )

    def case(self, peer: str, case_id: str) -> dict:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM cases WHERE scope=? AND case_id=?", (peer, case_id)
            ).fetchone()
            return dict(row) if row else {}

    def replace_document(self, peer: str, case_id: str, role: str, document_json: str) -> dict:
        column = {"order": "order_json", "delivery": "delivery_json"}[role]
        with self.connection() as db:
            db.execute(
                f"UPDATE cases SET {column}=?,revision=revision+1,report_json=NULL,approved_revision=NULL,approved_at=NULL WHERE scope=? AND case_id=?",
                (document_json, peer, case_id),
            )
            db.execute("UPDATE sessions SET pending_role=NULL WHERE scope=?", (peer,))
            row = db.execute(
                "SELECT * FROM cases WHERE scope=? AND case_id=?", (peer, case_id)
            ).fetchone()
            db.execute(
                "INSERT INTO events(scope,case_id,action,revision,created_at) VALUES (?,?,?,?,?)",
                (peer, case_id, f"replace_{role}", row["revision"], now()),
            )
            return dict(row)

    def save_report(self, peer: str, case_id: str, revision: int, report_json: str) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE cases SET report_json=? WHERE scope=? AND case_id=? AND revision=?",
                (report_json, peer, case_id, revision),
            )

    def approve(self, peer: str, case_id: str, revision: int) -> bool:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM cases WHERE scope=? AND case_id=?", (peer, case_id)
            ).fetchone()
            if not row or row["revision"] != revision or not row["report_json"]:
                return False
            if json.loads(row["report_json"])["status"] != "no_differences":
                return False
            stamp = now()
            db.execute(
                "UPDATE cases SET approved_revision=?,approved_at=? WHERE scope=? AND case_id=?",
                (revision, stamp, peer, case_id),
            )
            db.execute(
                "INSERT INTO events(scope,case_id,action,revision,created_at) VALUES (?,?,?,?,?)",
                (peer, case_id, "manual_confirmation", revision, stamp),
            )
            return True
