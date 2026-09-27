"""
boltjar.endpoints: the OpenAI-compatible chat endpoints.

OpenAI itself and many other servers (OpenRouter, Groq, LM Studio, llama.cpp,
vLLM) speak the same wire format: GET <base>/models lists the models and POST
<base>/chat/completions runs one. Two kinds of provider use it:

  - `openai`: api.openai.com, with the key OPENAI_API_KEY (set in Settings, AI Providers).
  - a named custom endpoint: a name (the provider id its models carry, e.g.
    "openrouter"), a base URL and, when the server wants a key, the name of the
    secret that holds it. The list lives in user/data/endpoints.json; the key
    VALUE lives in the secrets store and never in that file.

A base URL is written the way the OpenAI SDKs take it, with its version path
(https://openrouter.ai/api/v1, http://localhost:1234/v1). A bare host
(http://localhost:1234) gets /v1, the path every one of those servers uses.
"""
from __future__ import annotations

import json
import pathlib
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from boltjar import secrets as _secrets

ROOT = pathlib.Path(__file__).resolve().parent.parent
# Module-level so the test suite can point it at a tmp dir.
PATH = ROOT / "user" / "data" / "endpoints.json"

OPENAI = "openai"
OPENAI_BASE_URL = "https://api.openai.com/v1"
OPENAI_KEY = "OPENAI_API_KEY"

# a name is the provider id its models carry (<name>/<model>), so it is short,
# lowercase and never one a built-in provider or the model picker already uses.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
RESERVED = frozenset(_secrets.PROVIDERS) | {"mock", "rerank"}
# the secrets that hold a built-in provider's key (XAI_API_KEY, OPENAI_API_KEY...).
BUILTIN_KEYS = frozenset(v for v in _secrets.PROVIDERS.values() if v)


@dataclass(frozen=True)
class Endpoint:
    name: str
    base_url: str
    # the secret holding the key ("" when the server takes none, e.g. LM Studio).
    key_secret: str = ""

    def key(self) -> str:
        """The key value, or "" when there is none (never logged, never served)."""
        return (_secrets.get_secret(self.key_secret) or "") if self.key_secret else ""

    def ready(self) -> bool:
        """Usable now: a keyless endpoint always, a keyed one once its secret exists."""
        return not self.key_secret or bool(self.key())

    def as_dict(self) -> dict:
        return {"name": self.name, "base_url": self.base_url,
                "key_secret": self.key_secret or None, "has_key": bool(self.key())}


def normalise_base_url(url: str) -> str:
    """An http(s) base URL without a trailing slash; a bare host gets /v1.
    Raises ValueError for anything else."""
    text = str(url or "").strip().rstrip("/")
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"base URL {url!r} must start with http:// or https://")
    if parts.query or parts.fragment:
        raise ValueError(f"base URL {url!r} must not carry a query or a fragment")
    return text if parts.path not in ("", "/") else text + "/v1"


def secret_name_for(name: str) -> str:
    """The secret a key typed for endpoint `name` is saved under (openrouter ->
    OPENROUTER_API_KEY)."""
    return re.sub(r"[^A-Z0-9]", "_", name.upper()) + "_API_KEY"


def _read() -> dict[str, dict]:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict[str, dict]) -> None:
    global _parsed
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(PATH)
    _parsed = None


# The parsed list and the file state it was read from: the model list asks for
# the endpoints once per lookup, so the file is parsed again only when it
# changes (another path, mtime or size), not on every call.
_parsed: tuple[tuple, tuple[Endpoint, ...]] | None = None


def _stamp() -> tuple:
    try:
        st = PATH.stat()
    except OSError:
        return (str(PATH), None)
    return (str(PATH), st.st_mtime_ns, st.st_size)


def list_endpoints() -> list[Endpoint]:
    """Every custom endpoint, by name. A malformed entry is skipped."""
    global _parsed
    stamp = _stamp()
    if _parsed is not None and _parsed[0] == stamp:
        return list(_parsed[1])
    out: list[Endpoint] = []
    for name, entry in sorted(_read().items()):
        if not isinstance(entry, dict) or not _NAME_RE.match(name) or name in RESERVED:
            continue
        try:
            base = normalise_base_url(entry.get("base_url", ""))
        except ValueError:
            continue
        out.append(Endpoint(name, base, str(entry.get("key_secret") or "")))
    _parsed = (stamp, tuple(out))
    return out


def get(name: str) -> Endpoint | None:
    return next((e for e in list_endpoints() if e.name == name), None)


def check(name: str, base_url: str, key_secret: str = "", *, with_key: bool = False) -> Endpoint:
    """The endpoint `save` would write, checked, with nothing written: the name,
    the base URL and the secret its key lives in. With `with_key` (a key value
    comes with it) the secret defaults to <NAME>_API_KEY and may not be a
    built-in provider's key, which the new value would overwrite; without a key,
    naming one reuses it (an OpenAI proxy on OPENAI_API_KEY). Raises ValueError."""
    if not _NAME_RE.match(name or ""):
        raise ValueError(f"endpoint name {name!r} must be lowercase letters, digits, - or _")
    if name in RESERVED:
        raise ValueError(f"endpoint name {name!r} is taken by a built-in provider")
    base = normalise_base_url(base_url)
    secret = str(key_secret or "") or (secret_name_for(name) if with_key else "")
    if secret and not _secrets.valid_name(secret):
        raise ValueError(f"secret name {secret!r} must be uppercase letters, digits or _")
    if with_key and secret in BUILTIN_KEYS:
        raise ValueError(f"secret {secret} holds a built-in provider's key; store this "
                         "endpoint's key under another name, or send no key to reuse it")
    return Endpoint(name, base, secret)


def save(name: str, base_url: str, key_secret: str = "") -> Endpoint:
    """Add or replace custom endpoint `name`. Raises ValueError on a bad name,
    URL or secret name (see `check`); the key itself is stored separately, as
    the secret `key_secret`."""
    endpoint = check(name, base_url, key_secret)
    data = _read()
    data[name] = {"base_url": endpoint.base_url, "key_secret": endpoint.key_secret}
    _write(data)
    return endpoint


def delete(name: str) -> bool:
    data = _read()
    if name not in data:
        return False
    del data[name]
    _write(data)
    return True


def openai_builtin() -> Endpoint:
    return Endpoint(OPENAI, OPENAI_BASE_URL, OPENAI_KEY)


def resolve(provider: str) -> Endpoint | None:
    """The OpenAI-compatible endpoint behind `provider` when it is usable now:
    `openai` with its key set, or a custom endpoint whose key (if it takes one)
    exists. None otherwise."""
    if provider == OPENAI:
        endpoint = openai_builtin()
    else:
        endpoint = get(provider)
    return endpoint if endpoint is not None and endpoint.ready() else None


def usable(listed: list[Endpoint] | None = None) -> list[Endpoint]:
    """Every OpenAI-compatible endpoint usable now, the built-in one first
    (`listed`: the custom endpoints, when the caller already read them)."""
    out = [e for e in (list_endpoints() if listed is None else listed) if e.ready()]
    builtin = openai_builtin()
    return ([builtin] if builtin.ready() else []) + out
