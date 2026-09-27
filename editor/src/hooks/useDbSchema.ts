// ============================================================================
// useDbSchema: fetch GET /api/db/{key}/schema for a Database node and expose its
// live schema (tables + columns + row counts). Resilient like useModels /
// useObjectInfo: loading / error / empty states, and a `refetch` to re-pull after
// a mutation (create/rename/drop table or column). The mutation endpoints all
// return the fresh schema, so callers may either feed the result through
// `setSchema` directly or just call `refetch`.
// ============================================================================
import { useCallback, useEffect, useState } from "react";
import type { DbSchemaInfo, DbTable } from "../types/protocol";

export interface DbSchemaState {
  loading: boolean;
  error: string | null;
  tables: DbTable[];
  /** total rows across every table, for the compact node summary. */
  totalRows: number;
  /** re-pull the schema from the server. */
  refetch: () => void;
  /** adopt a schema returned inline by a mutation endpoint (skips a round-trip). */
  setTables: (tables: DbTable[]) => void;
}

export function useDbSchema(dbKey: string | null | undefined): DbSchemaState {
  const [tables, setTables] = useState<DbTable[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);

  const refetch = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    if (!dbKey) {
      setTables([]);
      setError(null);
      setLoading(false);
      return;
    }
    let alive = true;
    setLoading(true);
    (async () => {
      try {
        const res = await fetch(`/api/db/${encodeURIComponent(dbKey)}/schema`);
        if (!res.ok) throw new Error(`schema ${res.status}`);
        const json = (await res.json()) as DbSchemaInfo;
        if (alive) {
          setTables(json.schema ?? []);
          setError(null);
        }
      } catch (err) {
        if (alive) {
          setError(err instanceof Error ? err.message : String(err));
          setTables([]);
        }
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [dbKey, nonce]);

  const totalRows = tables.reduce((sum, t) => sum + (t.rows ?? 0), 0);

  return { loading, error, tables, totalRows, refetch, setTables };
}
