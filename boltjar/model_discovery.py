"""
boltjar.model_discovery: the live model list.

Every provider this install can actually use is asked which models exist now:

  - Ollama, when it answers: GET /api/tags lists the installed models and POST
    /api/show gives each one's `capabilities` (Ollama's set: completion, tools,
    insert, vision, embedding, thinking, image, audio) and context length.
  - xAI, with XAI_API_KEY: GET /v1/language-models (id, aliases, input and
    output modalities, reasoning efforts).
  - Anthropic, with ANTHROPIC_API_KEY: GET /v1/models (display name, capabilities,
    max_input_tokens, max_tokens).
  - OpenAI (OPENAI_API_KEY) and every custom OpenAI-compatible endpoint
    (boltjar.endpoints): GET <base>/models. OpenRouter-style extras
    (architecture modalities, context_length, supported_parameters) are read when
    a server sends them.

What each provider reports is mapped onto the manifest shape (kind, inputs,
outputs, tools, thinking, json, context) and cached in user/data/models-cache.json
with UTC timestamps. The list refreshes in the background when the server starts
(never blocking boot), on demand (POST /api/models/refresh), after a key or an
Ollama model changes, and when it is older than STALE_AFTER. A provider that
cannot be reached keeps its last good list, so the picker works offline.

TOML manifests are the optional enrichment: a listed model with a manifest keeps
the manifest's label, params and quirks; a listed model without one gets defaults
from what the provider reported; a manifest for a model its provider no longer
lists stays in the list, marked unavailable, and is never deleted.
"""
from __future__ import annotations

import asyncio
import dataclasses
import datetime
import difflib
import functools
import json
import logging
import os
import pathlib
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

import httpx

from boltjar import endpoints, models
from boltjar import secrets as _secrets
from boltjar.models import ModelManifest, Param, _capability_params

_log = logging.getLogger("boltjar.models")

ROOT = pathlib.Path(__file__).resolve().parent.parent
# Module-level so the test suite can point it at a tmp file.
CACHE_PATH = ROOT / "user" / "data" / "models-cache.json"
# A list older than this is refreshed in the background the next time it is read.
STALE_AFTER = 3 * 60 * 60  # seconds
# Background refreshes (start, stale, after a key or model change). The test suite
# turns them off so no test reaches a real provider; POST /api/models/refresh and
# refresh() always run.
AUTO_REFRESH = True
TIMEOUT = httpx.Timeout(8.0, connect=3.0)

XAI_MODELS_URL = "https://api.x.ai/v1/language-models"
ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"
ANTHROPIC_VERSION = "2023-06-01"

# The LLM model value that picks a runnable model at run time (resolve_auto).
AUTO = "auto"
# What an "auto" LLM replies with while nothing runnable is connected.
AUTO_MOCK_REPLY = ("No model is connected yet: open Connections to add a provider key "
                   "or install an Ollama model, and this node will use it.")

# the manifest kinds each provider's list covers: a manifest of another kind (xAI
# TTS, for one) is never marked unavailable because a chat list lacks it.
_LISTED_KINDS = {"ollama": frozenset({"llm", "embed"})}
_DEFAULT_LISTED_KINDS = frozenset({"llm"})

# names of OpenAI-compatible models no node here can run (embeddings, speech,
# images, moderation, realtime, legacy completions), for servers that report no
# modalities.
_NOT_CHAT = re.compile(r"embed|tts|whisper|transcribe|dall-e|gpt-image|moderation|realtime|"
                       r"audio|search|babbage|davinci|sora|computer-use", re.IGNORECASE)
# api.openai.com lists every model the key reaches and reports no modalities, so
# its list keeps the chat families (gpt-*, o<n>*, chatgpt-*) minus the models
# POST /chat/completions refuses or no node here runs: legacy completions
# (*-instruct), the Responses-only ones (*-pro, codex, deep research, computer
# use), search, realtime, live voice, audio, speech, transcription, images.
_OPENAI_HOST = "api.openai.com"
_OPENAI_CHAT = re.compile(r"^(gpt-|o\d|chatgpt-)", re.IGNORECASE)
_OPENAI_NOT_CHAT = re.compile(r"instruct|(^|-)pro($|-)|codex|deep-research|search|realtime|"
                              r"(^|-)live($|-)|audio|tts|transcribe|whisper|image|embed|"
                              r"moderation|computer-use", re.IGNORECASE)


