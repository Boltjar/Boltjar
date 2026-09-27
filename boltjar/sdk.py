"""
boltjar.sdk: the contract every node implements.

A node is a plain Python class decorated with ``@node(...)``. It declares typed
input and output ports (class attributes ``inputs`` / ``outputs``) and config
fields (annotated class attributes, or explicit ``Widget`` values) which the
editor renders as inspector widgets. Its execution surface is chosen by its
``Kind`` and driven by the runtime (see ``boltjar.runtime``):

    Kind.VALUE      def value(self) -> dict            # a constant source
    Kind.TRIGGER    async def start(self, ctx)         # self-driven actor
                    def on_input(self, port, v, ctx)   # or input-driven
    Kind.SENSOR     def read(self, ctx) -> dict
    Kind.TRANSFORM  def run(self, **inputs) -> dict     # sync or async
    Kind.LOGIC      def route(self, **inputs) -> str    # returns an output port
    Kind.SERVICE    async def open(self, ctx); call(...); async def close(self)
    Kind.STORE      async def open(self, ctx); read/write; async def close(self)
    Kind.OUTPUT     def deliver(self, value, ctx, inputs=None)

A node that does work declares an input with ``trigger=True``: an event landing
there fires it, and it pulls its other inputs at that moment. A trigger is the
only way a node runs, so every trigger input must be wired (a graph with an
unwired one does not turn On) and can never be ``optional``.

Nothing here imports a runtime; the SDK is the pure contract so node packs and
the server can be reasoned about in isolation.
"""
from __future__ import annotations

import enum
import re
import types as _pytypes
import typing
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


class NodeFailure(Exception):
    """A node's declared failure branch. Raise it (``from`` the original error)
    to fail with `outputs` to emit first, e.g. the LLM's ``{"error": message}``:
    the runtime emits them on the firing turn, so whatever is wired to that
    branch reacts, and then reports the failure like any other node error (log
    + node_error, the node is never marked ok). None of the node's normal
    outputs are emitted, so a failure can never flow on as a result."""

    def __init__(self, message: str, outputs: Optional[dict] = None) -> None:
        super().__init__(message)
        self.outputs: dict = dict(outputs or {})


class Kind(str, enum.Enum):
    VALUE = "value"
    TRIGGER = "trigger"
    SENSOR = "sensor"
    TRANSFORM = "transform"
    LOGIC = "logic"
    SERVICE = "service"
    STORE = "store"
    OUTPUT = "output"
    SUBGRAPH = "subgraph"


@dataclass
class Port:
    """A typed input or output socket. Inputs may be triggering or latching.

    A trigger input (`trigger=True`) fires the node, and a trigger is the only
    way a node runs, so it must be wired: a graph with an unwired trigger does
    not turn On, and declaring one `optional` raises here. A growable trigger
    needs a wire on at least one of its sockets. Every other input latches data
    and is pulled when the node fires; `optional` lets one of those stay
    unwired."""
    name: str
    type: str
    growable: bool = False   # one socket per wire, an empty socket always ready
    optional: bool = False   # a data input that may stay unwired (never a trigger)
    trigger: bool = False    # a triggering input fires the node; others latch
    default: Any = None
    # op-shaping: show this port only when the named config field equals one of
    # `op_values` (e.g. the KV `value` output only exists for operation="get").
    # Empty op_field means "always shown" (the common case).
    op_field: Optional[str] = None
    op_values: tuple[str, ...] = ()
    # a growable port names each minted socket after the wired source node; when
    # `ghost_base` is set, the socket is a meaningful field/column (Build JSON key,
    # Insert/Update SET column) rather than an auto-numbered slot.
    ghost_base: Optional[str] = None
    # a one-click editor affordance: an OUTPUT port carrying `scaffold` offers to
    # spawn the named node type and pre-wire this port into it (the target socket
    # is chosen by type-compatibility). Read GENERICALLY by the editor (no
    # per-node-id branch), so the gesture appears whenever a port declares it.
    # e.g. a Tool's `call` output scaffolds its Tool Args body. Purely UX; the
    # runtime ignores it.
    scaffold: Optional[str] = None

    def __post_init__(self) -> None:
        if self.trigger and self.optional:
            raise ValueError(f"trigger input {self.name!r} cannot be optional: a trigger "
                             f"fires the node and must be wired (drop optional=True)")

    def as_dict(self) -> dict:
        return {
            "name": self.name, "type": self.type, "growable": self.growable,
            "optional": self.optional, "trigger": self.trigger,
            "op_field": self.op_field, "op_values": list(self.op_values),
            "ghost_base": self.ghost_base, "scaffold": self.scaffold,
        }


