"""Pack discovery: the core pack plus every packs/<folder>/ with a pack.toml and
an __init__.py. The shipped example pack (examples/packs/hello) is copied into a
temp root for each test, so nothing here reads or writes the real packs/ or
user/ folders, and whatever an earlier load_all brought in from them (the
server's, at import) is set aside while a test runs.

A pack loads under a private module namespace, registers only ids under its own
prefix, never redefines another node, and a pack that breaks a rule is rolled
back completely and reported by /api/packs, while the rest keeps loading."""
from __future__ import annotations

import asyncio
import contextlib
import pathlib
import shutil
import sys
import textwrap

import pytest
from local_client import local_client

import boltjar.nodes.core  # registers the core nodes before any snapshot below
# importing the server runs its load_all on the real install; done here, before
# any test, so what it loads is set aside like any other earlier load.
import boltjar.server as server
from boltjar import __version__, models, packs
from boltjar.runtime import Runtime
from boltjar.sdk import _CORE_TYPES, NODE_REGISTRY, types

EXAMPLE = pathlib.Path(__file__).resolve().parent.parent / "examples" / "packs" / "hello"
CORE_MODELS = pathlib.Path(boltjar.nodes.core.__file__).resolve().parent / "models"

MANIFEST = """\
id = "{id}"
name = "{name}"
version = "1.0.0"
"""


def is_pack_module(name: str) -> bool:
    return name.startswith(packs.NAMESPACE + ".")


@contextlib.contextmanager
def registries_restored():
    """Every registry a pack load touches (nodes, types, models, the loaded and
    skipped packs, the pack modules), put back as it was on exit."""
    registry = dict(NODE_REGISTRY)
    type_meta = {name: dict(meta) for name, meta in types.meta.items()}
    manifests = dict(models.MODELS)
    loaded = dict(packs.LOADED)
    skipped = list(packs.FAILED)
    modules = {name: module for name, module in sys.modules.items() if is_pack_module(name)}
    try:
        yield
    finally:
        NODE_REGISTRY.clear()
        NODE_REGISTRY.update(registry)
        types.meta.clear()
        types.meta.update(type_meta)
        models.MODELS.clear()
        models.MODELS.update(manifests)
        packs.LOADED.clear()
        packs.LOADED.update(loaded)
        packs.FAILED[:] = skipped
        for name in [m for m in sys.modules if is_pack_module(m)]:
            del sys.modules[name]
        sys.modules.update(modules)


def only_the_core() -> None:
    """Set aside every node, type, model and pack module the core pack does not
    declare, and forget every pack but the core."""
    core = {nid: spec for nid, spec in NODE_REGISTRY.items() if nid.startswith(packs.CORE_ID + ".")}
    NODE_REGISTRY.clear()
    NODE_REGISTRY.update(core)
    types.meta.clear()
    for name, color, parent in _CORE_TYPES:
        types.register(name, color, parent)
    models.MODELS.clear()
    models.load_models(CORE_MODELS)
    packs.LOADED.clear()
    packs.FAILED.clear()
    for name in [m for m in sys.modules if is_pack_module(m)]:
        del sys.modules[name]


@pytest.fixture
def root(tmp_path):
    """A temp install root where only the core pack is loaded, with every
    registry restored after the test."""
    path = list(sys.path)
    with registries_restored():
        only_the_core()
        packs.load_all(tmp_path)  # the core pack's entry, before packs/ exists
        (tmp_path / "packs").mkdir()
        yield tmp_path
        assert sys.path == path, "loading packs must not touch sys.path"


def add_hello(root: pathlib.Path, folder: str = "hello") -> pathlib.Path:
    target = root / "packs" / folder
    shutil.copytree(EXAMPLE, target, ignore=shutil.ignore_patterns("__pycache__"))
    return target


def add_pack(root: pathlib.Path, folder: str, init: str, manifest: str | None = None,
             files: dict[str, str] | None = None) -> pathlib.Path:
    """A pack folder with the given __init__.py and extra files (by path inside
    the pack). The manifest defaults to one whose id is the folder name."""
    target = root / "packs" / folder
    target.mkdir(parents=True)
    if manifest is None:
        manifest = MANIFEST.format(id=folder, name=folder.title())
    (target / "pack.toml").write_text(manifest, encoding="utf-8")
    (target / "__init__.py").write_text(textwrap.dedent(init), encoding="utf-8")
    for name, text in (files or {}).items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")
    return target


