# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/2.0.0/).

## [Unreleased]

### Added

- A node editor on React Flow, with wires colored by type, undo and redo, node groups and a command palette.
- Graphs run as live servers you turn On, Off and Restart, and they keep running when the browser tab closes.
- A push/pull runtime: triggers push events, and the nodes they fire pull their data inputs on demand.
- Validation before power-on checks every node and every wire, port types included.
- Triggers: Interval, Schedule (cron with a timezone), Manual, Chat Input, Audio Input, Webhook and Agenda.
- The Webhook trigger can require a shared secret in the `X-Webhook-Secret` header.
- An LLM node that reshapes its ports and knobs to the selected model, with tool calling through Tool and Tool Args.
- Model manifests as TOML files, one per model: chat models from xAI, Anthropic, OpenAI and Ollama, plus voice, embedding and rerank models.
- A live model list: the picker shows the models your Ollama has installed and your keys can run, asked from each provider when the server starts, on Refresh, and when the list is read after it went stale (Ollama after 30 seconds, so opening a picker shows a model you just pulled; the others after 3 hours), and kept offline in a cache. A manifest enriches the model it names, and one its provider no longer lists is marked unavailable.
- OpenAI and custom OpenAI-compatible endpoints (OpenRouter, Groq, LM Studio, llama.cpp, vLLM) in the LLM node; a custom endpoint is added in Settings, AI Providers. GPT-4o, GPT-4o mini, GPT-4.1, GPT-4.1 mini, GPT-5, GPT-5 mini and GPT-5 nano take images and tools through their manifests; a model from another endpoint takes them when its server reports them.
- An `auto` model for the LLM node: the first installed Ollama chat model, else the first chat model of another connected provider that answered the last time it was asked, a model with a manifest before one the provider merely lists, else a reply that says how to connect one.
- Validation names a saved model that vanished and the closest one available, after asking its provider again.
- A mock mode: without a key, a cloud model in the LLM node answers with a mock reply, so the chat example replies out of the box. Its TTS node still needs a key.
- TTS and STT nodes for xAI, Fish Audio and ElevenLabs. Each needs its provider's API key.
- Retrieval nodes: Chunk, Sentences, Embed, Vector Store, Vectors and Rerank.
- Embedded stores: SQLite (Database, DB), key-value (KV Store, KV) and files (Read File, Write File, Append File, Delete File, List Dir).
- File nodes read and write only inside `user/data/files/`; a path that escapes it is rejected. Image and Audio read local files only from there and from `examples/`.
- The server answers only pages on this machine that hold its per-install token, checks the Host and Origin of every request, and never hands secrets to a graph beyond the ones you stored and the provider keys.
- The DB node binds wired values as SQL parameters and refuses ATTACH, DETACH and VACUUM INTO.
- Drop-in node packs in `packs/`, with a copyable example in `examples/packs/hello/` and `GET /api/packs` listing what loaded.
- Graph files carry a format number; a graph from a newer Boltjar is refused, never saved over.
- A Help menu: docs, a prefilled bug report, feedback, copy diagnostics and a way to support the project.
- The editor loads its fonts from the bundle and makes no request off this machine.
- Value nodes: Text, Integer, Float, Boolean, Image and Audio.
- Data and text nodes: Template, Format List, List, Compute, Logic, Parse, Stringify, Build JSON, Get, Split JSON, Strip and Tag Parse.
- Compute and Logic evaluate expressions with simpleeval, never Python `eval`.
- Flow nodes: For-each, Sync, Wait, Queue, Changed, Router, Wireless In and Wireless Out.
- A Meter node: a number that persists across fires and drifts back toward a resting value.
- An HTTP Request node.
- Sensors: Time, Screen Capture, Window Capture and Foreground Window (the last two on Windows only).
- Outputs: Chat shows the conversation, Preview shows a wire's value live, Log writes to the console.
- Provider keys and local Ollama models (list, pull, delete) in Settings, AI Providers.
- A Settings panel behind the gear, opening on its General tab, with AI Providers and Secrets beside it. General holds the startup options, each off until you turn it on: Launch with system, Start Ollama with Boltjar and Resume workflows after launch.
- Resume workflows after launch: the graphs On when Boltjar stops come back On at the next launch, as they ran, one at a time and through validation. One that fails stays Off, the terminal and the editor say why, and each launch tries it again until Stop resuming (the command palette or the Problems panel) ends that. `--no-resume` leaves them Off for one launch and keeps them for the next.
- Launch with system: one entry in your own account starts Boltjar at login without opening the browser (the Windows Startup folder, a macOS LaunchAgent, an XDG autostart entry on Linux). Only a browser on the same computer can turn it on or off.
- The Ollama card tells an installed but stopped Ollama from a missing one and starts it with Start Ollama (its output goes to `user/logs/ollama.log`). Only a browser on the same computer can start it or turn on Start Ollama with Boltjar. On exit, Boltjar stops only an Ollama it started.
- A secrets store. Node settings reference a secret as a `{{secret.NAME}}` token.
- No API route returns a secret's value. The editor sees names and presence only.
- Every Save keeps a timestamped snapshot, the newest 50 per graph, in `user/autosave/`.
- An MCP server (`python -m boltjar.mcp_server`) so an AI assistant can list nodes, edit and validate graphs, power them and chat with them.
- A headless runner: `python -m boltjar run <graph.json> [seconds]` runs a graph and prints every live event.
- Start scripts: `start.bat` and `start.sh` find Python 3.11 to 3.13 (or download a portable 3.12 and check it against its SHA-256 sums), create `.venv`, install `requirements.txt` whenever it changes and build the editor when it is missing, then start Boltjar.
- Update scripts: `update.bat` and `update.sh` pull the latest code (fast-forward only), rebuild the editor when it changed and start Boltjar.
- `python -m boltjar serve` starts the server with a boot checklist (Python, editor, port, node packs), reports a busy port before anything starts and opens the editor in a browser, except over SSH or on a machine without a desktop. A host other machines can reach is refused without `--allow-remote`.
- One Ctrl+C stops every running graph and closes the editor connections and media streams. A graph that does not stop within 5 seconds, or by a second Ctrl+C, is left behind.
- The terminal follows the live graphs: a line when a graph turns On or Off, is refused by validation, fails to start or hits a node error, plus each value a Log node writes, summarized to one line (a clip or an image as its type, size and length). Other wire values never print there; a known key shows as its `{{secret.NAME}}` token and a control character as its escape.
- Wires to ports that no longer exist are dropped when a graph loads, with a notice in the console.
- Two example graphs: `chat` and `demo`.

[Unreleased]: https://github.com/Boltjar/Boltjar/commits/main
