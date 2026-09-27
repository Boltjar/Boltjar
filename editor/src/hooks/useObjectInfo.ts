// ============================================================================
// useObjectInfo: fetch GET /api/object_info once and expose the node catalog
// plus a fast Map<id, NodeDef> for lookups. Resilient: a failed fetch yields an
// error string and an empty (but valid) catalog so the editor still mounts.
// ============================================================================
import { useEffect, useMemo, useState } from "react";
import type { NodeDef, ObjectInfo } from "../types/protocol";

export interface ObjectInfoState {
  loading: boolean;
  error: string | null;
  nodes: NodeDef[];
  /** type name -> seed colour hex (the design layer owns the canonical palette). */
  typeColors: Record<string, string>;
  defs: Map<string, NodeDef>;
}

export function useObjectInfo(): ObjectInfoState {
  const [data, setData] = useState<ObjectInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetch("/api/object_info");
        if (!res.ok) throw new Error(`object_info ${res.status}`);
        const json = (await res.json()) as ObjectInfo;
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

  const defs = useMemo(() => {
    const map = new Map<string, NodeDef>();
    for (const def of data?.nodes ?? []) map.set(def.id, def);
    return map;
  }, [data]);

  return {
    loading,
    error,
    nodes: data?.nodes ?? [],
    typeColors: data?.types ?? {},
    defs,
  };
}
