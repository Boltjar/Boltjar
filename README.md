# Boltjar

Boltjar is a visual, node-based builder for always-on systems. Every trigger, model, store, transform, conditional and output is a typed node, and you build behaviour by wiring them together on one canvas: think **ComfyUI** (a typed node graph you can rewire) crossed with **n8n** (triggers and always-on automation). A workflow is a live server you turn **On / Off / Restart**, not a one-shot run: triggers push events, and the nodes they fire pull their data inputs on demand. The backend is Python (FastAPI + asyncio), the editor is React + React Flow, and the LLM node answers in mock mode until you add a provider key, so a chat graph works out of the box.

## Quick start (Windows)

**Prerequisites:** Python 3.12 and Node.js 18+.

```powershell
# 1. backend
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. editor (built once; the server serves it)
npm ci --prefix editor
npm run build --prefix editor

# 3. run, then open http://localhost:8770
.\.venv\Scripts\python.exe -m uvicorn boltjar.server:app --port 8770
```

After the first install, `start.bat` rebuilds the editor, starts the server and opens the browser in one go.

In the editor, hit **On** and type in the Chat Input node (the `chat` example graph loads by default).

**Real models (optional):** copy `.env.example` to `.env` and fill in the keys you have (xAI, Anthropic, Fish Audio, ElevenLabs, ...), or point `OLLAMA_BASE_URL` at a local Ollama. Without a key the LLM node stays in mock mode, and a voice (TTS / STT) node fails with an error that names the missing key.

**Headless run:** `.\.venv\Scripts\python.exe -m boltjar examples\demo.json 6` runs a graph in the terminal for 6 seconds and prints every live event.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
npm test --prefix editor
```

## License

MIT, see [LICENSE](LICENSE).
