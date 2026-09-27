"""
boltjar.serve: `python -m boltjar serve`, the command that runs Boltjar.

    python -m boltjar serve [--host 127.0.0.1] [--port 8770] [--no-browser]
                            [--verbose] [--allow-remote]

It prints the banner with the boot checklist beside it (Python and its
virtualenv, the editor bundle, the port, the node packs): only facts that hold
for the whole run. Providers and keys change while it runs, in the editor's
Connections, so the editor shows them and the terminal never does. It binds the
port itself so a busy one is reported before anything starts, runs uvicorn in
this process with the console's quiet log setup, opens the browser once the
server reports ready (when a screen is in front of whoever started it, see
browser_can_open), and on Ctrl+C stops every graph and ends every live
connection BEFORE uvicorn waits on them, so one press exits.

The server learns its bind through the environment: BOLTJAR_ALLOWED_HOSTS (the
host names a request may carry, a comma list) and BOLTJAR_PORT.
"""
from __future__ import annotations

import asyncio
import errno
import ipaddress
import logging
import logging.config
import os
import pathlib
import socket
import sys
import threading
import webbrowser
from typing import Awaitable, Callable, Mapping

from boltjar import __version__, console, deps

ROOT = pathlib.Path(__file__).resolve().parent.parent
EDITOR_INDEX = ROOT / "editor" / "dist" / "index.html"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8770
LOOPBACK_NAMES = ("localhost", "127.0.0.1", "::1")
WILDCARD_HOSTS = ("0.0.0.0", "::", "")
PYTHON_RANGE = ((3, 11), (3, 13))
START = "start.bat" if os.name == "nt" else "./start.sh"
# How long each stage of an exit may wait: the graphs stopping (a node whose
# close hangs is left behind after this), then anything still open once they
# have (an Ollama model pull mid-download) before uvicorn cancels it. So an exit
# never hangs, and a second Ctrl+C ends either wait at once.
GRACEFUL_SECONDS = 5

# socket errors by meaning; Windows reports its own WSA codes in `winerror`.
_IN_USE = {errno.EADDRINUSE, 10048}
_DENIED = {errno.EACCES, 10013}

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------- the bind
def is_loopback(host: str) -> bool:
    name = host.strip().strip("[]").lower()
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def host_problem(host: str, allow_remote: bool) -> str | None:
    """Why `host` may not be served, or None. Anything but loopback puts the
    editor, and every key it can spend, on the network, so it needs the
    explicit --allow-remote."""
    if allow_remote or is_loopback(host):
        return None
    return f"{host or 'every interface'} can be reached from other machines"


def allowed_hosts(host: str, existing: str = "") -> list[str]:
    """The host names a request may carry: the loopback names, the bind host,
    and for a wildcard bind this machine's name and addresses. Names already in
    BOLTJAR_ALLOWED_HOSTS (`existing`, say a reverse proxy's) are kept."""
    names = [n.strip() for n in existing.split(",")] + list(LOOPBACK_NAMES)
    bare = host.strip().strip("[]")
    names += _machine_names() if bare in WILDCARD_HOSTS else [bare]
    out: list[str] = []
    seen: set[str] = set()
    for name in names:
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


def _machine_names() -> list[str]:
    """This machine's name and the addresses another machine reaches it on,
    IPv4 and IPv6. The name's own lookup can miss the LAN address (Debian and
    Ubuntu map the hostname to 127.0.1.1), so the address of the route out is
    added too. Loopback and link-local addresses are left out: no browser on
    another machine can use them."""
    try:
        hostname = socket.gethostname()
    except OSError:
        return []
    try:
        found = [info[4][0] for info in socket.getaddrinfo(hostname, None)]
    except OSError:
        found = []
    names = [hostname]
    for address in [*found, *_outbound_addresses()]:
        address = address.split("%", 1)[0]  # an IPv6 zone never appears in a Host header
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if not (ip.is_loopback or ip.is_link_local):
            names.append(address)
    return names


