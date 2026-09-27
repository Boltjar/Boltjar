// ============================================================================
// ChatBox: the inline chat surface on a Chat Input node. Two variants over one
// shell, sharing the send box that dispatches ws { action: "chat", node, text }
// only while the graph is powered On (Off disables it with a power-on hint):
//   • the full box (a local you/assistant history + the send box), kept for
//     reference;
//   • the send-only variant (the conversation lives at the END of the flow in a
//     Chat output node, so the trigger is just a send box).
// ============================================================================
import { useEffect, useRef, useState } from "react";
import { Icon } from "../../lib/icons";
import type { ChatMessage } from "../../hooks/useRunSocket";

// the send box's hint while the graph is On (Off shows the power-on hint)
const SEND_HINT = "Type a message...";

interface ChatBoxProps {
  messages: ChatMessage[];
  enabled: boolean; // power is On
  onSend: (text: string) => void;
  /** send-only: drop the message history (the conversation lives in a Chat output). */
  sendOnly?: boolean;
}

export function ChatBox({ messages, enabled, onSend, sendOnly = false }: ChatBoxProps) {
  const [text, setText] = useState("");
  const histRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = histRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length]);

  const submit = () => {
    const t = text.trim();
    if (!t || !enabled) return;
    onSend(t);
    setText("");
  };

  return (
    <div className={`chatbox nodrag ${sendOnly ? "send-only" : ""}`}>
      {!sendOnly && (
        <div className="chat-hist nowheel" ref={histRef}>
          {messages.length === 0 ? (
            <div className="chat-empty">
              {enabled ? "say hello to start the conversation" : "power on to chat"}
            </div>
          ) : (
            messages.map((m) => (
              <div className={`chat-msg ${m.role}`} key={m.id}>
                <span className="chat-who">{m.role}</span>
                <span className="chat-text">{m.text}</span>
              </div>
            ))
          )}
        </div>
      )}
      <div className={`chat-input ${enabled ? "" : "disabled"}`}>
        <input
          type="text"
          value={text}
          placeholder={enabled ? SEND_HINT : "off: power on to send"}
          disabled={!enabled}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            e.stopPropagation();
            if (e.key === "Enter") {
              e.preventDefault();
              submit();
            }
          }}
          spellCheck={false}
        />
        <button className="chat-send" onClick={submit} disabled={!enabled || !text.trim()} title="Send (Enter)">
          <Icon name="send" />
        </button>
      </div>
    </div>
  );
}
