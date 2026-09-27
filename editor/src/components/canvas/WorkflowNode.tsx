// ============================================================================
// WorkflowNode: the custom React Flow node and the heart of the product.
// A machined card:
//   • lean typed ports: inputs left, outputs right, coloured by type; growable
//     bases expand into NAMED sockets (Template {tags}, Compute value0…) plus a
//     ghost "add" socket; `event` (control) pipes render distinctly from data;
//   • a kind-coloured header with an editable, live-bound title (double-click);
//   • a validation badge when the node is broken, a dim wash when disabled;
//   • node-specific inline surfaces: a Chat box (Chat Input), a live Preview
//     (Preview), a {tag}-autocompleting Template editor, and compact knobs;
//   • a status footer reflecting live run telemetry.
// ============================================================================
import { memo, useEffect, useRef, useState, type CSSProperties } from "react";
import { Handle, NodeResizer, Position, useReactFlow, useUpdateNodeInternals, type NodeProps } from "@xyflow/react";
import type { Widget } from "../../types/protocol";
import { RESIZABLE_TYPES, type WFNodeData } from "../../lib/graphAdapter";
import { useEditor, type InboundWire } from "../../lib/editorContext";
import { liveGatePasses } from "../../lib/liveClassify";
import { functionColorVar, nodeIcon } from "../../lib/kinds";
import { typeColorVar, typesCompatible } from "../../lib/types";
import { Icon, hasIcon } from "../../lib/icons";
import { bodySummary, defaultOf, headerSubline } from "../../lib/nodeMeta";
import { useDraft } from "../../lib/useDraft";
import {
  concreteInputs,
  concreteOutputs,
  opVisible,
  llmPromoted,
  modelWidgetOf,
  nodePromoted,
  onBody,
  reservedInputNames,
  templateTags,
  DB_ID,
  KV_ID,
  LLM_ID,
  TEMPLATE_ID,
  WIRELESS_IN_ID,
  ROUTER_ID,
  type ConcretePort,
} from "../../lib/dynamicPorts";
import type { NodeRunStatus } from "../../hooks/useRunSocket";
import { TemplateField } from "../TemplateField";
import { SecretAutocompleteField } from "../SecretAutocompleteField";
import { PromotableField } from "../PromotableField";
import { createPortal } from "react-dom";
import { ContextMenu } from "../ContextMenu";
import { Knob } from "./Knob";
import { ChatBox } from "./ChatBox";
import { ChatConversation } from "./ChatConversation";
import { ModelPicker } from "./ModelPicker";
import { ParamKnobs } from "./ParamKnobs";
import { PreviewBody, previewTypeFor } from "./Preview";
import { StoreBody } from "./StoreBody";
import { schemaWidget } from "../../lib/storeSchema";
import { StoreHints } from "./StoreHints";
import { ToolHints } from "./ToolHints";
import { StoreSelect } from "./StoreSelect";

function footerFor(status: NodeRunStatus | undefined): { cls: string; icon: string; word: string } {
  switch (status) {
    case "running": return { cls: "run", icon: "sync-outline", word: "live" };
    case "ok": return { cls: "ok", icon: "checkmark-circle", word: "ok" };
    case "warn": return { cls: "warn", icon: "alert-circle-outline", word: "throttled" };
    case "error": return { cls: "error", icon: "close-circle", word: "error" };
    default: return { cls: "idle", icon: "ellipse-outline", word: "ready" };
  }
}

function shortVal(value: unknown, max = 14): string {
  let s = typeof value === "string" ? value : JSON.stringify(value);
  if (s === undefined) s = String(value);
  s = s.replace(/\s+/g, " ").trim();
  return s.length > max ? s.slice(0, max - 1) + "…" : s;
}

