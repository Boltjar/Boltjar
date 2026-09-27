"""Tests for the connections feature:
  - provider_status shape (no secret values),
  - provider_connected reflects set/unset keys,
  - setting a provider key via the API writes os.environ and flips model availability,
  - deleting a user-set key flips availability back,
  - as_dict() always carries the 'available' flag,
  - 409 when the key came from .env (not user secrets).
"""
from __future__ import annotations

import os
import pathlib

import pytest

import boltjar.secrets as secrets
from boltjar import models as _models
from fastapi.testclient import TestClient
from boltjar.server import app

http = TestClient(app)


# ---------------------------------------------------------------------------
# Fixture: isolated secrets (mirrors test_secrets.py pattern).
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def isolated_secrets(tmp_path, monkeypatch):
    """Repoint _PATH to a temp file and reload for every test."""
    tmp_file = tmp_path / "secrets.json"
    monkeypatch.setattr(secrets, "_PATH", tmp_file)
    secrets._load()
    # Reset the Ollama ping cache so tests that monkeypatch the ping start clean.
    monkeypatch.setattr(secrets, "_ollama_cache", None)
    yield
    secrets._store.clear()


# ---------------------------------------------------------------------------
# Fixture: stub out the Ollama ping so no real network calls happen.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def stub_ollama(monkeypatch):
    """Replace the internal Ollama connectivity check with a stub (offline)."""
    monkeypatch.setattr(secrets, "_ollama_connected", lambda: False)


# ---------------------------------------------------------------------------
# provider_status shape
# ---------------------------------------------------------------------------

def test_provider_status_returns_list():
    status = secrets.provider_status()
    assert isinstance(status, list)
    assert len(status) > 0


def test_provider_status_has_required_keys():
    for entry in secrets.provider_status():
        assert "provider" in entry
        assert "connected" in entry
        assert "envVar" in entry
        # Must never include a key value.
        assert "value" not in entry
        assert "key" not in entry


def test_provider_status_covers_all_providers():
    names = {e["provider"] for e in secrets.provider_status()}
    assert names == set(secrets.PROVIDERS.keys())


def test_provider_status_ollama_envvar_is_none():
    entry = next(e for e in secrets.provider_status() if e["provider"] == "ollama")
    assert entry["envVar"] is None


# ---------------------------------------------------------------------------
# provider_connected reflects set/unset keys (cloud providers)
# ---------------------------------------------------------------------------

def test_provider_connected_false_when_key_absent(monkeypatch):
    # Remove the env var so the provider appears disconnected.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert secrets.provider_connected("anthropic") is False


def test_provider_connected_true_when_key_present(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert secrets.provider_connected("anthropic") is True


def test_set_secret_makes_provider_connected():
    """set_secret mirrors into os.environ immediately."""
    os.environ.pop("OPENAI_API_KEY", None)
    assert secrets.provider_connected("openai") is False
    secrets.set_secret("OPENAI_API_KEY", "sk-test")
    assert secrets.provider_connected("openai") is True
    # Cleanup: remove from env so we don't bleed into other tests.
    os.environ.pop("OPENAI_API_KEY", None)


def test_delete_secret_makes_provider_disconnected():
    secrets.set_secret("OPENAI_API_KEY", "sk-test")
    assert secrets.provider_connected("openai") is True
    secrets.delete_secret("OPENAI_API_KEY")
    assert secrets.provider_connected("openai") is False


# ---------------------------------------------------------------------------
# as_dict() carries the 'available' flag
# ---------------------------------------------------------------------------

def _make_manifest(provider: str = "openai") -> _models.ModelManifest:
    return _models.ModelManifest(
        id=f"{provider}/test-model",
        provider=provider,
        model="test-model",
        label="Test Model",
    )


def test_as_dict_has_available_key():
    m = _make_manifest("openai")
    d = m.as_dict()
    assert "available" in d


def test_as_dict_available_false_when_key_absent(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    m = _make_manifest("openai")
    assert m.as_dict()["available"] is False


def test_as_dict_available_true_when_key_set():
    secrets.set_secret("OPENAI_API_KEY", "sk-test")
    m = _make_manifest("openai")
    assert m.as_dict()["available"] is True
    os.environ.pop("OPENAI_API_KEY", None)


def test_as_dict_unknown_provider_is_always_available():
    m = _make_manifest("unknown_provider_xyz")
    assert m.as_dict()["available"] is True


# ---------------------------------------------------------------------------
# API: POST /api/connections/providers/{provider}/key
# ---------------------------------------------------------------------------

def test_api_set_provider_key_writes_environ():
    os.environ.pop("XAI_API_KEY", None)
    resp = http.post("/api/connections/providers/xai/key", json={"value": "xai-test-key"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert os.environ.get("XAI_API_KEY") == "xai-test-key"
    os.environ.pop("XAI_API_KEY", None)


def test_api_set_provider_key_makes_model_available():
    monkeypatch_env = os.environ
    monkeypatch_env.pop("ANTHROPIC_API_KEY", None)

    m = _make_manifest("anthropic")
    assert m.as_dict()["available"] is False

    http.post("/api/connections/providers/anthropic/key", json={"value": "ant-test"})
    assert m.as_dict()["available"] is True

    os.environ.pop("ANTHROPIC_API_KEY", None)


def test_api_set_provider_key_rejects_ollama():
    resp = http.post("/api/connections/providers/ollama/key", json={"value": "anything"})
    assert resp.status_code == 400


def test_api_set_provider_key_rejects_unknown_provider():
    resp = http.post("/api/connections/providers/doesnotexist/key", json={"value": "x"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# API: DELETE /api/connections/providers/{provider}/key
# ---------------------------------------------------------------------------

def test_api_delete_provider_key_flips_availability():
    http.post("/api/connections/providers/openai/key", json={"value": "sk-test"})
    m = _make_manifest("openai")
    assert m.as_dict()["available"] is True

    resp = http.delete("/api/connections/providers/openai/key")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert m.as_dict()["available"] is False


def test_api_delete_provider_key_not_set_returns_404():
    # Ensure the key is not in user secrets and not in env.
    os.environ.pop("GOOGLE_API_KEY", None)
    resp = http.delete("/api/connections/providers/google/key")
    assert resp.status_code == 404


def test_api_delete_env_only_key_returns_409(monkeypatch):
    """Key present in os.environ but not in user secrets -> 409 (managed in .env)."""
    # Put the key in env but NOT in user secrets.
    monkeypatch.setenv("GOOGLE_API_KEY", "env-managed-value")
    # Ensure it's not in the user store.
    secrets._store.pop("GOOGLE_API_KEY", None)

    resp = http.delete("/api/connections/providers/google/key")
    assert resp.status_code == 409
    assert "managed in server .env" in resp.json()["error"]


def test_api_delete_ollama_returns_404():
    resp = http.delete("/api/connections/providers/ollama/key")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/connections
# ---------------------------------------------------------------------------

def test_api_get_connections_shape():
    resp = http.get("/api/connections")
    assert resp.status_code == 200
    data = resp.json()
    assert "providers" in data
    assert isinstance(data["providers"], list)
    for entry in data["providers"]:
        assert "provider" in entry
        assert "connected" in entry
        assert "envVar" in entry
        assert "value" not in entry
        assert "key" not in entry


def test_api_get_connections_never_returns_key_value():
    secrets.set_secret("XAI_API_KEY", "super-secret")
    resp = http.get("/api/connections")
    body = resp.text
    assert "super-secret" not in body
    os.environ.pop("XAI_API_KEY", None)
