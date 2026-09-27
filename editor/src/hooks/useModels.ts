// ============================================================================
// useModels: the live model list (GET /api/models) as a fast Map<id,
// ModelManifest> for the model nodes to resolve a model by id (the capability
// source that reshapes a node's ports and knobs), plus the list's state: when
// it was last updated and which providers did not answer.
//
// `reload` reads the server's list again (cheap: the server answers from its
// cache and refreshes a stale list in the background). `refresh` asks every
// provider now and waits for the answers. While the server reports a background
// refresh running (right after it starts, or after a key changes), the list is
// read again every few seconds until it settles. Resilient like useObjectInfo: a
// failed fetch yields an error string and an empty (but valid) registry so the
// editor still mounts and the model nodes fall back to their base ports.
// ============================================================================
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ModelManifest, ModelsInfo } from "../types/protocol";
import { listState, type ModelsMeta } from "../lib/modelMeta";

/** How often the list is read again while the server is refreshing it. */
const POLL_MS = 2000;
/** A refresh that never settles stops being polled after this many reads. */
const POLL_MAX = 30;

export interface ModelsState {
  loading: boolean;
  error: string | null;
  /** the served order (provider, then curated first). */
  manifests: ModelManifest[];
  /** id -> manifest, for O(1) resolution from a node's config.model. A model is
   *  also found by `<provider>/<its provider name>` and `<provider>/<alias>`, the
   *  ids the server resolves to it, so a graph saved with one of them reshapes. */
  models: Map<string, ModelManifest>;
  meta: ModelsMeta;
  /** read the server's list again. */
  reload: () => Promise<void>;
  /** ask every provider now, then show the fresh list. */
  refresh: () => Promise<void>;
}

function metaOf(info: ModelsInfo | null, loading: boolean, error: string | null): ModelsMeta {
  return {
    list: listState(info !== null, loading, error),
    updated: info?.updated ?? null,
    refreshing: info?.refreshing ?? false,
    providers: info?.providers ?? {},
  };
}

export function useModels(): ModelsState {
  const [data, setData] = useState<ModelsInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const alive = useRef(true);
  const polls = useRef(0);

  const load = useCallback(async (method: "GET" | "POST") => {
    try {
      const res = await fetch(method === "GET" ? "/api/models" : "/api/models/refresh", { method });
      if (!res.ok) throw new Error(`models ${res.status}`);
      const json = (await res.json()) as ModelsInfo;
      if (alive.current) {
        setData(json);
        setError(null);
      }
    } catch (err) {
      if (alive.current) setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (alive.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    void load("GET");
    return () => {
      alive.current = false;
    };
  }, [load]);

  // a background refresh on the server: read again until it settles.
  useEffect(() => {
    if (!data?.refreshing) {
      polls.current = 0;
      return;
    }
    if (polls.current >= POLL_MAX) return;
    const timer = window.setTimeout(() => {
      polls.current += 1;
      void load("GET");
    }, POLL_MS);
    return () => window.clearTimeout(timer);
  }, [data, load]);

  const reload = useCallback(() => load("GET"), [load]);
  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await load("POST");
    } finally {
      if (alive.current) setRefreshing(false);
    }
  }, [load]);

  const models = useMemo(() => {
    const map = new Map<string, ModelManifest>();
    const rows = data?.models ?? [];
    for (const m of rows) map.set(m.id, m);
    // the other ids second, so a real id always wins over an alias.
    for (const m of rows) {
      for (const name of [m.model, ...(m.aliases ?? [])]) {
        const other = `${m.provider}/${name}`;
        if (!map.has(other)) map.set(other, m);
      }
    }
    return map;
  }, [data]);

  const meta = useMemo(() => {
    const m = metaOf(data, loading, error);
    return refreshing ? { ...m, refreshing: true } : m;
  }, [data, loading, error, refreshing]);

  return {
    loading,
    error,
    manifests: data?.models ?? [],
    models,
    meta,
    reload,
    refresh,
  };
}
