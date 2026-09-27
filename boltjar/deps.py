"""
boltjar.deps: install requirements.txt only when it changed.

The start scripts run `python -m boltjar.deps` before every start. It hashes
requirements.txt and compares the hash with the one stored in the virtualenv
(`.requirements.sha256`) by the last install that succeeded: an unchanged file
costs nothing, and a pull that bumps a dependency installs it. The serve
checklist reads the same stamp. Stdlib only: it runs before any requirement is
installed.

    python -m boltjar.deps            install when needed, then store the hash
    python -m boltjar.deps --check    exit 0 when up to date, 1 when not
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import subprocess
import sys
from typing import Callable

ROOT = pathlib.Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements.txt"
STAMP_NAME = ".requirements.sha256"


def requirements_hash(path: pathlib.Path | None = None) -> str:
    """SHA-256 of the requirements file, line endings normalised so a checkout
    with CRLF hashes the same as one with LF."""
    path = REQUIREMENTS if path is None else path
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def stamp_path(prefix: str | pathlib.Path | None = None) -> pathlib.Path:
    """Where the hash of the last good install lives: inside the environment
    (sys.prefix), so a fresh venv always installs."""
    return pathlib.Path(sys.prefix if prefix is None else prefix) / STAMP_NAME


def status(requirements: pathlib.Path | None = None, stamp: pathlib.Path | None = None) -> str:
    """The install state: `current` when the stamp matches, `changed` when it
    does not, `untracked` when there is no stamp (packages installed by hand)."""
    stamp = stamp_path() if stamp is None else stamp
    try:
        stored = stamp.read_text(encoding="utf-8").strip()
    except OSError:
        return "untracked"
    return "current" if stored == requirements_hash(requirements) else "changed"


def pip_install(requirements: pathlib.Path) -> int:
    return subprocess.call([sys.executable, "-m", "pip", "install",
                            "--disable-pip-version-check", "-r", str(requirements)])


def sync(requirements: pathlib.Path | None = None, stamp: pathlib.Path | None = None,
         install: Callable[[pathlib.Path], int] | None = None) -> int:
    """Install when the stamp does not match; store the hash only after the
    install succeeds, so a failed or interrupted install is retried next start.
    Returns the installer's exit code (0 when nothing had to be done)."""
    requirements = REQUIREMENTS if requirements is None else requirements
    stamp = stamp_path() if stamp is None else stamp
    install = pip_install if install is None else install
    state = status(requirements, stamp)
    if state == "current":
        return 0
    digest = requirements_hash(requirements)
    reason = "first run" if state == "untracked" else "requirements.txt changed"
    print(f"Installing requirements ({reason})...", flush=True)
    code = install(requirements)
    if code == 0:
        stamp.write_text(digest + "\n", encoding="utf-8")
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m boltjar.deps",
                                     description="Install requirements.txt when it changed.")
    parser.add_argument("--check", action="store_true",
                        help="only report: exit 0 when up to date, 1 when an install is due")
    args = parser.parse_args(argv)
    if args.check:
        return 0 if status() == "current" else 1
    return sync()


if __name__ == "__main__":
    sys.exit(main())
