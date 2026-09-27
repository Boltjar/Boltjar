"""
boltjar.models: the model registry.

Each available model is declared in its own TOML file (see boltjar/nodes/core/models/).
A manifest carries the model's provider, its capabilities (which input/output
modalities it accepts, whether it does tool-calls), and its configurable params
(temperature, top_p, keep_alive, ...). The LLM node reshapes its inputs,
outputs, and knobs to the selected model's manifest, so a vision model grows an
image input and an audio model grows an audio input, all from one node.

Models are data, not code: drop a new .toml in the models dir and it appears.
The core pack's manifests load first, then each pack's (packs/<id>/models/),
then the user's own (user/models/); see boltjar.packs.
"""
from __future__ import annotations

import logging
import pathlib
import tomllib
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger("boltjar.models")


@dataclass
class Param:
    """One configurable model parameter (a knob, promotable to a typed input)."""
    name: str
    type: str = "float"        # float | int | text | bool | select
    default: Any = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: list[Any] = field(default_factory=list)
    label: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name, "type": self.type, "default": self.default,
            "min": self.min, "max": self.max, "step": self.step,
            "options": self.options, "label": self.label or self.name.replace("_", " "),
        }


@dataclass
class ModelManifest:
    """A concrete model and everything the node needs to reshape and call it."""
    id: str
    provider: str
    model: str
    label: str
    summary: str = ""
    context: int = 0
    # the node family this manifest serves: "llm" (chat), "tts" (text-to-speech),
    # "stt" (speech-to-text). The picker filters by this so a TTS node only lists
    # TTS models. Default "llm" so older TOMLs without the field keep working.
    kind: str = "llm"
    inputs: list[str] = field(default_factory=lambda: ["text"])   # accepted modalities
    outputs: list[str] = field(default_factory=lambda: ["text"])  # emitted modalities
    tools: bool = False
    # capability flags read straight from the manifest (default off, so older
    # TOMLs without them keep working):
    thinking: bool = False        # the model can reason / emits a trace
    thinking_style: str = ""      # how the lever reshapes: effort | level | budget | token | bool
    json: bool = False            # the model can be constrained to JSON output
    params: list[Param] = field(default_factory=list)

    def param_defaults(self) -> dict:
        return {p.name: p.default for p in self.params}

    def as_dict(self, probe: bool = True) -> dict:
        """The manifest as the picker reads it. `available` says whether the
        provider is usable right now, which checks its key or pings Ollama; with
        probe=False nothing is checked and it is None (an offline reader such as
        a docs generator)."""
        # Lazy import to avoid a circular dependency at module load time.
        from boltjar.secrets import provider_connected, PROVIDERS
        if not probe:
            available = None
        elif self.provider in PROVIDERS:
            available = provider_connected(self.provider)
        else:
            available = True  # unknown providers are treated as available
        return {
            "id": self.id, "provider": self.provider, "model": self.model,
            "label": self.label, "summary": self.summary, "context": self.context,
            "kind": self.kind,
            "inputs": self.inputs, "outputs": self.outputs, "tools": self.tools,
            "thinking": self.thinking, "thinking_style": self.thinking_style,
            "json": self.json,
            "params": [p.as_dict() for p in self.params],
            "available": available,
        }


MODELS: dict[str, ModelManifest] = {}

# Canonical names for the two capability-driven knobs the LLM node surfaces. The
# node and `_call_model` read these param values and reshape them to each
# provider's wire format. Kept here so the names are defined in one place.
THINK_PARAM = "think"   # the thinking lever (bool / effort-or-level select)
JSON_PARAM = "json"     # the JSON-output toggle (bool)


def _capability_params(thinking: bool, thinking_style: str, json: bool) -> list[Param]:
    """The synthetic knobs a model's capabilities imply: a thinking lever (shaped
    by thinking_style) and a JSON-output toggle. Empty when unsupported. They are
    ordinary Params, so they render as knobs and promote to ports like any other."""
    out: list[Param] = []
    if thinking:
        if thinking_style == "effort":
            out.append(Param(name=THINK_PARAM, type="select", default="low",
                             options=["none", "low", "medium", "high"],
                             label="reasoning effort"))
        elif thinking_style == "level":
            out.append(Param(name=THINK_PARAM, type="select", default="off",
                             options=["off", "adaptive"], label="thinking"))
        elif thinking_style in ("budget", "token"):
            out.append(Param(name=THINK_PARAM, type="int", default=0,
                             min=0, max=32000, label="thinking budget"))
        else:  # "bool" or unspecified
            out.append(Param(name=THINK_PARAM, type="bool", default=False,
                             label="thinking"))
    if json:
        out.append(Param(name=JSON_PARAM, type="bool", default=False,
                         label="JSON output"))
    return out


def _parse(path: pathlib.Path) -> ModelManifest:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    params = [
        Param(
            name=p["name"], type=p.get("type", "float"), default=p.get("default"),
            min=p.get("min"), max=p.get("max"), step=p.get("step"),
            options=list(p.get("options", [])), label=p.get("label", ""),
        )
        for p in data.get("params", [])
    ]
    thinking = bool(data.get("thinking", False))
    thinking_style = str(data.get("thinking_style", ""))
    json_cap = bool(data.get("json", False))
    params += _capability_params(thinking, thinking_style, json_cap)
    return ModelManifest(
        id=data["id"], provider=data["provider"],
        model=data.get("model", data["id"].split("/", 1)[-1]),
        label=data.get("label", data["id"]), summary=data.get("summary", ""),
        context=int(data.get("context", 0)),
        kind=str(data.get("kind", "llm")),
        inputs=list(data.get("inputs", ["text"])),
        outputs=list(data.get("outputs", ["text"])),
        tools=bool(data.get("tools", False)),
        thinking=thinking,
        thinking_style=thinking_style,
        json=json_cap,
        params=params,
    )


def load_models(directory: pathlib.Path | str, *, replace: bool = True) -> int:
    """Register every *.toml manifest in `directory`. A bad file is skipped, not
    fatal. With replace=False a manifest whose id is already declared is skipped
    too (a pack cannot redefine a model another source declared); with replace
    (the core pack, the user's own folder) it takes that id over."""
    directory = pathlib.Path(directory)
    if not directory.exists():
        return 0
    count = 0
    for path in sorted(directory.glob("*.toml")):
        try:
            manifest = _parse(path)
        except Exception as exc:  # one malformed manifest never breaks the server
            _log.warning("skip model %s: %s", path, exc)
            continue
        if manifest.id in MODELS:
            if not replace:
                _log.warning("skip model %s: %r is already declared", path, manifest.id)
                continue
            _log.info("model %r from %s replaces the one declared before", manifest.id, path)
        MODELS[manifest.id] = manifest
        count += 1
    return count


def get(model_id: str) -> ModelManifest | None:
    return MODELS.get(model_id)


def catalog(probe: bool = True) -> list[dict]:
    """All manifests as plain dicts, sorted for a stable picker order. probe=False
    skips the availability check (no key lookup, no Ollama ping)."""
    return [m.as_dict(probe) for m in sorted(MODELS.values(), key=lambda m: (m.provider, m.id))]