# Addresses kept for documentation (RFC 5737, RFC 3849): nothing answers there.
# Connecting a UDP socket to one sends nothing; it only asks the system which
# local address its route out uses.
_ROUTE_PROBES = ((socket.AF_INET, "192.0.2.1"), (socket.AF_INET6, "2001:db8::1"))


def _outbound_addresses() -> list[str]:
    """The local address of this machine's route out, per address family."""
    found = []
    for family, target in _ROUTE_PROBES:
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as probe:
                probe.connect((target, 9))
                found.append(probe.getsockname()[0])
        except OSError:  # no route in that family, or no IPv6 at all
            continue
    return found


def local_url(host: str, port: int) -> str:
    """The address to open on this machine (a wildcard bind is reached on loopback)."""
    bare = host.strip().strip("[]")
    if bare in WILDCARD_HOSTS:
        bare = DEFAULT_HOST
    if ":" in bare:
        bare = f"[{bare}]"
    return f"http://{bare}:{port}"


def editor_url(host: str, port: int) -> str:
    """The address the ready line prints and the browser opens. A loopback or a
    wildcard bind opens on loopback, where the editor is handed its cookie; a
    browser reaching any other address gets the cookie only through the
    one-time /?token=<token> link, so that bind opens the link (boltjar.security)."""
    url = local_url(host, port)
    if is_loopback(host) or host.strip().strip("[]") in WILDCARD_HOSTS:
        return url
    from boltjar import security

    return f"{url}/?{security.LINK_PARAM}={security.get_token()}"


def bind(host: str, port: int) -> socket.socket:
    """Bind the listening socket before anything else starts, so a busy port is
    reported plainly up front. uvicorn then serves on this very socket, so no
    other process can take the port in between."""
    bare = host.strip().strip("[]") or "0.0.0.0"
    family = socket.AF_INET6 if ":" in bare else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        if os.name != "nt":
            # as uvicorn does: a port the last run left in TIME_WAIT is free to
            # reuse. On Windows the same flag would let two servers share a port.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((bare, port))
    except OSError:
        sock.close()
        raise
    return sock


def port_problem(exc: OSError, port: int) -> console.Row:
    code = getattr(exc, "winerror", None) or exc.errno
    other = port + 1 if port < 65535 else port - 1
    if code in _IN_USE:
        return console.Row("Port", f"{port} is in use by another program (maybe another Boltjar)",
                           "bad", fixes=(f"stop that program, or use another port: --port {other}",))
    if code in _DENIED:
        return console.Row("Port", f"{port} is reserved or blocked by the system", "bad",
                           fixes=(f"use another port: --port {other}",))
    return console.Row("Port", f"cannot listen on {port}: {exc.strerror or exc}", "bad",
                       fixes=(f"use another port: --port {other}",))


# ---------------------------------------------------------------- the checklist
def python_row(version: tuple = tuple(sys.version_info[:3]), prefix: str = sys.prefix,
               base_prefix: str = sys.base_prefix, requirements: str | None = None) -> console.Row:
    """Python: the version and the virtualenv it runs in, and a fix for each
    thing that is off (an untested version, no virtualenv, requirements that
    changed since the last install)."""
    detail = ".".join(str(part) for part in version[:3])
    tone, fixes = "ok", []
    low, high = PYTHON_RANGE
    if not low <= tuple(version[:2]) <= high:
        tone = "warn"
        detail += " is untested (3.11 to 3.13 are supported)"
        fixes.append(f"{START} sets up a supported Python for you")
    if prefix == base_prefix:
        tone, venv = "warn", "no virtualenv"
        fixes.append(f"{START} creates .venv for you")
    else:
        venv = _venv_name(prefix)
    if (deps.status() if requirements is None else requirements) == "changed":
        tone = "warn"
        fixes.append(f"requirements.txt changed since the last install: run {START} again, "
                     "or python -m pip install -r requirements.txt")
    return console.Row("Python", detail, tone, aside=venv, gap="  ", fixes=tuple(fixes))


def _venv_name(prefix: str) -> str:
    """The virtualenv as the checklist shows it: relative to the install
    (.venv), or only its folder's name, never a path with the account name."""
    path = pathlib.Path(prefix).resolve()
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.name


