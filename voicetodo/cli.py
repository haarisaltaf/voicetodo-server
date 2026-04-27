"""Command-line interface.

The same `voicetodo` binary handles both the long-running daemon
(`voicetodo serve`) and one-shot commands like `voicetodo list`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_config
from .db import DB
from .decompose import decompose_smart


# --------------------------------------------------------------- subcommands


def cmd_serve(args, config):
    import uvicorn  # imported here so non-server commands don't pay the cost
    from .server import create_app

    app = create_app(config)
    uvicorn.run(
        app,
        host=config.get("host", "0.0.0.0"),
        port=int(config.get("port", 8765)),
        log_level="info",
    )


def cmd_transcribe(args, config):
    from .transcribe import Transcriber

    t = Transcriber(
        model_size=config.get("whisper_model", "base.en"),
        device=config.get("whisper_device", "cpu"),
        compute_type=config.get("whisper_compute_type", "int8"),
    )
    result = t.transcribe(args.path, language=args.language)
    print(result["text"])


def cmd_ingest(args, config):
    """Transcribe a local file and add its todos directly (no HTTP)."""
    from .transcribe import Transcriber

    t = Transcriber(
        model_size=config.get("whisper_model", "base.en"),
        device=config.get("whisper_device", "cpu"),
        compute_type=config.get("whisper_compute_type", "int8"),
    )
    result = t.transcribe(args.path, language=args.language)
    transcript = result["text"]
    if not transcript.strip():
        print("No speech detected.", file=sys.stderr)
        sys.exit(1)

    db = DB(config["db_path"])
    note_id = db.add_note(
        transcript=transcript,
        audio_path=str(args.path),
        duration=result.get("duration"),
        source="cli",
    )
    todos = decompose_smart(
        transcript,
        ollama_url=config.get("ollama_url"),
        ollama_model=config.get("ollama_model"),
    )

    print(f"Transcript: {transcript}")
    print(f"Note ID:    {note_id}")
    print("Todos:")
    if not todos:
        print("  (none extracted)")
    for text in todos:
        tid = db.add_todo(text, note_id=note_id)
        print(f"  [{tid}] {text}")


def _fmt_due(due_at: str | None) -> str:
    if not due_at:
        return ""
    # Show the local-time clock for human readability while keeping it
    # short enough to fit on one CLI line.
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
    except ValueError:
        return f"  @ {due_at}"
    return "  @ " + dt.astimezone().strftime("%a %b %d %H:%M")


def cmd_list(args, config):
    db = DB(config["db_path"])
    todos = db.list_todos(include_completed=args.all)
    if not todos:
        print("(no todos)")
        return
    for t in todos:
        mark = "x" if t["completed"] else " "
        prio = f" !{t['priority']}" if t["priority"] else ""
        due = _fmt_due(t.get("due_at"))
        print(f"[{mark}] {t['id']:>4}{prio}  {t['text']}{due}")


def cmd_add(args, config):
    db = DB(config["db_path"])
    text = " ".join(args.text)
    tid = db.add_todo(text, priority=args.priority)
    print(f"Added [{tid}]: {text}")


def cmd_done(args, config):
    db = DB(config["db_path"])
    for tid in args.ids:
        db.complete_todo(tid)
        print(f"Completed [{tid}]")


def cmd_undone(args, config):
    db = DB(config["db_path"])
    for tid in args.ids:
        db.uncomplete_todo(tid)
        print(f"Reopened  [{tid}]")


def cmd_rm(args, config):
    db = DB(config["db_path"])
    for tid in args.ids:
        db.delete_todo(tid)
        print(f"Deleted   [{tid}]")


def cmd_notes(args, config):
    db = DB(config["db_path"])
    if args.id is not None:
        n = db.get_note(args.id)
        if not n:
            print("Not found", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(n, indent=2))
    else:
        notes = db.list_notes(limit=args.limit)
        if not notes:
            print("(no notes)")
            return
        for n in notes:
            preview = n["transcript"].replace("\n", " ")
            if len(preview) > 80:
                preview = preview[:77] + "..."
            print(f"{n['id']:>4}  {n['created_at']}  {preview}")


def cmd_decompose(args, config):
    """Useful for tweaking the decomposer without involving audio."""
    text = " ".join(args.text) if args.text else sys.stdin.read()
    todos = decompose_smart(
        text,
        ollama_url=config.get("ollama_url"),
        ollama_model=config.get("ollama_model"),
    )
    if not todos:
        print("(no todos extracted)")
        return
    for t in todos:
        print(f"- {t}")


# --------------------------------------------------------------- argparse


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="voicetodo",
        description="Voice-driven todo list daemon (local Whisper STT).",
    )
    p.add_argument("-c", "--config", help="path to config YAML")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("serve", help="run the HTTP server")
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser(
        "transcribe", help="transcribe an audio file and print the text"
    )
    sp.add_argument("path", type=Path)
    sp.add_argument("--language", default=None, help="ISO code, e.g. 'en'")
    sp.set_defaults(func=cmd_transcribe)

    sp = sub.add_parser(
        "ingest",
        help="transcribe a file, decompose, and add the todos to the DB",
    )
    sp.add_argument("path", type=Path)
    sp.add_argument("--language", default=None)
    sp.set_defaults(func=cmd_ingest)

    sp = sub.add_parser("list", help="list todos")
    sp.add_argument("-a", "--all", action="store_true", help="include completed")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("add", help="add a todo manually")
    sp.add_argument("text", nargs="+")
    sp.add_argument("-p", "--priority", type=int, default=0)
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("done", help="mark todos completed")
    sp.add_argument("ids", nargs="+", type=int)
    sp.set_defaults(func=cmd_done)

    sp = sub.add_parser("undone", help="reopen completed todos")
    sp.add_argument("ids", nargs="+", type=int)
    sp.set_defaults(func=cmd_undone)

    sp = sub.add_parser("rm", help="delete todos")
    sp.add_argument("ids", nargs="+", type=int)
    sp.set_defaults(func=cmd_rm)

    sp = sub.add_parser("notes", help="list voice notes, or show one with --id")
    sp.add_argument("--id", type=int, default=None)
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(func=cmd_notes)

    sp = sub.add_parser(
        "decompose",
        help="decompose a transcript into todos (for testing the parser)",
    )
    sp.add_argument(
        "text", nargs="*", help="transcript text; reads stdin if omitted"
    )
    sp.set_defaults(func=cmd_decompose)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config(args.config)
    args.func(args, config)


if __name__ == "__main__":
    main()
