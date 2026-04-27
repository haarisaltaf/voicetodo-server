"""Turn a free-form voice-note transcript into a list of todo items.

Two strategies:

* `decompose_rules(transcript)` — pure regex/heuristics, zero deps, runs
  in microseconds. Good for short voice notes where you say things like
  "I need to buy milk and pick up the kids" or
  "First, finish the report. Then email John. Don't forget to renew the rego."

* `decompose_llm(transcript, ollama_url, model)` — hands the transcript to
  a local Ollama instance and parses a JSON array out of the reply. Returns
  None on any error so callers can fall back.

* `decompose_smart(...)` — uses LLM if configured, otherwise rules.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Optional


# ---------------------------------------------------------------- patterns

# Phrases that announce a todo. Used both for detection and for stripping.
_INTENT_RE = re.compile(
    r"""
    \b(?:
          i \s+ (?: need|have|want|ought ) \s+ to
        | i \s+ gotta
        | i \s+ (?: should|must|shall|will )
        | i'?ll
        | i'?m \s+ (?: going \s+ to | gonna )
        | i \s+ am \s+ going \s+ to
        | i'?ve \s+ (?: got | gotta ) \s+ to
        | i \s+ have \s+ (?: got | gotta ) \s+ to
        | (?: please \s+ )? remember \s+ to
        | remind \s+ me \s+ to
        | don'?t \s+ forget \s+ to
        | make \s+ sure \s+ (?: to | i )
        | gotta
        | todo \s* :?
        | task \s* :?
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Strip these from the start of a candidate todo so we end up with an
# imperative phrase ("Buy milk") rather than ("I need to buy milk").
_INTENT_STRIP = re.compile(
    r"""
    ^\s*(?:
          i \s+ (?: need | have | want | ought ) \s+ to
        | i \s+ gotta (?: \s+ to )?
        | i \s+ (?: should | must | will | shall )
        | i'?ll (?: \s+ need \s+ to )?
        | i'?m \s+ (?: going \s+ to | gonna )
        | i \s+ am \s+ going \s+ to
        | i \s+ (?: 've | have ) \s+ (?: got | gotta ) \s+ to
        | (?: please \s+ )? remember \s+ to
        | remind \s+ me \s+ to
        | don'?t \s+ forget \s+ to
        | make \s+ sure \s+ (?: to | i )
        | gotta
        | todo \s* :?
        | task \s* :?
    )\s+
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Imperative-leading verbs. If the candidate starts with one of these,
# we treat it as a todo even without an explicit intent phrase.
_IMPERATIVE_VERBS = {
    "add", "ask", "book", "bring", "build", "buy", "call", "cancel", "check",
    "clean", "clear", "complete", "configure", "confirm", "cook", "create",
    "deliver", "deploy", "do", "download", "drop", "email", "empty", "fetch",
    "fill", "find", "finish", "fix", "follow", "go", "grab", "install",
    "investigate", "make", "mail", "meet", "merge", "message", "move", "order",
    "organize", "pack", "pay", "pick", "plan", "post", "prepare", "print",
    "push", "put", "read", "register", "remind", "remove", "renew", "repair",
    "reply", "research", "reserve", "respond", "return", "review", "run",
    "save", "scan", "schedule", "send", "ship", "shop", "sign", "start",
    "stop", "submit", "take", "talk", "tell", "test", "text", "tidy", "try",
    "update", "upload", "verify", "visit", "wash", "watch", "write",
}

# Filler words to strip from the start of segments. "also" is here because
# in transcripts it usually appears at the start of a clause introducing the
# next item ("Also, schedule the oil change") and we want to keep just the
# action.
_FILLER_LEAD = re.compile(
    r"^(?:also|so|and|but|or|um+|uh+|er+|ah+|like|okay|ok|alright|yeah|well|hmm+|right|now)[,.]?\s+",
    re.IGNORECASE,
)

# Conjunctions / sentence boundaries we split on. Order matters: we want
# the phrase " and then " split as a single unit, not as two splits.
# Note: bare " also " is intentionally NOT a split point — too often
# adverbial ("I should also order"). Splits on "Also," at clause start
# happen via sentence-boundary punctuation instead.
_SPLIT_RE = re.compile(
    r"""
    \s* (?:
          [.!?]+ \s+                              # sentence boundary
        | ;\s+
        | ,?\s+ and \s+ (?: then | also \s+ )?    # ", and ", " and then ", " and also "
        | ,\s+ then ,?\s+
        | ,\s+ plus ,?\s+
        | ,?\s+ as \s+ well \s+ as \s+
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# List-marker words that introduce a new todo segment. "also" intentionally
# excluded — see _SPLIT_RE comment.
_LIST_MARKER = re.compile(
    r"""
    \b(?:
        first(?:ly)? | second(?:ly)? | third(?:ly)? | fourth(?:ly)? | fifth(?:ly)?
      | next | after \s+ that | finally | lastly | another \s+ thing
      | one \s+ more \s+ thing
    )\b [,]? \s+
    """,
    re.IGNORECASE | re.VERBOSE,
)


# ---------------------------------------------------------------- helpers

def _strip_filler(text: str) -> str:
    prev = ""
    while prev != text:
        prev = text
        text = _FILLER_LEAD.sub("", text).strip()
    return text


def _is_intent(segment: str) -> bool:
    s = segment.strip()
    if not s:
        return False
    if _INTENT_RE.search(s):
        return True
    m = re.match(r"[A-Za-z']+", s)
    if m and m.group(0).lower() in _IMPERATIVE_VERBS:
        return True
    return False


def _normalize(text: str) -> Optional[str]:
    text = text.strip().strip(".,;:!?-– \t")
    text = _INTENT_STRIP.sub("", text).strip()
    text = _strip_filler(text)
    text = text.strip().strip(".,;:!?-– \t")
    if not text:
        return None
    # Capitalize first letter, keep the rest as-is.
    text = text[0].upper() + text[1:]
    return text


def _segments(text: str) -> list[str]:
    """Yield candidate todo segments from a transcript."""
    text = text.strip()
    if not text:
        return []

    # First split out list markers so they become their own boundaries.
    parts = _LIST_MARKER.split(text)
    out: list[str] = []
    for p in parts:
        for chunk in _SPLIT_RE.split(p):
            chunk = chunk.strip()
            if chunk:
                out.append(chunk)
    return out


# ---------------------------------------------------------------- public API

def decompose_rules(transcript: str) -> list[str]:
    """Pure-regex decomposition. Always returns at least one item if the
    transcript contains anything word-like."""
    if not transcript or not transcript.strip():
        return []

    segments = [_strip_filler(s) for s in _segments(transcript)]
    segments = [s for s in segments if s]

    has_intent = any(_is_intent(s) for s in segments)

    todos: list[str] = []
    for seg in segments:
        if has_intent and not _is_intent(seg):
            # In "list mode" we drop chatty bits between todos.
            continue
        n = _normalize(seg)
        if n:
            todos.append(n)

    if not todos:
        # Fall back: treat the whole thing as one todo.
        n = _normalize(transcript)
        if n:
            todos.append(n)

    # De-dup, preserve order.
    seen: set[str] = set()
    deduped: list[str] = []
    for t in todos:
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        deduped.append(t)
    return deduped


_LLM_PROMPT = """You extract action items from a voice memo transcript.

Return ONLY a JSON array of strings, with no prose before or after.
Each string is one concrete todo, written as a short imperative phrase
(e.g. "Buy milk", "Call mom about Saturday", "Renew the car registration").

Rules:
- Skip non-actionable content (musings, observations, fillers).
- Don't invent tasks not implied by the transcript.
- Combine fragments that refer to the same task.
- If there are no action items, return [].

Transcript:
\"\"\"{transcript}\"\"\"

JSON array:"""


def decompose_llm(
    transcript: str, ollama_url: str, model: str, timeout: float = 60.0
) -> Optional[list[str]]:
    """Ask a local Ollama instance to extract todos. Returns None on any
    error so callers can fall back to the rule-based decomposer."""
    body = json.dumps({
        "model": model,
        "prompt": _LLM_PROMPT.format(transcript=transcript),
        "stream": False,
        "options": {"temperature": 0.1},
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{ollama_url.rstrip('/')}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None

    text = (data.get("response") or "").strip()
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return None
    try:
        items = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(items, list):
        return None
    return [str(i).strip() for i in items if str(i).strip()]


def decompose_smart(
    transcript: str,
    ollama_url: Optional[str] = None,
    ollama_model: Optional[str] = None,
) -> list[str]:
    """LLM if configured and reachable, rules otherwise."""
    if ollama_url and ollama_model:
        result = decompose_llm(transcript, ollama_url, ollama_model)
        if result is not None:
            return result
    return decompose_rules(transcript)
