// ============================================================================
// useRunSocket: owns the /ws connection to the runtime and the power model.
// Sends the power commands (on / off / restart) and chat injections, and folds
// the streamed events into editor-facing live state:
//   - `power`        : "on" | "off"  (the live status capsule + breathing dot)
//   - `connected`    : the ws transport is open
//   - `log`          : a bounded console feed (value / log / error lines)
//   - `nodeStatus`   : per-node lifecycle (running | ok | error | idle)
//   - `liveValues`   : latest value per `node:port` (lights ports)
//   - a `carry` lights the wires it names (lib/wirePulse), with no React state
//   - `valueHistory` : recent values per `node:port` (Preview sparklines/terminal)
//   - `chats`        : per-Chat-Input local message history
//   - `problems`     : the last `invalid` payload (broken-node badges)
//   - `counters`     : events/s + in-flight, for the status bar
// The socket connects ON MOUNT and reconnects with a capped backoff, so the
// editor immediately reflects the backend's live status after a browser refresh
// (power persistence: the runtime lives in the server, not in this connection).
// The connection itself (one current socket, the backoff, the queued payload)
// is lib/runConnection.
// ============================================================================
import { useCallback, useEffect, useRef, useState } from "react";
import type { Graph, Problem, RunEvent } from "../types/protocol";
import { liveEdgeKey, type Power } from "../lib/liveClassify";
import { consoleText } from "../lib/mediaSummary";
import { resumeNoticeAfter } from "../lib/resumeNotice";
import { pulseWires } from "../lib/wirePulse";
import { RunConnection } from "../lib/runConnection";
import { isReplayed } from "../lib/replayedValues";

export type NodeRunStatus = "idle" | "running" | "ok" | "warn" | "error";
export type { Power };

export interface ConsoleLine {
  id: number;
  ts: string;
  level: "info" | "ok" | "warn" | "bad";
  node?: string;
  /** one console-safe line (lib/mediaSummary): media as "mime · size", long
   *  text cut with its total length, never a base64 payload. */
  message: string;
  tag?: string;
}

export interface LiveValue {
  value: unknown;
  at: number;
}

/** A point in a port's recent value history (for Preview sparklines / terminals). */
export interface HistPoint {
  value: unknown;
  at: number;
}

/** One message in a Chat Input node's local history. */
export interface ChatMessage {
  id: number;
  role: "you" | "assistant";
  text: string;
  at: number;
}

export interface RunSocketState {
  connected: boolean;
  power: Power;
  log: ConsoleLine[];
  nodeStatus: Record<string, NodeRunStatus>;
  liveValues: Record<string, LiveValue>;
  valueHistory: Record<string, HistPoint[]>;
  /** node ids that are part of the running graph (empty when power is off). */
  liveNodes: Set<string>;
  /** edge keys (`liveEdgeKey`) that are part of the running graph (empty off). */
  liveEdges: Set<string>;
  chats: Record<string, ChatMessage[]>;
  problems: Problem[];
  /** the launch could not turn this graph back On and retries it at each one
   *  (lib/resumeNotice); the editor offers Stop resuming while it shows. */
  resumeNotice: boolean;
  counters: { eventsPerSec: number; inFlight: number };
  on: (graph: Graph) => void;
  off: () => void;
  restart: (graph: Graph) => void;
  chat: (node: string, text: string) => void;
  audio: (node: string, dataUrl: string, lang?: string) => void;
  fire: (node: string) => void;
  /** Post an editor-side line to the console (it comes from the editor, not the
   *  runtime): e.g. a dead wire the editor removed while loading a graph. */
  notice: (message: string, level?: ConsoleLine["level"]) => void;
  clearLog: () => void;
  clearProblems: () => void;
}

const MAX_LOG = 300;
const HIST_KEEP = 48; // points retained per port for sparklines
const MAX_CHAT = 80;
const VALUE_MAX = 60; // chars of a value shown after `port =`
const MESSAGE_MAX = 500; // chars of a log / error line before it is cut