function WorkflowNodeImpl({ id, data, selected }: NodeProps) {
  const {
    defs,
    models,
    power,
    nodeStatus,
    liveValues,
    valueHistory,
    chats,
    connectedInputs,
    connectedOutputs,
    inboundSources,
    problemsByNode,
    disabledIds,
    renameTarget,
    renameNode,
    updateConfig,
    setLlmModel,
    promoteParam,
    unpromoteParam,
    promoteWidget,
    sendChat,
    fire,
    wirelessChannels,
    effectiveOutput,
  } = useEditor();
  const nd = data as WFNodeData;
  const def = defs.get(nd.typeId);

  const [editing, setEditing] = useState(false);
  const [draftName, setDraftName] = useState(id);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) {
      setDraftName(id);
      const el = inputRef.current;
      if (el) {
        el.focus();
        el.select();
      }
    }
  }, [editing, id]);

  // a context-menu "Rename" on this node opens inline edit mode.
  useEffect(() => {
    if (renameTarget && renameTarget.id === id) setEditing(true);
  }, [renameTarget, id]);

  // growable/Template ports change the handle set; React Flow must remeasure so
  // edges reattach at the right y. The signature is the concrete input handle ids.
  const updateInternals = useUpdateNodeInternals();
  const inSetEarly = connectedInputs.get(id) ?? new Set<string>();
  // include the OUTPUT names too so the LLM remeasures when a model reshapes them.
  const portSig = def
    ? concreteInputs(def, nd.config, inSetEarly, models).map((p) => p.name).join("|") +
      "‖" + concreteOutputs(def, nd.config, models, wirelessChannels).map((p) => p.name).join("|")
    : "";
  useEffect(() => {
    if (portSig) updateNodeInternalsSafe(updateInternals, id);
  }, [portSig, id, updateInternals]);

  if (!def) {
    return (
      <div className="node error" style={{ ["--kc" as string]: "var(--bad)" } as CSSProperties}>
        <div className="node-head">
          <div className="nh-text">
            <div className="nh-title">{id}</div>
            <div className="nh-sub">unknown type · {nd.typeId}</div>
          </div>
        </div>
      </div>
    );
  }

  const status = nodeStatus[id];
  const running = status === "running";
  const foot = footerFor(status);
  const subline = headerSubline(def, nd.config);
  const inSet = inSetEarly;
  const outSet = connectedOutputs.get(id) ?? new Set<string>();
  const problems = problemsByNode.get(id);
  const broken = !!problems && problems.length > 0;
  const disabled = disabledIds.has(id);

  // Standard: the port named exactly `trigger` always renders at the TOP. Per-side
  // triggers (Chat's user_trigger / reply_trigger) are not named `trigger`, so they
  // keep their declared interleaved order. A name rule, no per-id table.
  const rawInputs: ConcretePort[] = concreteInputs(def, nd.config, inSet, models);
  let inputs: ConcretePort[] = [
    ...rawInputs.filter((p) => p.name === "trigger"),
    ...rawInputs.filter((p) => p.name !== "trigger"),
  ];
  // Wireless In sockets follow CONNECTION order (oldest on top), matching the Out
  // which mirrors them in edge order, instead of the generic alphabetical sort.
  if (def.id === WIRELESS_IN_ID) {
    const order = (inboundSources.get(id) ?? []).map((w) => w.dstPort);
    const rank = (n: string) => { const i = order.indexOf(n); return i < 0 ? order.length : i; };
    inputs = [
      ...inputs.filter((p) => !p.ghost).sort((a, b) => rank(a.name) - rank(b.name)),
      ...inputs.filter((p) => p.ghost),
    ];
  }
  // a passthrough's `out` (Preview/Chat) shows the type flowing through its `in`,
  // so its port + the cable adapt to text / audio / image. Declared via bypass.
  const rawOutputs = concreteOutputs(def, nd.config, models, wirelessChannels).map((o) => {
    const eff = effectiveOutput(id, o.name);
    return eff ? { ...o, type: eff.type } : o;
  });
  // same standard on the output side: the `trigger` output renders at the TOP.
  const outputs = [
    ...rawOutputs.filter((o) => o.name === "trigger"),
    ...rawOutputs.filter((o) => o.name !== "trigger"),
  ];

  const commit = () => {
    setEditing(false);
    if (draftName.trim() && draftName.trim() !== id) renameNode(id, draftName.trim());
  };

  // node-specific surfaces
  const isChat = def.id === "core.trigger.chat";
  const isManual = def.id === "core.trigger.manual";
  const isPreview = def.id === "core.output.preview";
  const isTemplate = def.id === TEMPLATE_ID;
  // an audio Preview shows a tall, fixed-height player; treat it as auto-sizing (not
  // a clipping resizable box) so the Autoplay knob at the bottom is never cut off.
  const inWire = isPreview
    ? inboundSources.get(id)?.find((w) => w.dstPort === "in") ?? inboundSources.get(id)?.[0]
    : undefined;
  const isAudioPreview = !!inWire && (inWire.srcType === "audio" || inWire.srcType === "pcm-audio");
  const isLLM = def.id === LLM_ID;
  // any node that declares a model widget (LLM, TTS, STT, Embed, Rerank, a pack's
  // own) gets the same picker + per-model param knobs; the list is filtered to
  // the family the widget declares (model_kind).
  const modelWidget = modelWidgetOf(def);
  const isModelNode = modelWidget !== null;
  const isChatOut = def.id === "core.output.chat";
  const isTool = def.id === "core.ai.tool";
  const isDbStore = def.id === "core.store.database";
  const isKvStore = def.id === "core.store.kv";
  const isStore = isDbStore || isKvStore;
  const storeKey = isStore
    ? String(nd.config[isDbStore ? "db_key" : "kv_key"] ?? id)
    : "";
  // the hidden widget this store keeps its declared tables in (by kind, not id).
  const declaredWidget = schemaWidget(def);

  const bodyRows = bodySummary(def, nd.config);
  const special = isChat || isPreview || isTemplate || isModelNode || isChatOut || isStore;
  const wide = isLLM || def.inputs.length + def.outputs.length >= 7;

  // the widgets actually rendered on the body (not modal, visible under the current
  // operation, not promoted to a port), so we can size around them. Special nodes
  // render their own surface, so they have no inline knob rows here.
  const promotedNames = new Set(nodePromoted(nd.config));
  const bodyWidgets = special ? [] : def.widgets.filter(
    (w) => onBody(w) && opVisible(w, nd.config) && !promotedNames.has(w.name),
  );
  const expandWidgets = bodyWidgets.filter((w) => w.expand);
  const fixedRows = bodyWidgets.length - expandWidgets.length;
  // resizable = a special single-surface body (Preview / Chat / Template) OR any
  // node with >=1 expandable field (Text / Compute / DB / KV). Declared, not a list.
  const resizable = (RESIZABLE_TYPES.has(def.id) || expandWidgets.length > 0) && !isAudioPreview;

  // minimum resize floor: never let a node shrink below the room its header, port
  // rows, footer and body actually need, so dragging can't clip the contents.
  const portRows = Math.max(inputs.length, outputs.length);
  const minNodeW = 290;
  // for an expandable inline-knob node the floor follows its real content (header +
  // ports + footer + each fixed knob row + a usable minimum per expandable field);
  // special single-surface nodes use a flat body floor.
  const minNodeH = expandWidgets.length > 0 && !special
    ? 64 + portRows * 26 + 32 + fixedRows * 46 + expandWidgets.length * 86
    : (special || resizable ? 168 : 80) + portRows * 22;

  // Enforce the min at RENDER, not just during a drag: a size SAVED below the
  // per-node floor (an old graph, or wiring more ports grew the floor) would
  // otherwise render clipped: the NodeResizer min only constrains dragging. If
  // the stored size is under the floor, clamp it up. Self-stable: once at/above
  // the floor it no-ops.
  const rf = useReactFlow();
  useEffect(() => {
    if (!resizable) return;
    const n = rf.getNode(id);
    if (!n) return;
    const w = typeof n.width === "number" ? n.width : minNodeW;
    const h = typeof n.height === "number" ? n.height : minNodeH;
    if (w < minNodeW || h < minNodeH) {
      rf.setNodes((nds) => nds.map((x) =>
        x.id === id ? { ...x, width: Math.max(w, minNodeW), height: Math.max(h, minNodeH) } : x));
    }
  }, [id, resizable, minNodeW, minNodeH, rf]);

  // the Router is a tiny pill (one in, one out, optional centred label), coloured
  // by the wire feeding it. Its own minimal render, not the standard node chrome.
  if (def.id === ROUTER_ID) {
    const inWire = inboundSources.get(id)?.find((w) => w.dstPort === "in");
    return (
      <RouterPill
        id={id}
        label={String(nd.config.label ?? "")}
        color={typeColorVar(inWire?.srcType)}
        inConnected={!!inWire}
        outConnected={outSet.has("out")}
        selected={selected}
        disabled={disabled}
        onLabel={(v) => updateConfig(id, "label", v)}
      />
    );
  }

  return (
    <div
      className={[
        "node",
        special ? "special" : wide ? "wide" : "",
        selected ? "selected" : "",
        running ? "running" : "",
        broken ? "broken" : "",
        disabled ? "disabled" : "",
        status === "error" ? "error" : "",
        resizable ? "resizable" : "",
      ].join(" ").trim()}
      style={{ ["--kc" as string]: functionColorVar(def) } as CSSProperties}
      data-typeid={def.id}
    >
      {resizable && (
        <NodeResizer isVisible={selected} minWidth={minNodeW} minHeight={minNodeH} maxWidth={640} maxHeight={640} />
      )}
      {/* ── header ── */}
      <div className="node-head">
        <div className="nh-ico">
          <Icon name={nodeIcon(def, hasIcon)} />
        </div>
        <div className="nh-text">
          {editing ? (
            <input
              ref={inputRef}
              className="nh-title-input nodrag"
              value={draftName}
              onChange={(e) => setDraftName(e.target.value)}
              onBlur={commit}
              onKeyDown={(e) => {
                if (e.key === "Enter") commit();
                if (e.key === "Escape") setEditing(false);
                e.stopPropagation();
              }}
            />
          ) : (
            <div
              className="nh-title"
              title="double-click to rename"
              onDoubleClick={(e) => {
                e.stopPropagation();
                setEditing(true);
              }}
            >
              {id}
            </div>
          )}
          <div className="nh-sub">{subline}</div>
        </div>
        {broken ? (
          <span className="nh-badge" title={problems!.map((p) => p.message).join("\n")}>
            <Icon name="warning-outline" />
          </span>
        ) : disabled ? (
          <span className="nh-badge off" title="disabled (bypassed)">
            <Icon name="ban-outline" />
          </span>
        ) : null}
        {running && <div className="run-sweep" />}
      </div>

      {/* ── ports ── */}
      <div className="ports">
        <div className="pcol in">
          {inputs.map((p) => (
            <InputPort key={p.name} nodeId={id} nodeTypeId={def.id} port={p} />
          ))}
        </div>
        <div className="pcol out">
          {outputs.map((p) => {
            const key = `${id}:${p.name}`;
            const live = liveValues[key];
            const connected = outSet.has(p.name);
            const isEvent = p.type === "event";
            return (
              <div
                key={p.name}
                className={`port out ${connected ? "connected" : ""} ${isEvent ? "evt" : ""}`}
                style={{ ["--pc" as string]: typeColorVar(p.type) } as CSSProperties}
              >
                <Handle
                  id={p.name}
                  type="source"
                  position={Position.Right}
                  className={`wf-dot right ${connected ? "connected" : ""} ${live ? "live" : ""}`}
                  style={{ ["--pc" as string]: typeColorVar(p.type) } as CSSProperties}
                  isConnectable
                />
                <span className="plabel" title={p.type}>{p.name}</span>
              </div>
            );
          })}
        </div>
      </div>

      {/* ── node-specific body ── */}
      {isManual && (
        <div className="node-body">
          <button
            type="button"
            className="manual-fire nodrag"
            disabled={power !== "on"}
            title={power === "on" ? "Fire one trigger event" : "Turn the graph On to fire"}
            onClick={() => fire(id)}
          >
            <Icon name="flash" /> Fire
          </button>
        </div>
      )}

      {isChat && (
        <div className="node-special">
          <ChatBox
            messages={chats[id] ?? []}
            placeholder={String(nd.config.placeholder ?? "Message…")}
            enabled={power === "on"}
            onSend={(text) => sendChat(id, text)}
            sendOnly
          />
        </div>
      )}

      {isPreview && (
        <div className="node-special">
          <PreviewSurface
            nodeId={id}
            inboundSources={inboundSources}
            valueHistory={valueHistory}
            power={power}
            autoplay={Boolean(nd.config.autoplay)}
            onAutoplay={(v) => updateConfig(id, "autoplay", v)}
          />
        </div>
      )}

      {isChatOut && (
        <div className="node-special">
          <ChatConversation nodeId={id} inboundSources={inboundSources} valueHistory={valueHistory} power={power} />
        </div>
      )}

      {isStore && (
        <div className="node-special">
          <StoreBody
            kind={isDbStore ? "db" : "kv"}
            storeKey={storeKey}
            nodeId={id}
            disabled={disabled}
            declared={declaredWidget ? nd.config[declaredWidget.name] : undefined}
            onDeclare={declaredWidget ? (t) => updateConfig(id, declaredWidget.name, t) : undefined}
          />
        </div>
      )}

      {modelWidget && (
        <div className="node-special">
          <LLMBody
            config={nd.config}
            widget={modelWidget}
            onSelectModel={(mid) => setLlmModel(id, mid)}
            onParamChange={(name, value) =>
              updateConfig(id, "params", { ...llmParams(nd.config), [name]: value })
            }
            onPromote={(name) => promoteParam(id, name)}
          />
        </div>
      )}

      {isTemplate && (
        <div className="node-special">
          <TemplateField
            variant="inline"
            fill={resizable}
            value={String(nd.config.template ?? "")}
            suggestions={templateSuggestions(id, inboundSources, nd.config, def)}
            onChange={(v) => updateConfig(id, "template", v)}
            placeholder="{tag} prose…"
          />
        </div>
      )}

      {/* ── compact inline knobs (non-special nodes) ── */}
      {!special && (
        <InlineKnobs
          def={def}
          nodeId={id}
          config={nd.config}
          bodyRows={bodyRows}
          inboundSources={inboundSources}
          onChange={(k, v) => updateConfig(id, k, v)}
          onPromote={(name) => promoteWidget(id, name)}
        />
      )}

      {isTool && (
        <ToolHints callWired={outSet.has("call")} resultWired={inSet.has("result")} />
      )}

    </div>
  );
}

