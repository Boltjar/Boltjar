// ============================================================================
// Framework-free checks of the editor's one /ws connection
// (src/lib/runConnection.ts), driven with a fake socket factory and fake
// timers. Drives the REAL module, transpiled with the installed TypeScript
// compiler (it has no imports). Run from editor/: `node test/run-connection.test.mjs`.
//
// The case it guards: switching tabs closed the old tab's socket, and that
// socket's late close cleared the NEW socket and reconnected, so two sockets
// were open for the new tab (and the one it cleared was never closed). Each
// socket got the server's replay of the latest values, so a Chat viewer showed
// the last message and its reply twice after a tab switch and back.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(resolve(here, "../src/lib/runConnection.ts"), "utf8");
const js = ts.transpileModule(src, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const { RunConnection, RECONNECT_FIRST } = await import("data:text/javascript," + encodeURIComponent(js));

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

// ---- fakes: a socket that stays CONNECTING until the test opens it, and a
// close() that, like the browser, reports onclose later (on `flush`).
function harness() {
  const sockets = [];
  const timers = new Map();
  let nextTimer = 1;
  const later = [];
  const messages = [];
  const log = [];
  const deps = {
    open(url) {
      const s = {
        url, readyState: 0, sent: [], onopen: null, onclose: null, onerror: null, onmessage: null,
        send(d) { this.sent.push(d); },
        close() {
          if (this.readyState === 3) return;
          this.readyState = 2;
          later.push(() => { this.readyState = 3; this.onclose?.({}); });
        },
      };
      sockets.push(s);
      return s;
    },
    setTimer(fn, ms) { const id = nextTimer++; timers.set(id, { fn, ms }); return id; },
    clearTimer(id) { timers.delete(id); },
  };
  const conn = new RunConnection(deps, {
    onOpen: () => log.push("open"),
    onClose: () => log.push("close"),
    onError: () => log.push("error"),
    onMessage: (d) => messages.push(d),
  });
  const openSock = (s) => { s.readyState = 1; s.onopen?.({}); };
  const flush = () => { while (later.length) later.shift()(); };
  const runTimers = () => { const all = [...timers.values()]; timers.clear(); all.forEach((t) => t.fn()); };
  const live = () => sockets.filter((s) => s.readyState === 0 || s.readyState === 1).map((s) => s.url);
  return { conn, sockets, timers, messages, log, openSock, flush, runTimers, live };
}

// ---- a tab switch leaves exactly one socket, for the new tab
{
  const h = harness();
  h.conn.connect("ws/a");
  h.openSock(h.sockets[0]);
  h.conn.connect("ws/b"); // switch to tab B while A is open
  h.openSock(h.sockets[1]);
  h.flush(); // A's close arrives after B opened
  h.runTimers();
  check("after a tab switch one socket is open, for the new tab", h.live(), ["ws/b"]);
  check("the old socket's late close schedules no reconnect", h.timers.size, 0);
  h.conn.connect("ws/a"); // and back to A
  h.flush();
  h.runTimers();
  h.openSock(h.sockets[h.sockets.length - 1]);
  check("switching back leaves one socket, for that tab", h.live(), ["ws/a"]);
  check("three connects opened three sockets, never a fourth", h.sockets.length, 3);
}

// ---- a switch while the old socket is still connecting
{
  const h = harness();
  h.conn.connect("ws/a"); // never opened
  h.conn.connect("ws/b");
  h.flush();
  h.runTimers();
  check("a socket closed while connecting opens nothing in its place", h.live(), ["ws/b"]);
}

// ---- a replaced socket never delivers another frame
{
  const h = harness();
  h.conn.connect("ws/a");
  h.openSock(h.sockets[0]);
  const a = h.sockets[0];
  h.conn.connect("ws/b");
  a.onmessage?.({ data: "late frame for a" });
  h.sockets[1].onmessage?.({ data: "frame for b" });
  check("only the current socket's frames reach the editor", h.messages, ["frame for b"]);
}

// ---- the server dropping the current socket reconnects, with the backoff
{
  const h = harness();
  h.conn.connect("ws/a");
  h.openSock(h.sockets[0]);
  h.sockets[0].close(); // the server went away
  h.flush();
  check("a dropped socket reports the close", h.log, ["open", "close"]);
  check("and waits the first backoff before reconnecting", [...h.timers.values()].map((t) => t.ms), [RECONNECT_FIRST]);
  h.runTimers();
  check("then reconnects to the same tab", h.live(), ["ws/a"]);
}

// ---- close() stops for good; a queued payload goes out on open
{
  const h = harness();
  h.conn.connect("ws/a");
  h.conn.sendOrQueue({ action: "on" });
  h.openSock(h.sockets[0]);
  check("a payload queued while connecting is sent on open", h.sockets[0].sent, [JSON.stringify({ action: "on" })]);
  h.conn.close();
  h.flush();
  h.runTimers();
  check("close() leaves no socket and no reconnect", [h.live(), h.timers.size], [[], 0]);
}

if (failures) {
  console.error(`\n${failures} failing check(s)`);
  process.exit(1);
}
console.log("\nall run connection checks passed");
