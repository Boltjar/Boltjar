"""
boltjar.custom_nodes: custom node discovery.

The core nodes (boltjar.nodes.core) always load. Every custom node is a folder
under custom_nodes/ holding a ``custom_node.toml`` manifest and an ``__init__.py``;
importing it registers its nodes (``@node``) the same way the core nodes are
registered, and its model manifests load from ``custom_nodes/<id>/models/*.toml``.
The user's own manifests in ``user/models/*.toml`` load last.

A custom node imports under a private module namespace
(``_boltjar_custom_nodes.<id>``), so its name never collides with anything on
sys.path and nothing is added to it; relative imports inside it work as usual.
Every node id a custom node registers must start with ``<its id>.``, and no
custom node may redefine a node or a pipe type another one (or the core)
registered. A custom node that breaks any rule, fails to import or needs a newer
Boltjar is rolled back and skipped with a logged error: one broken custom node
never stops the server from booting.

custom_node.toml::

    id = "hello"                  # required, lowercase letters, digits, _ and -
    name = "Hello"                # required
    version = "0.1.0"             # required
    author = "..."                # optional from here on
    license = "MIT"
    description = "..."
    homepage = "https://..."
    min_boltjar = "0.1.0"         # refuse to load on an older Boltjar
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import pathlib
import re
import sys
import tomllib
import types as _pytypes
from dataclasses import dataclass, field
from typing import Optional

from boltjar import __version__, models
from boltjar.sdk import NODE_REGISTRY, types

_log = logging.getLogger("boltjar.custom_nodes")

ROOT = pathlib.Path(__file__).resolve().parent.parent

CORE_ID = "core"
# the private parent module every custom node is imported under.
NAMESPACE = "_boltjar_custom_nodes"

_ID_RE = re.compile(r"^[a-z0-9_-]+$")
_REQUIRED = ("id", "name", "version")
_OPTIONAL = ("author", "license", "description", "homepage", "min_boltjar")
_VERSION_RE = re.compile(r"^\d+(\.\d+)*")


class CustomNodeError(Exception):
    """A custom node that cannot load; the message is what /api/custom-nodes reports."""


@dataclass
class CustomNode:
    id: str
    name: str
    version: str
    author: str = ""
    license: str = ""
    description: str = ""
    homepage: str = ""
    min_boltjar: str = ""
    folder: Optional[pathlib.Path] = None   # None for the core nodes
    module: str = ""
    nodes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "version": self.version, "nodes": len(self.nodes)}


# Every loaded custom node by id (the core first), and the folders the latest scan skipped.
LOADED: dict[str, CustomNode] = {}
FAILED: list[dict] = []


def load_all(root: pathlib.Path | str | None = None) -> dict:
    """Load the core nodes, every custom node under ``<root>/custom_nodes/`` and
    the user's model manifests under ``<root>/user/models/``. ``root`` defaults
    to the install root. Safe to call again: loaded custom nodes stay loaded and
    are not re-imported, skipped ones are tried again. Returns the report
    `/api/custom-nodes` serves."""
    base = pathlib.Path(root) if root is not None else ROOT
    _load_core()
    FAILED.clear()
    folder_root = base / "custom_nodes"
    if folder_root.is_dir():
        for folder in sorted(folder_root.iterdir(), key=lambda p: p.name):
            if folder.is_dir() and not folder.name.startswith((".", "_")):
                _load_folder(folder)
    models.load_models(base / "user" / "models")
    return report()


def report() -> dict:
    """The loaded custom nodes (id, name, version, node count), the core nodes
    first, and the skipped ones (id when the manifest named one, folder, error)."""
    return {"loaded": [p.as_dict() for p in LOADED.values()], "failed": [dict(f) for f in FAILED]}


# -------------------------------------------------------------- the core nodes
def _load_core() -> None:
    if CORE_ID in LOADED:
        return
    core = importlib.import_module("boltjar.nodes.core")
    LOADED[CORE_ID] = CustomNode(
        id=CORE_ID, name="Core", version=__version__, license="Apache-2.0",
        description="The built-in nodes: values, triggers, data, logic, AI, stores and outputs.",
        module=core.__name__,
        nodes=[nid for nid in NODE_REGISTRY if nid.startswith(CORE_ID + ".")],
    )


# ------------------------------------------------------- a custom node folder
def _load_folder(folder: pathlib.Path) -> None:
    folder = folder.resolve()
    if any(p.folder == folder for p in LOADED.values()):
        return  # loaded by an earlier scan
    custom_id: Optional[str] = None
    try:
        custom = _read_manifest(folder)
        custom_id = custom.id
        _check_admissible(custom)
    except CustomNodeError as exc:
        _skip(folder, custom_id, str(exc))
        return

    registry_before = dict(NODE_REGISTRY)
    types_before = {name: dict(meta) for name, meta in types.meta.items()}
    models_before = dict(models.MODELS)
    try:
        _import(custom)
        added = [nid for nid, spec in NODE_REGISTRY.items() if registry_before.get(nid) is not spec]
        taken = [nid for nid in added if nid in registry_before]
        if taken:
            raise CustomNodeError(f"redefines node ids that are already registered: {', '.join(taken)}")
        outside = [nid for nid in added if not nid.startswith(custom.id + ".")]
        if outside:
            raise CustomNodeError(f"node ids must start with '{custom.id}.': {', '.join(outside)}")
        # a new pipe type is fine, and so is registering one again unchanged; a new
        # colour or parent would recolour or rewire every node that uses it.
        redefined = [name for name, meta in types_before.items() if types.meta.get(name) != meta]
        if redefined:
            raise CustomNodeError(f"redefines types that are already registered: {', '.join(redefined)}")
        models.load_models(folder / "models", replace=False)
    except (Exception, SystemExit) as exc:
        # roll the custom node back completely: no half-registered nodes, types or models.
        NODE_REGISTRY.clear()
        NODE_REGISTRY.update(registry_before)
        types.meta.clear()
        types.meta.update(types_before)
        models.MODELS.clear()
        models.MODELS.update(models_before)
        _forget_modules(custom.module)
        message = str(exc) if isinstance(exc, CustomNodeError) else f"{type(exc).__name__}: {exc}"
        _skip(folder, custom.id, message, exc_info=None if isinstance(exc, CustomNodeError) else exc)
        return
    custom.nodes = added
    LOADED[custom.id] = custom
    _log.info("custom node %s %s loaded: %d node(s), %d model(s)", custom.id, custom.version,
              len(custom.nodes), len(models.MODELS) - len(models_before))


def _read_manifest(folder: pathlib.Path) -> CustomNode:
    manifest = folder / "custom_node.toml"
    if not manifest.is_file():
        raise CustomNodeError("no custom_node.toml in the folder")
    if not (folder / "__init__.py").is_file():
        raise CustomNodeError("no __init__.py in the folder")
    try:
        data = tomllib.loads(manifest.read_text(encoding="utf-8-sig"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise CustomNodeError(f"custom_node.toml: {exc}") from exc
    fields: dict[str, str] = {}
    for key in _REQUIRED + _OPTIONAL:
        value = data.get(key)
        if value is None:
            if key in _REQUIRED:
                raise CustomNodeError(f"custom_node.toml: '{key}' is required")
            continue
        if not isinstance(value, str):
            raise CustomNodeError(f"custom_node.toml: '{key}' must be a string")
        fields[key] = value.strip()
    if not _ID_RE.match(fields["id"]):
        raise CustomNodeError(f"custom_node.toml: id {fields['id']!r} may only use lowercase letters, digits, _ and -")
    return CustomNode(**fields, folder=folder, module=f"{NAMESPACE}.{fields['id'].replace('-', '_')}")


def _check_admissible(custom: CustomNode) -> None:
    if custom.id == CORE_ID:
        raise CustomNodeError(f"the id '{CORE_ID}' is reserved for the built-in nodes")
    if custom.id in LOADED:
        other = LOADED[custom.id].folder
        raise CustomNodeError(f"the id '{custom.id}' is already taken by {other.name if other else 'another custom node'}")
    clash = next((p for p in LOADED.values() if p.module == custom.module), None)
    if clash:
        raise CustomNodeError(f"the id '{custom.id}' clashes with the custom node '{clash.id}' (- and _ read the same)")
    if custom.min_boltjar:
        wanted = _version_tuple(custom.min_boltjar)
        if wanted is None:
            raise CustomNodeError(f"custom_node.toml: min_boltjar {custom.min_boltjar!r} is not a version like 0.1.0")
        if _padded(_version_tuple(__version__) or (0,), len(wanted)) < _padded(wanted, len(wanted)):
            raise CustomNodeError(f"needs Boltjar {custom.min_boltjar} or newer (this is {__version__})")


def _version_tuple(text: str) -> Optional[tuple[int, ...]]:
    match = _VERSION_RE.match(text.strip())
    return tuple(int(part) for part in match.group(0).split(".")) if match else None


def _padded(version: tuple[int, ...], width: int) -> tuple[int, ...]:
    return version + (0,) * max(0, width - len(version))


def _import(custom: CustomNode) -> None:
    """Import the custom node's __init__.py as `custom.module`, a package whose
    search path is its own folder (so `from . import nodes` resolves inside it)."""
    if NAMESPACE not in sys.modules:
        parent = _pytypes.ModuleType(NAMESPACE, "Custom nodes loaded by boltjar.custom_nodes.")
        parent.__path__ = []
        sys.modules[NAMESPACE] = parent
    assert custom.folder is not None
    spec = importlib.util.spec_from_file_location(
        custom.module, custom.folder / "__init__.py", submodule_search_locations=[str(custom.folder)])
    if spec is None or spec.loader is None:
        raise CustomNodeError("__init__.py cannot be imported")
    module = importlib.util.module_from_spec(spec)
    sys.modules[custom.module] = module
    spec.loader.exec_module(module)
    setattr(sys.modules[NAMESPACE], custom.module.rsplit(".", 1)[1], module)


def _forget_modules(module: str) -> None:
    for name in [n for n in sys.modules if n == module or n.startswith(module + ".")]:
        del sys.modules[name]
    parent = sys.modules.get(NAMESPACE)
    if parent is not None and module.startswith(NAMESPACE + "."):
        parent.__dict__.pop(module.rsplit(".", 1)[1], None)


def _skip(folder: pathlib.Path, custom_id: Optional[str], error: str, exc_info=None) -> None:
    FAILED.append({"id": custom_id, "folder": folder.name, "error": error})
    _log.error("custom node %s skipped: %s", custom_id or folder.name, error, exc_info=exc_info)
