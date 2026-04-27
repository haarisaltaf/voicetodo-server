# voicetodo

A small Debian-friendly daemon that:

1. Accepts uploaded voice notes (any audio format ffmpeg understands).
2. Transcribes them locally with [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper).
3. Decomposes each transcript into individual todo items.
4. Stores everything in a single SQLite file you can edit by hand.
5. Exposes an HTTP API for the companion Android app.

The same `voicetodo` binary also gives you a complete CLI for managing
the list (`add`, `list`, `done`, `rm`, `notes`, `ingest a-file.wav`, etc.)
so you can use the server without the phone if you want.

## Hardware sizing

Designed for the target box (i7-7700k, 32GB DDR4, 1080 Ti).

The `base.en` Whisper model in `int8` mode is the sweet spot on this
CPU — about **5–7× realtime** for English voice notes. A 30-second
voice memo transcribes in roughly 4–5 seconds. The 1080 Ti is left
idle by default; flip two config keys (`whisper_device: cuda`,
`whisper_compute_type: float16`) if you ever need more throughput.

| model     | RAM (~) | speed (CPU, int8) | quality           |
|-----------|---------|-------------------|-------------------|
| tiny.en   | 200 MB  | ~10× realtime     | OK for clean audio |
| base.en   | 400 MB  | ~5–7× realtime    | recommended       |
| small.en  | 1.0 GB  | ~2–3× realtime    | great             |

## Install (Debian)

```bash
unzip voicetodo-server.zip
cd voicetodo-server
sudo bash install.sh
sudo ln -s /opt/voicetodo/.venv/bin/voicetodo /usr/local/bin/voicetodo # if wanting to add voicetodo cli to path
```

The installer:

* installs Python and `ffmpeg`
* creates a `voicetodo` system user
* sets up a venv at `/opt/voicetodo/.venv`
* installs the package (this pulls down `faster-whisper`, ~150 MB on first run)
* drops a default config in `/etc/voicetodo/config.yaml`
* generates a random API key in `/etc/voicetodo/voicetodo.env`
* installs and enables a systemd unit

Start it:

```bash
sudo systemctl start voicetodo
journalctl -u voicetodo -f      # watch logs (the model downloads on first transcribe)
curl http://localhost:8765/health
```

The Whisper model (~75 MB for `base.en`) is downloaded on the first
audio request and cached in the service user's home dir.

## Manual install (no systemd)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
voicetodo -c config.example.yaml serve
```

## CLI

```text
voicetodo serve                    # run the HTTP server
voicetodo list                     # show open todos
voicetodo list -a                  # include completed
voicetodo add Buy bread -p 2       # add a todo with priority 2
voicetodo done 3 4                 # mark todos 3 and 4 done
voicetodo undone 3                 # reopen a todo
voicetodo rm 5                     # delete a todo
voicetodo notes                    # list voice notes (most recent first)
voicetodo notes --id 12            # show one note + its todos as JSON
voicetodo ingest memo.m4a          # transcribe a file and add the todos
voicetodo transcribe memo.m4a      # just print the transcript
voicetodo decompose "I need to buy milk and call the dentist"
                                   # parser playground (no audio)
```

All commands accept `-c PATH` to point at a different config file.

## HTTP API

Auth: every endpoint except `/health` requires
`Authorization: Bearer <api_key>` when `api_key` is set in config.

| Method | Path                | Body / params                                 |
|--------|---------------------|-----------------------------------------------|
| GET    | `/health`           | —                                             |
| POST   | `/audio`            | multipart form: `audio` (required), `language`, `source` |
| GET    | `/todos`            | `?include_completed=true` to include done     |
| POST   | `/todos`            | JSON `{"text": "...", "priority": 0}`         |
| GET    | `/todos/{id}`       | —                                             |
| PATCH  | `/todos/{id}`       | JSON `{"text"?, "priority"?, "completed"?}`   |
| DELETE | `/todos/{id}`       | —                                             |
| GET    | `/notes`            | `?limit=50`                                   |
| GET    | `/notes/{id}`       | returns the note plus its todos               |

`POST /audio` returns:

```json
{
  "note_id": 12,
  "transcript": "I need to buy milk and call the dentist",
  "language": "en",
  "duration": 4.7,
  "todos": [
    {"id": 23, "text": "Buy milk"},
    {"id": 24, "text": "Call the dentist"}
  ]
}
```

Smoke test from your laptop:

```bash
curl -X POST http://server:8765/audio \
  -H "Authorization: Bearer $KEY" \
  -F "audio=@memo.m4a" \
  -F "source=curl"
```

OpenAPI / Swagger is at `http://server:8765/docs`.

## Storage

Single SQLite file (`/var/lib/voicetodo/voicetodo.db` by default), two tables:

```text
notes(id, created_at, transcript, audio_path, duration_seconds, source)
todos(id, created_at, completed_at, text, note_id, completed, priority)
```

Hand-edit any time with `sqlite3 voicetodo.db`. Todos can survive their
parent note (`note_id` becomes NULL on cascade).

Audio files are kept under `audio_dir` if `keep_audio: true`, named by
upload timestamp. Set `keep_audio: false` if you don't want them retained.

## Decomposition: how it works

The default decomposer is rule-based: it splits the transcript on
sentence boundaries, list markers ("first / second / next"), and "and"
conjunctions, then keeps segments that look like todos (start with an
imperative verb or contain phrases like *I need to*, *remember to*,
*don't forget to*, *I'll*, *I'm going to*, *gotta*, *todo:*, *task:*).

It handles things like:

* "okay so um I gotta finish the slides and then send them to Sarah" →
  `["Finish the slides", "Send them to Sarah"]`
* "First, finish the report. Second, email it to John. Third, schedule
  the meeting for Tuesday." → three todos.
* "Make sure I water the plants. Also, schedule the oil change." →
  `["Water the plants", "Schedule the oil change"]`

If you want better quality on rambling inputs, point voicetodo at a
local Ollama instance:

```yaml
ollama_url:   http://localhost:11434
ollama_model: llama3.2:3b
```

The server then prompts the LLM for a JSON array of todos and falls
back to the rule-based parser silently on any error (timeout, bad JSON,
Ollama down).

## GPU mode

Edit `/etc/voicetodo/config.yaml`:

```yaml
whisper_device: cuda
whisper_compute_type: float16
```

then `pip install ctranslate2` with CUDA support if the default wheel
isn't already CUDA-enabled, and restart the service. CPU mode is fine
for ordinary use; the GPU mostly helps when many people are uploading
simultaneously or you're processing very long memos.

## Layout

```text
voicetodo-server/
├── README.md
├── pyproject.toml
├── requirements.txt
├── config.example.yaml
├── voicetodo.service
├── install.sh
├── voicetodo/
│   ├── __init__.py
│   ├── __main__.py        # python -m voicetodo
│   ├── cli.py             # argparse subcommands
│   ├── config.py          # YAML + env loader
│   ├── db.py              # SQLite layer
│   ├── decompose.py       # transcript -> todos
│   ├── server.py          # FastAPI app
│   └── transcribe.py      # faster-whisper wrapper
└── tests/
    ├── test_decompose.py
    └── test_db.py
```

Run tests:

```bash
python tests/test_decompose.py
python tests/test_db.py
```

## Roadmap

* Companion Android app (record / import / upload to this server). Coming next.
* WebSocket streaming so the phone can stream audio chunks while you're talking.
* Optional client-side VAD on the phone to trim long silences before upload.
