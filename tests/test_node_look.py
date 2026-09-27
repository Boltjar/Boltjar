"""A node declares how it looks in the editor on its @node decorator: the glyph
(`icon`) and the mono line under its title (`subline`, a template over its
fields). The editor reads both from /api/object_info, with no table keyed by
node id.

The editor's own test (editor/test/node-look.test.mjs) renders every core node
from a snapshot of these definitions, editor/test/fixtures/core-nodes.json, and
compares the result with what the editor drew before the declarations existed.
The snapshot test here keeps that file equal to the live registry; after a
deliberate change, rewrite it with BOLTJAR_UPDATE_FIXTURES=1 and rerun."""
from __future__ import annotations

import json
import os
import pathlib
import re

import pytest

import boltjar.nodes.core  # noqa: F401  (registers the core nodes)
from boltjar.sdk import NODE_REGISTRY, SUBLINE_FILTERS, Kind, node

FIXTURE = pathlib.Path(__file__).resolve().parent.parent / "editor" / "test" / "fixtures" / "core-nodes.json"


def _look(d: dict) -> dict:
    """The part of a served definition the editor's icon, subline, knob rows and
    palette search read (editor/test/knob-rows.test.mjs and node-search.test.mjs
    read the same file)."""
    return {
        "id": d["id"], "name": d["name"], "kind": d["kind"], "category": d["category"],
        "summary": d["summary"],
        "icon": d["icon"], "subline": d["subline"],
        "inputs": [{"name": p["name"], "growable": p["growable"]} for p in d["inputs"]],
        "widgets": [{"name": w["name"], "kind": w["kind"], "label": w["label"],
                     "default": w["default"], "surface": w["surface"],
                     "promotable": w["promotable"], "op_field": w["op_field"],
                     "op_values": w["op_values"], "model_kind": w["model_kind"]}
                    for w in d["widgets"]],
    }


def _core_looks() -> list[dict]:
    return [_look(s.definition()) for nid, s in NODE_REGISTRY.items() if nid.startswith("core.")]


def test_the_editor_snapshot_matches_the_core_nodes():
    live = _core_looks()
    if os.environ.get("BOLTJAR_UPDATE_FIXTURES"):
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(live, indent=1, ensure_ascii=False) + "\n",
                           encoding="utf-8", newline="\n")
    saved = json.loads(FIXTURE.read_text(encoding="utf-8"))
    by_id = lambda looks: sorted(looks, key=lambda d: d["id"])  # noqa: E731
    assert by_id(saved) == by_id(live), ("editor/test/fixtures/core-nodes.json is out of "
                                         "date: rerun with BOLTJAR_UPDATE_FIXTURES=1")


def test_every_definition_carries_its_look():
    for spec in NODE_REGISTRY.values():
        d = spec.definition()
        assert isinstance(d["icon"], str) and isinstance(d["subline"], str), d["id"]


def test_every_core_node_declares_an_icon():
    # a node that declares none draws its kind's glyph, which it would share
    # with every other node of that kind
    bare = [d["id"] for d in _core_looks() if not d["icon"]]
    assert bare == [], f"core nodes without an icon: {bare}"


def test_no_two_core_nodes_share_an_icon():
    # the glyph tells a node apart in the library, the palette and the canvas;
    # the editor test (node-look.test.mjs) checks the editor ships each one
    owners: dict[str, list[str]] = {}
    for d in _core_looks():
        owners.setdefault(d["icon"], []).append(d["id"])
    shared = {icon: ids for icon, ids in owners.items() if len(ids) > 1}
    assert shared == {}, f"icons declared by more than one core node: {shared}"


# Two icons of one Ionicons family (the first word of the name) are one glyph
# with a small mark added: at library size Parse's code-download and
# Stringify's code-working were the same brackets, and Audio Input's
# mic-circle was STT's mic. The two pairs below predate this check and are the
# only ones kept; a new node picks a glyph of its own family.
_KNOWN_FAMILY_PAIRS = {
    "document": {"core.data.template", "core.file.read"},
    "git": {"core.logic.condition", "core.flow.sync"},
}


def test_no_two_core_nodes_draw_the_same_glyph_family():
    families: dict[str, set[str]] = {}
    for d in _core_looks():
        families.setdefault(d["icon"].split("-")[0], set()).add(d["id"])
    shared = {f: ids for f, ids in families.items()
              if len(ids) > 1 and ids != _KNOWN_FAMILY_PAIRS.get(f)}
    assert shared == {}, f"core nodes whose icons are one glyph family: {shared}"


