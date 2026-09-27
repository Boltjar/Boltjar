# Boltjar license and the node pack exception

Boltjar
Copyright (C) 2026 DKLRD and Boltjar contributors

This program is free software: you can redistribute it and/or modify it under the terms of the
GNU Affero General Public License as published by the Free Software Foundation, either version 3
of the License, or (at your option) any later version. See [LICENSE](LICENSE).

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without
even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
Affero General Public License for more details.

## Additional permission under GNU AGPL version 3 section 7 (Node Pack Exception)

As a special exception, the copyright holders of Boltjar give you permission to create, convey
and use node packs, model manifests and workflow graphs ("Extensions") under terms of your choice,
and to run them together with Boltjar, provided that:

1. each Extension interacts with Boltjar only through its extension interface: the public API of
   the `boltjar.sdk` module, the pack manifest (`pack.toml`), model manifests (`*.toml`) and the
   graph file format; and
2. the Extension does not include, copy or modify any part of Boltjar itself.

An Extension that meets these conditions is not considered a work based on Boltjar, and the
GNU AGPL does not govern its license. This exception does not apply to Boltjar itself or to any
modified version of it, which remain covered by the GNU AGPL version 3 or later. If you modify
Boltjar, you may extend this exception to your version, but you are not obligated to do so; if you
do not wish to, delete this exception statement from your version.

## What this means in practice

- You can use Boltjar for anything, including at work and commercially.
- If you distribute Boltjar, or a modified version of it, you share its source under the same
  license. If you run a modified version as a network service, its users can get that source too.
- Node packs, model manifests and graphs you build on top of Boltjar are yours: license them
  however you want, open or closed.
- The Boltjar name and logo are covered separately, see [TRADEMARK.md](TRADEMARK.md).