function nowStamp(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export function useRunSocket(slug: string = "_default"): RunSocketState {
  const connRef = useRef<RunConnection | null>(null);
  const lineId = useRef(0);
  const chatId = useRef(0);
  const eventTimes = useRef<number[]>([]);
  // the last value event id seen per `node:port`, so a replay after a
  // reconnect is recognised (lib/replayedValues); forgotten with the history.
  const lastValueIds = useRef<Map<string, string>>(new Map());
  // map a Chat Input node -> the output node whose value is its reply (heuristic:
  // we surface every "deliver"/reply value globally, but tie replies to chats by
  // recency since the runtime has one chat turn at a time).
  const lastChatNode = useRef<string | null>(null);

  const [connected, setConnected] = useState(false);
  const [power, setPower] = useState<Power>("off");
  const [log, setLog] = useState<ConsoleLine[]>([]);
  const [nodeStatus, setNodeStatus] = useState<Record<string, NodeRunStatus>>({});
  const [liveValues, setLiveValues] = useState<Record<string, LiveValue>>({});
  const [valueHistory, setValueHistory] = useState<Record<string, HistPoint[]>>({});
  const [liveNodes, setLiveNodes] = useState<Set<string>>(new Set());
  const [liveEdges, setLiveEdges] = useState<Set<string>>(new Set());
  const [chats, setChats] = useState<Record<string, ChatMessage[]>>({});
  const [problems, setProblems] = useState<Problem[]>([]);
  const [resumeNotice, setResumeNotice] = useState(false);
  const [counters, setCounters] = useState({ eventsPerSec: 0, inFlight: 0 });

  // every console line passes through here, so none can carry a raw payload
  // (a TTS reply is megabytes of base64) into the panel or the bounded log.
  const pushLine = useCallback((line: Omit<ConsoleLine, "id">) => {
    const message = consoleText(line.message, MESSAGE_MAX);
    setLog((prev) => {
      const next = [...prev, { ...line, message, id: lineId.current++ }];
      return next.length > MAX_LOG ? next.slice(next.length - MAX_LOG) : next;
    });
  }, []);

  const handleEvent = useCallback(
    (evt: RunEvent) => {
      const t = Date.now();
      setResumeNotice((showing) => resumeNoticeAfter(showing, evt));
      switch (evt.kind) {
        case "value": {
          const key = `${evt.node}:${evt.port}`;
          // a replay of a value this editor already holds: nothing happened.
          if (isReplayed(lastValueIds.current, key, evt.id)) break;
          eventTimes.current.push(t);
          setLiveValues((p) => ({ ...p, [key]: { value: evt.value, at: t } }));
          setValueHistory((p) => {
            const prev = p[key] ?? [];
            const next = [...prev, { value: evt.value, at: t }];
            return { ...p, [key]: next.length > HIST_KEEP ? next.slice(next.length - HIST_KEEP) : next };
          });
          // status is driven by node_status (fire start/end), NOT by value events:
          // a value emit is instantaneous, so it must not mark a node "running"
          // (that is what left the Chat Input stuck animating forever).
          pushLine({
            ts: nowStamp(),
            level: "info",
            node: evt.node,
            message: `${evt.port} = ${consoleText(evt.value, VALUE_MAX)}`,
            tag: evt.port,
          });
          break;
        }
        case "carry":
          // only the wires that carried the value light, each on its own
          pulseWires(evt.wires);
          break;
        case "node_status":
          setNodeStatus((p) => ({ ...p, [evt.node]: evt.status }));
          break;
        case "log": {
          pushLine({ ts: nowStamp(), level: "ok", node: evt.node, message: evt.message });
          setNodeStatus((p) => ({ ...p, [evt.node]: p[evt.node] === "error" ? "error" : "ok" }));
          // a Deliver to the chat channel feeds the most recent chat as the assistant's turn.
          const m = /^->\s*chat:\s*(.*)$/i.exec(evt.message);
          if (m && lastChatNode.current) {
            const node = lastChatNode.current;
            setChats((prev) => {
              const list = prev[node] ?? [];
              const msg: ChatMessage = { id: chatId.current++, role: "assistant", text: m[1], at: t };
              const next = [...list, msg];
              return { ...prev, [node]: next.length > MAX_CHAT ? next.slice(next.length - MAX_CHAT) : next };
            });
          }
          break;
        }
        case "warning":
          pushLine({ ts: nowStamp(), level: "warn", node: evt.node, message: evt.message });
          break;
        case "node_error":
          pushLine({ ts: nowStamp(), level: "bad", node: evt.node, message: evt.error });
          setNodeStatus((p) => ({ ...p, [evt.node]: "error" }));
          break;
        // a tool hop the model made: show the call args then the result it got
        // back, attributed to the Tool node, so the agentic loop is legible.
        case "tool_call":
          pushLine({ ts: nowStamp(), level: "info", node: evt.node, message: `tool call ${consoleText(evt.args, VALUE_MAX)}` });
          break;
        case "tool_result":
          pushLine({ ts: nowStamp(), level: "ok", node: evt.node, message: `tool result -> ${consoleText(evt.result, VALUE_MAX)}` });
          break;
        case "status":
          setPower(evt.power);
          pushLine({
            ts: nowStamp(),
            level: evt.power === "on" ? "ok" : "info",
            message: evt.power === "on" ? "power on: graph is live" : "power off",
          });
          if (evt.power === "off") {
            setNodeStatus((p) => {
              const next = { ...p };
              for (const k of Object.keys(next)) if (next[k] === "running") next[k] = "ok";
              return next;
            });
          } else {
            setProblems([]);
          }
          break;
        case "invalid": {
          setProblems(evt.problems);
          setPower("off");
          const count = `${evt.problems.length} problem${evt.problems.length === 1 ? "" : "s"}`;
          pushLine({
            ts: nowStamp(),
            level: "bad",
            message: evt.resume ? `not resumed after the launch: ${count}` : `cannot start: ${count}`,
          });
          break;
        }
        case "error":
          pushLine({ ts: nowStamp(), level: "bad", message: evt.resume ? `not resumed after the launch: ${evt.error}` : evt.error });
          setPower("off");
          break;
        case "live_graph":
          // the authoritative live set: which nodes/wires the running graph is
          // built from. Draft-only structural edits (not yet Saved & Restarted)
          // are absent, so live values never paint/pulse through them.
          setLiveNodes(new Set(evt.nodes));
          setLiveEdges(new Set(evt.edges.map((e) => liveEdgeKey(e[0], e[1], e[2], e[3]))));
          break;
      }
    },
    [pushLine],
  );

  // the one connection, made once; its handlers read the latest callbacks
  // through a ref, so the connection never has to be rebuilt when they change.
  const handlersRef = useRef({ handleEvent, pushLine });
  handlersRef.current = { handleEvent, pushLine };
  if (connRef.current === null) {
    connRef.current = new RunConnection(
      {
        open: (url) => new WebSocket(url),
        setTimer: (fn, ms) => window.setTimeout(fn, ms),
        clearTimer: (id) => window.clearTimeout(id),
      },
      {
        onOpen: () => setConnected(true),
        // Do NOT force power off: the runtime lives in the server and may still be
        // live. Keep the last known power; the `status` replayed on reconnect sets it.
        onClose: () => setConnected(false),
        onError: () =>
          handlersRef.current.pushLine({ ts: nowStamp(), level: "bad", message: "socket error: is the backend running on :8770?" }),
        onMessage: (data) => {
          try {
            handlersRef.current.handleEvent(JSON.parse(String(data)) as RunEvent);
          } catch {
            /* ignore malformed frame */
          }
        },
      },
    );
  }
  const conn = connRef.current;

  const send = useCallback((payload: object) => conn.send(payload), [conn]);

  const on = useCallback(
    (graph: Graph) => {
      setNodeStatus({});
      setLiveValues({});
      setValueHistory({});
      lastValueIds.current.clear();
      // clear the live set until the server echoes the new one; nothing counts as
      // live in the gap, and values were just cleared too, so nothing shows.
      setLiveNodes(new Set());
      setLiveEdges(new Set());
      setProblems([]);
      conn.sendOrQueue({ action: "on", graph });
    },
    [conn],
  );

  const off = useCallback(() => {
    conn.clearPending();
    send({ action: "off" });
    setPower("off");
    setLiveNodes(new Set());
    setLiveEdges(new Set());
  }, [conn, send]);

  const restart = useCallback(
    (graph: Graph) => {
      setNodeStatus({});
      setLiveValues({});
      setValueHistory({});
      lastValueIds.current.clear();
      setLiveNodes(new Set());
      setLiveEdges(new Set());
      setProblems([]);
      conn.sendOrQueue({ action: "restart", graph });
    },
    [conn],
  );

  const chat = useCallback(
    (node: string, text: string) => {
      lastChatNode.current = node;
      setChats((prev) => {
        const list = prev[node] ?? [];
        const msg: ChatMessage = { id: chatId.current++, role: "you", text, at: Date.now() };
        const next = [...list, msg];
        return { ...prev, [node]: next.length > MAX_CHAT ? next.slice(next.length - MAX_CHAT) : next };
      });
      send({ action: "chat", node, text });
    },
    [send],
  );

  const fire = useCallback(
    (node: string) => { send({ action: "fire", node }); },
    [send],
  );

  // stream a mic clip into an Audio Input node (data: URL), the audio mirror of chat.
  const audio = useCallback(
    (node: string, dataUrl: string, lang = "") => { send({ action: "audio", node, audio: dataUrl, lang }); },
    [send],
  );

  const notice = useCallback(
    (message: string, level: ConsoleLine["level"] = "info") => pushLine({ ts: nowStamp(), level, message }),
    [pushLine],
  );

  const clearLog = useCallback(() => setLog([]), []);
  const clearProblems = useCallback(() => setProblems([]), []);

  // Throttle: derive events/sec on a light timer.
  useEffect(() => {
    const id = window.setInterval(() => {
      const t = Date.now();
      eventTimes.current = eventTimes.current.filter((x) => t - x < 1000);
      const eps = eventTimes.current.length;
      setCounters((prev) => {
        const inFlight = power === "on" ? Math.min(eps, 9) : 0;
        if (prev.eventsPerSec === eps && prev.inFlight === inFlight) return prev;
        return { eventsPerSec: eps, inFlight };
      });
    }, 250);
    return () => window.clearInterval(id);
  }, [power]);

  // Connect on mount; reconnect whenever the active slug changes (so the editor
  // shows the live status / values of the ACTIVE tab's graph). Each Hub on the
  // backend replays its status + recent values to a fresh subscriber, so a
  // browser refresh OR a tab switch immediately reflects truth.
  useEffect(() => {
    // reset live-state that belonged to the previous slug (the new Hub will
    // replay its own status + values immediately on subscribe).
    setNodeStatus({});
    setLiveValues({});
    setValueHistory({});
    lastValueIds.current.clear();
    setLiveNodes(new Set());
    setLiveEdges(new Set());
    setProblems([]);
    setResumeNotice(false);
    setPower("off");
    // one socket, for the new slug: connect() drops the prior one for good.
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    conn.connect(`${proto}://${window.location.host}/ws?slug=${encodeURIComponent(slug)}`);
    return () => conn.close();
  }, [slug, conn]);

  return {
    connected,
    power,
    log,
    nodeStatus,
    liveValues,
    valueHistory,
    liveNodes,
    liveEdges,
    chats,
    problems,
    resumeNotice,
    counters,
    on,
    off,
    restart,
    chat,
    audio,
    fire,
    notice,
    clearLog,
    clearProblems,
  };
}
