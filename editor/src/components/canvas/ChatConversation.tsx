// ============================================================================
// ChatConversation: the Chat output node's live surface.
// The conversation lives at the END of the flow, not on the trigger. This sink
// reconstructs the running chat from the wired value streams: the source feeding
// the `user` input carries your turns, the source feeding `reply` carries the
// model's turns. It reads valueHistory["<src>:<srcPort>"] for each, tags every
// point with its port, merges into one timeline sorted by the HistPoint `at`
// timestamp, and renders scrollable bubbles labelled by the PORT NAME (never a
// hardcoded role), autoscrolling to the latest.
// ============================================================================
import { useEffect, useMemo, useRef } from "react";
import { Icon } from "../../lib/icons";
import type { InboundWire } from "../../lib/editorContext";
import type { HistPoint, Power } from "../../hooks/useRunSocket";
import { liveGatePasses } from "../../lib/liveClassify";

// the two content input ports of the Chat node; also the style hook and the label
type Side = "user" | "reply";

interface Turn {
  side: Side;
  text: string;
  at: number;
  key: string;
}

interface ChatConversationProps {
  nodeId: string;
  inboundSources: Map<string, InboundWire[]>;
  valueHistory: Record<string, HistPoint[]>;
  power: Power;
}

export function ChatConversation({ nodeId, inboundSources, valueHistory, power }: ChatConversationProps) {
  const wires = inboundSources.get(nodeId) ?? [];
  const userWire = wires.find((w) => w.dstPort === "user");
  const replyWire = wires.find((w) => w.dstPort === "reply");
  const userTrigWire = wires.find((w) => w.dstPort === "user_trigger");
  const replyTrigWire = wires.find((w) => w.dstPort === "reply_trigger");

  const turns = useMemo<Turn[]>(() => {
    const merged: Turn[] = [];
    // while power is on, only LIVE wires contribute turns: a draft-only wire (added
    // since the last Save & Restart) is not part of the running graph, so it must
    // not paint live conversation. Off: nothing is live, show cached turns as before.
    const usable = (wire: InboundWire | undefined) => !!wire && liveGatePasses(power, wire.live);
    const histOf = (wire: InboundWire | undefined) => (usable(wire) ? (valueHistory[`${wire!.src}:${wire!.srcPort}`] ?? []) : []);
    // each side commits a turn when its TRIGGER fires, taking the data value
    // current at that moment (the latest data point at/just before the trigger).
    // Before its trigger is wired (the graph cannot turn On until it is) the
    // side shows every value on its data wire.
    const collect = (dataWire: InboundWire | undefined, trigWire: InboundWire | undefined, side: Side) => {
      const data = histOf(dataWire);
      if (trigWire) {
        const trigs = histOf(trigWire);
        trigs.forEach((t, i) => {
          // the data value current at this trigger (slight tolerance for the data
          // landing a hair after the event); else pair by index.
          const d = [...data].reverse().find((x) => x.at <= t.at + 80) ?? data[i];
          const text = d?.value === null || d?.value === undefined ? "" : String(d?.value ?? "");
          if (text === "") return;
          merged.push({ side, text, at: t.at, key: `${side}:${i}:${t.at}` });
        });
        return;
      }
      data.forEach((h, i) => {
        const text = h.value === null || h.value === undefined ? "" : String(h.value);
        if (text === "") return;
        merged.push({ side, text, at: h.at, key: `${side}:${i}:${h.at}` });
      });
    };
    collect(userWire, userTrigWire, "user");
    collect(replyWire, replyTrigWire, "reply");
    // one timeline, oldest first (the value-stream points carry timestamps).
    // Each point is one event: a replay after a reconnect never lands in the
    // history twice (lib/replayedValues), so two equal bubbles are two sends.
    merged.sort((a, b) => a.at - b.at);
    return merged;
  }, [userWire, replyWire, userTrigWire, replyTrigWire, valueHistory, power]);

  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns.length]);

  if (!userWire && !replyWire) {
    return (
      <div className="convo-hint">
        <Icon name="git-network-outline" />
        <span>wire <b>user</b> / <b>reply</b> (+ their triggers) to show the conversation</span>
      </div>
    );
  }

  const legend = (wire: InboundWire | undefined, side: Side) => (
    <span className={`convo-src ${wire ? "" : "off"}`}>
      <span className={`convo-dot ${side}`} /> {side}
      <span className="convo-srcname">{wire ? `${wire.src}.${wire.srcPort}` : "unwired"}</span>
    </span>
  );

  return (
    <div className="convo">
      <div className="convo-srcs">
        {legend(userWire, "user")}
        {legend(replyWire, "reply")}
      </div>
      <div className="convo-scroll nodrag nowheel" ref={scrollRef}>
        {turns.length === 0 ? (
          <div className="convo-empty">no turns yet: send a message to start the conversation</div>
        ) : (
          turns.map((t) => (
            <div className={`convo-bubble ${t.side}`} key={t.key}>
              <span className="convo-who">{t.side}</span>
              <span className="convo-text">{t.text}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
