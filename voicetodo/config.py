"""Configuration loading.

Search order for the config file:
  1. Path passed via -c / --config
  2. ~/.config/voicetodo/config.yaml
  3. /etc/voicetodo/config.yaml

Anything not set in the file uses the defaults below.
The API key can also be supplied via the VOICETODO_API_KEY env var
(useful for systemd EnvironmentFile setups so the secret doesn't sit
in a world-readable yaml).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


DEFAULTS: dict[str, Any] = {
    # --- Network ---
    "host": "0.0.0.0",
    "port": 8765,

    # --- Storage ---
    "db_path": "~/.local/share/voicetodo/voicetodo.db",
    "audio_dir": "~/.local/share/voicetodo/audio",
    "keep_audio": True,

    # --- Whisper (faster-whisper) ---
    # Models: tiny.en / base.en / small.en (English)
    #         tiny / base / small / medium     (multilingual)
    # base.en is the sweet spot for CPU on an i7-7700k.
    "whisper_model": "base.en",
    "whisper_device": "cpu",         # "cuda" to use the 1080 Ti
    "whisper_compute_type": "int8",  # "float16" if you switch to cuda

    # --- Auth ---
    # Clients send `Authorization: Bearer <api_key>`. Empty disables auth.
    "api_key": "",

    # --- Optional LLM-based decomposition ---
    # If both are set, the server asks the local Ollama instance to
    # extract todos from the transcript, and falls back to the rule
    # based parser on any error.
    "ollama_url": None,    # e.g. "http://localhost:11434"
    "ollama_model": None,  # e.g. "llama3.2:3b"
}


def default_config_path() -> Path:
    return Path(os.path.expanduser("~/.config/voicetodo/config.yaml"))


def _load_yaml(p: Path) -> dict:
    if yaml is None:
        raise RuntimeError(
            "PyYAML is required to read config files. Install with: pip install pyyaml"
        )
    with open(p) as f:
        return yaml.safe_load(f) or {}


def load_config(path: Optional[str] = None) -> dict:
    cfg: dict[str, Any] = dict(DEFAULTS)

    candidates: list[Path] = []
    if path:
        candidates.append(Path(path))
    else:
        candidates.append(default_config_path())
        candidates.append(Path("/etc/voicetodo/config.yaml"))

    for p in candidates:
        if p.exists():
            user = _load_yaml(p)
            for k, v in user.items():
                if v is not None:
                    cfg[k] = v
            break

    # Env overrides
    env_key = os.environ.get("VOICETODO_API_KEY")
    if env_key:
        cfg["api_key"] = env_key

    # Expand user paths
    cfg["db_path"] = os.path.expanduser(cfg["db_path"])
    cfg["audio_dir"] = os.path.expanduser(cfg["audio_dir"])

    return cfg
