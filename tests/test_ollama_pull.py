"""Tests for the three Ollama model-management routes.

All tests monkeypatch httpx so no real network calls happen.
"""
from __future__ import annotations

import json
import pytest
from local_client import local_client


http = local_client()


# ---------------------------------------------------------------------------
# Helpers: fake httpx responses
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code: int, body: dict | str = ""):
        self.status_code = status_code
        self._body = body if isinstance(body, str) else json.dumps(body)
        self.text = self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=None, response=self  # type: ignore[arg-type]
            )

    def json(self):
        return json.loads(self._body)


import httpx


# ---------------------------------------------------------------------------
# GET /api/connections/ollama/models: parsed shape
# ---------------------------------------------------------------------------

def test_ollama_models_parsed_shape(monkeypatch):
    """GET /api/connections/ollama/models returns the expected shape when Ollama is up."""
    fake_tags = {
        "models": [
            {"name": "gemma4:e4b", "size": 3_300_000_000, "modified_at": "2025-01-01T00:00:00Z"},
            {"name": "llama3.2:3b", "size": 2_000_000_000, "modified_at": "2025-02-01T00:00:00Z"},
        ]
    }

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get(self, url, **__):
            return _FakeResponse(200, fake_tags)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeClient())

    resp = http.get("/api/connections/ollama/models")
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert len(data["models"]) == 2
    first = data["models"][0]
    assert first["name"] == "gemma4:e4b"
    assert first["size"] == 3_300_000_000
    assert "modified" in first


def test_ollama_models_offline(monkeypatch):
    """GET /api/connections/ollama/models returns offline=True when Ollama is unreachable."""
    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get(self, url, **__):
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeClient())

    resp = http.get("/api/connections/ollama/models")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("offline") is True
    assert data["models"] == []


# ---------------------------------------------------------------------------
# POST /api/connections/ollama/pull: streaming NDJSON
# ---------------------------------------------------------------------------

FAKE_PULL_LINES = [
    {"status": "pulling manifest"},
    {"status": "downloading", "digest": "sha256:abc", "total": 1000, "completed": 500},
    {"status": "success"},
]


def test_ollama_pull_streams_ndjson(monkeypatch):
    """POST /api/connections/ollama/pull proxies NDJSON lines verbatim."""

    async def _fake_aiter_lines():
        for obj in FAKE_PULL_LINES:
            yield json.dumps(obj)

    class _FakeStreamCtx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def aiter_lines(self):
            return _fake_aiter_lines()

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def stream(self, method, url, **__):
            return _FakeStreamCtx()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeClient())

    resp = http.post(
        "/api/connections/ollama/pull",
        json={"model": "gemma4:e4b"},
    )
    assert resp.status_code == 200
    assert "application/x-ndjson" in resp.headers.get("content-type", "")

    lines = [l for l in resp.text.splitlines() if l.strip()]
    assert len(lines) == len(FAKE_PULL_LINES)
    for raw, expected in zip(lines, FAKE_PULL_LINES):
        parsed = json.loads(raw)
        assert parsed["status"] == expected["status"]


def test_ollama_pull_requires_model():
    """POST /api/connections/ollama/pull with no model returns 400."""
    resp = http.post("/api/connections/ollama/pull", json={})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# DELETE /api/connections/ollama/models/{name}: smoke test
# ---------------------------------------------------------------------------

def test_ollama_delete_success(monkeypatch):
    """DELETE /api/connections/ollama/models/{name} returns {ok: True} on 200."""

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def request(self, method, url, **__):
            return _FakeResponse(200, {"status": "success"})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeClient())

    resp = http.delete("/api/connections/ollama/models/gemma4:e4b")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_ollama_delete_ollama_error(monkeypatch):
    """DELETE returns 502 when Ollama returns an error status."""

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def request(self, method, url, **__):
            return _FakeResponse(404, "model not found")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeClient())

    resp = http.delete("/api/connections/ollama/models/doesnotexist:latest")
    assert resp.status_code == 502
    assert "error" in resp.json()
