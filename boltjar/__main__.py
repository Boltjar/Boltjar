"""Headless runner: `python -m boltjar <graph.json> [seconds]`.

Loads the core pack and every pack under packs/, builds the graph, runs the
actor engine for a while, and prints every live event. Used for testing the
runtime without the editor. The graph is migrated like the server migrates it,
and one this Boltjar cannot read (saved by a newer Boltjar) is refused.
"""
from __future__ import annotations

import asyncio
import json
import sys

from boltjar import packs, secrets
from boltjar.graph_format import GraphFormatError, migrate
from boltjar.runtime import Runtime


async def _run(path: str, seconds: float) -> None:
    with open(path, "r", encoding="utf-8") as fh:
        graph = migrate(json.load(fh))
    packs.load_all()
    secrets.ensure_loaded()  # the graph's nodes read provider keys from os.environ
    runtime = Runtime(observer=lambda e: print(json.dumps(e)))
    runtime.build(graph)
    await runtime.run()
    await asyncio.sleep(seconds)
    await runtime.stop()


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "examples/demo.json"
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 6.0
    try:
        asyncio.run(_run(path, seconds))
    except GraphFormatError as exc:
        sys.exit(f"{path}: {exc}")


if __name__ == "__main__":
    main()
