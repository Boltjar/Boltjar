// ============================================================================
// EditorContext: shared, read-mostly state the custom node renderer needs but
// that React Flow does not thread through `data`: the node-definition catalog,
// the derived connection maps, live run state (power, per-node status, per-port
// values + history), validation problems, the disabled set, and the imperative
// callbacks (rename, edit config, chat). Kept stable so node re-renders stay cheap.
// ============================================================================
import { createContext, useContext } from "react";
import type { ModelManifest, NodeDef, Problem } from "../types/protocol";
import type { ChatMessage, HistPoint, LiveValue, NodeRunStatus, Power } from "../hooks/useRunSocket";
import type { ModelsMeta } from "./modelMeta";

/** A resolved inbound wire into a node: where it comes from + the carried type. */
export interface InboundWire {
  src: string;
  srcPort: string;
  srcType: string;
  dstPort: string;
  /** the RAW edge is part of the last-saved LIVE graph (not a draft-only edit).
   *  Live consumers (Preview / Chat / wire pulse) only paint/pulse through live
   *  wires while power is on, so a structural edit never appears live. */
  live: boolean;
}

export interface EditorContextValue {
  defs: Map<string, NodeDef>;
  /** model id -> manifest, so the LLM node + inspector resolve the selected model. */
  models: ReadonlyMap<string, ModelManifest>;
  /** the live list's state: what "auto" runs, when it was updated, who answered. */
  modelsMeta: ModelsMeta;
  /** read the model list again (the server answers from its cache). */
  reloadModels: () => Promise<void>;
  /** ask every provider for its models now, then show the fresh list. */
  refreshModels: () => Promise<void>;
  power: Power;
  nodeStatus: Record<string, NodeRunStatus>;
  liveValues: Record<string, LiveValue>;
  valueHistory: Record<string, HistPoint[]>;
  /** edge keys (`liveEdgeKey`) that belong to the running graph; empty when off.
   *  A wire pulse (TypedEdge) is gated on membership so a draft wire never pulses. */
  liveEdges: Set<string>;
  chats: Record<string, ChatMessage[]>;
  /** instance id -> set of connected input handle ids (literal dst_port names). */
  connectedInputs: Map<string, Set<string>>;
  /** instance id -> set of connected output handle ids. */
  connectedOutputs: Map<string, Set<string>>;
  /** instance id -> every wire into it (src, port, type, dstPort) for Preview taps. */
  inboundSources: Map<string, InboundWire[]>;
  /** node id -> its validation problems (broken-node badges). */
  problemsByNode: Map<string, Problem[]>;
  /** node ids the author has disabled (bypassed). */
  disabledIds: Set<string>;
  /** a request for a node to enter inline-rename (from the context menu). */
  renameTarget: { id: string; nonce: number } | null;
  /** a drag-from-port in progress: its source type drives target highlighting. */
  connecting: { fromType: string; fromId: string } | null;
  renameNode: (id: string, name: string) => void;
  updateConfig: (id: string, key: string, value: unknown) => void;
  /** Select an LLM model (resets params, prunes stale ports). */
  setLlmModel: (id: string, modelId: string) => void;
  /** Promote an LLM param to a typed input port. */
  promoteParam: (id: string, name: string) => void;
  /** Demote a promoted LLM param back to a widget. */
  unpromoteParam: (id: string, name: string) => void;
  /** Promote any node widget to a typed input port (universal knob promotion). */
  promoteWidget: (id: string, name: string) => void;
  /** Demote a promoted widget back to a knob (prunes any wire feeding it). */
  unpromoteWidget: (id: string, name: string) => void;
  sendChat: (node: string, text: string) => void;
  /** Fire a Manual trigger by hand (only meaningful while power is "on"). */
  fire: (node: string) => void;
  /** per-channel sockets broadcast by Wireless In nodes, for the Out to mirror. */
  wirelessChannels: import("./dynamicPorts").WirelessChannelMap;
  /** channel -> the Wireless In node that owns it (first wins). A channel may have
   *  only ONE In, so another In's channel dropdown hides the taken channels. */
  wirelessInOwners: ReadonlyMap<string, string>;
  /** the effective output of a source port: a Wireless Out resolves to the channel's
   *  real source; a bypass passthrough (Preview/Chat) keeps the node as `src` but its
   *  `type` follows the wire into `in`. null for a plain port. Used for edge colour,
   *  the live pulse, and downstream type detection. */
  effectiveOutput: (srcId: string, srcPort: string) => { type: string; src: string; srcPort: string } | null;
  /** Open the Connections window (AI Providers tab). Provided by App. */
  openConnections: () => void;
  /** Resolve the store key feeding a DB/KV transform node: walk the inbound
   *  wire on `port` ("db" | "kv") back to the source node and return its
   *  store key (the source's db_key/kv_key config, or its id as fallback).
   *  Returns null when nothing is wired into that port. Lets the DB/KV node
   *  bodies offer real table / key suggestions from the connected store. */
  storeKeyForInput: (nodeId: string, port: string) => string | null;
}

const EditorContext = createContext<EditorContextValue | null>(null);

export const EditorProvider = EditorContext.Provider;

export function useEditor(): EditorContextValue {
  const ctx = useContext(EditorContext);
  if (!ctx) throw new Error("useEditor must be used within an EditorProvider");
  return ctx;
}
