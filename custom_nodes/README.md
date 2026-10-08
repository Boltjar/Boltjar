# Custom nodes

Custom nodes go in this folder. To install a custom node, drop its folder here and restart the server; its nodes show up in the node library next to the core nodes.

A custom node is a folder with two files at its top:

- `custom_node.toml`: the manifest. `id` (lowercase letters, digits, `_` and `-`), `name` and `version` are required; `author`, `license`, `description`, `homepage` and `min_boltjar` are optional.
- `__init__.py`: imported at startup. Importing it registers its nodes with `@node` from `boltjar.sdk`. Every node id starts with the custom node's id and a dot (`hello.shout` in a custom node whose id is `hello`).

Model manifests a custom node ships go in a `models/` folder inside it, one `.toml` per model.

A custom node that fails to load (a missing or invalid `custom_node.toml`, an import error, a node id outside its own namespace, a node or pipe type that redefines one already registered, a `min_boltjar` newer than this install) is skipped with an error in the server log; the server and every other custom node still load. `GET /api/custom-nodes` lists the custom nodes that loaded and the ones that were skipped, with the reason.

`examples/custom_nodes/hello/` is a minimal working custom node: copy that folder here to try it. A full guide to writing custom nodes will be on the docs site.

Everything in this folder except this README is ignored by git.
