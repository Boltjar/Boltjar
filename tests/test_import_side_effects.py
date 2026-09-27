"""Importing the node registry has no side effects.

A docs generator (or any offline tool) loads the registry (the SDK, the core
pack, packs.load_all) to read node definitions. That must not copy the user's
saved secrets into the environment nor reach the network (the Ollama
availability ping). Secrets load on first use, and when the server starts."""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import textwrap

import pytest
from fastapi.testclient import TestClient

import boltjar.nodes.core  # noqa: F401  (registers the core nodes + model manifests)
import boltjar.secrets as secrets
from boltjar import models

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _run_isolated(code: str, *args: str) -> subprocess.CompletedProcess:
    """Run `code` in a fresh interpreter (a clean import state) from the repo root;
    `args` arrive as sys.argv[1:]."""
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code), *args], cwd=ROOT,
                          capture_output=True, text=True, timeout=120)


def test_loading_the_registry_reads_no_secrets_and_opens_no_socket(tmp_path):
    # packs load from an empty install root: a pack in the real packs/ is third
    # party code, and what it does at import is not Boltjar's to test.
    result = _run_isolated("""
        import os, socket, sys

        touched = []

        def refuse(*args, **kwargs):
            # recorded, then refused: a caller that swallows the error still counts.
            touched.append(args)
            raise OSError("network blocked in this test")

        socket.socket.connect = refuse
        socket.create_connection = refuse
        environ = dict(os.environ)

        import boltjar.sdk
        import boltjar.nodes.core
        from boltjar import models, packs, secrets

        packs.load_all(sys.argv[1])

        assert not secrets._loaded, "secrets were read at import"
        assert dict(os.environ) == environ, "the environment changed at import"
        catalog = models.catalog(probe=False)
        assert catalog and all(m["available"] is None for m in catalog)
        assert not touched, f"the network was touched: {touched}"
        print(len(boltjar.sdk.NODE_REGISTRY))
    """, str(tmp_path))
    assert result.returncode == 0, result.stderr
    assert int(result.stdout.strip()) > 0


@pytest.fixture
def unread_secrets(tmp_path, monkeypatch):
    """A secrets file on disk that this process has not read yet."""
    path = tmp_path / "secrets.json"
    path.write_text(json.dumps({"BOLTJAR_TEST_SAVED": "saved-value"}), encoding="utf-8")
    monkeypatch.setattr(secrets, "_PATH", path)
    monkeypatch.setattr(secrets, "_store", {})
    monkeypatch.setattr(secrets, "_loaded", False)
    monkeypatch.delenv("BOLTJAR_TEST_SAVED", raising=False)
    monkeypatch.delenv("BOLTJAR_TEST_NEW", raising=False)
    yield path
    os.environ.pop("BOLTJAR_TEST_SAVED", None)
    os.environ.pop("BOLTJAR_TEST_NEW", None)


def test_the_first_lookup_loads_the_saved_secrets(unread_secrets):
    assert secrets.get_secret("BOLTJAR_TEST_SAVED") == "saved-value"
    assert os.environ["BOLTJAR_TEST_SAVED"] == "saved-value"


def test_saving_before_any_read_keeps_the_saved_secrets(unread_secrets):
    secrets.set_secret("BOLTJAR_TEST_NEW", "new-value")
    on_disk = json.loads(unread_secrets.read_text(encoding="utf-8"))
    assert on_disk == {"BOLTJAR_TEST_SAVED": "saved-value", "BOLTJAR_TEST_NEW": "new-value"}


def test_the_server_loads_the_secrets_when_it_starts(unread_secrets):
    import boltjar.server as server

    assert "BOLTJAR_TEST_SAVED" not in os.environ
    with TestClient(server.app):
        assert os.environ["BOLTJAR_TEST_SAVED"] == "saved-value"


def test_a_catalog_without_probe_checks_no_provider(monkeypatch):
    def refuse(provider):
        raise AssertionError(f"probed {provider}")

    monkeypatch.setattr(secrets, "provider_connected", refuse)
    catalog = models.catalog(probe=False)
    assert catalog and all(m["available"] is None for m in catalog)
