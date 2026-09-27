"""A TestClient that reaches the app the way the editor does: through a loopback
Host, carrying the per-install token. The bare TestClient talks to
`http://testserver` with no token, which the server's LocalGuard rejects.
"""
from __future__ import annotations

from urllib.parse import urljoin

from fastapi.testclient import TestClient

from boltjar import security
from boltjar.server import app

BASE_URL = "http://127.0.0.1:8770"


class LocalClient(TestClient):
    def websocket_connect(self, url: str, *args, **kwargs):
        # TestClient joins a relative websocket url onto ws://testserver, whatever
        # base_url says; join it onto the loopback host instead.
        return super().websocket_connect(urljoin("ws://127.0.0.1:8770", url), *args, **kwargs)


def local_client(token: bool = True, **kwargs) -> LocalClient:
    """A client on the loopback host, sending the install token as a Bearer
    header (pass token=False to test what an unauthenticated client gets)."""
    headers = {"Authorization": f"Bearer {security.get_token()}"} if token else {}
    return LocalClient(app, base_url=BASE_URL, headers=headers, **kwargs)
