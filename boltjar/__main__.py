"""The command line: `python -m boltjar <command>`.

    serve                        run the server and open the editor (boltjar.serve)
    run <graph.json> [seconds]   run one graph headless and print every live event

`python -m boltjar <graph.json> [seconds]`, the original headless form, still
runs a graph. The runner loads the core nodes and every custom node under custom_nodes/, and
migrates the graph like the server does: one this Boltjar cannot read (saved by
a newer Boltjar) is refused before anything loads. Nothing heavy is imported
until a command needs it, so `--help` works before the requirements are installed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from boltjar import __version__, custom_nodes, secrets
from boltjar.graph_format import GraphFormatError, migrate
from boltjar.runtime import Runtime
from boltjar.serve import DEFAULT_HOST, DEFAULT_PORT

COMMANDS = ("serve", "run")


async def _run(path: str, seconds: float) -> None:
    with open(path, "r", encoding="utf-8") as fh:
        graph = migrate(json.load(fh))
    custom_nodes.load_all()
    secrets.ensure_loaded()  # the graph's nodes read provider keys from os.environ
    runtime = Runtime(observer=lambda e: print(json.dumps(e)))
    runtime.build(graph)
    await runtime.run()
    await asyncio.sleep(seconds)
    await runtime.stop()


def _port(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a port number: {text!r}") from None
    if not 1 <= value <= 65535:
        raise argparse.ArgumentTypeError(f"a port is 1 to 65535, not {value}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m boltjar",
        description="Boltjar: a visual node builder for always-on AI systems.")
    parser.add_argument("--version", action="version", version=f"Boltjar {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="command")

    serve = commands.add_parser("serve", help="run the server and open the editor",
                                description="Run the Boltjar server and open the editor.")
    serve.add_argument("--version", action="version", version=f"Boltjar {__version__}")
    serve.add_argument("--host", default=DEFAULT_HOST,
                       help=f"address to listen on (default {DEFAULT_HOST}, this machine only)")
    serve.add_argument("--port", type=_port, default=DEFAULT_PORT,
                       help=f"port to listen on (default {DEFAULT_PORT})")
    serve.add_argument("--no-browser", action="store_true",
                       help="do not open the editor in a browser")
    serve.add_argument("--verbose", action="store_true",
                       help="also show the web server's own lines, every request and full tracebacks")
    serve.add_argument("--allow-remote", action="store_true",
                       help="allow a --host that other machines can reach")
    serve.add_argument("--no-resume", action="store_true",
                       help="leave Off, for this launch only, the graphs that were On when "
                            "Boltjar last stopped (with Resume workflows after launch on in "
                            "Settings, the next launch powers them back On)")

    run = commands.add_parser("run", help="run a graph headless and print its live events",
                              description="Run one graph without the editor and print every live event.")
    run.add_argument("graph", help="path to a graph .json file")
    run.add_argument("seconds", nargs="?", type=float, default=6.0,
                     help="how long to run it (default 6)")
    return parser


def parse_args(argv: list[str]) -> argparse.Namespace:
    argv = list(argv)
    if argv and argv[0] not in COMMANDS and not argv[0].startswith("-"):
        argv.insert(0, "run")  # the original form: python -m boltjar <graph.json> [seconds]
    return build_parser().parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "serve":
        from boltjar.serve import serve
        return serve(host=args.host, port=args.port, open_browser=not args.no_browser,
                     verbose=args.verbose, allow_remote=args.allow_remote,
                     resume=not args.no_resume)
    if args.command == "run":
        try:
            asyncio.run(_run(args.graph, args.seconds))
        except GraphFormatError as exc:
            sys.exit(f"{args.graph}: {exc}")
        return 0
    build_parser().print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