/** One input socket row. Growable ghost sockets render dashed; event sockets get
 *  the control-pipe (notched) treatment; stale Template tags read muted-red. A
 *  drag-from-port dims sockets whose type is incompatible (type-guided wiring). */
function InputPort({ nodeId, nodeTypeId, port }: { nodeId: string; nodeTypeId: string; port: ConcretePort }) {
  const { liveValues, connecting, connectedInputs, unpromoteParam, unpromoteWidget, inboundSources } = useEditor();
  // the LLM's promoted params demote through its model-aware path; every other
  // node's promoted widget demotes through the generic path.
  const demote = nodeTypeId === LLM_ID ? unpromoteParam : unpromoteWidget;
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  // a promoted port reverts to a knob from its OWN right-click (no inline icon),
  // matching the knob's convert menu; stop the bubble so the node menu stays shut.
  const onPortContextMenu = (e: React.MouseEvent) => {
    if (!port.promotedParam) return;
    e.preventDefault();
    e.stopPropagation();
    e.nativeEvent.stopImmediatePropagation();
    setMenu({ x: e.clientX, y: e.clientY });
  };
  // an `any` socket type-codes itself by what is actually plugged in: a Preview
  // tapping audio shows the audio colour on its `in` LED, not the wildcard grey.
  let effType = port.type;
  if (port.type === "any") {
    const wire = inboundSources.get(nodeId)?.find((w) => w.dstPort === port.name);
    if (wire) effType = wire.srcType;
  }
  const color = typeColorVar(effType);
  const isEvent = effType === "event" || port.trigger;
  const key = `${nodeId}:${port.name}`;
  const live = liveValues[key];
  // a port lights when a wire is plugged in (connected), brighter when live.
  const wired = connectedInputs.get(nodeId)?.has(port.name) ?? false;
  // while dragging a wire, light compatible targets and dim the rest.
  let dragCls = "";
  if (connecting) {
    if (connecting.fromId === nodeId) dragCls = "dim";
    else dragCls = typesCompatible(connecting.fromType, port.type) ? "compat" : "dim";
  }
  const cls = [
    "port in",
    port.ghost ? "ghost" : "",
    port.stale ? "stale" : "",
    isEvent ? "evt" : "",
    port.promotedParam ? "promoted" : "",
    dragCls,
  ].join(" ").trim();

  return (
    <div className={cls} style={{ ["--pc" as string]: color } as CSSProperties} onContextMenu={onPortContextMenu}>
      <Handle
        id={port.name}
        type="target"
        position={Position.Left}
        className={`wf-dot left ${port.ghost ? "ghost" : ""} ${wired || live ? "connected" : ""} ${live ? "live" : ""}`}
        style={{ ["--pc" as string]: color } as CSSProperties}
        isConnectable
      />
      <span className="plabel">
        {port.ghost ? `＋ ${ghostLabel(port.base)}` : port.name}
      </span>
      {port.stale && <span className="stale-mark" title="tag removed from template">stale</span>}
      {menu && createPortal(
        <ContextMenu
          x={menu.x}
          y={menu.y}
          items={[{ id: "revert", label: "Revert to widget", icon: "exit-outline", run: () => demote(nodeId, port.name) }]}
          onClose={() => setMenu(null)}
        />,
        document.body,
      )}
    </div>
  );
}

