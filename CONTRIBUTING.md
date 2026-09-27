# Contributing to Boltjar

Thanks for helping build Boltjar. Fixes, new nodes, docs and tests are all welcome.
Small, focused pull requests are the quickest to review.

## Before you start

A bug fix or a small change can go straight to a pull request. For anything bigger, like a new core node or a runtime change, open an issue or a post in [Discussions Ideas](https://github.com/Boltjar/Boltjar/discussions/categories/ideas) first, so we agree on the shape before you spend time on code.

Questions about using Boltjar go where [SUPPORT.md](SUPPORT.md) points.

## Dev setup

You need Python 3.11, 3.12 or 3.13 (3.11 is the floor because the model loader uses `tomllib`), Node.js 20 or newer for the editor, and Git.

Windows (PowerShell):

```powershell
git clone https://github.com/Boltjar/Boltjar.git
cd Boltjar
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
npm ci --prefix editor
npm run build --prefix editor
```

macOS and Linux:

```bash
git clone https://github.com/Boltjar/Boltjar.git
cd Boltjar
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
npm ci --prefix editor
npm run build --prefix editor
```

`requirements-dev.txt` installs everything in `requirements.txt` plus pytest. The commands on this page assume the venv is active. If PowerShell refuses to run `Activate.ps1`, call the venv's Python directly instead: `.\.venv\Scripts\python.exe -m ...`.

Start the server. It opens the editor at http://127.0.0.1:8770 (`--no-browser` skips that, `--verbose` prints every request and full tracebacks):

```
python -m boltjar serve
```

The server serves the built editor from `editor/dist`, so rebuild after an editor change. For hot reload while you work on the editor, keep the server running, start Vite in a second terminal and open http://localhost:5173. Vite forwards `/api` and `/ws` to port 8770.

```
npm run dev --prefix editor
```

Provider keys are optional. Copy `.env.example` to `.env` and fill in the ones you have. Without a key, a cloud model in the LLM node answers with a mock reply, so chat graphs still run. Ollama models need a running Ollama instead of a key. TTS and STT nodes need a key for their provider and fail with an error that names the missing key.

## Running the tests

```
python -m pytest -q
npm test --prefix editor
npm run build --prefix editor
```

The editor build runs `tsc --noEmit` first, so it is also the type check. The Python suite needs no API keys and no running Ollama. When a node calls a vendor API, test it through the `vendor_http` fixture in `tests/conftest.py`. The node builds its real request, and the fixture records it and answers with whatever the test sets. Nothing leaves the machine.

CI runs the same commands on every pull request: Python 3.11, 3.12 and 3.13 on Linux, Python 3.12 on Windows and macOS, the editor build and tests on Node 20, and a gitleaks secret scan.

## Project layout

```
boltjar/                 the Python package
  server.py              FastAPI app: REST API, WebSocket, serves the editor
  runtime.py             the engine: triggers push events, fired nodes pull their data
  sdk.py                 the node contract: @node, Kind, Port, Widget
  models.py              loads the model manifests
  model_discovery.py     the live model list: asks each connected provider which models exist
  endpoints.py           OpenAI and custom OpenAI-compatible endpoints
  secrets.py             secrets store and provider keys
  *_store.py             SQLite, key-value, vector and sandboxed file stores
  mcp_server.py          MCP server, so an AI assistant can drive the runtime
  __main__.py            headless runner: python -m boltjar <graph.json> [seconds]
  nodes/core/builtin.py  every core node
  nodes/core/models/     one TOML manifest per model
editor/                  React + React Flow editor, built with Vite
  src/components/        canvas, node bodies, panels and menus
  src/lib/               graph logic the canvas shares (ports, wires, renames)
  test/                  editor tests, plain Node scripts
examples/                graphs that ship with Boltjar (read-only)
packs/                   drop-in folder for third-party node packs
tests/                   the pytest suite
tools/                   small dev scripts
user/                    your graphs, snapshots, stores and secrets (gitignored)
```

## Adding a node

A node is a Python class with a `@node(...)` decorator. It declares its ports and knobs, and the editor draws it from that declaration. A new node needs no editor code.

Core (`boltjar/nodes/core/`) is for general building blocks that many graphs need. Anything tied to one service or one niche workflow belongs in a pack, which ships on its own schedule and under its own license.

### A core node

Add the class to `boltjar/nodes/core/builtin.py`, in the section for its category:

```python
@node(id="core.text.upper", name="Upper", kind=Kind.TRANSFORM, category="Text",
      pulled=True, summary="Uppercase the incoming text.")
class Upper:
    inputs = [Port("text", "text")]
    outputs = [Port("out", "text")]

    def run(self, text=None, **_):
        return {"out": (text or "").upper()}
```

A few things to know:

- `pulled=True` makes a data node. It runs on demand, when a fired node needs its value. A node that does work takes an input with `trigger=True` instead: it fires when an event arrives and pulls its other inputs at that moment.
- Knobs are annotated class attributes (`seconds: float = 2.0`) or `Widget` values from `boltjar.sdk` (`select`, `slider`, `code`). In the editor, a right-click turns a knob into an input, unless its `Widget` sets `promotable=False`.
- Put behavior on the `Port` and `Widget` declarations (`growable`, `optional`, `op_field` and the rest). The editor reads those generically.
- Raise `NodeFailure` to fail and still emit outputs on a declared error branch.
- Core ids start with `core.` (`core.text.strip`). Saved graphs store the id, so it stays fixed once shipped.
- Add a test in `tests/`. `tests/test_split_node.py` is a short one to copy.

A new model from a provider Boltjar already supports needs no code and no file: once the provider lists it (Ollama has it installed, or your key's account offers it), it shows up in the model picker with knobs from its reported capabilities. A TOML manifest in `boltjar/nodes/core/models/` is optional enrichment: copy the closest one to give a model its own label, knobs or capabilities, and restart the server. The LLM node reshapes its inputs, outputs and knobs to the selected model.

A node that calls a model declares its picker as a widget, `model("tts", "xai/tts")` (the family it lists, then the default), and gets the same picker and per-model knobs as the LLM node.

### A node pack

A pack is a folder in `packs/` with a `pack.toml` manifest and an `__init__.py` that registers its nodes. Drop the folder in, restart, and its nodes show up in the library. Every node id starts with the pack's id and a dot (`yourpack.upper` in a pack whose id is `yourpack`), and a pack can't redefine a core node or an existing pipe type. A pack that fails to load is skipped and listed with its error; the rest keep working.

Start from `examples/packs/hello/`: one pulled node, one fired node, and a `pack.toml` with every field (`id`, `name`, `version`, `author`, `license`, `description`, `homepage`, `min_boltjar`). The examples folder is MIT-0, so copy it freely.

Talk to Boltjar only through `boltjar.sdk` and the manifest files, and don't copy Boltjar code into the pack. Then the [Node Pack Exception](LICENSE-EXCEPTION.md) applies: the pack is yours to license however you want, open or closed. Packs can ship model manifests too, in a `models/` folder next to `pack.toml`.

## Pull requests

1. Fork the repo and branch from `main`. One topic per pull request.
2. Keep refactors out of feature and fix PRs. They get their own.
3. Run the tests above.
4. Open the pull request and fill in the template. Editor changes need a before and after screenshot.
5. CI has to pass. I review every pull request myself, so response times vary.

## Commit style

The subject reads `area: what is true now`.

- `area` is one of `runtime`, `editor`, `server`, `nodes`, `sdk`, `packs`, `examples`, `mcp`, `models`, `docs`, `readme`, `deps`, `tests`, `ci`, `security`, `release`, `license`, `site`, `brand`, `community`, or a node's name as the editor shows it (`Preview:`, `Database:`).
- After the colon it is lowercase (node names and proper nouns keep their case), present tense, about 60 characters and 72 at most, with no period. It says how things are once the commit lands. Plain verbs (add, drop, bump) are fine for mechanical changes.
- No `feat:` or `fix:` prefixes.
- A body is optional: one to three lines with the symptom and the reason. Any number in it is one you measured.
- A change that breaks saved graphs or the SDK carries a `Breaking:` line in the body.

Real examples:

```
license: AGPL-3.0-or-later with a node pack exception
```

```
editor: console shows media as type and size, never base64
```

```
editor: a bool knob reads a saved "false" as off

Graphs saved while the toggle was a text box hold strings, and the truthy
check drew "false" as on. An unset toggle now shows its declared default.
```

## Sign-off (DCO)

Every commit needs a `Signed-off-by:` line. `git commit -s` adds it from your git name and email.

With the sign-off you agree to the [Developer Certificate of Origin](https://developercertificate.org): you wrote the change or otherwise have the right to submit it under this project's license, and you understand the contribution and the sign-off are public record. Forgot one? `git commit --amend -s` fixes the last commit, and `git rebase --signoff main` fixes a whole branch (then force-push it).

## License of contributions

Boltjar is licensed AGPL-3.0-or-later with the [Node Pack Exception](LICENSE-EXCEPTION.md). Contributions come in under the same terms they go out: by opening a pull request you license your change under AGPL-3.0-or-later with the Node Pack Exception. There is no CLA, and you keep the copyright on your work. The name and logo are covered separately in [TRADEMARK.md](TRADEMARK.md).

## Conduct and security

Everyone in the project's spaces follows the [Code of Conduct](CODE_OF_CONDUCT.md).

Found a vulnerability? Please do not open an issue. Report it through [private vulnerability reporting](https://github.com/Boltjar/Boltjar/security/advisories/new).
