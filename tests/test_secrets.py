"""Tests for boltjar.secrets and the /api/secrets routes in boltjar.server."""
from __future__ import annotations

import importlib
import pathlib

import pytest

import boltjar.secrets as secrets


# ---------------------------------------------------------------------------
# Fixture: repoint the module's data path to a temp file and reload state.
# The real user/data/secrets.json is never touched.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def isolated_secrets(tmp_path, monkeypatch):
    """For every test: repoint _PATH to a fresh tmp file and reload."""
    tmp_file = tmp_path / "secrets.json"
    monkeypatch.setattr(secrets, "_PATH", tmp_file)
    # Reload in-memory state from the (empty) tmp path.
    secrets._load()
    yield
    # Reload back to an empty state after the test so state never leaks.
    secrets._store.clear()


# ---------------------------------------------------------------------------
# resolve_secrets
# ---------------------------------------------------------------------------

def test_resolve_known_secret_is_replaced():
    secrets.set_secret("MY_KEY", "abc123")
    result = secrets.resolve_secrets("token={{secret.MY_KEY}}")
    assert result == "token=abc123"


def test_resolve_unknown_secret_leaves_token_intact():
    result = secrets.resolve_secrets("token={{secret.UNKNOWN_KEY}}")
    # Must NOT become empty; must stay as-is so the caller can detect it.
    assert result == "token={{secret.UNKNOWN_KEY}}"


def test_resolve_spaced_token_form():
    secrets.set_secret("SPACED_KEY", "spaced_value")
    result = secrets.resolve_secrets("a={{ secret.SPACED_KEY }}b")
    assert result == "a=spaced_valueb"


def test_resolve_multiple_tokens():
    secrets.set_secret("A_KEY", "alpha")
    secrets.set_secret("B_KEY", "beta")
    result = secrets.resolve_secrets("{{secret.A_KEY}}-{{secret.B_KEY}}-{{secret.MISSING}}")
    assert result == "alpha-beta-{{secret.MISSING}}"


def test_resolve_reads_a_curated_provider_key_from_env(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-from-env")
    assert secrets.resolve_secrets("Bearer {{secret.XAI_API_KEY}}") == "Bearer xai-from-env"


@pytest.mark.parametrize("name", ["AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN", "PATH"])
def test_resolve_never_reads_other_env_vars(monkeypatch, name):
    # a shared graph must not be able to send the process environment anywhere.
    monkeypatch.setenv(name, "must-not-leak")
    assert secrets.get_secret(name) is None
    assert secrets.resolve_secrets("{{secret.%s}}" % name) == "{{secret.%s}}" % name


def test_stored_secret_resolves_by_any_valid_name():
    secrets.set_secret("MY_SERVICE_TOKEN", "stored")
    assert secrets.resolve_secrets("{{secret.MY_SERVICE_TOKEN}}") == "stored"


def test_unresolved_names_each_token_that_resolves_to_nothing(monkeypatch):
    secrets.set_secret("KNOWN_KEY", "k")
    monkeypatch.setenv("XAI_API_KEY", "xai-from-env")
    monkeypatch.setenv("ENV_ONLY_NAME", "not-a-secret")
    text = ("{{secret.KNOWN_KEY}} {{secret.XAI_API_KEY}} {{secret.ENV_ONLY_NAME}} "
            "{{ secret.MISSING }} {{secret.ENV_ONLY_NAME}}")
    assert secrets.unresolved(text) == ["ENV_ONLY_NAME", "MISSING"]
    assert secrets.unresolved("no tokens here") == []


# ---------------------------------------------------------------------------
# set / get / delete round-trip + persistence across a reload
# ---------------------------------------------------------------------------

def test_set_get_roundtrip():
    secrets.set_secret("API_KEY", "secret_value")
    assert secrets.get_secret("API_KEY") == "secret_value"


def test_delete_removes_secret():
    secrets.set_secret("DEL_KEY", "v")
    assert secrets.delete_secret("DEL_KEY") is True
    assert secrets.get_secret("DEL_KEY") is None


def test_persistence_across_reload():
    secrets.set_secret("PERSIST_KEY", "durable")
    # Simulate a fresh process: reload from disk into a new module-level _store.
    secrets._load()
    assert secrets.get_secret("PERSIST_KEY") == "durable"


def test_delete_persists_across_reload():
    secrets.set_secret("EPHEMERAL", "v")
    secrets.delete_secret("EPHEMERAL")
    secrets._load()
    assert secrets.get_secret("EPHEMERAL") is None


# ---------------------------------------------------------------------------
# Name validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_name", [
    "lowercase",
    "Mixed_Case",
    "has space",
    "has.dot",
    "has-dash",
    "",
])
def test_invalid_name_raises(bad_name):
    with pytest.raises(ValueError, match="invalid"):
        secrets.set_secret(bad_name, "v")


