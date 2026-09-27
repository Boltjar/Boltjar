"""The Gemini provider call (`_google`), checked at the HTTP boundary."""
from __future__ import annotations

import asyncio

import httpx
import pytest

import boltjar.nodes.core.builtin as b


def test_key_travels_in_a_header_not_the_url(vendor_http, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "g-key-123")
    vendor_http.reply = lambda request: httpx.Response(
        200, json={"candidates": [{"content": {"parts": [{"text": "hi"}]}}]})
    assert asyncio.run(b._google("gemini-test", "hello", {})) == ("hi", "")
    request = vendor_http.last
    assert request.headers["x-goog-api-key"] == "g-key-123"
    assert "g-key-123" not in str(request.url)


def test_an_http_error_never_quotes_the_key(vendor_http, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "g-key-123")
    vendor_http.reply = lambda request: httpx.Response(400, json={"error": "bad"})
    with pytest.raises(httpx.HTTPStatusError) as exc:
        asyncio.run(b._google("gemini-test", "hello", {}))
    assert "g-key-123" not in str(exc.value)