def node_source(node_id: str, name: str = "Thing") -> str:
    return f"""
        from boltjar.sdk import Kind, Port, Widget, node

        @node(id="{node_id}", name="{name}", kind=Kind.VALUE, category="Test", pulled=True)
        class Thing:
            value_text = Widget(kind="text", default="x")
            outputs = [Port("out", "text")]

            def value(self):
                return {{"out": self.value_text}}
    """


def failed(report: dict) -> dict[str, str]:
    return {f["folder"]: f["error"] for f in report["failed"]}


def loaded(report: dict) -> dict[str, dict]:
    return {p["id"]: p for p in report["loaded"]}


# --------------------------------------------------------------- the example pack

def test_the_example_pack_loads_next_to_the_core(root):
    add_hello(root)
    report = packs.load_all(root)
    assert report["loaded"][0]["id"] == "core"
    assert loaded(report)["hello"] == {"id": "hello", "name": "Hello", "version": "0.1.0", "nodes": 2}
    assert report["failed"] == []
    assert {"hello.shout", "hello.greet"} <= set(NODE_REGISTRY)
    assert NODE_REGISTRY["hello.shout"].pulled is True
    assert [w.kind for w in NODE_REGISTRY["hello.greet"].widgets] == ["text", "number"]
    # a pack node declares its look on @node, the same way a core node does.
    shout = NODE_REGISTRY["hello.shout"].definition()
    assert (shout["icon"], shout["subline"]) == ("text-outline", "shout · {suffix|or:no suffix}")
    assert NODE_REGISTRY["hello.greet"].definition()["icon"] == "hand-left-outline"


def test_a_pack_imports_under_the_private_namespace(root):
    add_hello(root)
    packs.load_all(root)
    assert f"{packs.NAMESPACE}.hello" in sys.modules
    # the relative `from . import nodes` resolved inside the pack, not on sys.path.
    assert f"{packs.NAMESPACE}.hello.nodes" in sys.modules
    assert "hello" not in sys.modules
    assert NODE_REGISTRY["hello.shout"].cls.__module__ == f"{packs.NAMESPACE}.hello.nodes"


def test_the_example_nodes_run(root):
    add_hello(root)
    packs.load_all(root)
    events: list[dict] = []
    rt = Runtime(observer=events.append)
    rt.build({
        "nodes": [
            {"id": "name", "type": "core.value.text", "config": {"text": "Ada"}},
            {"id": "shout", "type": "hello.shout", "config": {"suffix": "?"}},
            {"id": "fire", "type": "core.trigger.manual"},
            {"id": "greet", "type": "hello.greet", "config": {"greeting": "Hi", "times": 2}},
        ],
        "edges": [
            {"src": "name", "src_port": "out", "dst": "shout", "dst_port": "text"},
            {"src": "fire", "src_port": "trigger", "dst": "greet", "dst_port": "trigger"},
            {"src": "shout", "src_port": "out", "dst": "greet", "dst_port": "name"},
        ],
    })

    async def drive() -> None:
        await rt.run()
        await asyncio.sleep(0.2)
        await rt.stop()

    asyncio.run(drive())
    greeted = [e["value"] for e in events
               if e["kind"] == "value" and e["node"] == "greet" and e["port"] == "out"]
    assert greeted == ["Hi, ADA?! Hi, ADA?!"]


def test_loading_again_keeps_the_loaded_packs_and_imports_nothing_twice(root):
    add_hello(root)
    add_pack(root, "broken", "raise RuntimeError('boom')")
    packs.load_all(root)
    module = sys.modules[f"{packs.NAMESPACE}.hello"]
    report = packs.load_all(root)
    assert sys.modules[f"{packs.NAMESPACE}.hello"] is module
    assert list(loaded(report)) == ["core", "hello"]
    assert list(failed(report)) == ["broken"]


