"""HTTP server.

POST   /audio              upload audio, transcribe, decompose, add todos
GET    /todos              list todos
POST   /todos              add a todo manually
GET    /todos/{id}         get a todo
PATCH  /todos/{id}         update text/priority/completed
DELETE /todos/{id}         delete a todo
GET    /notes              list voice notes
GET    /notes/{id}         get a voice note + its todos
GET    /health             liveness check (no auth)

Auth: if `api_key` is set in config, every endpoint except /health
requires `Authorization: Bearer <api_key>`.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Header, UploadFile
from pydantic import BaseModel

from .db import DB
from .decompose import decompose_smart
from .transcribe import Transcriber


class TodoUpdate(BaseModel):
    text: Optional[str] = None
    priority: Optional[int] = None
    completed: Optional[bool] = None
    # ISO 8601 datetime (e.g. "2026-05-01T17:00:00Z").
    # - field absent or null  → unchanged
    # - empty string ""       → clear the reminder
    # - non-empty string      → set the reminder
    due_at: Optional[str] = None


class TodoCreate(BaseModel):
    text: str
    priority: int = 0
    due_at: Optional[str] = None


def create_app(config: dict) -> FastAPI:
    app = FastAPI(title="VoiceTodo", version="0.1.0")

    db = DB(config["db_path"])
    audio_dir = Path(config["audio_dir"])
    audio_dir.mkdir(parents=True, exist_ok=True)

    transcriber = Transcriber(
        model_size=config.get("whisper_model", "base.en"),
        device=config.get("whisper_device", "cpu"),
        compute_type=config.get("whisper_compute_type", "int8"),
    )

    api_key: str = config.get("api_key") or ""
    ollama_url = config.get("ollama_url")
    ollama_model = config.get("ollama_model")
    keep_audio: bool = bool(config.get("keep_audio", True))

    def check_auth(authorization: Optional[str]) -> None:
        if not api_key:
            return  # auth disabled
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "Missing or invalid Authorization header")
        token = authorization.split(" ", 1)[1].strip()
        if token != api_key:
            raise HTTPException(401, "Invalid API key")

    # --------------------------------------------------------------- routes

    @app.get("/health")
    def health():
        return {"status": "ok", "version": app.version}

    @app.post("/audio")
    async def upload_audio(
        audio: UploadFile = File(...),
        language: Optional[str] = Form(None),
        source: Optional[str] = Form(None),
        authorization: Optional[str] = Header(None),
    ):
        check_auth(authorization)

        # Stream the upload to a temp file so we don't blow memory.
        suffix = Path(audio.filename or "audio.bin").suffix or ".bin"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            shutil.copyfileobj(audio.file, tmp)
            tmp_path = Path(tmp.name)

        try:
            result = transcriber.transcribe(tmp_path, language=language)
            transcript = result["text"]
            if not transcript.strip():
                raise HTTPException(422, "No speech detected in audio")

            # Optionally archive the audio so the user can re-listen later.
            saved_path: Optional[str] = None
            if keep_audio:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
                dst = audio_dir / f"{stamp}{suffix}"
                shutil.copy(tmp_path, dst)
                saved_path = str(dst)

            note_id = db.add_note(
                transcript=transcript,
                audio_path=saved_path,
                duration=result.get("duration"),
                source=source,
            )

            todo_texts = decompose_smart(
                transcript,
                ollama_url=ollama_url,
                ollama_model=ollama_model,
            )
            todo_ids = [db.add_todo(t, note_id=note_id) for t in todo_texts]

            return {
                "note_id": note_id,
                "transcript": transcript,
                "language": result.get("language"),
                "duration": result.get("duration"),
                "todos": [
                    {"id": tid, "text": t}
                    for tid, t in zip(todo_ids, todo_texts)
                ],
            }
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    @app.get("/todos")
    def list_todos(
        include_completed: bool = False,
        authorization: Optional[str] = Header(None),
    ):
        check_auth(authorization)
        return {"todos": db.list_todos(include_completed=include_completed)}

    @app.post("/todos")
    def create_todo(
        body: TodoCreate, authorization: Optional[str] = Header(None)
    ):
        check_auth(authorization)
        tid = db.add_todo(body.text, priority=body.priority, due_at=body.due_at)
        return db.get_todo(tid)

    @app.get("/todos/{todo_id}")
    def get_todo(todo_id: int, authorization: Optional[str] = Header(None)):
        check_auth(authorization)
        t = db.get_todo(todo_id)
        if not t:
            raise HTTPException(404, "Not found")
        return t

    @app.patch("/todos/{todo_id}")
    def update_todo(
        todo_id: int,
        body: TodoUpdate,
        authorization: Optional[str] = Header(None),
    ):
        check_auth(authorization)
        existing = db.get_todo(todo_id)
        if not existing:
            raise HTTPException(404, "Not found")

        # Translate the API convention to the DB layer:
        #   due_at = ""        -> clear
        #   due_at = "..."     -> set
        #   due_at = None      -> leave alone
        clear_due = body.due_at == ""
        new_due = body.due_at if (body.due_at is not None and body.due_at != "") else None

        if (
            body.text is not None
            or body.priority is not None
            or body.due_at is not None
        ):
            db.update_todo(
                todo_id,
                text=body.text,
                priority=body.priority,
                due_at=new_due,
                clear_due_at=clear_due,
            )
        if body.completed is True and not existing["completed"]:
            db.complete_todo(todo_id)
        elif body.completed is False and existing["completed"]:
            db.uncomplete_todo(todo_id)
        return db.get_todo(todo_id)

    @app.delete("/todos/{todo_id}")
    def delete_todo(todo_id: int, authorization: Optional[str] = Header(None)):
        check_auth(authorization)
        if not db.get_todo(todo_id):
            raise HTTPException(404, "Not found")
        db.delete_todo(todo_id)
        return {"deleted": todo_id}

    @app.get("/notes")
    def list_notes(
        limit: int = 50, authorization: Optional[str] = Header(None)
    ):
        check_auth(authorization)
        return {"notes": db.list_notes(limit=limit)}

    @app.get("/notes/{note_id}")
    def get_note(note_id: int, authorization: Optional[str] = Header(None)):
        check_auth(authorization)
        n = db.get_note(note_id)
        if not n:
            raise HTTPException(404, "Not found")
        return n

    return app