/** The Router pill: a tiny reroute node (one in, one out) with an optional centred
 *  label, coloured by the wire feeding it. Resizes horizontally only (fixed height). */
const ROUTER_H = 34;
function RouterPill({ id, label, color, inConnected, outConnected, selected, disabled, onLabel }: {
  id: string; label: string; color: string; inConnected: boolean; outConnected: boolean;
  selected: boolean; disabled: boolean; onLabel: (v: string) => void;
}) {
  void id;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(label);
  useEffect(() => { if (!editing) setDraft(label); }, [label, editing]);
  const commit = () => { setEditing(false); const v = draft.trim(); if (v !== label) onLabel(v); };
  return (
    <div className={`router-pill ${selected ? "selected" : ""} ${disabled ? "disabled" : ""}`} style={{ ["--rc" as string]: color } as CSSProperties}>
      <NodeResizer isVisible={selected} minWidth={64} maxWidth={400} minHeight={ROUTER_H} maxHeight={ROUTER_H} />
      <Handle id="in" type="target" position={Position.Left} className={`wf-dot left router-dot ${inConnected ? "connected" : ""}`} style={{ ["--pc" as string]: color } as CSSProperties} isConnectable />
      {editing ? (
        <input
          className="router-label-edit nodrag"
          value={draft}
          autoFocus
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => { e.stopPropagation(); if (e.key === "Enter") commit(); if (e.key === "Escape") { setDraft(label); setEditing(false); } }}
        />
      ) : (
        <span className="router-label" onDoubleClick={() => setEditing(true)} title="double-click to label">{label}</span>
      )}
      <Handle id="out" type="source" position={Position.Right} className={`wf-dot right router-dot ${outConnected ? "connected" : ""}`} style={{ ["--pc" as string]: color } as CSSProperties} isConnectable />
    </div>
  );
}