def editor_row(index: pathlib.Path = EDITOR_INDEX) -> console.Row:
    if index.is_file():
        return console.Row("Editor", "bundle ready")
    return console.Row("Editor", "not built: the API runs, the editor page does not", "warn",
                       fixes=("npm ci --prefix editor, then npm run build --prefix editor "
                              "(needs Node.js 18+)",))


def port_row(host: str, port: int, hosts: list[str]) -> console.Row:
    if is_loopback(host):
        return console.Row("Port", f"{port} free")
    # the host check answers to these names only, so another machine uses one
    names = ", ".join(name for name in hosts if not is_loopback(name))
    return console.Row("Port", f"{host or '0.0.0.0'}:{port}, reachable from other machines"
                       + (f" as {names}" if names else ""), "warn",
                       fixes=("to use another name, add it to BOLTJAR_ALLOWED_HOSTS (a comma list)",))


def packs_row(report: dict | None = None) -> console.Row:
    """The node packs that loaded, with their node count, and any that did not."""
    if report is None:
        from boltjar import packs

        report = packs.report()
    loaded = report.get("loaded", [])
    ids = ", ".join(str(p.get("id")) for p in loaded) or "none"
    count = sum(int(p.get("nodes") or 0) for p in loaded)
    aside = f"({count} node{'' if count == 1 else 's'})"
    failed = report.get("failed", [])
    if not failed:
        return console.Row("Packs", ids, aside=aside)
    names = ", ".join(str(f.get("id") or f.get("folder")) for f in failed)
    return console.Row("Packs", f"{ids}; failed: {names}", "warn", aside=aside,
                       fixes=("the reason is in the log line above, and at /api/packs",))


# ---------------------------------------------------------------- the server
def browser_can_open(platform: str | None = None, env: Mapping[str, str] | None = None) -> bool:
    """Whether opening the editor reaches a browser window in front of the
    person who started it. Not over SSH, and not on a Linux or BSD box without
    a graphical session, where Python's webbrowser falls back to a text browser
    (lynx, w3m) that would take over this terminal. The ready line has the URL."""
    platform = sys.platform if platform is None else platform
    env = os.environ if env is None else env
    if env.get("SSH_CONNECTION"):
        return False
    if platform in ("win32", "darwin"):
        return True
    return bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))


def make_server(config, shutdown_app: Callable[[], Awaitable[int]],
                on_ready: Callable[[], None], on_stop: Callable[[], None],
                running: Callable[[], list[str]] = list):
    """uvicorn's Server with Boltjar's start and stop around it: `on_ready` once
    the app has started and the socket is listening; on shutdown, stop taking
    connections, then `shutdown_app()` (stop the graphs, end the streams), then
    uvicorn's own shutdown, which waits for connections that are now closing.
    `running()` names the graphs still running, for a stop that runs late."""
    import uvicorn

    class BoltjarServer(uvicorn.Server):
        graphs_stopped = 0

        async def startup(self, sockets=None) -> None:
            await super().startup(sockets=sockets)
            if self.started and not self.should_exit:
                on_ready()

        async def shutdown(self, sockets=None) -> None:
            on_stop()
            # stop accepting first: an editor tab reconnects the moment its
            # socket closes, and must find nothing listening.
            for listener in getattr(self, "servers", []):
                listener.close()
            await self.stop_app()
            await super().shutdown(sockets=sockets)

        async def stop_app(self) -> None:
            """shutdown_app() for GRACEFUL_SECONDS at most, and no longer once a
            second Ctrl+C sets force_exit: a stop that never finishes (a pack
            node whose close hangs) must not hold the exit. Graphs still
            stopping by then are named and left behind."""
            before = len(running())
            task = asyncio.ensure_future(shutdown_app())
            loop = asyncio.get_running_loop()
            deadline = loop.time() + GRACEFUL_SECONDS
            # polled like uvicorn polls it: the signal handler only sets a flag
            while not task.done() and not self.force_exit and loop.time() < deadline:
                await asyncio.wait({task}, timeout=min(0.1, max(0.0, deadline - loop.time())))
            if task.done():
                self.graphs_stopped = task.result()
                return
            task.cancel()
            left = running()
            self.graphs_stopped = before - len(left)
            names = ", ".join(left) or "the graphs"
            if self.force_exit:
                _log.warning("Ctrl+C again: exiting without waiting for %s", names)
            else:
                _log.warning("%s did not stop within %s s: exiting without waiting longer",
                             names, GRACEFUL_SECONDS)
            # uvicorn would otherwise run the app's lifespan shutdown next, which
            # waits on the very stop that just ran out of time.
            self.force_exit = True

    return BoltjarServer(config)


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, open_browser: bool = True,
          verbose: bool = False, allow_remote: bool = False) -> int:
    """Run Boltjar until Ctrl+C. Returns the process exit code."""
    console.prepare_streams()
    out = console.Console()
    logging.config.dictConfig(console.log_config(verbose))
    try:
        return _serve(out, host, port, open_browser, verbose, allow_remote)
    except KeyboardInterrupt:  # Ctrl+C during the boot
        out.section("Shutdown")
        out.goodbye()
        return 130
    finally:
        out.show_cursor()


