// ============================================================================
// useModels: fetch GET /api/models once and expose the model registry as a
// fast Map<id, ModelManifest> for the LLM node + inspector to resolve a model by
// id (the capability source that reshapes the node's ports and knobs). Resilient
// like useObjectInfo: a failed fetch yields an error string and an empty (but
// valid) registry so the editor still mounts and the LLM falls back to base ports.
// ============================================================================
import { useEffect, useMemo, useState } from "react";
import type { ModelManifest, ModelsInfo } from "../types/protocol";

export interface ModelsState {
  loading: boolean;
  error: string | null;
  /** stable picker order (provider, then id), as served. */
  manifests: ModelManifest[];
  /** id -> manifest, for O(1) resolution from a node's config.model. */
  models: Map<string, ModelManifest>;
}

export function useModels(): ModelsState {
  const [data, setData] = useState<ModelsInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetch("/api/models");
        if (!res.ok) throw new Error(`models ${res.status}`);
        const json = (await res.json()) as ModelsInfo;
        if (alive) setData(json);
      } catch (err) {
        if (alive) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const models = useMemo(() => {
    const map = new Map<string, ModelManifest>();
    for (const m of data?.models ?? []) map.set(m.id, m);
    return map;
  }, [data]);

  return {
    loading,
    error,
    manifests: data?.models ?? [],
    models,
  };
}