@dataclass
class Widget:
    """A config field rendered on the node body (and, when it needs room, in a
    shared modal). Its behaviour is declared here, never guessed by the editor:
    one declaration drives the knob, the right-click menu, promotion, the
    secret/tag autocomplete, and the op-shaped reshape."""
    name: str = ""
    # text | number | bool | select | code | secret | color | model | schema. A
    # schema widget holds the core Database node's declared tables, and only that
    # node's tables are created from one.
    kind: str = "text"
    default: Any = None
    options: list[Any] = field(default_factory=list)
    label: str = ""
    # for numeric widgets: when min+max are set the editor draws a slider (like the
    # model-param knobs), so every numeric knob reads the same across nodes.
    min: Any = None
    max: Any = None
    step: Any = None
    # --- declared behaviour (the editor reads these, never a per-id table) ---
    # "body" (inline knob) | "modal" (opens the shared modal) | "hidden" (saved with
    # the graph, never drawn: a value a surface of the node keeps, e.g. the tables
    # the Database node's schema editor declares)
    surface: str = "body"
    promotable: bool = True  # offer right-click "Convert to input" (knob -> typed port)
    port_type: str = "any"   # the input port's type when this widget is promoted
    template: bool = False   # value carries {tag} pipes substituted from wired inputs
    # value may REFERENCE a secret via {{secret.NAME}} (autocomplete on `{{`). This
    # is distinct from kind="secret", which HOLDS a masked credential.
    accepts_secrets: bool = False
    # op-shaping: show this widget only when the named field equals one of op_values.
    op_field: Optional[str] = None
    op_values: tuple[str, ...] = ()
    # an example shown in the empty field (greyed), e.g. the col=value format.
    placeholder: str = ""
    # dynamic dropdown: populate this field's options from a live source instead
    # of `options`. "db.tables" -> the tables of the wired db; "kv.keys" -> the
    # keys of the wired kv. The editor resolves it from the connected store.
    options_from: Optional[str] = None
    # a code/text field that GROWS vertically when the node is resized. A node with
    # >=1 expandable field becomes resizable; only expandable fields take the extra
    # height (proportionally if several), every other knob stays fixed. The design
    # system enforces a min so the node can never shrink to clip content.
    expand: bool = False
    # a model picker (kind="model") lists the models of this family: llm, tts, stt,
    # embed or rerank (the manifest `kind`). Declared here so any pack node can
    # carry a model picker; the editor never keys the family off a node id. Its
    # `options` are the special values the picker offers above the model list
    # ("auto" on the LLM: the runtime picks the model, see boltjar.model_discovery).
    model_kind: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "name": self.name, "kind": self.kind, "default": self.default,
            "options": self.options, "label": self.label,
            "min": self.min, "max": self.max, "step": self.step,
            "surface": self.surface, "promotable": self.promotable,
            "port_type": self.port_type, "template": self.template,
            "accepts_secrets": self.accepts_secrets,
            "op_field": self.op_field, "op_values": list(self.op_values),
            "placeholder": self.placeholder, "options_from": self.options_from,
            "expand": self.expand,
            # a model picker with no declared family lists LLMs (the manifest default).
            "model_kind": (self.model_kind or "llm") if self.kind == "model" else None,
        }

    def coerce(self, value: Any) -> Any:
        """The saved `value` as this widget's kind reads it, so a node runs what
        the editor shows. A graph saved while a knob was still a text box holds
        strings ("false", "3"); a bool reads "false" as off (never the truthy
        string) and a number reads "3" as 3. Anything else passes through."""
        if self.kind == "bool":
            if isinstance(value, str):
                return value.strip().lower() in _TRUE_WORDS
            return bool(value)
        if self.kind == "number" and isinstance(value, str):
            text = value.strip()
            if not text:
                return 0  # the number knob shows a blank as 0
            for parse in (int, float):
                try:
                    return parse(text)
                except ValueError:
                    pass
        return value