def test_the_packs_route_reports_loaded_and_failed_packs(root):
    add_hello(root)
    add_pack(root, "broken", "raise RuntimeError('boom')")
    packs.load_all(root)
    body = local_client().get("/api/packs").json()
    assert [p["id"] for p in body["loaded"]] == ["core", "hello"]
    assert body["loaded"][0]["version"] == __version__
    assert body["loaded"][0]["nodes"] == len([n for n in NODE_REGISTRY if n.startswith("core.")])
    assert body["failed"] == [{"id": "broken", "folder": "broken", "error": "RuntimeError: boom"}]


# --------------------------------------------------------------- rules

def test_a_node_outside_the_pack_namespace_rejects_the_whole_pack(root):
    add_pack(root, "mine", "from . import a, b",
             files={"a.py": node_source("mine.ok"), "b.py": node_source("theirs.thing")})
    report = packs.load_all(root)
    assert "must start with 'mine.'" in failed(report)["mine"]
    assert "theirs.thing" in failed(report)["mine"]
    assert "mine.ok" not in NODE_REGISTRY and "theirs.thing" not in NODE_REGISTRY
    assert not [m for m in sys.modules if m.startswith(f"{packs.NAMESPACE}.mine")]


def test_a_pack_node_with_an_optional_trigger_is_skipped(root):
    add_hello(root)
    add_pack(root, "lazy", """
        from boltjar.sdk import Kind, Port, node

        @node(id="lazy.thing", name="Thing", kind=Kind.TRANSFORM, category="Test")
        class Thing:
            inputs = [Port("go", "event", trigger=True, optional=True)]
            outputs = [Port("out", "text")]
    """)
    report = packs.load_all(root)
    assert failed(report)["lazy"] == ("ValueError: node 'lazy.thing': trigger input 'go' "
                                      "cannot be optional: a trigger fires the node and "
                                      "must be wired (drop optional=True)")
    assert "lazy.thing" not in NODE_REGISTRY
    assert "hello" in loaded(report)


@pytest.mark.parametrize("picker, said", [
    ('model("tts", "acme/voice")', "a default model 'acme/voice'"),
    ('model("llm", auto=True)', "the pick 'auto'"),
], ids=["default", "auto"])
def test_a_pack_node_whose_model_picker_picks_a_model_is_skipped(root, picker, said):
    # a model can cost money: only a person picks one, never a pack's declaration.
    add_hello(root)
    add_pack(root, "picky", f"""
        from boltjar.sdk import Kind, Port, Widget, model, node

        @node(id="picky.voice", name="Voice", kind=Kind.TRANSFORM, category="Test")
        class Voice:
            model: Widget = {picker}
            inputs = [Port("trigger", "event", trigger=True)]
            outputs = [Port("audio", "audio")]
    """)
    report = packs.load_all(root)
    assert failed(report)["picky"] == (
        f"ValueError: node 'picky.voice': model picker 'model' declares {said}; a model "
        "node never picks a model on its own, so the picker starts empty and the person "
        "picks one (drop it)")
    assert "picky.voice" not in NODE_REGISTRY
    assert "hello" in loaded(report)


def test_a_pack_cannot_redefine_a_core_node(root):
    core_text = NODE_REGISTRY["core.value.text"]
    add_pack(root, "sneaky", node_source("core.value.text", "Hijack"))
    report = packs.load_all(root)
    assert "redefines" in failed(report)["sneaky"]
    assert NODE_REGISTRY["core.value.text"] is core_text


@pytest.mark.parametrize("register", [
    'types.register("text", "#c9d6e3", parent="json")',  # a new parent rewires every text port
    'types.register("event", "#000000")',               # a new colour recolours every trigger
])
def test_a_pack_cannot_redefine_a_core_type(root, register):
    core = {name: dict(meta) for name, meta in types.meta.items()}
    add_pack(root, "retype", f"""
        from boltjar.sdk import types
        from . import nodes
        {register}
    """, files={"nodes.py": node_source("retype.thing")})
    report = packs.load_all(root)
    assert "redefines types that are already registered" in failed(report)["retype"]
    assert types.meta == core
    assert "retype.thing" not in NODE_REGISTRY


