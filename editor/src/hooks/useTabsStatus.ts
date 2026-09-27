// ============================================================================
// useTabsStatus: opens a LIGHTWEIGHT ws per open tab slug, listens ONLY for
// `status` events, and returns a {slug -> "on"|"off"} map so each tab in the
// WorkflowTabs bar can show a green dot when its backend Hub is live. It also
// hands on `graph-saved` (the tab's graph was saved, here or anywhere else,
// with its new version), so the editor can follow a save made elsewhere.
//
// One WS per slug. Sockets are torn down when a slug leaves the open list;
// reconnect with a capped backoff on close. Does NOT mutate live values, logs,
// chats, errors, or counters; that surface belongs to useRunSocket (active tab).
// ============================================================================
import { useEffect, useRef, useState } from "react";
import type { Power } from "./useRunSocket";

interface PerSlug {
  ws: WebSocket | null;
  reconnectTimer: number | null;
  reconnectDelay: number;
  closedByUs: boolean;
}

export function useTabsStatus(
  openSlugs: string[],
  onGraphSaved?: (slug: string, version: string) => void,
): Record<string, Power> {
  const [status, setStatus] = useState<Record<string, Power>>({});
  const sockets = useRef<Map<string, PerSlug>>(new Map());
  const savedRef = useRef(onGraphSaved);
  savedRef.current = onGraphSaved;

  useEffect(() => {
    const wanted = new Set(openSlugs);

    // tear down sockets for slugs that are no longer open
    for (const [slug, st] of sockets.current.entries()) {
      if (!wanted.has(slug)) {
        st.closedByUs = true;
        if (st.reconnectTimer !== null) {
          window.clearTimeout(st.reconnectTimer);
          st.reconnectTimer = null;
        }
        try { st.ws?.close(); } catch { /* ignore */ }
        sockets.current.delete(slug);
        setStatus((p) => {
          if (!(slug in p)) return p;
          const { [slug]: _drop, ...rest } = p;
          return rest;
        });
      }
    }

    // open a socket for each new slug
    for (const slug of openSlugs) {
      if (sockets.current.has(slug)) continue;
      const st: PerSlug = {
        ws: null,
        reconnectTimer: null,
        reconnectDelay: 0,
        closedByUs: false,
      };
      sockets.current.set(slug, st);
      const connect = () => {
        if (st.closedByUs) return;
        const proto = window.location.protocol === "https:" ? "wss" : "ws";
        const ws = new WebSocket(`${proto}://${window.location.host}/ws?slug=${encodeURIComponent(slug)}`);
        st.ws = ws;
        ws.onopen = () => {
          st.reconnectDelay = 0;
        };
        ws.onmessage = (e) => {
          try {
            const msg = JSON.parse(e.data) as { kind?: string; power?: Power; version?: unknown };
            if (msg.kind === "status" && (msg.power === "on" || msg.power === "off")) {
              setStatus((p) => (p[slug] === msg.power ? p : { ...p, [slug]: msg.power as Power }));
            } else if (msg.kind === "graph-saved" && typeof msg.version === "string") {
              savedRef.current?.(slug, msg.version);
            }
          } catch { /* ignore malformed */ }
        };
        ws.onclose = () => {
          st.ws = null;
          if (st.closedByUs) return;
          const delay = st.reconnectDelay === 0 ? 500 : Math.min(st.reconnectDelay * 2, 4000);
          st.reconnectDelay = delay;
          st.reconnectTimer = window.setTimeout(() => {
            st.reconnectTimer = null;
            connect();
          }, delay);
        };
        ws.onerror = () => { /* ignore: onclose handles reconnect */ };
      };
      connect();
    }
  }, [openSlugs]);

  // tear down everything on unmount
  useEffect(() => {
    const map = sockets.current;
    return () => {
      for (const st of map.values()) {
        st.closedByUs = true;
        if (st.reconnectTimer !== null) window.clearTimeout(st.reconnectTimer);
        try { st.ws?.close(); } catch { /* ignore */ }
      }
      map.clear();
    };
  }, []);

  return status;
}
