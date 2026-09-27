"""
boltjar.packs: node pack discovery.

The core pack (boltjar.nodes.core) always loads. Every other pack is a folder
under packs/ holding a ``pack.toml`` manifest and an ``__init__.py``; importing it
registers its nodes (``@node``) the same way the core pack does, and its model
manifests load from ``packs/<id>/models/*.toml``. The user's own manifests in
``user/models/*.toml`` load last.

A pack imports under a private module namespace (``_boltjar_packs.<id>``), so
its name never collides with anything on sys.path and nothing is added to it;
relative imports inside the pack work as usual. Every node id a pack registers
must start with ``<pack id>.``, and no pack may redefine a node or a pipe type
another pack (or the core) registered. A pack that breaks any rule, fails to
import or needs a newer Boltjar is rolled back and skipped with a logged error:
one broken pack never stops the server from booting.

pack.toml::

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

_log = logging.getLogger("boltjar.packs")

ROOT = pathlib.Path(__file__).resolve().parent.parent

CORE_ID = "core"
# the private parent module every pack is imported under.
NAMESPACE = "_boltjar_packs"

_ID_RE = re.compile(r"^[a-z0-9_-]+$")
_REQUIRED = ("id", "name", "version")
_OPTIONAL = ("author", "license", "description", "homepage", "min_boltjar")
_VERSION_RE = re.compile(r"^\d+(\.\d+)*")


class PackError(Exception):
    """A pack that cannot load; the message is what /api/packs reports."""


@dataclass
class Pack:
    id: str
    name: str
    version: str
    author: str = ""
    license: str = ""
    description: str = ""
    homepage: str = ""
    min_boltjar: str = ""
    folder: Optional[pathlib.Path] = None   # None for the core pack
    module: str = ""
    nodes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "version": self.version, "nodes": len(self.nodes)}


# Every loaded pack by id (the core first), and the folders the latest scan skipped.
LOADED: dict[str, Pack] = {}
FAILED: list[dict] = []


def load_all(root: pathlib.Path | str | None = None) -> dict:
    """Load the core pack, every pack under ``<root>/packs/`` and the user's model
    manifests under ``<root>/user/models/``. ``root`` defaults to the install
    root. Safe to call again: loaded packs stay loaded and are not re-imported,
    skipped ones are tried again. Returns the report `/api/packs` serves."""
    base = pathlib.Path(root) if root is not None else ROOT
    _load_core()
    FAILED.clear()
    packs_dir = base / "packs"
    if packs_dir.is_dir():
        for folder in sorted(packs_dir.iterdir(), key=lambda p: p.name):
            if folder.is_dir() and not folder.name.startswith((".", "_")):
                _load_folder(folder)
    models.load_models(base / "user" / "models")
    return report()


def report() -> dict:
    """The loaded packs (id, name, version, node count) and the skipped ones
    (id when the manifest named one, folder, error)."""
    return {"loaded": [p.as_dict() for p in LOADED.values()], "failed": [dict(f) for f in FAILED]}


# --------------------------------------------------------------- the core pack
def _load_core() -> None:
    if CORE_ID in LOADED:
        return
    core = importlib.import_module("boltjar.nodes.core")
    LOADED[CORE_ID] = Pack(
        id=CORE_ID, name="Core", version=__version__, license="Apache-2.0",
        description="The built-in nodes: values, triggers, data, logic, AI, stores and outputs.",
        module=core.__name__,
        nodes=[nid for nid in NODE_REGISTRY if nid.startswith(CORE_ID + ".")],
    )


# --------------------------------------------------------------- a pack folder
def _load_folder(folder: pathlib.Path) -> None:
    folder = folder.resolve()
    if any(p.folder == folder for p in LOADED.values()):
        return  # loaded by an earlier scan
    pack_id: Optional[str] = None
    try:
        pack = _read_manifest(folder)
        pack_id = pack.id
        _check_admissible(pack)
    except PackError as exc:
        _skip(folder, pack_id, str(exc))
        return

    registry_before = dict(NODE_REGISTRY)
    types_before = {name: dict(meta) for name, meta in types.meta.items()}
    models_before = dict(models.MODELS)
    try:
        _import(pack)
        added = [nid for nid, spec in NODE_REGISTRY.items() if registry_before.get(nid) is not spec]
        taken = [nid for nid in added if nid in registry_before]
        if taken:
            raise PackError(f"redefines node ids that are already registered: {', '.join(taken)}")
        outside = [nid for nid in added if not nid.startswith(pack.id + ".")]
        if outside:
            raise PackError(f"node ids must start with '{pack.id}.': {', '.join(outside)}")
        # a new pipe type is fine, and so is registering one again unchanged; a new
        # colour or parent would recolour or rewire every node that uses it.
        redefined = [name for name, meta in types_before.items() if types.meta.get(name) != meta]
        if redefined:
            raise PackError(f"redefines types that are already registered: {', '.join(redefined)}")
        models.load_models(folder / "models", replace=False)
    except (Exception, SystemExit) as exc:
        # roll the pack back completely: no half-registered nodes, types or models.
        NODE_REGISTRY.clear()
        NODE_REGISTRY.update(registry_before)
        types.meta.clear()
        types.meta.update(types_before)
        models.MODELS.clear()
        models.MODELS.update(models_before)
        _forget_modules(pack.module)
        message = str(exc) if isinstance(exc, PackError) else f"{type(exc).__name__}: {exc}"
        _skip(folder, pack.id, message, exc_info=None if isinstance(exc, PackError) else exc)
        return
    pack.nodes = added
    LOADED[pack.id] = pack
    _log.info("pack %s %s loaded: %d node(s), %d model(s)", pack.id, pack.version,
              len(pack.nodes), len(models.MODELS) - len(models_before))


def _read_manifest(folder: pathlib.Path) -> Pack:
    manifest = folder / "pack.toml"
    if not manifest.is_file():
        raise PackError("no pack.toml in the folder")
    if not (folder / "__init__.py").is_file():
        raise PackError("no __init__.py in the folder")
    try:
        data = tomllib.loads(manifest.read_text(encoding="utf-8-sig"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise PackError(f"pack.toml: {exc}") from exc
    fields: dict[str, str] = {}
    for key in _REQUIRED + _OPTIONAL:
        value = data.get(key)
        if value is None:
            if key in _REQUIRED:
                raise PackError(f"pack.toml: '{key}' is required")
            continue
        if not isinstance(value, str):
            raise PackError(f"pack.toml: '{key}' must be a string")
        fields[key] = value.strip()
    if not _ID_RE.match(fields["id"]):
        raise PackError(f"pack.toml: id {fields['id']!r} may only use lowercase letters, digits, _ and -")
    return Pack(**fields, folder=folder, module=f"{NAMESPACE}.{fields['id'].replace('-', '_')}")


def _check_admissible(pack: Pack) -> None:
    if pack.id == CORE_ID:
        raise PackError(f"the id '{CORE_ID}' is reserved for the built-in nodes")
    if pack.id in LOADED:
        other = LOADED[pack.id].folder
        raise PackError(f"the id '{pack.id}' is already taken by {other.name if other else 'another pack'}")
    clash = next((p for p in LOADED.values() if p.module == pack.module), None)
    if clash:
        raise PackError(f"the id '{pack.id}' clashes with the pack '{clash.id}' (- and _ read the same)")
    if pack.min_boltjar:
        wanted = _version_tuple(pack.min_boltjar)
        if wanted is None:
            raise PackError(f"pack.toml: min_boltjar {pack.min_boltjar!r} is not a version like 0.1.0")
        if _padded(_version_tuple(__version__) or (0,), len(wanted)) < _padded(wanted, len(wanted)):
            raise PackError(f"needs Boltjar {pack.min_boltjar} or newer (this is {__version__})")


def _version_tuple(text: str) -> Optional[tuple[int, ...]]:
    match = _VERSION_RE.match(text.strip())
    return tuple(int(part) for part in match.group(0).split(".")) if match else None


def _padded(version: tuple[int, ...], width: int) -> tuple[int, ...]:
    return version + (0,) * max(0, width - len(version))


def _import(pack: Pack) -> None:
    """Import the pack's __init__.py as `pack.module`, a package whose search path
    is its own folder (so `from . import nodes` resolves inside it)."""
    if NAMESPACE not in sys.modules:
        parent = _pytypes.ModuleType(NAMESPACE, "Third-party node packs loaded by boltjar.packs.")
        parent.__path__ = []
        sys.modules[NAMESPACE] = parent
    assert pack.folder is not None
    spec = importlib.util.spec_from_file_location(
        pack.module, pack.folder / "__init__.py", submodule_search_locations=[str(pack.folder)])
    if spec is None or spec.loader is None:
        raise PackError("__init__.py cannot be imported")
    module = importlib.util.module_from_spec(spec)
    sys.modules[pack.module] = module
    spec.loader.exec_module(module)
    setattr(sys.modules[NAMESPACE], pack.module.rsplit(".", 1)[1], module)


def _forget_modules(module: str) -> None:
    for name in [n for n in sys.modules if n == module or n.startswith(module + ".")]:
        del sys.modules[name]
    parent = sys.modules.get(NAMESPACE)
    if parent is not None and module.startswith(NAMESPACE + "."):
        parent.__dict__.pop(module.rsplit(".", 1)[1], None)


def _skip(folder: pathlib.Path, pack_id: Optional[str], error: str, exc_info=None) -> None:
    FAILED.append({"id": pack_id, "folder": folder.name, "error": error})
    _log.error("pack %s skipped: %s", pack_id or folder.name, error, exc_info=exc_info)