def test_valid_name_uppercase_digits_underscore():
    secrets.set_secret("VALID_KEY_123", "ok")
    assert secrets.get_secret("VALID_KEY_123") == "ok"


# ---------------------------------------------------------------------------
# delete_secret returns False for env/unknown keys
# ---------------------------------------------------------------------------

def test_delete_returns_false_for_unknown_key():
    assert secrets.delete_secret("DOES_NOT_EXIST") is False


def test_delete_returns_false_for_env_only_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env_val")
    # Env keys are not in _store; delete must return False.
    assert secrets.delete_secret("OPENAI_API_KEY") is False


# ---------------------------------------------------------------------------
# list_secrets: no values, correct source labels
# ---------------------------------------------------------------------------

def test_list_secrets_never_includes_value():
    secrets.set_secret("MY_TOKEN", "super_secret")
    entries = secrets.list_secrets()
    for entry in entries:
        assert "value" not in entry, f"entry {entry!r} must not contain a value field"


def test_list_secrets_user_source():
    secrets.set_secret("USER_KEY", "v")
    entries = {e["name"]: e for e in secrets.list_secrets()}
    assert "USER_KEY" in entries
    assert entries["USER_KEY"]["source"] == "user"
    assert entries["USER_KEY"]["hasValue"] is True


def test_list_secrets_env_source(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env_val")
    entries = {e["name"]: e for e in secrets.list_secrets()}
    assert "ANTHROPIC_API_KEY" in entries
    assert entries["ANTHROPIC_API_KEY"]["source"] == "env"
    assert entries["ANTHROPIC_API_KEY"]["hasValue"] is True


def test_list_secrets_absent_env_key_not_included(monkeypatch):
    # Remove all curated env keys so none bleed in from the real environment.
    for key in secrets._ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    entries = {e["name"]: e for e in secrets.list_secrets()}
    for key in secrets._ENV_KEYS:
        assert key not in entries


# ---------------------------------------------------------------------------
# TestClient: GET / POST / DELETE over the FastAPI routes
# ---------------------------------------------------------------------------

from local_client import local_client

http = local_client()


def test_http_get_secrets_returns_list():
    resp = http.get("/api/secrets")
    assert resp.status_code == 200
    assert "secrets" in resp.json()
    assert isinstance(resp.json()["secrets"], list)


def test_http_post_set_valid_secret():
    resp = http.post("/api/secrets", json={"name": "HTTP_KEY", "value": "val"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    # The key should now appear in the list.
    names = {e["name"] for e in http.get("/api/secrets").json()["secrets"]}
    assert "HTTP_KEY" in names
    # Clean up.
    secrets.delete_secret("HTTP_KEY")


def test_http_post_invalid_name_returns_400():
    resp = http.post("/api/secrets", json={"name": "bad name", "value": "v"})
    assert resp.status_code == 400
    assert "error" in resp.json()


def test_http_delete_existing_secret():
    secrets.set_secret("TO_DELETE", "v")
    resp = http.delete("/api/secrets/TO_DELETE")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_http_delete_missing_returns_404():
    resp = http.delete("/api/secrets/DOES_NOT_EXIST_AT_ALL")
    assert resp.status_code == 404


def test_http_get_never_returns_values():
    secrets.set_secret("HIDDEN_VALUE", "shh")
    resp = http.get("/api/secrets")
    assert resp.status_code == 200
    for entry in resp.json()["secrets"]:
        assert "value" not in entry
    secrets.delete_secret("HIDDEN_VALUE")