# the strings a bool knob reads as on (mirrors the editor's knobBool); every
# other string reads as off.
_TRUE_WORDS = frozenset({"true", "1", "yes", "on"})


def select(options: list[Any], default: Any = None) -> Widget:
    """A dropdown config field. The default is the first option unless given."""
    return Widget(kind="select", options=list(options),
                  default=default if default is not None else (options[0] if options else None))


def model(model_kind: str = "llm", default: str = "", *, auto: bool = False,
          label: str = "Model") -> Widget:
    """A model picker listing the models of one family (llm, tts, stt, embed,
    rerank). `default` is the model id the node runs with when nothing is picked.
    `auto` offers the "auto" value, which the node must resolve itself at run time
    (the LLM does, through boltjar.model_discovery.resolve_auto)."""
    return Widget(kind="model", model_kind=model_kind, default=default,
                  options=["auto"] if auto else [], label=label)


def slider(default: float, min: float, max: float, step: float = 0.1, label: str = "") -> Widget:
    """A bounded numeric field; the editor draws it as a slider + value readout."""
    return Widget(kind="number", default=default, min=min, max=max, step=step, label=label)


def secret(label: str = "") -> Widget:
    """A masked credential field (never logged, never serialized to the editor)."""
    return Widget(kind="secret", default="", label=label)


def code(default: str = "", *, accepts_secrets: bool = False, expand: bool = False) -> Widget:
    """A multi-line code/expression field. Set accepts_secrets for a field whose
    text may reference {{secret.NAME}} (e.g. an HTTP body carrying a token). Set
    expand to make this the field that grows when the node is resized."""
    return Widget(kind="code", default=default, accepts_secrets=accepts_secrets, expand=expand)


def tmpl(default: str = "", *, kind: str = "code", port_type: str = "any",
         op_field: Optional[str] = None, op_values: tuple[str, ...] = (),
         placeholder: str = "", options_from: Optional[str] = None,
         expand: bool = False) -> Widget:
    """A template field: its value carries {tag} pipes (substituted from wired
    inputs) and may reference {{secret.NAME}}. Promotable like any field: the
    whole value can be converted to a single typed `port_type` input. op_field/
    op_values make it visible only under the matching operation. `placeholder`
    shows an example when empty; `options_from` turns it into a live dropdown
    (e.g. "db.tables"); `expand` makes it grow when the node is resized."""
    return Widget(kind=kind, default=default, template=True, accepts_secrets=True,
                  port_type=port_type, op_field=op_field, op_values=op_values,
                  placeholder=placeholder, options_from=options_from, expand=expand)


# --------------------------------------------------------------- type registry
class _Types:
    """Named pipe types with colors and single-parent subtyping."""

    def __init__(self) -> None:
        self.meta: dict[str, dict] = {}

    def register(self, name: str, color: str = "#5a6b7c", parent: Optional[str] = None) -> str:
        self.meta[name] = {"color": color, "parent": parent}
        setattr(self, name.upper().replace("-", "_"), name)
        return name

    def color(self, name: str) -> str:
        return self.meta.get(name, {}).get("color", "#5a6b7c")

    def compatible(self, out_type: str, in_type: str) -> bool:
        if in_type == "any" or out_type == "any" or out_type == in_type:
            return True
        node = self.meta.get(out_type)
        seen: set[str] = set()
        while node and node.get("parent") and node["parent"] not in seen:
            if node["parent"] == in_type:
                return True
            seen.add(node["parent"])
            node = self.meta.get(node["parent"])
        return False

    def catalog(self) -> dict[str, str]:
        return {name: meta["color"] for name, meta in self.meta.items()}


types = _Types()

