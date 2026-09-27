# Boltjar

Boltjar is a visual, node-based builder for always-on systems. Every trigger, model, store, transform, conditional and output is a typed node, and you build behaviour by wiring them together on one canvas: think **ComfyUI** (a typed node graph you can rewire) crossed with **n8n** (triggers and always-on automation). A workflow is a live server you turn **On / Off / Restart**, not a one-shot run: triggers push events, and the nodes they fire pull their data inputs on demand. A trigger input fires its node, so every trigger input must be wired before a graph turns On. The backend is Python (FastAPI + asyncio), the editor is React + React Flow, and an LLM node with no model picked answers in mock mode, so a chat graph works out of the box.

## Quick start

**Windows:** double-click `start.bat`, or run it in a terminal. **macOS and Linux:** run `./start.sh`.

**Prerequisites:** [Node.js](https://nodejs.org) 18+ to build the editor on the first run. Python 3.11 to 3.13 is used when one is installed; when none is, the start script downloads a portable Python 3.12 from [python-build-standalone](https://github.com/astral-sh/python-build-standalone) into `.python/` and checks it against the release's SHA-256 sums.

The start script creates `.venv`, installs `requirements.txt` whenever the file changes, builds the editor when it is missing, then starts the server and opens http://127.0.0.1:8770. Ctrl+C stops it: every running graph is turned off cleanly on the way out.

**Startup settings:** the gear in the top bar opens Settings on its General tab, with three options that stay off until you turn them on. **Launch with system** starts Boltjar when you log in, without opening the browser, through one entry in your own account (a `Boltjar.cmd` in the Windows Startup folder, `~/Library/LaunchAgents/link.boltjar.plist` on macOS, `~/.config/autostart/boltjar.desktop` on Linux); turning it off deletes that file, and it can be changed only from this computer. **Start Ollama with Boltjar** starts an installed Ollama that is not running, and Boltjar stops it again when it exits; it too can be changed only from this computer. **Resume workflows after launch** turns back On the graphs that were On when Boltjar last stopped, as they ran; one that no longer validates stays Off, the editor says why, and each launch tries it again until you pick **Stop resuming** (the command palette, or the Problems panel).

In the editor, hit **On** and type in the Chat Input node (the `chat` example graph loads by default). It answers in mock mode: Boltjar never picks a model for you, since a model can cost money. Its TTS and Audio Preview ship bypassed, so the chat runs text only; to hear the replies, pick a voice model in the TTS node, then right-click the TTS and the Audio Preview and choose **Enable**.

**Updating:** `update.bat` or `./update.sh` pulls the latest code (fast-forward only), rebuilds the editor when it changed, and starts Boltjar.

**Real models (optional):** copy `.env.example` to `.env` and fill in the keys you have (xAI, Anthropic, Fish Audio, ElevenLabs, ...), or point `OLLAMA_BASE_URL` at a local Ollama, then pick a model in each model node's picker. A model node starts with none picked: the LLM answers in mock mode until you pick one, and a TTS, STT, Embed or Rerank node keeps its graph Off until you do. Without a key the LLM node stays in mock mode, and a voice (TTS / STT) node fails with an error that names the missing key.

### Server options

Arguments given to the start script pass through to `python -m boltjar serve`, for example `start.bat --port 8771 --no-browser`:

| Option | Effect |
|---|---|
| `--port 8771` | listen on another port (the default is 8770) |
| `--no-browser` | do not open the editor in a browser |
| `--verbose` | also print the web server's own lines, every request and full tracebacks |
| `--no-resume` | leave Off, for this launch only, the graphs that were On when Boltjar last stopped; with **Resume workflows after launch** on, the next launch turns them back On |
| `--host 0.0.0.0 --allow-remote` | listen where other machines can reach it. Another machine opens `http://<name>:8770/?token=<token>` once, with a name the boot checklist lists and the token from `user/data/token`; bound to one address (`--host 192.168.1.20`), the ready line prints that link itself. Any host but loopback is refused without `--allow-remote`, and [SECURITY.md](SECURITY.md#exposing-the-server) says to keep it on loopback |

### Manual setup

With Python 3.11 to 3.13 and Node.js 18+ installed:

```powershell
# Windows
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm ci --prefix editor
npm run build --prefix editor
.\.venv\Scripts\python.exe -m boltjar serve
```

```sh
# macOS and Linux
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm ci --prefix editor
npm run build --prefix editor
.venv/bin/python -m boltjar serve
```

**Headless run:** `python -m boltjar run examples/demo.json 6` (with the `.venv` Python) runs a graph in the terminal for 6 seconds and prints every live event.

## Answering HTTP calls

A game, a script or any other HTTP client talks to a graph through generic nodes, never a node shaped for that client. A **Webhook** receives the call at `http://127.0.0.1:8770/hook/<graph>/<path>`. Set its **reply** to **from Respond to Webhook** and the caller waits, up to the Webhook's **timeout** (30 seconds unless you change it), for the **Respond to Webhook** its run reaches. That node writes its `body` with the status, headers and content type you set; `auto` sends a JSON object or list as JSON and anything else as text. Fire it with **last** off to stream the answer piece by piece, one line per piece or as server-sent events when the caller sends `Accept: text/event-stream`, and with **last** on to end it. Each call gets its own run, so calls in flight at the same time never read each other's data. A call with no answer in time gets 504, and one still waiting when the graph turns Off gets 503. One graph holds up to 64 waiting calls, and one response carries up to 8 MB.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
npm test --prefix editor
```

## License

[Apache-2.0](LICENSE): use Boltjar for anything, commercial work included, change it and ship it, as long as you keep the credit in [NOTICE](NOTICE) where your product shows its credits: "Built with Boltjar (https://boltjar.link), designed by DKLRD". Your own custom nodes and workflows are yours to license however you want. Community nodes that join Boltjar are credited in [CREDITS.md](CREDITS.md); the name and logo are covered by [TRADEMARK.md](TRADEMARK.md).