/** The Preview node's live surface: find the single wire feeding `in`, then show
 *  that (source, port) value stream, adapting to its (already-resolved) type. */
function PreviewSurface({
  nodeId,
  inboundSources,
  valueHistory,
  power,
  autoplay,
  onAutoplay,
}: {
  nodeId: string;
  inboundSources: Map<string, InboundWire[]>;
  valueHistory: Record<string, import("../../hooks/useRunSocket").HistPoint[]>;
  power: import("../../hooks/useRunSocket").Power;
  autoplay: boolean;
  onAutoplay: (v: boolean) => void;
}) {
  const wires = inboundSources.get(nodeId) ?? [];
  const wire = wires.find((w) => w.dstPort === "in") ?? wires[0];
  if (!wire) {
    return <div className="pv-empty">fan a wire into this Preview to tap a value</div>;
  }
  // A tap only shows live data through a LIVE wire. While power is on, a draft-only
  // wire (this Preview or its wire added since the last Save & Restart) must not
  // paint live values; it shows a clear "Save & Restart to tap" affordance instead.
  // (Off: nothing is live, so the last-captured values stay visible as before.)
  if (!liveGatePasses(power, wire.live)) {
    return (
      <div className="pv-empty pv-draft">
        <Icon name="refresh-outline" />
        <span>Save &amp; Restart to tap</span>
      </div>
    );
  }
  const key = `${wire.src}:${wire.srcPort}`;
  const inHistory = valueHistory[key] ?? [];
  // when a `trigger` is wired, the Preview is GATED by it: it shows the `in` value
  // sampled at each trigger event (the value current at that moment), so a Preview
  // after an LLM only updates once the LLM's done fires - not the instant `in`
  // changes. With no trigger wired it taps `in` live (every value). A draft-only
  // trigger wire is ignored while live (it isn't part of the running graph).
  const trigWire = wires.find((w) => w.dstPort === "trigger" && liveGatePasses(power, w.live));
  const history = trigWire
    ? (valueHistory[`${trigWire.src}:${trigWire.srcPort}`] ?? [])
        .map((t) => {
          const d = [...inHistory].reverse().find((x) => x.at <= t.at + 80) ?? inHistory[inHistory.length - 1];
          return d ? { at: t.at, value: d.value } : null;
        })
        .filter((p): p is { at: number; value: unknown } => p !== null)
    : inHistory;
  const latest = history[history.length - 1];
  const srcType = wire.srcType;
  const pvType = previewTypeFor(srcType, latest?.value);

  return (
    <div className="pv-wrap">
      <div className="pv-src">
        <Icon name="git-network-outline" />
        <span className="pv-srcname">{wire.src}.{wire.srcPort}</span>
        <span className="pv-srctype" style={{ ["--pc" as string]: typeColorVar(srcType) } as CSSProperties}>{srcType}</span>
      </div>
      <PreviewBody type={pvType} history={history} latest={latest} autoplay={autoplay} onAutoplay={onAutoplay} />
    </div>
  );
}

