"""update.bat pulls a new copy of itself and must still finish the run it
started. cmd reads a batch file one line at a time, reopening it at a saved
byte offset, so every step after the pull has to be parsed before git replaces
the file. Here upstream edits update.bat and the local copy runs the update."""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="update.bat runs on Windows"),
    pytest.mark.skipif(shutil.which("git") is None, reason="needs git"),
]

# start.bat stands in for the real one: it reports its arguments and exits with
# a code of its own, which update.bat hands back.
STUB_START = "@echo off\r\necho started with %*\r\nexit /b 3\r\n"


def _git(cwd: pathlib.Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-c", "user.name=Test",
         "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false", *args],
        cwd=cwd, check=True, capture_output=True,
    )


def _header_edits(script: str) -> dict[str, str]:
    """Two upstream edits of update.bat: a longer header and a shorter one, so
    cmd's saved offset lands both short of and past the line it stood at."""
    lines = script.split("\r\n")
    rems = [i for i, line in enumerate(lines) if line.startswith("rem ")]
    longer = lines[:1] + ["rem " + "a longer header line " * 3] * 4 + lines[1:]
    shorter = [line for i, line in enumerate(lines) if i not in rems[:3]]
    return {"longer": "\r\n".join(longer), "shorter": "\r\n".join(shorter)}


def _checkout(tmp_path: pathlib.Path, script: str) -> tuple[pathlib.Path, pathlib.Path]:
    """An upstream repo holding `script` as update.bat and a clone of it."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _git(upstream, "init", "-q", "-b", "main")
    (upstream / "update.bat").write_bytes(script.encode("ascii"))
    (upstream / "start.bat").write_bytes(STUB_START.encode("ascii"))
    _git(upstream, "add", ".")
    _git(upstream, "commit", "-q", "-m", "first")
    _git(tmp_path, "clone", "-q", str(upstream), "local")
    return upstream, tmp_path / "local"


def _update(local: pathlib.Path, path: str | None = None) -> subprocess.CompletedProcess:
    # stdin is empty, so a `pause` on the failure path returns at once
    env = dict(os.environ) if path is None else {**os.environ, "PATH": path}
    return subprocess.run(["cmd", "/d", "/c", str(local / "update.bat"), "--port", "9000"],
                          cwd=local, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, timeout=60)


def _edit_the_editor(upstream: pathlib.Path, script: str) -> None:
    """An upstream commit that changes update.bat and the editor's lockfile."""
    (upstream / "update.bat").write_bytes(_header_edits(script)["longer"].encode("ascii"))
    (upstream / "editor").mkdir()
    (upstream / "editor" / "package-lock.json").write_text("{}")
    _git(upstream, "add", ".")
    _git(upstream, "commit", "-q", "-m", "editor changes")


def _path_without_npm(*extra: pathlib.Path) -> str:
    """This PATH minus every folder that holds npm, with `extra` in front."""
    keep = [folder for folder in os.environ.get("PATH", "").split(os.pathsep)
            if folder and not any((pathlib.Path(folder) / name).is_file()
                                  for name in ("npm", "npm.cmd", "npm.exe"))]
    return os.pathsep.join([*map(str, extra), *keep])


@pytest.mark.parametrize("edit", ["longer", "shorter"])
def test_update_finishes_after_pulling_a_new_update_bat(tmp_path, edit):
    script = (ROOT / "update.bat").read_bytes().decode("ascii")
    upstream, local = _checkout(tmp_path, script)
    (upstream / "update.bat").write_bytes(_header_edits(script)[edit].encode("ascii"))
    _git(upstream, "commit", "-q", "-am", "update.bat changes")

    run = _update(local)
    assert (local / "update.bat").read_bytes().decode("ascii") == _header_edits(script)[edit]
    assert run.stdout.count("started with --port 9000") == 1, run.stdout + run.stderr
    assert run.returncode == 3  # start.bat's own exit code


def test_a_changed_editor_is_rebuilt_after_the_pull(tmp_path):
    script = (ROOT / "update.bat").read_bytes().decode("ascii")
    upstream, local = _checkout(tmp_path, script)
    _edit_the_editor(upstream, script)
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "npm.cmd").write_bytes(b"@echo npm %*\r\n")

    run = _update(local, _path_without_npm(tools))
    steps = [line for line in run.stdout.splitlines() if line.startswith(("npm ", "started"))]
    assert steps == ["npm ci --prefix editor", "npm run build --prefix editor",
                     "started with --port 9000"], run.stdout + run.stderr
    assert run.returncode == 3


def test_a_changed_editor_without_npm_still_starts(tmp_path):
    script = (ROOT / "update.bat").read_bytes().decode("ascii")
    upstream, local = _checkout(tmp_path, script)
    _edit_the_editor(upstream, script)

    run = _update(local, _path_without_npm())
    assert "npm was not found" in run.stdout, run.stdout + run.stderr
    assert run.stdout.count("started with --port 9000") == 1
    assert run.returncode == 3


def test_a_pull_that_cannot_fast_forward_stops_before_start(tmp_path):
    script = (ROOT / "update.bat").read_bytes().decode("ascii")
    upstream, local = _checkout(tmp_path, script)
    (upstream / "notes.txt").write_text("upstream")
    _git(upstream, "add", ".")
    _git(upstream, "commit", "-q", "-m", "upstream change")
    (local / "notes.txt").write_text("local")
    _git(local, "add", ".")
    _git(local, "commit", "-q", "-m", "local change")

    run = _update(local)
    assert "could not fast-forward" in run.stdout, run.stdout + run.stderr
    assert "started with" not in run.stdout
    assert run.returncode == 1
