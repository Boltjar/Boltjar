# Security

## Threat model

Boltjar is built for one person running it on their own machine. The server
listens on `127.0.0.1` by default, and every request has to pass three checks:

- **Host**: the `Host` header must name this machine (`127.0.0.1`, `localhost`,
  `[::1]`, plus any names in `BOLTJAR_ALLOWED_HOSTS`). This stops DNS
  rebinding, where a web page re-points its own domain at your loopback address.
- **Origin**: WebSockets and every request that changes something must come
  from a page served at the address the request was sent to (same host name
  and port), or from a client that is not a browser. A page another local
  server serves on its own port is refused.
- **Token**: `/api`, `/ws`, `/stream` and `/audio` need the per-install token
  in `user/data/token`, created on first start. A browser holds it as an
  `HttpOnly`, `SameSite=Strict` cookie. Other local clients (the MCP server, your
  own scripts) send `Authorization: Bearer <token>`. To rotate it, stop the
  server, delete the file and start again.

The server hands the cookie only to a browser on this machine that talks to it
directly: from a loopback address, to `127.0.0.1`, `localhost` or `[::1]`, with
no proxy headers. A browser anywhere else opens `http://<host>:<port>/?token=<token>`
once; the server sets the cookie and redirects to the editor.

Together these keep web pages you visit and other machines away from the API.
They do not keep out other accounts on this machine: anyone who can connect to
`127.0.0.1` can ask for the cookie. Do not run Boltjar on a machine you share
with people you do not trust.

Browsers send a cookie to every port of the host that set it, so every local web
server your browser visits at `127.0.0.1` or `localhost` receives the Boltjar
cookie. A server that keeps it can call the API with the editor's full access.

## A graph is code

Running a graph is running a program, with your permissions. A graph someone
else made can:

- read files its nodes can reach: the Files sandbox (`user/data/files`), the
  shipped `examples/`, and the databases and stores under `user/data`;
- capture your screen and windows (Screen Capture, Window Capture, Foreground
  Window);
- call any URL with HTTP Request, including `localhost`, your LAN and cloud
  metadata addresses;
- send your keys anywhere, since `{{secret.NAME}}` resolves inside any field.

Read a graph before you press **On**. A graph keeps running after you close
the browser tab; only stopping the server stops it.

Treat webhook bodies, chat messages, HTTP responses and LLM output as
untrusted. The DB node binds a wired `{tag}` as a value, never as SQL, but a
URL built from a tag goes wherever the tag says. An LLM wired to HTTP, DB or
file tools can be steered by instructions hidden in the text it reads.

Third-party node packs in `packs/` are Python code that runs with your
permissions: install only packs you trust. The MCP server gives the connected
AI the same control of the API that the editor has.

## Exposing the server

Keep it on loopback. Boltjar has no user accounts, and the token is not a
login: whoever holds it has full control, and plain HTTP shows it to anyone on
the network path. If other machines must reach the editor, put the server behind
a reverse proxy that terminates TLS and authenticates every user itself, pass
the original `Host` header through, and add every name clients use to
`BOLTJAR_ALLOWED_HOSTS` (`python -m boltjar serve --host <address> --allow-remote`
adds the bind address). A proxy that rewrites the `Host` to `127.0.0.1` and adds
no `X-Forwarded-For` makes every client it serves look like a browser on this
machine, and each one gets the cookie. Never port-forward or tunnel the whole
server.

To receive webhooks, expose only `/hook/*`. Those routes skip the token and
host checks because outside services call them, so give every Webhook node a
secret; callers send it in the `X-Webhook-Secret` header. The secret and other
credential headers are never passed into the graph. A Webhook whose secret is a
`{{secret.NAME}}` that is not defined refuses every call.

## Secrets at rest

Keys you add in the editor are stored in `user/data/secrets.json`, and keys
you put in `.env` stay in that file. Both are plain text and both are
gitignored, so anyone who can read your user account's files can read your keys.

A graph saved with `{{secret.NAME}}` tokens is safe to share. A key typed
straight into a field is saved as plain text in the graph file, in its
snapshots under `user/autosave/` and in the browser's local draft. Live values
shown in the editor display a known secret (8 characters or longer) as its
`{{secret.NAME}}` token.

## Reporting a vulnerability

Please report privately through GitHub:
<https://github.com/DKLRD/Boltjar/security/advisories/new>. Do not open a
public issue for a vulnerability.
