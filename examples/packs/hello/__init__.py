# SPDX-License-Identifier: MIT-0
"""Hello: a minimal example pack. Copy this folder into packs/ and restart.

Importing the pack registers its nodes; every node id starts with the pack id
from pack.toml ("hello.").
"""
from . import nodes  # noqa: F401  (importing registers every @node)
