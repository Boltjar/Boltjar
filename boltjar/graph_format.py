"""
boltjar.graph_format: the saved graph's format version and its migrations.

A saved graph carries a top-level ``"format"`` number. When the shape of a saved
graph changes, CURRENT_FORMAT goes up by one and a step that turns the previous
format into the new one is added to _STEPS. migrate() runs every step a graph
is behind, in order, so a graph saved by any earlier Boltjar still loads. A
graph without the field predates it and is format 0.

The server migrates every graph it loads (GET, power, validate), stamps the
current format on every save and never saves over a graph whose format it
cannot read; the editor writes the same number (GRAPH_FORMAT in
editor/src/lib/graphAdapter.ts).
"""
from __future__ import annotations

import copy
from typing import Callable

CURRENT_FORMAT = 1


class GraphFormatError(ValueError):
    """A graph this Boltjar cannot read: saved by a newer Boltjar, or with a
    `format` that is not a format number."""


def _from_0(graph: dict) -> dict:
    """Format 0 (saved before the field existed) already has the format 1 shape."""
    return graph


# the step that turns a graph of format N (the key) into format N + 1.
_STEPS: dict[int, Callable[[dict], dict]] = {
    0: _from_0,
}


def format_of(graph: dict) -> int:
    """The graph's format number. Raises GraphFormatError for a graph from a newer
    Boltjar (loading it here could silently drop what this version does not
    know) or with a `format` that is not a whole number."""
    version = graph.get("format", 0)
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise GraphFormatError(f"the graph's format {version!r} is not a format number")
    if version > CURRENT_FORMAT:
        raise GraphFormatError(
            f"the graph was saved by a newer Boltjar (format {version}); "
            f"this one reads formats up to {CURRENT_FORMAT}, update Boltjar to open it")
    return version


def migrate(graph: dict) -> dict:
    """The graph in CURRENT_FORMAT, with `format` stamped as its first key. The
    input is never modified. Raises GraphFormatError for a graph this Boltjar
    cannot read (see format_of)."""
    version = format_of(graph)
    out = copy.deepcopy(graph)
    while version < CURRENT_FORMAT:
        out = _STEPS[version](out)
        version += 1
    return {"format": CURRENT_FORMAT, **{k: v for k, v in out.items() if k != "format"}}