/** Read an LLM node's config.params as a record (always an object). */
function llmParams(config: Record<string, unknown>): Record<string, unknown> {
  const p = config.params;
  return p && typeof p === "object" ? (p as Record<string, unknown>) : {};
}

/** The model-driven node body (any node with a model widget): the same picker +
 *  per-model knob surface. The picker lists the runnable models of the family
 *  the widget declares, so a TTS node only lists TTS models. With nothing picked
 *  it shows the model the backend runs, the widget's declared default (TTS
 *  xai/tts, STT fish/asr); a node whose default is empty (the LLM) shows a picker
 *  only and its base ports. "auto" also keeps the base ports: the model it runs
 *  can change with what is installed. */
function LLMBody({
  config,
  widget,
  onSelectModel,
  onParamChange,
  onPromote,
}: {
  config: Record<string, unknown>;
  widget: Widget;
  onSelectModel: (modelId: string) => void;
  onParamChange: (name: string, value: unknown) => void;
  onPromote: (name: string) => void;
}) {
  const { models } = useEditor();
  const picked = config[widget.name];
  const selectedId = (typeof picked === "string" && picked) || String(widget.default ?? "");
  const manifest = models.get(selectedId);
  const promoted = llmPromoted(config);

  return (
    <div className="llm-body nodrag">
      <ModelPicker
        manifests={[...new Set(models.values())]}
        kind={widget.model_kind || "llm"}
        offersAuto={widget.options.includes("auto")}
        selectedId={selectedId}
        selected={manifest}
        onSelect={onSelectModel}
        variant="node"
      />
      {manifest ? (
        <ParamKnobs
          manifest={manifest}
          params={llmParams(config)}
          promoted={promoted}
          variant="node"
          onParamChange={onParamChange}
          onPromote={onPromote}
        />
      ) : (
        <div className="llm-nomodel">pick a model to configure its parameters</div>
      )}
    </div>
  );
}

/** Compact inline knobs for ordinary nodes: the one or two most-identifying
 *  config values, editable in place (text/number/bool/select), else a snippet. */
