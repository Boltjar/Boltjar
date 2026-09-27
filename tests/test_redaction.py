"""A resolved secret never leaves the runtime in a live event: Runtime._notify
swaps every known secret value back for its {{secret.NAME}} token, while the
values flowing between nodes stay real."""
from __future__ import annotations

import asyncio

import pytest

import boltjar.nodes.core  # noqa: F401  (registers the core nodes)
from boltjar import secrets
from boltjar.runtime import Runtime

KEY = "sk-live-0123456789abcdef"


@pytest.fixture(autouse=True)
def stored_key(monkeypatch):
    monkeypatch.setitem(secrets._store, "SERVICE_KEY", KEY)


def test_redact_names_the_secret():
    assert secrets.redact(f"Authorization: Bearer {KEY}") == "Authorization: Bearer {{secret.SERVICE_KEY}}"


def test_redact_covers_provider_keys_from_env(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-env-key-0000")
    assert secrets.redact("key=xai-env-key-0000") == "key={{secret.XAI_API_KEY}}"


def test_redact_never_touches_other_env_vars(monkeypatch):
    monkeypatch.setenv("SOME_SETTING", "ordinary-setting-value")
    assert secrets.redact("ordinary-setting-value") == "ordinary-setting-value"


def test_redact_skips_short_values(monkeypatch):
    # a 3-letter "secret" would blank every "yes" on every wire.
    monkeypatch.setitem(secrets._store, "SHORT", "yes")
    assert secrets.redact("yes, yes") == "yes, yes"


def test_redact_replaces_the_longest_value_first(monkeypatch):
    monkeypatch.setitem(secrets._store, "OUTER_KEY", KEY + "-extended")
    assert secrets.redact(KEY + "-extended") == "{{secret.OUTER_KEY}}"


def test_live_events_carry_the_token_and_wires_carry_the_key():
    events: list[dict] = []
    rt = Runtime(observer=events.append)
    rt.build({
        "nodes": [
            # the key reaching a wire as plain text, as a resolved secret would.
            {"id": "text", "type": "core.value.text", "config": {"text": KEY}},
            {"id": "tpl", "type": "core.data.template", "config": {"template": "Bearer {key}"}},
        ],
        "edges": [{"src": "text", "src_port": "out", "dst": "tpl", "dst_port": "key"}],
    })
    asyncio.run(rt._fire(rt.nodes["tpl"], "trigger", 1, rt.new_turn()))
    # the wire itself holds the real key (a downstream HTTP node needs it) ...
    assert rt.nodes["tpl"].out_latch["out"] == f"Bearer {KEY}"
    # ... but no event handed to the editor does.
    assert events and all(KEY not in repr(e) for e in events)
    out = next(e for e in events if e.get("node") == "tpl" and e.get("port") == "out")
    assert out["value"] == "Bearer {{secret.SERVICE_KEY}}"


def test_node_errors_and_logs_are_redacted_too():
    events: list[dict] = []
    rt = Runtime(observer=events.append)
    rt.log("n1", f"calling with {KEY}")
    rt._notify({"kind": "node_error", "node": "n1", "error": f"HTTPStatusError('... key={KEY}')"})
    assert [e.get("message") or e.get("error") for e in events] == [
        "calling with {{secret.SERVICE_KEY}}", "HTTPStatusError('... key={{secret.SERVICE_KEY}}')"]
