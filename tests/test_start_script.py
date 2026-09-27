"""start.sh looks for a supported Python before it fetches one. On macOS the
Pythons in /usr/bin are stubs until the Command Line Tools are installed, and
running one opens their install dialog, so the search passes them by until
xcode-select reports the tools. The function runs as start.sh defines it, with
`uname` and `xcode-select` stood in for."""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")


def _function(name: str) -> str:
    script = (ROOT / "start.sh").read_text()
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", script, re.M | re.S)
    assert match, f"start.sh defines {name}()"
    return match.group(0)


def _stubs(tmp_path: pathlib.Path, system: str, tools: bool) -> list[str]:
    """The paths macos_stub passes by on `system`, with or without the tools."""
    fake = tmp_path / "bin"
    fake.mkdir()
    # `xcode-select -p` succeeds only once the tools are installed
    select = fake / "xcode-select"
    select.write_bytes(b"#!/bin/sh\nexit %d\n" % (0 if tools else 1))
    select.chmod(0o755)
    script = "\n".join([
        _function("macos_stub"),
        'PATH="$(cd "$FAKE_BIN" && pwd):$PATH"',
        f'uname() {{ echo "{system}"; }}',
        "for path in /usr/bin/python3 /usr/bin/python /opt/homebrew/bin/python3.12 "
        "/usr/local/bin/python3; do",
        '    if macos_stub "$path"; then echo "$path"; fi',
        "done",
    ])
    run = subprocess.run(["sh", "-c", script], env={**os.environ, "FAKE_BIN": str(fake)},
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    return run.stdout.split()


def test_macos_without_the_tools_passes_the_system_stubs_by(tmp_path):
    assert _stubs(tmp_path, "Darwin", tools=False) == ["/usr/bin/python3", "/usr/bin/python"]


def test_macos_with_the_tools_tries_every_python(tmp_path):
    assert _stubs(tmp_path, "Darwin", tools=True) == []


def test_linux_tries_every_python(tmp_path):
    assert _stubs(tmp_path, "Linux", tools=False) == []
