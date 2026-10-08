# SPDX-License-Identifier: MIT-0
"""Hello: a minimal example custom node. Copy this folder into custom_nodes/ and
restart.

Importing it registers its nodes; every node id starts with the id from
custom_node.toml ("hello.").
"""
from . import nodes  # noqa: F401  (importing registers every @node)
