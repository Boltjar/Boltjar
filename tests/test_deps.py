"""The requirement-hash gate the start scripts run before every start."""
from __future__ import annotations

import pathlib

from boltjar import deps


def _files(tmp_path: pathlib.Path, text: str = "fastapi>=0.110\nuvicorn>=0.29\n"):
    req = tmp_path / "requirements.txt"
    req.write_bytes(text.encode())
    return req, tmp_path / "venv" / deps.STAMP_NAME


def test_line_endings_do_not_change_the_hash(tmp_path):
    lf = tmp_path / "lf.txt"
    crlf = tmp_path / "crlf.txt"
    lf.write_bytes(b"a\nb\n")
    crlf.write_bytes(b"a\r\nb\r\n")
    assert deps.requirements_hash(lf) == deps.requirements_hash(crlf)


def test_status_reads_the_stamp(tmp_path):
    req, stamp = _files(tmp_path)
    assert deps.status(req, stamp) == "untracked"
    stamp.parent.mkdir()
    stamp.write_text(deps.requirements_hash(req) + "\n")
    assert deps.status(req, stamp) == "current"
    req.write_text("fastapi>=0.120\n")
    assert deps.status(req, stamp) == "changed"


def test_sync_installs_once_then_skips(tmp_path):
    req, stamp = _files(tmp_path)
    stamp.parent.mkdir()
    calls = []

    def install(path):
        calls.append(path)
        return 0

    assert deps.sync(req, stamp, install) == 0
    assert deps.sync(req, stamp, install) == 0
    assert calls == [req]
    assert stamp.read_text().strip() == deps.requirements_hash(req)


def test_a_changed_file_installs_again(tmp_path):
    req, stamp = _files(tmp_path)
    stamp.parent.mkdir()
    calls = []
    deps.sync(req, stamp, lambda p: calls.append(p) or 0)
    req.write_text("fastapi>=0.120\n")
    deps.sync(req, stamp, lambda p: calls.append(p) or 0)
    assert len(calls) == 2


def test_a_failed_install_leaves_no_stamp_so_the_next_start_retries(tmp_path):
    req, stamp = _files(tmp_path)
    stamp.parent.mkdir()
    assert deps.sync(req, stamp, lambda p: 1) == 1
    assert not stamp.exists()
    assert deps.status(req, stamp) == "untracked"


def test_the_stamp_lives_in_the_environment(tmp_path):
    assert deps.stamp_path(tmp_path) == tmp_path / ".requirements.sha256"


def test_check_mode_reports_without_installing(tmp_path, monkeypatch):
    req, stamp = _files(tmp_path)
    monkeypatch.setattr(deps, "REQUIREMENTS", req)
    monkeypatch.setattr(deps, "stamp_path", lambda prefix=None: stamp)
    monkeypatch.setattr(deps, "pip_install", lambda p: (_ for _ in ()).throw(AssertionError("installed")))
    assert deps.main(["--check"]) == 1
    stamp.parent.mkdir()
    stamp.write_text(deps.requirements_hash(req))
    assert deps.main(["--check"]) == 0
