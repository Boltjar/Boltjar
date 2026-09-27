"""
boltjar.secrets: a server-managed secrets store.

User secrets persist to user/data/secrets.json (a flat {name: value} JSON).
Curated provider env keys are surfaced as read-only 'env' sources.

Nothing is read at import: the file is loaded (and mirrored into os.environ) on
first use or when the server starts (`ensure_loaded`), so importing the node
registry offline, e.g. to generate docs, never touches the user's keys.

Nodes can later reference {{secret.NAME}} tokens; the resolver replaces
them with the resolved value, or leaves the literal token intact when the
secret is unknown (fail visibly, never silently empty). A token reaches only
the stored secrets and the curated provider keys, never the rest of the
process environment (a shared graph must not read AWS_* or GITHUB_TOKEN).
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import time
from typing import Optional

# ---------------------------------------------------------------------------
# Curated env keys shown as read-only 'env' source (presence only).
# ---------------------------------------------------------------------------
_ENV_KEYS: list[str] = [
    "XAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
    "FISH_API_KEY",
    "ELEVENLABS_API_KEY",
]

# ---------------------------------------------------------------------------
# Provider -> env var mapping (None means no key needed, e.g. Ollama).
# Order determines display order in the connections panel.
# ---------------------------------------------------------------------------
PROVIDERS: dict[str, Optional[str]] = {
    "ollama":      None,
    "anthropic":   "ANTHROPIC_API_KEY",
    "xai":         "XAI_API_KEY",
    "openai":      "OPENAI_API_KEY",
    "google":      "GOOGLE_API_KEY",
    "fish":        "FISH_API_KEY",
    "elevenlabs":  "ELEVENLABS_API_KEY",
}

# ---------------------------------------------------------------------------
# The only env vars a {{secret.NAME}} token may read: the curated keys above.
# ---------------------------------------------------------------------------
_ENV_SECRETS: frozenset[str] = frozenset(_ENV_KEYS) | frozenset(v for v in PROVIDERS.values() if v)

# ---------------------------------------------------------------------------
# Ollama connectivity cache: avoid a network round-trip on every call.
# ---------------------------------------------------------------------------
_ollama_cache: tuple[float, bool] | None = None   # (timestamp, result)
_OLLAMA_CACHE_TTL = 4.0  # seconds

# ---------------------------------------------------------------------------
# Data file path, overridable by tests so the real file is never touched.
# ---------------------------------------------------------------------------
_PATH: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent / "user" / "data" / "secrets.json"

# In-memory dict of user-managed secrets, and whether _PATH has been read yet.
_store: dict[str, str] = {}
_loaded = False

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
_NAME_RE = re.compile(r"^[A-Z0-9_]+$")

# Regex for resolving {{secret.NAME}} tokens (optional inner spaces, NAME
# allows upper/lower/digit/underscore so it matches _ENV_KEYS too):
#   \{\{  \s*  secret\.  ([A-Za-z0-9_]+)  \s*  \}\}
_TOKEN_RE = re.compile(r"\{\{\s*secret\.([A-Za-z0-9_]+)\s*\}\}")


def _load() -> None:
    """Load (or reload) the user-secrets dict from _PATH into _store.

    Missing file, empty file, or corrupt JSON are all treated as empty.
    Also syncs every stored secret into os.environ so providers pick them
    up without a restart.
    """
    global _store, _loaded
    _loaded = True
    try:
        text = _PATH.read_text(encoding="utf-8").strip()
        _store = json.loads(text) if text else {}
        if not isinstance(_store, dict):
            _store = {}
    except (FileNotFoundError, json.JSONDecodeError):
        _store = {}
    # Mirror all user secrets into the process environment.
    for name, value in _store.items():
        os.environ[name] = value


def _persist() -> None:
    """Rewrite _PATH from the current _store (atomic-ish via a tmp sibling)."""
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(_store, indent=2), encoding="utf-8")
    tmp.replace(_PATH)


def ensure_loaded() -> None:
    """Load the user secrets once: the first call reads _PATH, later calls do
    nothing. Every public function here calls it, and the server calls it at
    startup so nodes reading provider keys from os.environ see them."""
    if not _loaded:
        _load()

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_secrets() -> list[dict]:
    """Return one entry per secret: {name, source, hasValue}.

    - Every curated env key present in os.environ appears with source 'env'.
    - Every user secret appears with source 'user'.
    - Values are NEVER included.
    """
    ensure_loaded()
    entries: list[dict] = []
    for name in _ENV_KEYS:
        if name in os.environ:
            entries.append({"name": name, "source": "env", "hasValue": True})
    for name, value in _store.items():
        entries.append({"name": name, "source": "user", "hasValue": bool(value)})
    return entries


def get_secret(name: str) -> str | None:
    """Resolve a secret: user dict first, then os.environ for a curated
    provider key. Any other env var is not a secret and resolves to None."""
    ensure_loaded()
    if name in _store:
        return _store[name]
    if name in _ENV_SECRETS:
        return os.environ.get(name)
    return None


def valid_name(name: str) -> bool:
    """A name a secret can be stored under: uppercase letters, digits, _."""
    return bool(_NAME_RE.match(name or ""))


def set_secret(name: str, value: str) -> None:
    """Store a user secret and mirror it into os.environ immediately.

    Raises ValueError if the name does not match ^[A-Z0-9_]+$.
    """
    if not valid_name(name):
        raise ValueError(
            f"Secret name {name!r} is invalid: only uppercase letters, digits, "
            "and underscores are allowed (^[A-Z0-9_]+$)"
        )
    ensure_loaded()  # never persist over secrets that were not read yet
    _store[name] = value
    os.environ[name] = value
    _persist()


def delete_secret(name: str) -> bool:
    """Remove a user secret and persist.

    Returns False if the name is not a user secret (env-only or unknown).
    Returns True on success.  Only user secrets (those in _store) are removed
    from os.environ; env-only keys are not touched.
    """
    ensure_loaded()
    if name not in _store:
        return False
    del _store[name]
    os.environ.pop(name, None)
    _persist()
    return True


# ---------------------------------------------------------------------------
# Provider connectivity
# ---------------------------------------------------------------------------

def provider_connected(provider: str) -> bool:
    """Return True if the given provider appears to be usable.

    - "ollama": pings the local Ollama API (cached for a few seconds).
    - Others: True iff their env var is set to a non-empty string.
    """
    ensure_loaded()
    env_var = PROVIDERS.get(provider)
    if provider == "ollama":
        return _ollama_connected()
    if env_var is None:
        # Provider is in PROVIDERS but needs no key: treat as connected.
        return True
    return bool(os.environ.get(env_var))


def _ollama_connected() -> bool:
    """Ping Ollama's /api/version with a short timeout; cache the result."""
    global _ollama_cache
    now = time.monotonic()
    if _ollama_cache is not None and (now - _ollama_cache[0]) < _OLLAMA_CACHE_TTL:
        return _ollama_cache[1]
    try:
        import httpx
        base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        resp = httpx.get(f"{base}/api/version", timeout=0.4)
        result = resp.status_code == 200
    except Exception:
        result = False
    _ollama_cache = (now, result)
    return result