def test_a_pack_may_add_a_type_and_register_a_core_one_unchanged(root):
    text = dict(types.meta["text"])
    add_pack(root, "typed", f"""
        from boltjar.sdk import types
        from . import nodes
        types.register("text", {text["color"]!r}, parent={text["parent"]!r})
        types.register("typed-signal", "#123456", parent="event")
    """, files={"nodes.py": node_source("typed.thing")})
    report = packs.load_all(root)
    assert "typed" in loaded(report)
    assert types.meta["text"] == text
    assert types.meta["typed-signal"] == {"color": "#123456", "parent": "event"}


def test_a_pack_cannot_redefine_another_packs_type(root):
    add_pack(root, "first", 'from boltjar.sdk import types\ntypes.register("first-signal", "#111111")')
    add_pack(root, "second", 'from boltjar.sdk import types\ntypes.register("first-signal", "#222222")')
    report = packs.load_all(root)
    assert "first" in loaded(report)
    assert "first-signal" in failed(report)["second"]
    assert types.meta["first-signal"]["color"] == "#111111"


def test_a_pack_cannot_redefine_another_packs_node(root):
    add_pack(root, "first", node_source("first.thing"))
    add_pack(root, "second", node_source("first.thing", "Copy"))
    report = packs.load_all(root)
    assert "first" in loaded(report)
    assert "redefines" in failed(report)["second"]
    assert NODE_REGISTRY["first.thing"].name == "Thing"


def test_the_core_id_is_reserved(root):
    add_pack(root, "fake-core", node_source("core.extra"), manifest=MANIFEST.format(id="core", name="Core"))
    report = packs.load_all(root)
    assert "reserved" in failed(report)["fake-core"]
    assert "core.extra" not in NODE_REGISTRY


def test_a_duplicate_pack_id_is_skipped(root):
    add_hello(root, "hello")
    add_hello(root, "hello-copy")
    report = packs.load_all(root)
    assert loaded(report)["hello"]["nodes"] == 2
    assert "already taken by hello" in failed(report)["hello-copy"]


def test_ids_that_share_a_module_name_clash(root):
    add_pack(root, "my-pack", node_source("my-pack.thing"))
    add_pack(root, "my_pack", node_source("my_pack.thing"))
    report = packs.load_all(root)
    assert "my-pack" in loaded(report)
    assert "clashes" in failed(report)["my_pack"]


@pytest.mark.parametrize("init, error", [
    ("raise RuntimeError('boom')", "RuntimeError: boom"),
    ("import a_module_that_does_not_exist", "ModuleNotFoundError"),
    ("import sys\nsys.exit(3)", "SystemExit: 3"),
    ("def broken(:\n    pass", "SyntaxError"),
])
def test_a_pack_that_fails_to_import_is_skipped(root, init, error):
    add_hello(root)
    add_pack(root, "broken", init)
    report = packs.load_all(root)
    assert error in failed(report)["broken"]
    assert "hello" in loaded(report)


def test_a_failed_pack_leaves_no_types_or_nodes_behind(root):
    add_pack(root, "halfway", """
        from boltjar.sdk import types
        from . import nodes
        types.register("halfway-signal", "#123456")
        raise RuntimeError("late failure")
    """, files={"nodes.py": node_source("halfway.thing")})
    packs.load_all(root)
    assert "halfway.thing" not in NODE_REGISTRY
    assert "halfway-signal" not in types.meta


@pytest.mark.parametrize("manifest, error", [
    ('name = "X"\nversion = "1"\n', "'id' is required"),
    ('id = "x"\nversion = "1"\n', "'name' is required"),
    ('id = "Bad Id"\nname = "X"\nversion = "1"\n', "may only use lowercase"),
    ('id = "x"\nname = "X"\nversion = 1\n', "'version' must be a string"),
    ('id = "x"\nname = "X"\nversion = "1"\nmin_boltjar = "soon"\n', "is not a version"),
    ('id = "x\n', "pack.toml:"),
])
def test_an_invalid_manifest_is_reported(root, manifest, error):
    add_pack(root, "x", node_source("x.thing"), manifest=manifest)
    report = packs.load_all(root)
    assert error in failed(report)["x"]
    assert "x.thing" not in NODE_REGISTRY


