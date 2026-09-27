// ============================================================================
// StoreBody: the inline body for the Database and KV Store source nodes. Polls
// the backend's /api/store/{kind}/{key}/info endpoint so the card actually
// shows what's in the store: how many tables (or keys), the busiest ones, and
// the file on disk. Without this the source node was a black-box handle.
// ============================================================================
import { useEffect, useState } from "react";
import { Icon } from "../../lib/icons";
import { NodeModal } from "./NodeModal";
import { DbSchemaEditor } from "../DbSchemaEditor";

interface DbColumn { name: string; type: string; pk: boolean }
interface DbInfo {
  path: string;
  tables: Array<{ name: string; rows: number; columns?: DbColumn[] }>;
}
interface KvInfo {
  path: string;
  count: number;
  sample: string[];
}

interface StoreBodyProps {
  kind: "db" | "kv";
  /** The store key the node emits: either cfg.db_key/cfg.kv_key if the editor
   *  has assigned one, or the node id as fallback (mirrors the backend node). */
  storeKey: string;
  /** node id, for the modal title. */
  nodeId: string;
  /** bypassed node: the schema editor renders read-only. */
  disabled?: boolean;
}

const POLL_MS = 4000;

export function StoreBody({ kind, storeKey, nodeId, disabled = false }: StoreBodyProps) {
  const [info, setInfo] = useState<DbInfo | KvInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [schemaOpen, setSchemaOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const fetchInfo = async () => {
      try {
        const r = await fetch(`/api/store/${kind}/${encodeURIComponent(storeKey)}/info`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        if (!cancelled) {
          setInfo(data);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(String((e as Error).message ?? e));
      }
    };
    void fetchInfo();
    const id = window.setInterval(fetchInfo, POLL_MS);
    return () => { cancelled = true; window.clearInterval(id); };
  }, [kind, storeKey]);

  if (error) {
    return <div className="store-body store-body--err">unavailable · {error}</div>;
  }
  if (!info) {
    return <div className="store-body store-body--loading">reading store…</div>;
  }

  // file path: show just the filename + a title with the full absolute path,
  // so the card stays compact but the user can hover to copy / verify.
  const filename = info.path.split(/[\\/]/).pop() ?? info.path;

  if (kind === "db") {
    const dbInfo = info as DbInfo;
    const empty = dbInfo.tables.length === 0;
    const visible = dbInfo.tables.slice(0, 3);
    const more = dbInfo.tables.length - visible.length;
    return (
      <div className="store-body">
        <div className="store-head">
          <span className={`store-badge ${empty ? "empty" : "has"}`}>
            {empty ? "empty" : `${dbInfo.tables.length} table${dbInfo.tables.length === 1 ? "" : "s"}`}
          </span>
          <span className="store-file" title={dbInfo.path}>
            <Icon name="document-outline" />
            {filename}
          </span>
        </div>
        {!empty && (
          <div className="store-tables">
            {visible.map((t) => (
              <div key={t.name} className="store-table">
                <div className="store-table-head">
                  <span className="store-name">{t.name}</span>
                  <span className="store-count">{t.rows.toLocaleString()} row{t.rows === 1 ? "" : "s"}</span>
                </div>
                {(t.columns ?? []).length > 0 && (
                  <ul className="store-cols">
                    {(t.columns ?? []).map((c) => (
                      <li key={c.name}>
                        <span className="store-col-name">{c.name}{c.pk && <em className="store-col-pk">pk</em>}</span>
                        <span className="store-col-type">{c.type.toLowerCase()}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
            {more > 0 && <div className="store-more">+{more} more table{more === 1 ? "" : "s"}</div>}
          </div>
        )}
        <button type="button" className="store-edit nodrag" onClick={() => setSchemaOpen(true)}>
          <Icon name="create-outline" /> Edit
        </button>
        {schemaOpen && (
          <NodeModal
            title={nodeId}
            subtitle="schema"
            icon="albums-outline"
            onClose={() => setSchemaOpen(false)}
          >
            <DbSchemaEditor dbKey={storeKey} disabled={disabled} />
          </NodeModal>
        )}
      </div>
    );
  }

  const kvInfo = info as KvInfo;
  const empty = kvInfo.count === 0;
  return (
    <div className="store-body">
      <div className="store-head">
        <span className={`store-badge ${empty ? "empty" : "has"}`}>
          {empty ? "empty" : `${kvInfo.count.toLocaleString()} key${kvInfo.count === 1 ? "" : "s"}`}
        </span>
        <span className="store-file" title={kvInfo.path}>
          <Icon name="document-outline" />
          {filename}
        </span>
      </div>
      {!empty && (
        <ul className="store-list">
          {kvInfo.sample.map((k) => (
            <li key={k}>
              <span className="store-name">{k}</span>
            </li>
          ))}
          {kvInfo.count > kvInfo.sample.length && (
            <li className="store-more">+{kvInfo.count - kvInfo.sample.length} more</li>
          )}
        </ul>
      )}
    </div>
  );
}
