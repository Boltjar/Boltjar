# Node packs

Third-party node packs go in this folder. To install one, drop its folder here and restart the server; its nodes show up in the node library next to the built-in ones.

A pack is a folder with two files at its top:

- `pack.toml`: the manifest. `id` (lowercase letters, digits, `_` and `-`), `name` and `version` are required; `author`, `license`, `description`, `homepage` and `min_boltjar` are optional.
- `__init__.py`: imported at startup. Importing it registers the pack's nodes with `@node` from `boltjar.sdk`. Every node id starts with the pack id and a dot (`hello.shout` in a pack whose id is `hello`).

Model manifests the pack ships go in a `models/` folder inside it, one `.toml` per model.

A pack that fails to load (a missing or invalid `pack.toml`, an import error, a node id outside its own namespace, a node or pipe type that redefines one already registered, a `min_boltjar` newer than this install) is skipped with an error in the server log; the server and every other pack still load. `GET /api/packs` lists the packs that loaded and the ones that were skipped, with the reason.

`examples/packs/hello/` is a minimal working pack: copy that folder here to try it. A full guide to writing packs will be on the docs site.

Everything in this folder except this README is ignored by git.