def provider_status() -> list[dict]:
    """Return one entry per provider: {provider, connected, envVar}.

    Values are NEVER included.
    """
    return [
        {
            "provider": provider,
            "connected": provider_connected(provider),
            "envVar": env_var,
        }
        for provider, env_var in PROVIDERS.items()
    ]


# A secret shorter than this is too likely to be ordinary text (redacting "yes"
# would blank every "yes" on every wire), so redaction leaves it alone.
_REDACT_MIN_LEN = 8


def redact(text: str) -> str:
    """Replace every known secret value in `text` (the stored secrets and the
    curated provider keys) with its {{secret.NAME}} token, longest value first so
    a secret containing another is replaced whole. Used on live events, so a
    resolved key never reaches the editor, the /state cache or a websocket."""
    known: dict[str, str] = {}
    for name in _ENV_SECRETS:
        value = os.environ.get(name)
        if value:
            known[value] = name
    for name, value in _store.items():
        if value:
            known[value] = name
    for value in sorted(known, key=len, reverse=True):
        if len(value) >= _REDACT_MIN_LEN and value in text:
            text = text.replace(value, "{{secret." + known[value] + "}}")
    return text


def resolve_secrets(text: str) -> str:
    """Replace every {{secret.NAME}} token in text with the resolved value.

    If the secret is unknown the original token is left UNTOUCHED so the
    caller can detect the unresolved reference (fail visibly, never silently
    empty string).
    """
    def _replace(m: re.Match) -> str:
        name = m.group(1)
        value = get_secret(name)
        return value if value is not None else m.group(0)

    return _TOKEN_RE.sub(_replace, text)


def unresolved(text: str) -> list[str]:
    """The names of the {{secret.NAME}} tokens in text that resolve to nothing
    (resolve_secrets leaves those as literal text), each named once."""
    names = dict.fromkeys(m.group(1) for m in _TOKEN_RE.finditer(text))
    return [name for name in names if get_secret(name) is None]
