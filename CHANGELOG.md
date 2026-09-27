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
- Model manifests as TOML files, one per model: chat models from xAI, Anthropic and Ollama, plus voice, embedding and rerank models.
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
- A Connections window for provider keys and local Ollama models (list, pull, delete).
- A secrets store. Node settings reference a secret as a `{{secret.NAME}}` token.
- No API route returns a secret's value. The editor sees names and presence only.
- Every Save keeps a timestamped snapshot, the newest 50 per graph, in `user/autosave/`.
- An MCP server (`python -m boltjar.mcp_server`) so an AI assistant can list nodes, edit and validate graphs, power them and chat with them.
- A headless runner: `python -m boltjar <graph.json> [seconds]` runs a graph and prints every live event.
- Wires to ports that no longer exist are dropped when a graph loads, with a notice in the console.
- Two example graphs: `chat` and `demo`.

[Unreleased]: https://github.com/Boltjar/Boltjar/commits/main