def test_a_folder_that_is_not_a_pack_is_reported(root):
    (root / "packs" / "no-manifest").mkdir()
    (root / "packs" / "no-manifest" / "__init__.py").write_text("", encoding="utf-8")
    (root / "packs" / "no-init").mkdir()
    (root / "packs" / "no-init" / "pack.toml").write_text(MANIFEST.format(id="no-init", name="N"),
                                                           encoding="utf-8")
    (root / "packs" / "__pycache__").mkdir()
    (root / "packs" / ".hidden").mkdir()
    (root / "packs" / "notes.txt").write_text("not a folder", encoding="utf-8")
    report = packs.load_all(root)
    assert failed(report) == {"no-init": "no __init__.py in the folder",
                              "no-manifest": "no pack.toml in the folder"}


def test_min_boltjar_gates_the_pack_on_this_version(root):
    newer = f"{int(__version__.split('.')[0]) + 1}.0.0"
    add_pack(root, "future", node_source("future.thing"),
             manifest=MANIFEST.format(id="future", name="F") + f'min_boltjar = "{newer}"\n')
    add_pack(root, "today", node_source("today.thing"),
             manifest=MANIFEST.format(id="today", name="T") + f'min_boltjar = "{__version__}"\n')
    report = packs.load_all(root)
    assert f"needs Boltjar {newer} or newer" in failed(report)["future"]
    assert "today" in loaded(report)


# --------------------------------------------------------------- models

MODEL = """\
id = "{id}"
provider = "ollama"
label = "{label}"
"""


def test_a_pack_ships_models_but_cannot_replace_a_declared_one(root):
    core_id = next(iter(models.MODELS))
    core_label = models.MODELS[core_id].label
    add_pack(root, "withmodels", node_source("withmodels.thing"), files={
        "models/extra.toml": MODEL.format(id="ollama/pack-extra", label="Pack Extra"),
        "models/clash.toml": MODEL.format(id=core_id, label="Clash"),
    })
    report = packs.load_all(root)
    assert "withmodels" in loaded(report)
    assert models.MODELS["ollama/pack-extra"].label == "Pack Extra"
    assert models.MODELS[core_id].label == core_label


def test_user_models_load_last_and_may_replace_a_declared_one(root):
    core_id = next(iter(models.MODELS))
    user_models = root / "user" / "models"
    user_models.mkdir(parents=True)
    (user_models / "mine.toml").write_text(MODEL.format(id="ollama/user-own", label="Mine"),
                                           encoding="utf-8")
    (user_models / "tuned.toml").write_text(MODEL.format(id=core_id, label="Tuned"), encoding="utf-8")
    packs.load_all(root)
    assert models.MODELS["ollama/user-own"].label == "Mine"
    assert models.MODELS[core_id].label == "Tuned"


# --------------------------------------------------------------- the real install

@pytest.fixture
def installed(tmp_path_factory):
    """The example pack and a user model that retunes a core one, loaded from
    another install root before the test's own root is set up: what the
    server's load_all at import does with the real packs/ and user/models/.
    Yields the id of the retuned model."""
    other = tmp_path_factory.mktemp("install")
    with registries_restored():
        core_id = next(iter(models.MODELS))
        add_hello(other)
        (other / "user" / "models").mkdir(parents=True)
        (other / "user" / "models" / "tuned.toml").write_text(MODEL.format(id=core_id, label="Installed"),
                                                            encoding="utf-8")
        packs.load_all(other)
        assert "hello" in packs.LOADED and models.MODELS[core_id].label == "Installed"
        yield core_id


def test_what_an_earlier_load_brought_in_is_set_aside(installed, root):
    assert list(packs.LOADED) == ["core"]
    assert not [nid for nid in NODE_REGISTRY if nid.startswith("hello.")]
    assert not [m for m in sys.modules if is_pack_module(m)]
    assert models.MODELS[installed].label != "Installed"
    add_hello(root)
    report = packs.load_all(root)
    assert report["failed"] == []
    assert loaded(report)["hello"]["nodes"] == 2
