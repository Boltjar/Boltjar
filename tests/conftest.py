"""Shared test utilities.

`vendor_http` stands in for a vendor at the HTTP boundary. Every
`httpx.AsyncClient` the code under test opens becomes a REAL client on an
`httpx.MockTransport`, so the request (URL, headers, JSON or multipart body) is
built exactly as in production and recorded, and nothing leaves the machine.
Testing here, rather than by monkeypatching a vendor helper, is what catches a
wrong payload (the Fish `chunk_length` 400 went unnoticed because the helper
itself was mocked away).
"""
from __future__ import annotations

import json
import pathlib
import shutil
import tempfile
from typing import Callable

import httpx
import pytest

from boltjar import endpoints, model_discovery, security, settings


def pytest_configure(config):
    # The server keeps its token in user/data/token; the suite gets its own, so
    # a test run never creates or reads the real one. The model list cache and
    # the custom endpoints get the same treatment, and no background refresh
    # ever reaches a real provider (a test that wants one sets AUTO_REFRESH).
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="boltjar-test-"))
    security.TOKEN_PATH = tmp / "token"
    model_discovery.CACHE_PATH = tmp / "models-cache.json"
    model_discovery.AUTO_REFRESH = False
    endpoints.PATH = tmp / "endpoints.json"
    # the install's settings: the suite's own
    settings.PATH = tmp / "settings.json"


def pytest_unconfigure(config):
    shutil.rmtree(security.TOKEN_PATH.parent, ignore_errors=True)


class VendorHTTP:
    """Records every request and answers with `reply(request)` (settable)."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.reply: Callable[[httpx.Request], httpx.Response] = lambda request: httpx.Response(200)

    def handle(self, request: httpx.Request) -> httpx.Response:
        request.read()  # materialise the body (a multipart stream) for assertions
        self.requests.append(request)
        return self.reply(request)

    @property
    def last(self) -> httpx.Request:
        assert self.requests, "no HTTP request was made"
        return self.requests[-1]

    def last_json(self) -> dict:
        return json.loads(self.last.content)

    def last_parts(self) -> list[tuple[str, bytes]]:
        """The (field name, value) parts of the last multipart request, in wire order."""
        request = self.last
        ctype = request.headers["content-type"]
        assert ctype.startswith("multipart/form-data"), ctype
        boundary = ctype.split("boundary=", 1)[1].encode()
        parts: list[tuple[str, bytes]] = []
        for chunk in request.content.split(b"--" + boundary):
            if b"\r\n\r\n" not in chunk:
                continue  # the preamble and the closing `--` marker
            head, _, value = chunk.partition(b"\r\n\r\n")
            name = head.split(b'name="', 1)[1].split(b'"', 1)[0].decode()
            parts.append((name, value[:-2] if value.endswith(b"\r\n") else value))
        return parts


@pytest.fixture
def vendor_http(monkeypatch) -> VendorHTTP:
    rec = VendorHTTP()
    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(rec.handle)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    return rec