# Core types. Colors are seeded here and overridden by the design tokens at the
# editor layer; the design system owns the canonical palette.
_CORE_TYPES = [
    ("event", "#e0af68", None),
    ("text", "#c9d6e3", None),
    ("number", "#7dcfff", None),
    ("int", "#7dcfff", "number"),
    ("float", "#7dcfff", "number"),
    ("bool", "#9ece6a", None),
    ("json", "#bb9af7", None),
    # a list IS json-shaped: the List node's `list` output feeds any `json` input
    # (For-each's `list`, DB rows, Format List). Subtype so the editor accepts the
    # wire the runtime already consumes; directional (a bare json is not a list).
    ("list", "#bb9af7", "json"),
    ("image", "#bb9af7", None),
    ("audio", "#f7768e", None),
    ("pcm-audio", "#ff9e64", None),
    ("embedding", "#73daca", None),
    ("memory-set", "#9d7cd8", None),
    ("state", "#ff75a0", None),
    ("message", "#7aa2f7", None),
    ("tool", "#2ac3de", None),
    # a Tool node's `call` output. Subtype of `tool` so it wires into the LLM's
    # growable `tools` input (the Tool is offered to the model); it also carries the
    # live invocation into a Tool Args body. One port, two roles: the subtype lets
    # the same wire land on a `tool` input.
    ("tool-call", "#2ac3de", "tool"),
    ("tool-result", "#41a6b5", None),
    ("mood", "#e0af68", None),
    ("action", "#9ece6a", None),
    # one streamable avatar unit (text + audio + mood + action + lang). Subtype of
    # message so it flows into message-shaped consumers.
    ("utterance", "#7aa2f7", "message"),
    # a language tag (en / pt / ...). Subtype of text so it wires into any text input.
    ("lang", "#b4f9f8", "text"),
    ("schedule", "#b4f9f8", None),
    ("observation", "#cfc9a0", None),
    ("secret", "#f7768e", None),
    ("db", "#37c6b0", None),
    ("kv", "#5bbf8a", None),
    # a vector-store handle. Subtype of `db` so the SAME sqlite file is reachable
    # by the generic core.db node (for the FTS / bitemporal-facts side) too.
    ("vectors", "#34d0b0", "db"),
    ("any", "#5a6b7c", None),
]
for _name, _color, _parent in _CORE_TYPES:
    types.register(_name, _color, _parent)


# --------------------------------------------------------------- node registry
@dataclass
class NodeSpec:
    id: str
    name: str
    kind: Kind
    category: str
    version: str
    summary: str
    cls: type
    inputs: list[Port] = field(default_factory=list)
    outputs: list[Port] = field(default_factory=list)
    widgets: list[Widget] = field(default_factory=list)
    pulled: bool = False     # a data node: evaluated on demand, not fired
    volatile: bool = False   # re-read on every pull (Sensors); else memoized per turn
    # a fired node that opens a FRESH turn (epoch) for each emit, so the body it
    # drives re-pulls every time instead of reading the incoming turn's memoized
    # cache. For-each (loops) and Sync set this; pure runtime, surfaced nowhere
    # in the editor. See runtime._fire.
    opens_turn: bool = False
    # passthrough shape: {input_port: output_port}. When a node carrying this is
    # disabled, the runtime WIRES THROUGH (source of input_port -> consumers of
    # output_port) instead of dropping its edges, so a mid-flow node (e.g. Preview)
    # can be bypassed without breaking the flow. Empty = a source/sink: disable
    # just removes it.
    bypass: dict = field(default_factory=dict)
    # how the node looks in the editor, declared rather than looked up by id:
    # the header / palette glyph and the mono line under the title (see node()).
    icon: str = ""
    subline: str = ""

    def growable_base(self, port: str, promoted: Any = ()) -> Optional[Port]:
        """The growable input that owns the socket `port` (a Queue's `chat`, a
        Sync's `in0`, a Template's `{tag}`), or None when `port` is a declared
        input or a knob promoted to an input (`promoted`, the node's
        `config.promoted`). A node with one growable input owns every other
        socket, whatever it is named: a Queue names each after its source. On a
        node with several, a socket belongs to the base its name starts with."""
        bases = [p for p in self.inputs if p.growable]
        for p in self.inputs:
            if p.name == port:
                return p if p.growable else None
        if not bases or (isinstance(promoted, (list, tuple, set, frozenset)) and port in promoted):
            return None
        if len(bases) == 1:
            return bases[0]
        return next((p for p in bases if port.startswith(p.name)), None)

    def definition(self) -> dict:
        """The serialized contract the editor consumes (the /object_info payload)."""
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind.value,
            "pulled": self.pulled,
            "category": self.category,
            "version": self.version,
            "summary": self.summary,
            "inputs": [p.as_dict() for p in self.inputs],
            "outputs": [p.as_dict() for p in self.outputs],
            "widgets": [w.as_dict() for w in self.widgets if w.kind != "secret"],
            "colors": {p.name: types.color(p.type) for p in (self.inputs + self.outputs)},
            "bypass": dict(self.bypass),
            "icon": self.icon,
            "subline": self.subline,
        }


