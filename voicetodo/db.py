"""SQLite storage for voice notes and todos.

Schema:
  notes   - one row per voice note (transcript + metadata)
  todos   - one row per todo, optionally linked back to the note it came from

Designed to be easy to inspect and edit by hand:
  sqlite3 voicetodo.db
  > select id, completed, text from todos where completed = 0;
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at       TEXT    NOT NULL,
    transcript       TEXT    NOT NULL,
    audio_path       TEXT,
    duration_seconds REAL,
    source           TEXT
);

CREATE TABLE IF NOT EXISTS todos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT    NOT NULL,
    completed_at TEXT,
    text         TEXT    NOT NULL,
    note_id      INTEGER REFERENCES notes(id) ON DELETE SET NULL,
    completed    INTEGER NOT NULL DEFAULT 0,
    priority     INTEGER NOT NULL DEFAULT 0,
    due_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_todos_completed ON todos(completed);
CREATE INDEX IF NOT EXISTS idx_todos_note_id   ON todos(note_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Lightweight forward-only migrations for older DBs."""
        cols = {row[1] for row in conn.execute("PRAGMA table_info(todos)").fetchall()}
        if "due_at" not in cols:
            conn.execute("ALTER TABLE todos ADD COLUMN due_at TEXT")
        # Idempotent — runs on both fresh and migrated DBs.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_todos_due_at ON todos(due_at)")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------ notes

    def add_note(
        self,
        transcript: str,
        audio_path: Optional[str] = None,
        duration: Optional[float] = None,
        source: Optional[str] = None,
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO notes (created_at, transcript, audio_path, duration_seconds, source) "
                "VALUES (?, ?, ?, ?, ?)",
                (_now(), transcript, audio_path, duration, source),
            )
            return int(cur.lastrowid)

    def list_notes(self, limit: int = 50) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM notes ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_note(self, note_id: int) -> Optional[dict]:
        with self.connect() as conn:
            note = conn.execute(
                "SELECT * FROM notes WHERE id = ?", (note_id,)
            ).fetchone()
            if not note:
                return None
            todos = conn.execute(
                "SELECT * FROM todos WHERE note_id = ? ORDER BY id", (note_id,)
            ).fetchall()
            note = dict(note)
            note["todos"] = [dict(t) for t in todos]
            return note

    def delete_note(self, note_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))

    # ------------------------------------------------------------ todos

    def add_todo(
        self,
        text: str,
        note_id: Optional[int] = None,
        priority: int = 0,
        due_at: Optional[str] = None,
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO todos (created_at, text, note_id, priority, due_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (_now(), text, note_id, priority, due_at),
            )
            return int(cur.lastrowid)

    def list_todos(self, include_completed: bool = False) -> list[dict]:
        # Open todos: items with a due date come first, ordered by due_at ASC
        # (earliest deadline first), then by priority desc, then newest first.
        # Completed todos: most recently completed first.
        with self.connect() as conn:
            if include_completed:
                rows = conn.execute(
                    "SELECT * FROM todos "
                    "ORDER BY completed ASC, "
                    "         CASE WHEN due_at IS NULL THEN 1 ELSE 0 END, "
                    "         due_at ASC, "
                    "         priority DESC, "
                    "         created_at DESC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM todos WHERE completed = 0 "
                    "ORDER BY CASE WHEN due_at IS NULL THEN 1 ELSE 0 END, "
                    "         due_at ASC, "
                    "         priority DESC, "
                    "         created_at DESC"
                ).fetchall()
            return [dict(r) for r in rows]

    def get_todo(self, todo_id: int) -> Optional[dict]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM todos WHERE id = ?", (todo_id,)
            ).fetchone()
            return dict(row) if row else None

    def update_todo(
        self,
        todo_id: int,
        text: Optional[str] = None,
        priority: Optional[int] = None,
        due_at: Optional[str] = None,
        clear_due_at: bool = False,
    ) -> None:
        sets: list[str] = []
        params: list = []
        if text is not None:
            sets.append("text = ?")
            params.append(text)
        if priority is not None:
            sets.append("priority = ?")
            params.append(priority)
        if clear_due_at:
            sets.append("due_at = NULL")
        elif due_at is not None:
            sets.append("due_at = ?")
            params.append(due_at)
        if not sets:
            return
        params.append(todo_id)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE todos SET {', '.join(sets)} WHERE id = ?", params
            )

    def complete_todo(self, todo_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE todos SET completed = 1, completed_at = ? WHERE id = ?",
                (_now(), todo_id),
            )

    def uncomplete_todo(self, todo_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE todos SET completed = 0, completed_at = NULL WHERE id = ?",
                (todo_id,),
            )

    def delete_todo(self, todo_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
