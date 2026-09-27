"""A node's summary is plain help: the node library shows it on hover and the
MCP list_node_types hands it to whoever authors a graph."""
from __future__ import annotations

import re

import boltjar.nodes.core  # noqa: F401  (registers the core nodes)
from boltjar.sdk import NODE_REGISTRY

# names that are written in capitals anyway, never for emphasis
_ACRONYMS = {"LLM", "JSON", "HTTP", "SQL", "TTS", "STT", "URL", "ISO", "SSE",
             "VRM", "API", "GET", "POST"}
_DASHES = (chr(0x2013), chr(0x2014))  # the en and the em dash


def test_no_summary_reads_like_a_developer_note():
    for nid, spec in NODE_REGISTRY.items():
        if not nid.startswith("core."):
            continue
        prose = re.sub(r"\{+[^}]*\}+|`[^`]*`", "", spec.summary)  # {{secret.NAME}} is syntax
        shouted = [w for w in re.findall(r"\b[A-Z]{3,}\b", prose) if w not in _ACRONYMS]
        assert not shouted, (nid, shouted)
        assert " - " not in spec.summary and "(V1)" not in spec.summary, nid
        assert not any(dash in spec.summary for dash in _DASHES), nid