NODE_REGISTRY: dict[str, NodeSpec] = {}

_ANNOTATION_WIDGET = {str: "text", int: "number", float: "number", bool: "bool", dict: "code"}
# the same types by name, for an annotation that stays a string (a module with
# `from __future__ import annotations` whose hints cannot all be resolved).
_ANNOTATION_NAMES = {t.__name__: t for t in _ANNOTATION_WIDGET}


def _type_hints(cls: type) -> dict[str, Any]:
    """The class's annotations resolved to real types where possible. Under
    `from __future__ import annotations` every annotation is a string; resolving
    them all can fail on one bad name, and then the raw strings are used."""
    try:
        return typing.get_type_hints(cls)
    except Exception:
        return dict(getattr(cls, "__annotations__", {}))


def _annotation_type(ann: Any) -> Any:
    """The plain type a bare knob annotation stands for: `Optional[int]` and
    `int | None` read as int, `dict[str, Any]` as dict, the string "bool" as
    bool. None when it names no widget type."""
    if isinstance(ann, str):
        parts = [p.strip() for p in ann.split("|") if p.strip() != "None"]
        name = parts[0] if len(parts) == 1 else ""
        if name.startswith(("Optional[", "typing.Optional[")) and name.endswith("]"):
            name = name[name.index("[") + 1:-1].strip()
        name = name.split("[", 1)[0].rsplit(".", 1)[-1]
        return _ANNOTATION_NAMES.get(name)
    origin = typing.get_origin(ann)
    if origin is typing.Union or origin is _pytypes.UnionType:
        args = [a for a in typing.get_args(ann) if a is not type(None)]
        return _annotation_type(args[0]) if len(args) == 1 else None
    if origin is not None:
        ann = origin
    return ann if isinstance(ann, type) and ann in _ANNOTATION_WIDGET else None


def _inferred_widget(attr: str, ann: Any, default: Any) -> Widget:
    """The widget a bare annotated knob (`seconds: float = 2.0`) declares: its
    kind from the annotation, whole-number steps for an int."""
    kind_type = _annotation_type(ann)
    return Widget(name=attr, kind=_ANNOTATION_WIDGET.get(kind_type, "text"),
                  default=default, label=attr.replace("_", " ").title(),
                  step=1 if kind_type is int else None)


# a subline placeholder: {field} or {field|filter|filter:arg}
SUBLINE_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)((?:\|[a-z]+(?::[^|}]*)?)*)\}")
# the filters the editor applies to a subline value, in the order written:
#   clip:N  one line, cut to N characters (22 when N is left out)
#   or:TEXT TEXT when the value is empty
#   bool    true / false
#   model   a model id as "provider · model"
#   tags    how many {tags} a template text holds, as "N tags"
SUBLINE_FILTERS = frozenset({"clip", "or", "bool", "model", "tags"})
# every {...} in a subline is read as a placeholder, so each must be one
_SUBLINE_BRACES = re.compile(r"\{[^}]*\}")
# clip's length: a whole number of characters above zero
_CLIP_LENGTH = re.compile(r"[1-9][0-9]*")