def _serve(out: console.Console, host: str, port: int, open_browser: bool,
           verbose: bool, allow_remote: bool) -> int:
    rows: list[console.Row] = []

    def refuse(row: console.Row, code: int) -> int:
        """The banner with the checklist so far, ending on the line that stopped it."""
        out.banner(__version__, [*rows, row])
        return code

    problem = host_problem(host, allow_remote)
    if problem:
        return refuse(console.Row("Host", problem, "bad", fixes=(
            f"add --allow-remote to serve {host or 'every interface'} on purpose",)), 2)
    rows += [python_row(), editor_row()]

    try:
        sock = bind(host, port)
    except OSError as exc:
        return refuse(port_problem(exc, port), 1)
    # read by the server when it builds its host check (see boltjar.security)
    hosts = allowed_hosts(host, os.environ.get("BOLTJAR_ALLOWED_HOSTS", ""))
    os.environ["BOLTJAR_ALLOWED_HOSTS"] = ",".join(hosts)
    os.environ["BOLTJAR_PORT"] = str(port)
    rows.append(port_row(host, port, hosts))

    try:
        with out.pending("loading the node packs"):
            from boltjar import server as app_module
            rows.append(packs_row())
    except Exception as exc:
        sock.close()
        missing = isinstance(exc, ImportError)
        if verbose:
            _log.exception("the server could not load")
        return refuse(console.Row("Packs", f"{type(exc).__name__}: {exc}", "bad", fixes=(
            f"run {START} again to install what is missing" if missing
            else "run with --verbose for the full traceback",)), 1)
    out.banner(__version__, rows)

    import uvicorn

    url = editor_url(host, port)
    opens = open_browser and browser_can_open()
    sep = f" {out.style.glyphs['sep']} "
    note = sep.join([*(["opening your browser"] if opens else []), "Ctrl+C stops everything",
                     *([] if verbose else ["--verbose shows requests"])])

    def ready() -> None:
        out.ready(url, note)
        if opens:
            threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()

    def stopping() -> None:
        out.section("Shutdown")
        running = app_module.running_graphs()
        if running:
            out.line("stop", f"stopping {_count(len(running), 'graph')}")

    config = uvicorn.Config(
        app_module.app, host=host, port=port,
        log_config=console.log_config(verbose), access_log=verbose,
        timeout_graceful_shutdown=GRACEFUL_SECONDS,
    )
    server = make_server(config, app_module.shutdown_all, on_ready=ready, on_stop=stopping,
                         running=app_module.running_graphs)
    try:
        server.run(sockets=[sock])
    except KeyboardInterrupt:
        pass  # uvicorn hands back the Ctrl+C it handled, once it has shut down
    except SystemExit as exc:  # uvicorn exits this way when the app cannot start
        out.line("bad", "the server could not start",
                 hint="the reason is in the log above; --verbose shows more")
        return exc.code if isinstance(exc.code, int) else 1
    finally:
        sock.close()
    # each graph's own power-off line says it stopped
    out.goodbye()
    return 0


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"
