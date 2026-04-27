"""Smoke test for voicetodo.db.DB."""

import os, sys, sqlite3, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from voicetodo.db import DB


def test_basic():
    with tempfile.TemporaryDirectory() as tmp:
        db = DB(os.path.join(tmp, "t.db"))

        nid = db.add_note("Buy milk and call mom", source="test")
        assert nid == 1

        t1 = db.add_todo("Buy milk", note_id=nid)
        t2 = db.add_todo("Call mom", note_id=nid, priority=2)

        todos = db.list_todos()
        assert len(todos) == 2
        # Higher priority first (no due dates, so falls through to priority)
        assert todos[0]["text"] == "Call mom"

        db.complete_todo(t1)
        assert len(db.list_todos()) == 1
        assert len(db.list_todos(include_completed=True)) == 2

        db.update_todo(t2, text="Call mom about Saturday", priority=5)
        updated = db.get_todo(t2)
        assert updated["text"] == "Call mom about Saturday"
        assert updated["priority"] == 5

        db.delete_todo(t2)
        assert db.get_todo(t2) is None

        db.uncomplete_todo(t1)
        assert db.list_todos()[0]["id"] == t1

    print("DB basic tests passed.")


def test_due_at():
    with tempfile.TemporaryDirectory() as tmp:
        db = DB(os.path.join(tmp, "t.db"))

        # No due date by default
        a = db.add_todo("Pay rent")
        assert db.get_todo(a)["due_at"] is None

        # Set due date on creation
        b = db.add_todo("Take meds", due_at="2026-04-28T08:00:00Z")
        assert db.get_todo(b)["due_at"] == "2026-04-28T08:00:00Z"

        # Set via update
        db.update_todo(a, due_at="2026-04-30T17:00:00Z")
        assert db.get_todo(a)["due_at"] == "2026-04-30T17:00:00Z"

        # Add one with later due date and one with no due date
        c = db.add_todo("Renew rego", due_at="2026-05-15T09:00:00Z")
        d = db.add_todo("Buy bread")  # no due date

        # Sort: due-soon first, then no-due-date, by priority/created_at.
        ordered = [t["text"] for t in db.list_todos()]
        assert ordered[0] == "Take meds",       f"got {ordered}"
        assert ordered[1] == "Pay rent",        f"got {ordered}"
        assert ordered[2] == "Renew rego",      f"got {ordered}"
        # last one is the no-due
        assert ordered[3] == "Buy bread",       f"got {ordered}"

        # Clear
        db.update_todo(a, clear_due_at=True)
        assert db.get_todo(a)["due_at"] is None

    print("DB due_at tests passed.")


def test_migration_from_v0():
    """Build a DB without due_at, then open it with the new code."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "old.db")

        # Simulate the original v0 schema (no due_at column).
        with sqlite3.connect(path) as conn:
            conn.executescript("""
                CREATE TABLE notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    transcript TEXT NOT NULL,
                    audio_path TEXT,
                    duration_seconds REAL,
                    source TEXT
                );
                CREATE TABLE todos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    text TEXT NOT NULL,
                    note_id INTEGER REFERENCES notes(id) ON DELETE SET NULL,
                    completed INTEGER NOT NULL DEFAULT 0,
                    priority INTEGER NOT NULL DEFAULT 0
                );
            """)
            conn.execute(
                "INSERT INTO todos (created_at, text) VALUES (?, ?)",
                ("2026-04-26T10:00:00Z", "Old todo"),
            )
            conn.commit()

        # Open with the new code — should auto-migrate.
        db = DB(path)
        rows = db.list_todos()
        assert len(rows) == 1
        assert rows[0]["text"] == "Old todo"
        assert rows[0]["due_at"] is None

        # And we can use the new column afterwards.
        db.update_todo(rows[0]["id"], due_at="2026-05-01T12:00:00Z")
        assert db.get_todo(rows[0]["id"])["due_at"] == "2026-05-01T12:00:00Z"

    print("DB migration tests passed.")


def main():
    test_basic()
    test_due_at()
    test_migration_from_v0()


if __name__ == "__main__":
    main()