def test_a_node_without_a_declaration_serves_empty_strings():
    @node(id="test.look.plain", name="Plain", kind=Kind.TRANSFORM, category="Data")
    class Plain:
        pass

    try:
        d = NODE_REGISTRY["test.look.plain"].definition()
        assert (d["icon"], d["subline"]) == ("", "")
    finally:
        NODE_REGISTRY.pop("test.look.plain", None)


def test_a_pack_node_declares_its_look_the_same_way():
    @node(id="test.look.pack", name="Pack", kind=Kind.TRANSFORM, category="Data",
          icon="globe-outline", subline="fetch · {url|clip:12|or:no url}")
    class Pack:
        url: str = ""

    try:
        d = NODE_REGISTRY["test.look.pack"].definition()
        assert d["icon"] == "globe-outline"
        assert d["subline"] == "fetch · {url|clip:12|or:no url}"
    finally:
        NODE_REGISTRY.pop("test.look.pack", None)


NOT_A_PLACEHOLDER = "is not {field} or {field|filter|filter:arg}"


@pytest.mark.parametrize("subline, error", [
    ("every · {secs}s", "subline names 'secs', which is not a field of the node"),
    ("every · {seconds|round}s", "subline filter 'round' is not one of"),
    # the editor would draw each of these literally
    ("every {seconds|Clip}s", f"subline placeholder '{{seconds|Clip}}' {NOT_A_PLACEHOLDER}"),
    ("every {seconds|clip3}s", f"subline placeholder '{{seconds|clip3}}' {NOT_A_PLACEHOLDER}"),
    ("every {seconds }s", f"subline placeholder '{{seconds }}' {NOT_A_PLACEHOLDER}"),
    ("every {}s", f"subline placeholder '{{}}' {NOT_A_PLACEHOLDER}"),
    # and would fill these with something other than what is written
    ("every {seconds|clip:x}s", "subline filter 'clip:x' needs a whole number of characters"),
    ("every {seconds|clip:0}s", "subline filter 'clip:0' needs a whole number of characters"),
    ("every {seconds|clip:}s", "subline filter 'clip:' needs a whole number of characters"),
    ("every {seconds|bool:yes}s", "subline filter 'bool' takes no argument"),
])
def test_a_subline_that_names_nothing_real_is_refused(subline, error):
    with pytest.raises(ValueError, match=re.escape(error)):
        @node(id="test.look.bad", name="Bad", kind=Kind.TRIGGER, category="Triggers",
              subline=subline)
        class Bad:
            seconds: float = 1.0
    assert "test.look.bad" not in NODE_REGISTRY


def test_a_well_formed_subline_is_accepted():
    @node(id="test.look.good", name="Good", kind=Kind.TRIGGER, category="Triggers",
          subline="{seconds|clip|clip:8|or:none|or:} · {on|bool} {not a placeholder")
    class Good:
        seconds: float = 1.0
        on: bool = False

    try:
        assert "test.look.good" in NODE_REGISTRY
    finally:
        NODE_REGISTRY.pop("test.look.good", None)


def test_the_filters_are_the_ones_the_editor_applies():
    # editor/src/lib/nodeMeta.ts applyFilter handles exactly these names.
    source = (FIXTURE.parent.parent.parent / "src" / "lib" / "nodeMeta.ts").read_text(encoding="utf-8")
    handled = {line.strip()[len('case "'):].split('"', 1)[0]
               for line in source.split("function applyFilter", 1)[1].splitlines()
               if line.strip().startswith('case "')}
    assert handled == SUBLINE_FILTERS


def test_the_pack_author_docs_say_what_an_empty_subline_shows():
    # the editor draws a model node's model and any other node's category
    # (editor/src/lib/nodeMeta.ts headerSubline); the three places a pack
    # author reads about `subline` say so
    repo = pathlib.Path(__file__).resolve().parent.parent
    docs = {
        "CONTRIBUTING.md": (repo / "CONTRIBUTING.md").read_text(encoding="utf-8"),
        "boltjar/sdk.py node()": node.__doc__ or "",
        "editor/src/types/protocol.ts": (repo / "editor" / "src" / "types" / "protocol.ts")
        .read_text(encoding="utf-8").replace("*", " "),
    }
    for where, text in docs.items():
        prose = " ".join(text.split())
        assert "a node with a model widget shows its model" in prose, where
        assert "any other its category in lower case" in prose, where
