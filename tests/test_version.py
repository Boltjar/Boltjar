"""One version source: boltjar.__version__. GET /api/version reports it with the
Python version and platform (what a bug report needs), and every other place
that states a version agrees with it."""
from __future__ import annotations

import json
import pathlib
import platform
import re

from local_client import local_client

import boltjar
import boltjar.server as server
from boltjar import custom_nodes

REPO = pathlib.Path(__file__).resolve().parent.parent
client = local_client()


def test_the_version_is_a_release_number():
    assert re.fullmatch(r"\d+\.\d+\.\d+", boltjar.__version__)


def test_the_version_route_reports_boltjar_python_and_platform():
    body = client.get("/api/version").json()
    assert body == {"version": boltjar.__version__, "python": platform.python_version(),
                    "platform": platform.platform()}


def test_every_stated_version_is_the_package_version():
    assert server.app.version == boltjar.__version__
    assert custom_nodes.LOADED["core"].version == boltjar.__version__
    package = json.loads((REPO / "editor" / "package.json").read_text(encoding="utf-8"))
    assert package["version"] == boltjar.__version__