function InlineKnobs({
  def,
  nodeId,
  config,
  bodyRows,
  inboundSources,
  onChange,
  onPromote,
}: {
  def: import("../../types/protocol").NodeDef;
  nodeId: string;
  config: Record<string, unknown>;
  bodyRows: ReturnType<typeof bodySummary>;
  inboundSources: Map<string, InboundWire[]>;
  onChange: (key: string, value: unknown) => void;
  onPromote: (name: string) => void;
}) {
  // The body shows EVERY declared widget: there is no inspector and no per-id
  // INLINE_KNOBS subset. A widget is shown unless it is: surface="modal" (opens
  // the shared modal instead) or "hidden" (never drawn), op-shaped and out of its
  // op, or promoted to a port.
  const promoted = new Set(nodePromoted(config));
  const widgets = def.widgets.filter(
    (w) => onBody(w) && opVisible(w, config) && !promoted.has(w.name),
  );

  if (widgets.length === 0) {
    if (bodyRows.length === 0) return null;
    return (
      <div className="node-body">
        {bodyRows.map((row) => (
          <div className="ctl" key={row.key}>
            <span className="ck">{row.key}</span>
            <span className={`cv ${row.value.length > 12 ? "code" : ""}`}>{row.value}</span>
          </div>
        ))}
      </div>
    );
  }

  // template fields carry {tag} pipes; their suggestions are the source ports
  // wired into the node's growable `tag` socket (the dst_port name IS the tag).
  // Declared, not keyed by id: offer them when the node has any template widget.
  const tagSuggestions = def.widgets.some((w) => w.template)
    ? httpTagSuggestions(nodeId, inboundSources)
    : undefined;

  return (
    <div className="node-body">
      {widgets.map((w) => (
        <InlineWidget
          key={w.name}
          widget={w}
          nodeId={nodeId}
          nodeTypeId={def.id}
          value={config[w.name]}
          tagSuggestions={tagSuggestions}
          onChange={(v) => onChange(w.name, v)}
          onPromote={() => onPromote(w.name)}
        />
      ))}
    </div>
  );
}

function InlineWidget({
  widget,
  nodeId,
  nodeTypeId,
  value,
  tagSuggestions,
  onChange,
  onPromote,
}: {
  widget: Widget;
  nodeId: string;
  nodeTypeId: string;
  value: unknown;
  tagSuggestions?: string[];
  onChange: (v: unknown) => void;
  onPromote: () => void;
}) {
  const label = widget.label || widget.name;
  const { storeKeyForInput, wirelessInOwners } = useEditor();

  // a Wireless In owns its channel exclusively: its channel dropdown hides the
  // channels already taken by ANOTHER Wireless In (its own current value stays).
  const effectiveOptions =
    nodeTypeId === WIRELESS_IN_ID && widget.name === "channel"
      ? widget.options.filter((o) => {
          const ch = String(o);
          const owner = wirelessInOwners.get(ch);
          return !owner || owner === nodeId || ch === String(value);
        })
      : widget.options;

  // the empty-field example is declared on the widget (no per-id check).
  const placeholder = widget.placeholder || undefined;
  // draft buffer so the caret holds while the code value round-trips through state.
  const [draft, emitDraft] = useDraft(String(value ?? widget.default ?? ""), onChange as (v: string) => void);

  // a field whose value may reference {{secret.NAME}} renders through the
  // SecretAutocompleteField so typing `{{` opens the secret picker. Declared.
  const wantsSecrets = widget.accepts_secrets ?? false;

  // Universal right-click: every editable field offers Convert to input + Reset
  // to default, just like a Knob. A template field is promotable too: converting
  // promotes the WHOLE value to one typed input (the {tag} mechanism is an
  // additive inline-composition feature, not a reason to hide the menu item).
  const resetToDefault = () => onChange(widget.default);
  const promoteHandler = (widget.promotable ?? true) ? onPromote : undefined;

  // a field declaring options_from ("db.tables") is a live dropdown sourced from
  // the wired store. It renders in the SAME knob-row layout as every other
  // dropdown (OPERATION etc.): label left, the shared Select right.
  if (widget.options_from) {
    const storePort = widget.options_from.split(".")[0]; // "db" | "kv"
    return (
      <PromotableField className="knob select" onPromote={promoteHandler} onReset={resetToDefault}>
        <span className="knob-lbl">{label}</span>
        <StoreSelect
          source={widget.options_from}
          storeKey={storeKeyForInput(nodeId, storePort)}
          value={draft}
          placeholder={placeholder}
          onChange={emitDraft}
        />
      </PromotableField>
    );
  }

  // KV `key` gets a row of clickable chips of the connected store's real keys
  // (you can still type a NEW key freely; chips suggest, they don't constrain).
  const storeHintKind = nodeTypeId === KV_ID && widget.name === "key" ? "kv" : null;
  const hints = storeHintKind ? (
    <StoreHints kind={storeHintKind} storeKey={storeKeyForInput(nodeId, storeHintKind)} onPick={(n) => onChange(n)} />
  ) : null;

  // the code widget is a growable body surface (Text / Compute / DB sql+fields /
  // KV value). A field flagged `expand` grows with the node when resized; the
  // editor reads the flag, the node author just declares it.
  if (widget.kind === "code") {
    return (
      <PromotableField className={`ib code${widget.expand ? " expand" : ""}`} onPromote={promoteHandler} onReset={resetToDefault}>
        <span className="ib-lbl">{label}</span>
        {wantsSecrets ? (
          <SecretAutocompleteField
            value={draft}
            onChange={emitDraft}
            multiline
            rows={2}
            inline
            // an expand field is sized by the node's flex layout; autogrow off so
            // the two don't fight. A non-expand field autogrows to its content.
            autoGrow={!widget.expand}
            placeholder={placeholder}
            tagSuggestions={tagSuggestions}
          />
        ) : (
          <textarea
            className="nodrag nowheel"
            value={draft}
            rows={2}
            spellCheck={false}
            placeholder={placeholder}
            onChange={(e) => emitDraft(e.target.value)}
            onKeyDown={(e) => e.stopPropagation()}
          />
        )}
        {hints}
      </PromotableField>
    );
  }

  // a plain text widget on a secrets-aware node also gets the autocomplete (the
  // HTTP node's `url` is a one-liner that often carries a key in its query).
  if (widget.kind === "text" && wantsSecrets) {
    return (
      <PromotableField className="ib code" onPromote={promoteHandler} onReset={resetToDefault}>
        <span className="ib-lbl">{label}</span>
        <SecretAutocompleteField
          value={draft}
          onChange={emitDraft}
          multiline={false}
          inline
          tagSuggestions={tagSuggestions}
        />
        {hints}
      </PromotableField>
    );
  }

  const kind =
    widget.kind === "bool" || widget.kind === "select" || widget.kind === "number"
      ? widget.kind
      : "text";
  return (
    <Knob
      label={label}
      kind={kind}
      value={value}
      default={widget.default}
      min={widget.min}
      max={widget.max}
      step={widget.step}
      options={effectiveOptions}
      onChange={onChange}
      onConvert={onPromote}
      onReset={() => onChange(widget.default)}
    />
  );
}

