"""Speech-to-text using faster-whisper.

The model is loaded lazily on first use so commands that don't need it
(like `voicetodo list`) start instantly. Once loaded the model stays in
memory, so the daemon transcribes subsequent audio without re-loading.

Performance notes (i7-7700k, 32GB RAM, no GPU):
  tiny.en  int8  ~10x realtime  (39MB)
  base.en  int8  ~5-7x realtime (74MB)  <-- recommended default
  small.en int8  ~2-3x realtime (244MB)
With CUDA on the 1080 Ti and compute_type="float16" you get
roughly an order of magnitude more throughput.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional


class Transcriber:
    def __init__(
        self,
        model_size: str = "base.en",
        device: str = "cpu",
        compute_type: str = "int8",
    ):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None
        self._lock = threading.Lock()

    def _ensure_model(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    # Imported lazily so importing this module is cheap.
                    from faster_whisper import WhisperModel  # type: ignore
                    self._model = WhisperModel(
                        self.model_size,
                        device=self.device,
                        compute_type=self.compute_type,
                    )
        return self._model

    def transcribe(
        self, audio_path: str | Path, language: Optional[str] = None
    ) -> dict:
        model = self._ensure_model()
        segments, info = model.transcribe(
            str(audio_path),
            language=language,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            beam_size=1,  # fastest; "good enough" for short voice notes
        )
        # `segments` is a generator — consume it to get the full transcript.
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return {
            "text": text,
            "language": getattr(info, "language", None),
            "language_probability": getattr(info, "language_probability", None),
            "duration": getattr(info, "duration", None),
        }