def _check_subline(node_id: str, subline: str, widgets: list[Widget]) -> None:
    """Refuse a subline with a placeholder the editor would draw literally or
    fill wrongly: one that is not {field|filter|filter:arg}, names a field the
    node does not have or a filter the editor does not know, gives clip a
    length that is not a whole number, or gives an argument to a filter that
    takes none. So a typo fails where the node is declared."""
    names = {w.name for w in widgets}
    for braces in _SUBLINE_BRACES.findall(subline):
        m = SUBLINE_PLACEHOLDER.fullmatch(braces)
        if m is None:
            raise ValueError(f"{node_id}: subline placeholder {braces!r} is not "
                             f"{{field}} or {{field|filter|filter:arg}}")
        if m.group(1) not in names:
            raise ValueError(f"{node_id}: subline names {m.group(1)!r}, which is not a field of the node")
        for f in m.group(2).split("|")[1:]:
            name, colon, arg = f.partition(":")
            if name not in SUBLINE_FILTERS:
                raise ValueError(f"{node_id}: subline filter {f!r} is not one of "
                                 f"{', '.join(sorted(SUBLINE_FILTERS))}")
            if name == "clip" and colon and not _CLIP_LENGTH.fullmatch(arg):
                raise ValueError(f"{node_id}: subline filter {f!r} needs a whole number "
                                 f"of characters above zero, as in clip:16")
            if colon and name not in ("clip", "or"):
                raise ValueError(f"{node_id}: subline filter {name!r} takes no argument")


def node(*, id: str, name: str, kind: Kind, category: str,
         version: str = "0.1.0", summary: str = "", pulled: bool = False,
         volatile: bool = False, opens_turn: bool = False,
         icon: str = "", subline: str = "") -> Callable[[type], type]:
    """Register a node class. Reads ``inputs`` / ``outputs`` and config fields.

    Config fields are either annotated scalars (``seconds: float = 60.0``) whose
    widget is inferred from the annotation, or explicit ``Widget`` values
    (``backend = select([...])``).

    ``icon`` is the Ionicons name the editor draws for the node in its header,
    the palette and the menus (``"timer-outline"``). Left empty, or naming an
    icon the editor does not ship, the node draws its kind's glyph.

    ``subline`` is the short mono line under the node's title: text with
    ``{field}`` placeholders naming the node's fields, each optionally passed
    through filters, ``{text|clip:16|or:empty}``. A field that is not set reads
    as its default. The filters are listed in ``SUBLINE_FILTERS``. Left empty,
    the line is the node's category in lower case.
    """
    def deco(cls: type) -> type:
        inputs = list(getattr(cls, "inputs", []))
        outputs = list(getattr(cls, "outputs", []))
        widgets: list[Widget] = []
        seen: set[str] = set()

        hints = _type_hints(cls)
        for attr, ann in getattr(cls, "__annotations__", {}).items():
            if attr in ("inputs", "outputs"):
                continue
            default = getattr(cls, attr, None)
            if isinstance(default, Widget):
                w = default
                w.name = w.name or attr
                w.label = w.label or attr.replace("_", " ").title()
            else:
                w = _inferred_widget(attr, hints.get(attr, ann), default)
            widgets.append(w)
            seen.add(attr)

        for attr, value in list(vars(cls).items()):
            if isinstance(value, Widget) and attr not in seen:
                value.name = value.name or attr
                value.label = value.label or attr.replace("_", " ").title()
                widgets.append(value)
                seen.add(attr)

        spec = NodeSpec(id=id, name=name, kind=kind, category=category,
                        version=version, summary=summary, cls=cls,
                        inputs=inputs, outputs=outputs, widgets=widgets,
                        pulled=pulled, volatile=volatile, opens_turn=opens_turn,
                        bypass=dict(getattr(cls, "bypass", {}) or {}),
                        icon=icon, subline=subline)
        _check_subline(id, subline, widgets)
        cls._spec = spec  # type: ignore[attr-defined]
        NODE_REGISTRY[id] = spec
        return cls
    return deco


def registry_definitions() -> list[dict]:
    """All registered node definitions, for the editor's /object_info endpoint."""
    return [spec.definition() for spec in NODE_REGISTRY.values()]