/** The {tag} autocomplete candidates for an HTTP node: the source nodes wired
 *  into its `tag` growable port (the dst_port name IS the tag). Mirrors
 *  templateSuggestions; only the port-filter differs (HTTP tags land on `tag`,
 *  Template tags fill whatever dst_port matches the {tag} in the string). */
function httpTagSuggestions(
  nodeId: string,
  inboundSources: Map<string, InboundWire[]>,
): string[] {
  const out = new Set<string>();
  for (const w of inboundSources.get(nodeId) ?? []) {
    // the dst_port name IS the tag (set on ghost drop, named after the source).
    // Skip the trigger and any ghost placeholder; everything else is a real tag.
    if (!w.dstPort || w.dstPort === "trigger" || w.dstPort.endsWith("·+")) continue;
    out.add(w.dstPort);
  }
  return [...out];
}

/** The {tag} autocomplete candidates for a Template: the source nodes wired in,
 *  plus any tags already present in the string (so existing tags re-suggest). A
 *  wire on a declared port (the `trigger`) carries no tag, so it offers none. */
function templateSuggestions(
  nodeId: string,
  inboundSources: Map<string, InboundWire[]>,
  config: Record<string, unknown>,
  def: import("../../types/protocol").NodeDef,
): string[] {
  const reserved = reservedInputNames(def);
  const out = new Set<string>();
  for (const w of inboundSources.get(nodeId) ?? []) {
    if (reserved.has(w.dstPort)) continue;
    // the dst_port IS the tag name; also offer the bare source node name.
    if (w.dstPort && !w.dstPort.endsWith("·+")) out.add(w.dstPort);
    out.add(w.src);
  }
  for (const tag of templateTags(def, String(config.template ?? ""))) out.add(tag);
  return [...out];
}

/** Remeasure a node's handles on the next frame (after the new DOM paints). */
function updateNodeInternalsSafe(fn: (id: string) => void, id: string) {
  requestAnimationFrame(() => fn(id));
}

/** The "＋ x" label on a growable ghost socket, singularised from its base. */
function ghostLabel(base: string | undefined): string {
  if (!base) return "add";
  if (base === "tag") return "tag";
  return base.endsWith("s") ? base.slice(0, -1) : base;
}

function footMeta(
  typeId: string,
  config: Record<string, unknown>,
  liveValues: Record<string, { value: unknown; at: number }>,
  nodeId: string,
): React.ReactNode {
  switch (typeId) {
    case "core.trigger.interval":
      return <>every <b>{String(config.seconds ?? 2)}s</b></>;
    case "core.ai.llm": {
      const mid = String(config.model ?? "mock/echo");
      return <>model <b>{mid.includes("/") ? mid.split("/").pop() : mid}</b></>;
    }
    default: {
      const hit = Object.keys(liveValues).find((k) => k.startsWith(`${nodeId}:`));
      if (hit) return <>last <b>{shortVal(liveValues[hit].value, 18)}</b></>;
      return <span style={{ color: "var(--ink-ghost)" }}>idle</span>;
    }
  }
}

export const WorkflowNode = memo(WorkflowNodeImpl);