# ---------------------------------------------------------------- state + cache

def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(when: datetime.datetime) -> str:
    return when.astimezone(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _age(iso: str | None) -> float:
    """Seconds since an ISO UTC timestamp; infinite when there is none."""
    if not iso:
        return float("inf")
    try:
        then = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return float("inf")
    return (_now() - then).total_seconds()


@dataclass
class Listing:
    """What one provider said the last time it was asked."""
    ok: bool                       # the last attempt succeeded
    checked: str                   # UTC time of the last attempt
    updated: str | None = None     # UTC time of the last success (the list's age)
    error: str | None = None       # why the last attempt failed
    models: list[ModelManifest] = field(default_factory=list)

    @property
    def known(self) -> bool:
        """The provider answered at least once, so its list says what exists."""
        return self.updated is not None

    def to_cache(self) -> dict:
        return {"ok": self.ok, "checked": self.checked, "updated": self.updated,
                "error": self.error, "models": [m.to_cache() for m in self.models]}

    @classmethod
    def from_cache(cls, data: dict) -> "Listing":
        found = []
        for raw in data.get("models") or []:
            try:
                found.append(ModelManifest.from_cache(raw))
            except (KeyError, TypeError, ValueError):
                continue
        return cls(ok=bool(data.get("ok")), checked=str(data.get("checked") or ""),
                   updated=data.get("updated"), error=data.get("error"), models=found)


_state: dict[str, Listing] = {}
_loaded = False
_task: asyncio.Task | None = None


def ensure_loaded() -> None:
    """Read the cache once (the first lookup, or the server start)."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        providers = data.get("providers") if isinstance(data, dict) else None
    except (OSError, ValueError):
        providers = None
    _state.clear()
    for name, raw in (providers or {}).items():
        if isinstance(raw, dict):
            _state[name] = Listing.from_cache(raw)
    _publish()


def reset() -> None:
    """Forget everything in memory (the next lookup reads the cache again)."""
    global _loaded, _task
    _loaded = False
    _task = None
    _state.clear()
    models.DISCOVERED.clear()
    models.ALIASES.clear()


def _save() -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_PATH.with_suffix(".json.tmp")
        body = {"version": 1, "providers": {n: l.to_cache() for n, l in _state.items()}}
        tmp.write_text(json.dumps(body, indent=2), encoding="utf-8")
        tmp.replace(CACHE_PATH)
    except OSError as exc:  # a read-only disk costs the offline copy, not the list
        _log.warning("could not write the model cache %s: %s", CACHE_PATH, exc)


def _publish() -> None:
    """Rebuild models.DISCOVERED (listed models no manifest names) and
    models.ALIASES (another id of a listed model -> the id it runs as: a listed id
    a manifest enriches -> the manifest, an alias the provider reports -> the
    model)."""
    models.DISCOVERED.clear()
    models.ALIASES.clear()
    for listing in _state.values():
        for found in listing.models:
            manifest = _manifest_for(found)
            if manifest is None:
                models.DISCOVERED[found.id] = found
            target = manifest.id if manifest is not None else found.id
            for other in (found.id, *(f"{found.provider}/{a}" for a in found.aliases)):
                if other != target and other not in models.MODELS:
                    models.ALIASES.setdefault(other, target)


def _manifest_for(found: ModelManifest) -> ModelManifest | None:
    """The manifest that enriches a listed model: same provider and kind, naming
    the model by its provider name, an alias, or the same id."""
    names = {found.model, *found.aliases}
    for manifest in models.MODELS.values():
        if manifest.provider != found.provider or manifest.kind != found.kind:
            continue
        if manifest.id == found.id or manifest.model in names:
            return manifest
    return None


# ------------------------------------------------------------ provider mapping

def _supported(cap: Any) -> bool:
    """An Anthropic capability entry ({"supported": true}) or a bare flag."""
    if isinstance(cap, dict):
        return bool(cap.get("supported"))
    return cap is True


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _llm_manifest(provider: str, model: str, *, label: str = "", summary: str = "",
                  context: int = 0, inputs: list[str] | None = None, tools: bool = False,
                  thinking: bool = False, thinking_style: str = "", json_out: bool = False,
                  params: list[Param] | None = None, aliases: list[str] | None = None,
                  think_param: Param | None = None) -> ModelManifest:
    """A chat model as the LLM node reads it, with the capability knobs its flags
    imply (or `think_param`, a lever shaped from what the provider reported)."""
    extra = _capability_params(thinking and think_param is None, thinking_style, json_out)
    if think_param is not None:
        extra.insert(0, think_param)
    return ModelManifest(
        id=f"{provider}/{model}", provider=provider, model=model, label=label or model,
        summary=summary, context=context, kind="llm", inputs=inputs or ["text"],
        outputs=["text"], tools=tools, thinking=thinking, thinking_style=thinking_style,
        json=json_out, params=list(params or []) + extra, source="discovered",
        aliases=list(aliases or []))


def ollama_manifest(tag: dict, show: dict | None) -> ModelManifest | None:
    """An installed Ollama model from its /api/tags entry and /api/show reply.
    `completion` makes it a chat model, `embedding` an embed model; one that does
    neither (an image generator) is not listed, since no node runs it. The node
    sends Ollama text and images only, so `audio` adds no input and `image`
    (image output) adds no output."""
    name = str(tag.get("name") or tag.get("model") or "").strip()
    if not name:
        return None
    caps = {str(c) for c in (show or {}).get("capabilities") or []}
    if not caps:
        caps = {"completion"}  # an Ollama too old to report, or a failed /api/show
    info = (show or {}).get("model_info") or {}
    context = next((_int(v) for k, v in info.items() if k.endswith(".context_length")), 0)
    details = tag.get("details") or (show or {}).get("details") or {}
    facts = " · ".join(str(details[k]) for k in ("parameter_size", "quantization_level")
                       if details.get(k))
    summary = "Installed in Ollama" + (f" ({facts})" if facts else "")
    aliases = [name[: -len(":latest")]] if name.endswith(":latest") else []
    if "completion" in caps:
        # Ollama's own defaults; format:"json" works on every Ollama chat model.
        params = [
            Param(name="temperature", type="float", default=0.8, min=0.0, max=2.0, step=0.05),
            Param(name="num_ctx", type="int", default=min(8192, context) if context else 8192,
                  min=256, max=context or 131072),
            Param(name="keep_alive", type="text", default="5m"),
        ]
        return _llm_manifest(
            "ollama", name, summary=summary, context=context,
            inputs=["text", "image"] if "vision" in caps else ["text"],
            tools="tools" in caps, thinking="thinking" in caps,
            thinking_style="bool" if "thinking" in caps else "", json_out=True,
            params=params, aliases=aliases)
    if "embedding" in caps:
        return ModelManifest(id=f"ollama/{name}", provider="ollama", model=name, label=name,
                             summary=summary, context=context, kind="embed",
                             source="discovered", aliases=aliases)
    return None


def xai_manifest(entry: dict) -> ModelManifest | None:
    """A chat model from xAI's /v1/language-models. A model whose outputs lack
    text is skipped. xAI reports modalities and reasoning efforts only, so tools
    and JSON stay off until a manifest says otherwise."""
    mid = str(entry.get("id") or "").strip()
    outputs = entry.get("output_modalities") or ["text"]
    if not mid or "text" not in outputs:
        return None
    inputs = entry.get("input_modalities") or ["text"]
    caps = entry.get("capabilities") or {}
    efforts = [str(e) for e in caps.get("reasoning_effort") or [] if str(e)]
    think = None
    if efforts:
        default = str(caps.get("default_reasoning_effort") or efforts[0])
        think = Param(name=models.THINK_PARAM, type="select", options=efforts,
                      default=default if default in efforts else efforts[0],
                      label="reasoning effort")
    return _llm_manifest(
        "xai", mid, summary="Listed by xAI", thinking=bool(efforts),
        thinking_style="effort" if efforts else "",
        inputs=["text"] + (["image"] if "image" in inputs else []),
        aliases=[str(a) for a in entry.get("aliases") or [] if a], think_param=think,
        params=[Param(name="max_completion_tokens", type="int", default=2048, min=1, max=32768)])


def anthropic_manifest(entry: dict) -> ModelManifest | None:
    """A chat model from Anthropic's /v1/models. Every Claude model takes tools on
    the Messages API; images, adaptive thinking (the only thinking the node
    sends) and JSON follow the reported capabilities."""
    mid = str(entry.get("id") or "").strip()
    if not mid:
        return None
    caps = entry.get("capabilities") or {}
    thinking = caps.get("thinking") or {}
    adaptive = _supported(thinking) and _supported((thinking.get("types") or {}).get("adaptive")
                                                   if isinstance(thinking, dict) else None)
    max_out = _int(entry.get("max_tokens"))
    return _llm_manifest(
        "anthropic", mid, label=str(entry.get("display_name") or mid),
        summary="Listed by Anthropic", context=_int(entry.get("max_input_tokens")),
        inputs=["text", "image"] if _supported(caps.get("image_input")) else ["text"],
        tools=True, thinking=adaptive, thinking_style="level" if adaptive else "",
        json_out=_supported(caps.get("structured_outputs")),
        params=[Param(name="max_tokens", type="int",
                      default=min(4096, max_out) if max_out else 4096, min=1,
                      max=max_out or 64000)])


def openai_manifest(provider: str, entry: dict, *, openai_api: bool = False) -> ModelManifest | None:
    """A chat model from an OpenAI-compatible /models list. Plain servers send
    only an id, so a model is text in and out with no tools until a manifest says
    more; a server that reports modalities, context and supported parameters
    (OpenRouter, Groq, vLLM) is read as reported. `openai_api`: the list comes
    from api.openai.com, which keeps only its chat completions families."""
    mid = str(entry.get("id") or "").strip()
    if not mid:
        return None
    if openai_api and (not _OPENAI_CHAT.match(mid) or _OPENAI_NOT_CHAT.search(mid)):
        return None
    arch = entry.get("architecture") if isinstance(entry.get("architecture"), dict) else {}
    ins, outs = arch.get("input_modalities"), arch.get("output_modalities")
    if outs is not None:
        if "text" not in outs:
            return None
    elif _NOT_CHAT.search(mid):
        return None
    supported = {str(p) for p in entry.get("supported_parameters") or []}
    context = _int(entry.get("context_length") or entry.get("context_window")
                   or entry.get("max_model_len"))
    return _llm_manifest(
        provider, mid, label=str(entry.get("name") or mid),
        summary=f"Listed by the {provider} endpoint", context=context,
        inputs=["text", "image"] if ins and "image" in ins else ["text"],
        tools="tools" in supported,
        json_out=bool(supported & {"response_format", "structured_outputs"}))


# ------------------------------------------------------------------ discovery

def _ollama_base() -> str:
    return os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")


def _check(resp: httpx.Response, vendor: str) -> None:
    if not resp.is_success:
        raise RuntimeError(f"{vendor} {resp.status_code}: {resp.text[:200]}")


async def _discover_ollama(client: httpx.AsyncClient) -> list[ModelManifest]:
    base = _ollama_base()
    resp = await client.get(f"{base}/api/tags")
    _check(resp, "Ollama")
    tags = [t for t in resp.json().get("models") or [] if isinstance(t, dict)]
    gate = asyncio.Semaphore(4)

    async def one(tag: dict) -> ModelManifest | None:
        name = tag.get("name") or tag.get("model")
        async with gate:
            try:
                shown = await client.post(f"{base}/api/show", json={"model": name})
                show = shown.json() if shown.is_success else None
            except (httpx.HTTPError, ValueError) as exc:
                _log.info("ollama show %s failed: %s", name, exc)
                show = None
        return ollama_manifest(tag, show)

    found = await asyncio.gather(*(one(t) for t in tags))
    return [m for m in found if m is not None]


async def _discover_xai(client: httpx.AsyncClient) -> list[ModelManifest]:
    key = _secrets.get_secret("XAI_API_KEY") or ""
    resp = await client.get(XAI_MODELS_URL, headers={"Authorization": f"Bearer {key}"})
    _check(resp, "xAI")
    return [m for e in resp.json().get("models") or [] if isinstance(e, dict)
            if (m := xai_manifest(e)) is not None]


async def _discover_anthropic(client: httpx.AsyncClient) -> list[ModelManifest]:
    key = _secrets.get_secret("ANTHROPIC_API_KEY") or ""
    headers = {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
    found: list[ModelManifest] = []
    after = None
    for _ in range(20):  # 1000 per page; the cap only guards a looping cursor
        params: dict[str, Any] = {"limit": 1000}
        if after:
            params["after_id"] = after
        resp = await client.get(ANTHROPIC_MODELS_URL, params=params, headers=headers)
        _check(resp, "Anthropic")
        data = resp.json()
        found += [m for e in data.get("data") or [] if isinstance(e, dict)
                  if (m := anthropic_manifest(e)) is not None]
        if not data.get("has_more") or not data.get("last_id"):
            break
        after = data["last_id"]
    return found


async def _discover_openai(endpoint: endpoints.Endpoint,
                           client: httpx.AsyncClient) -> list[ModelManifest]:
    key = endpoint.key()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    resp = await client.get(f"{endpoint.base_url}/models", headers=headers)
    _check(resp, endpoint.name)
    data = resp.json()
    items = data.get("data") if isinstance(data, dict) else data
    openai_api = urlsplit(endpoint.base_url).hostname == _OPENAI_HOST
    return [m for e in items or [] if isinstance(e, dict)
            if (m := openai_manifest(endpoint.name, e, openai_api=openai_api)) is not None]


Discoverer = Callable[[httpx.AsyncClient], Awaitable[list[ModelManifest]]]


def _discoverers(listed: list[endpoints.Endpoint] | None = None) -> dict[str, Discoverer]:
    """The providers to ask now: Ollama always (answering is the test), a cloud
    provider once its key exists, every usable OpenAI-compatible endpoint
    (`listed`: the custom endpoints, when the caller already read them)."""
    _secrets.ensure_loaded()
    out: dict[str, Discoverer] = {"ollama": _discover_ollama}
    if _secrets.get_secret("ANTHROPIC_API_KEY"):
        out["anthropic"] = _discover_anthropic
    if _secrets.get_secret("XAI_API_KEY"):
        out["xai"] = _discover_xai
    for endpoint in endpoints.usable(listed):
        out[endpoint.name] = functools.partial(_discover_openai, endpoint)
    return out


def _short_error(exc: BaseException) -> str:
    text = str(exc).strip() or type(exc).__name__
    return _secrets.redact(text)[:200]


async def _refresh() -> None:
    ensure_loaded()
    asked = _discoverers()
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        results = await asyncio.gather(*(fn(client) for fn in asked.values()),
                                       return_exceptions=True)
    checked = _iso(_now())
    fresh: dict[str, Listing] = {}
    for name, result in zip(asked, results):
        before = _state.get(name)
        if isinstance(result, BaseException):
            if isinstance(result, asyncio.CancelledError):
                raise result
            # unreachable or refused: keep the last good list (the offline copy).
            fresh[name] = Listing(ok=False, checked=checked,
                                  updated=before.updated if before else None,
                                  error=_short_error(result),
                                  models=before.models if before else [])
        else:
            fresh[name] = Listing(ok=True, checked=checked, updated=checked, models=result)
    # a provider no longer usable (its key removed) drops out of the list.
    _state.clear()
    _state.update(fresh)
    _publish()
    _save()


def _running(loop: asyncio.AbstractEventLoop) -> bool:
    return _task is not None and not _task.done() and _task.get_loop() is loop


def _log_failure(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception() is not None:
        _log.warning("model list refresh failed: %s", task.exception())


def _start(loop: asyncio.AbstractEventLoop) -> asyncio.Task:
    global _task
    if not _running(loop):
        _task = loop.create_task(_refresh())
        _task.add_done_callback(_log_failure)
    return _task


async def refresh() -> None:
    """Ask every usable provider now and wait for the answers (joins a refresh
    already running). Never raises for a provider that fails: it keeps its list."""
    task = _start(asyncio.get_running_loop())
    await asyncio.shield(task)


def refreshing() -> bool:
    try:
        return _running(asyncio.get_running_loop())
    except RuntimeError:
        return False


def schedule_refresh() -> None:
    """Refresh in the background (no-op without a running loop, or with
    AUTO_REFRESH off)."""
    if not AUTO_REFRESH:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _start(loop)


def refresh_if_stale() -> None:
    """Schedule a background refresh when a usable provider was never asked, or
    was last asked more than STALE_AFTER ago."""
    ensure_loaded()
    if any(_age(_state[n].checked if n in _state else None) > STALE_AFTER
           for n in _usable_names()):
        schedule_refresh()


def startup() -> None:
    """Server start: read the cache, then refresh in the background."""
    ensure_loaded()
    schedule_refresh()


async def shutdown() -> None:
    """Server stop: cancel a refresh still running on this loop."""
    task = _task
    if task is not None and _running(asyncio.get_running_loop()):
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


# --------------------------------------------------------------- merged list

@dataclass
class View:
    """What the merged list is read from, taken once per lookup: the custom
    endpoints (one read of their file) and the providers discovery asks now. A
    caller that looks up many rows (the picker payload, a graph's validation)
    passes one View down instead of reading them again for every row."""
    custom: dict[str, endpoints.Endpoint]
    usable: tuple[str, ...]

    @classmethod
    def now(cls) -> "View":
        _secrets.ensure_loaded()
        listed = endpoints.list_endpoints()
        return cls({e.name: e for e in listed}, tuple(_discoverers(listed)))

    def has_key(self, provider: str) -> bool:
        """A cloud provider whose key exists, or a usable OpenAI-compatible endpoint."""
        env = _secrets.PROVIDERS.get(provider)
        if env:
            return bool(_secrets.get_secret(env))
        endpoint = self.custom.get(provider)
        return endpoint is not None and endpoint.ready()

    def rank(self, provider: str) -> tuple[int, str]:
        order = list(_secrets.PROVIDERS)
        if provider in order:
            return order.index(provider), provider
        return len(order) + (0 if provider in self.custom else 1), provider


def _usable_names() -> list[str]:
    """The providers discovery asks now, without calling any of them."""
    return list(_discoverers())


def _provider_label(provider: str) -> str:
    return {"ollama": "Ollama", "xai": "xAI", "anthropic": "Anthropic", "openai": "OpenAI",
            "google": "Google", "fish": "Fish Audio", "elevenlabs": "ElevenLabs"}.get(
        provider, provider)


def _unlisted_reason(provider: str) -> str:
    if provider == "ollama":
        return "not installed in Ollama: pull it in Connections"
    return f"no longer offered by {_provider_label(provider)}"


def _unlisted_availability(provider: str, view: View) -> tuple[bool, str | None]:
    """Whether a model of a provider WITHOUT a known list can run, and why not."""
    if provider == "ollama":
        return False, "Ollama is not running"
    env = _secrets.PROVIDERS.get(provider)
    if env is not None:
        return (True, None) if _secrets.get_secret(env) else (
            False, f"no {_provider_label(provider)} key: add it in Connections")
    endpoint = view.custom.get(provider)
    if endpoint is not None:
        return (True, None) if endpoint.ready() else (
            False, f"secret {endpoint.key_secret} is not defined: add it in Connections")
    return True, None  # a provider nothing here knows (a pack's own) is assumed usable


@dataclass
class Entry:
    """One row of the merged list."""
    manifest: ModelManifest
    source: str                 # manifest | discovered | both
    available: bool
    reason: str | None = None
    unlisted: bool = False      # its provider's list is known and lacks it
    installed: bool = False     # the provider listed it (discovered or both)


def entries(view: View | None = None) -> list[Entry]:
    """The merged list: every manifest and every listed model, once each. Per
    provider: listed models with a manifest first (curated), then listed models
    without one (in the provider's order), then manifests it does not list."""
    ensure_loaded()
    view = view or View.now()
    usable = set(view.usable)
    listings = {n: l for n, l in _state.items() if n in usable and l.known}
    listed: dict[str, ModelManifest] = {}      # manifest id -> the listed model
    extra: list[ModelManifest] = []
    for listing in listings.values():
        for found in listing.models:
            manifest = _manifest_for(found)
            if manifest is None:
                extra.append(found)
            else:
                listed.setdefault(manifest.id, found)
    rows: list[tuple[tuple, Entry]] = []
    for manifest in sorted(models.MODELS.values(), key=lambda m: m.id):
        found = listed.get(manifest.id)
        rank = view.rank(manifest.provider)
        if found is not None:
            merged = dataclasses.replace(
                manifest, context=manifest.context or found.context,
                aliases=sorted({*manifest.aliases, found.model, *found.aliases} - {manifest.model}))
            rows.append(((rank, 0, 0), Entry(merged, "both", True, installed=True)))
        elif manifest.provider in listings and manifest.kind in _LISTED_KINDS.get(
                manifest.provider, _DEFAULT_LISTED_KINDS):
            rows.append(((rank, 2, 0), Entry(manifest, "manifest", False,
                                             _unlisted_reason(manifest.provider), unlisted=True)))
        else:
            ok, reason = _unlisted_availability(manifest.provider, view)
            rows.append(((rank, 2, 0), Entry(manifest, "manifest", ok, reason)))
    for index, found in enumerate(extra):
        rows.append(((view.rank(found.provider), 1, index),
                     Entry(found, "discovered", True, installed=True)))
    rows.sort(key=lambda row: row[0])
    return [entry for _, entry in rows]


@dataclass
class Snapshot:
    """The merged list with the View it was built from, for a caller that looks
    up several models at once (validation checks every model widget of a graph
    against one Snapshot)."""
    view: View
    rows: list[Entry]

    @classmethod
    def now(cls) -> "Snapshot":
        view = View.now()
        return cls(view, entries(view))


def catalog(rows: list[Entry] | None = None) -> list[dict]:
    """The merged list as the picker reads it: every manifest field plus `source`
    (manifest, discovered or both), `available` and, when unavailable, `reason`."""
    out = []
    for entry in entries() if rows is None else rows:
        row = entry.manifest.as_dict(probe=False)
        row.update(source=entry.source, available=entry.available)
        if entry.reason:
            row["reason"] = entry.reason
        out.append(row)
    return out


def _chat(manifest: ModelManifest) -> bool:
    return manifest.kind == "llm" and "text" in manifest.outputs


def resolve_auto(snap: Snapshot | None = None) -> ModelManifest | None:
    """What an "auto" LLM runs: the first installed Ollama chat model (Ollama
    answered the last time it was asked), else the first chat model of a provider
    with a key, else None (the node replies with AUTO_MOCK_REPLY)."""
    snap = snap or Snapshot.now()
    ollama = _state.get("ollama")
    if ollama is not None and ollama.ok:
        for entry in snap.rows:
            if entry.manifest.provider == "ollama" and entry.installed and _chat(entry.manifest):
                return entry.manifest
    for entry in snap.rows:
        m = entry.manifest
        if m.provider != "ollama" and entry.available and _chat(m) and snap.view.has_key(m.provider):
            return m
    return None


def _lookup(model_id: str, rows: list[Entry]) -> Entry | None:
    target = models.ALIASES.get(model_id, model_id)
    return next((e for e in rows if e.manifest.id == target), None)


def _family(name: str) -> str:
    """A model's family: its name up to the first digit (qwen3.5:9b -> qwen,
    claude-sonnet-4-6 -> claude-sonnet, grok-4.20 -> grok)."""
    base = name.lower().split(":", 1)[0]
    stem = re.split(r"\d", base, maxsplit=1)[0].strip("-_. ")
    return stem or base


def closest(model_id: str, kind: str, rows: list[Entry] | None = None) -> str | None:
    """The available model of `kind` nearest to `model_id`: same provider and
    family first, then same provider, then same family, then any; ties go to the
    most similar name."""
    provider, _, name = model_id.partition("/") if "/" in model_id else ("", "", model_id)
    family = _family(name)

    def score(entry: Entry) -> tuple:
        m = entry.manifest
        same_provider = m.provider == provider
        same_family = _family(m.model) == family
        tier = 0 if same_provider and same_family else 1 if same_provider else 2 if same_family else 3
        ratio = difflib.SequenceMatcher(None, name.lower(), m.model.lower()).ratio()
        return tier, -ratio, m.id

    candidates = [e for e in (entries() if rows is None else rows)
                  if e.available and e.manifest.kind == kind and e.manifest.id != model_id]
    return min(candidates, key=score).manifest.id if candidates else None


def model_problem(model_id: str, kind: str, snap: Snapshot | None = None) -> str | None:
    """Why a saved graph cannot run `model_id` on a node that takes `kind` models:
    the model vanished (no manifest names it and no provider lists it, or its
    provider's list no longer has it) or it is another kind of model. None when
    it can run, or when it is empty, "auto" or the offline mock. A caller that
    checks several models passes one Snapshot."""
    if not model_id or model_id == AUTO or model_id.startswith("mock/"):
        return None
    snap = snap or Snapshot.now()
    entry = _lookup(model_id, snap.rows)
    if entry is not None and entry.manifest.kind != kind:
        return f"model {model_id} is a {entry.manifest.kind} model; this node takes {kind} models"
    if entry is not None and not entry.unlisted:
        return None
    provider = model_id.split("/", 1)[0] if "/" in model_id else ""
    if entry is not None:
        why = _unlisted_reason(provider)
    elif provider in _state and _state[provider].known and provider in snap.view.usable:
        why = _unlisted_reason(provider)
    else:
        known = provider in _secrets.PROVIDERS or provider in snap.view.custom
        ok, reason = _unlisted_availability(provider, snap.view) if known else (True, None)
        why = "not in the model list" + (f" ({reason})" if not ok and reason else "")
    nearest = closest(model_id, kind, snap.rows)
    hint = (f"closest available: {nearest}" if nearest
            else f"pick another model, or connect a {kind} model in Connections")
    return f"model {model_id} is {why}; {hint}"


def payload() -> dict:
    """GET /api/models: the merged list, what "auto" resolves to (None = the
    mock), each asked provider's last answer, and whether a refresh is running.
    Times are ISO 8601 UTC."""
    snap = Snapshot.now()
    providers = {}
    for name in snap.view.usable:
        listing = _state.get(name)
        if listing is None:
            continue
        providers[name] = {"ok": listing.ok, "checked": listing.checked,
                           "updated": listing.updated, "error": listing.error,
                           "count": len(listing.models)}
    updated = max((p["updated"] for p in providers.values() if p["updated"]), default=None)
    auto = resolve_auto(snap)
    return {"models": catalog(snap.rows), "auto": auto.id if auto else None,
            "updated": updated, "refreshing": refreshing(), "providers": providers}
