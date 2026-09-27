# Boltjar

Boltjar is a visual, node-based builder for always-on systems. Every trigger, model, store, transform, conditional and output is a typed node, and you build behaviour by wiring them together on one canvas: think **ComfyUI** (a typed node graph you can rewire) crossed with **n8n** (triggers and always-on automation). A workflow is a live server you turn **On / Off / Restart**, not a one-shot run: triggers push events, and the nodes they fire pull their data inputs on demand. The backend is Python (FastAPI + asyncio), the editor is React + React Flow, and the LLM node answers in mock mode until you add a provider key, so a chat graph works out of the box.

## Quick start

**Windows:** double-click `start.bat`, or run it in a terminal. **macOS and Linux:** run `./start.sh`.

**Prerequisites:** [Node.js](https://nodejs.org) 18+ to build the editor on the first run. Python 3.11 to 3.13 is used when one is installed; when none is, the start script downloads a portable Python 3.12 from [python-build-standalone](https://github.com/astral-sh/python-build-standalone) into `.python/` and checks it against the release's SHA-256 sums.

The start script creates `.venv`, installs `requirements.txt` whenever the file changes, builds the editor when it is missing, then starts the server and opens http://127.0.0.1:8770. Ctrl+C stops it: every running graph is turned off cleanly on the way out.

**Startup settings:** the gear in the top bar opens Settings on its General tab, with three options that stay off until you turn them on. **Launch with system** starts Boltjar when you log in, without opening the browser, through one entry in your own account (a `Boltjar.cmd` in the Windows Startup folder, `~/Library/LaunchAgents/link.boltjar.plist` on macOS, `~/.config/autostart/boltjar.desktop` on Linux); turning it off deletes that file, and it can be changed only from this computer. **Start Ollama with Boltjar** starts an installed Ollama that is not running, and Boltjar stops it again when it exits; it too can be changed only from this computer. **Resume workflows after launch** turns back On the graphs that were On when Boltjar last stopped, as they ran; one that no longer validates stays Off, the editor says why, and each launch tries it again until you pick **Stop resuming** (the command palette, or the Problems panel).

In the editor, hit **On** and type in the Chat Input node (the `chat` example graph loads by default).

**Updating:** `update.bat` or `./update.sh` pulls the latest code (fast-forward only), rebuilds the editor when it changed, and starts Boltjar.

**Real models (optional):** copy `.env.example` to `.env` and fill in the keys you have (xAI, Anthropic, Fish Audio, ElevenLabs, ...), or point `OLLAMA_BASE_URL` at a local Ollama. Without a key the LLM node stays in mock mode, and a voice (TTS / STT) node fails with an error that names the missing key.

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

## Tests

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
npm test --prefix editor
```

## License

AGPL-3.0-or-later with a node pack exception: use Boltjar for anything, share your changes to Boltjar itself, and license your own packs and graphs however you want. Details in [LICENSE-EXCEPTION.md](LICENSE-EXCEPTION.md); the name and logo in [TRADEMARK.md](TRADEMARK.md).
