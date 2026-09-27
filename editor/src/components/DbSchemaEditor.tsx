// ============================================================================
// DbSchemaEditor: the Database node's full schema editor, shown inside the
// shared NodeModal (opened from the Database node body). Lists tables; each
// expands to its columns (name + SQL type + pk). Add / rename / drop a table,
// add / rename / drop a column, all through proper inline forms (a name input,
// a type dropdown, a pk checkbox; destructive actions ask an inline Yes/No), no
// window.prompt anywhere. Every mutation hits the REST endpoints on the shared
// store (which return the fresh schema) and the editor adopts that result, so
// the node's compact body and this editor stay in step. There is deliberately
// NO delete-database button: whole-DB delete is the normal node-delete flow.
// ============================================================================
import { useState, type CSSProperties } from "react";
import type { DbColumn, DbTable } from "../types/protocol";
import { Icon } from "../lib/icons";
import { typeColorVar } from "../lib/types";
import { useDbSchema } from "../hooks/useDbSchema";
import { Select } from "./canvas/Select";

/** The column types offered in the dropdown -> the SQLite affinity sent to the
 *  backend. A small, friendly set (the store maps anything else to TEXT). */
const COL_TYPES: ReadonlyArray<{ label: string; value: string }> = [
  { label: "text", value: "text" },
  { label: "integer", value: "int" },
  { label: "real", value: "real" },
  { label: "boolean", value: "bool" },
  { label: "blob", value: "blob" },
];

/** Map a SQLite affinity (TEXT/INTEGER/REAL/…) to a pipe-type colour token so a
 *  column's type chip reads in the same palette as the wires (int blue, real cyan,
 *  text sky). Unknown affinities fall back to text. */
function typeChipColor(coltype: string): string {
  const t = coltype.toLowerCase();
  if (t.includes("int")) return typeColorVar("int");
  if (t.includes("real") || t.includes("floa") || t.includes("doub")) return typeColorVar("float");
  if (t.includes("blob")) return typeColorVar("json");
  return typeColorVar("text");
}

interface DbSchemaEditorProps {
  dbKey: string;
  disabled: boolean;
}

export function DbSchemaEditor({ dbKey, disabled }: DbSchemaEditorProps) {
  const { tables, loading, error, refetch, setTables } = useDbSchema(dbKey);
  const [busy, setBusy] = useState(false);
  const [opError, setOpError] = useState<string | null>(null);
  // add-table inline form (name draft); null = form closed.
  const [newTable, setNewTable] = useState<string | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);

  /** Run a schema mutation; adopt the fresh schema it returns (then refetch as a
   *  safety net). Serialised behind `busy` so rapid clicks cannot interleave.
   *  Returns true on success so callers can close their inline form. */
  async function mutate(method: string, path: string, body: unknown): Promise<boolean> {
    if (busy || disabled) return false;
    setBusy(true);
    setOpError(null);
    try {
      const res = await fetch(`/api/db/${encodeURIComponent(dbKey)}/${path}`, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const txt = await res.text().catch(() => "");
        throw new Error(txt || `${method} ${path} ${res.status}`);
      }
      const json = (await res.json()) as { schema?: DbTable[] };
      if (json.schema) setTables(json.schema);
      else refetch();
      return true;
    } catch (err) {
      setOpError(err instanceof Error ? err.message : String(err));
      refetch();
      return false;
    } finally {
      setBusy(false);
    }
  }

  const submitNewTable = async () => {
    const name = (newTable ?? "").trim();
    if (!name) { setNewTable(null); return; }
    if (await mutate("POST", "table", { table: name })) setNewTable(null);
  };

  return (
    <div className="db-schema">
      <div className="db-schema-head">
        <span className="db-schema-count">
          {tables.length} {tables.length === 1 ? "table" : "tables"}
        </span>
        {newTable === null ? (
          <button
            type="button"
            className="db-add-table nodrag"
            onClick={() => setNewTable("")}
            disabled={disabled || busy}
            title="Add a table"
          >
            <Icon name="add-outline" /> table
          </button>
        ) : (
          <span className="db-inline-form">
            <input
              className="db-inline-input nodrag"
              autoFocus
              placeholder="table name"
              value={newTable}
              spellCheck={false}
              onChange={(e) => setNewTable(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") { e.preventDefault(); void submitNewTable(); }
                else if (e.key === "Escape") setNewTable(null);
              }}
            />
            <button type="button" className="db-inline-ok nodrag" onClick={() => void submitNewTable()} disabled={busy}>add</button>
            <button type="button" className="db-inline-cancel nodrag" onClick={() => setNewTable(null)}>cancel</button>
          </span>
        )}
      </div>

      {error && <div className="db-schema-msg bad">schema unavailable: {error}</div>}
      {opError && <div className="db-schema-msg bad">{opError}</div>}
      {loading && tables.length === 0 && <div className="db-schema-msg">reading schema…</div>}
      {!loading && !error && tables.length === 0 && newTable === null && (
        <div className="db-schema-msg">No tables yet. Add one to get started.</div>
      )}

      {tables.map((t) => (
        <TableCard key={t.name} table={t} disabled={disabled || busy} mutate={mutate} />
      ))}

      {/* clear all DATA: delete every row from every table (the schema stays).
          A proper footer button, gated behind an inline yes/no. */}
      {tables.length > 0 && (() => {
        const totalRows = tables.reduce((n, t) => n + t.rows, 0);
        return (
          <div className="db-schema-foot">
            <span className="db-foot-warn">
              <Icon name="warning-outline" />
              Deletes every row across all tables. This cannot be undone.
            </span>
            {confirmClear ? (
              <span className="db-confirm">
                <span className="db-confirm-lbl">delete all {totalRows} {totalRows === 1 ? "row" : "rows"}?</span>
                <button type="button" className="db-inline-ok del nodrag"
                  onClick={async () => { setConfirmClear(false); await mutate("DELETE", "all", {}); }}>yes</button>
                <button type="button" className="db-inline-cancel nodrag" onClick={() => setConfirmClear(false)}>no</button>
              </span>
            ) : (
              <button type="button" className="db-clear nodrag"
                onClick={() => setConfirmClear(true)} disabled={disabled || busy || totalRows === 0}
                title="Delete all rows (keep the tables)">
                <Icon name="trash-outline" /> Clear data
              </button>
            )}
          </div>
        );
      })()}
    </div>
  );
}

function TableCard({
  table,
  disabled,
  mutate,
}: {
  table: DbTable;
  disabled: boolean;
  mutate: (method: string, path: string, body: unknown) => Promise<boolean>;
}) {
  const [open, setOpen] = useState(true);
  const [renaming, setRenaming] = useState<string | null>(null);   // table-rename draft
  const [confirmDrop, setConfirmDrop] = useState(false);
  // add-column inline form
  const [addingCol, setAddingCol] = useState(false);
  const [colName, setColName] = useState("");
  const [colType, setColType] = useState("text");

  const submitRename = async () => {
    const next = (renaming ?? "").trim();
    if (next && next !== table.name) await mutate("PATCH", "table", { old: table.name, new: next });
    setRenaming(null);
  };
  const submitColumn = async () => {
    const name = colName.trim();
    if (!name) { setAddingCol(false); return; }
    if (await mutate("POST", "column", { table: table.name, name, type: colType })) {
      setColName(""); setColType("text"); setAddingCol(false);
    }
  };

  return (
    <div className="db-card">
      <div className="db-card-head">
        {renaming === null ? (
          <button type="button" className="db-card-toggle nodrag" onClick={() => setOpen((o) => !o)} title={open ? "collapse" : "expand"}>
            <span className={`db-caret ${open ? "open" : ""}`}>{open ? "▾" : "▸"}</span>
            <span className="db-card-name">{table.name}</span>
            <span className="db-card-rows">{table.rows} {table.rows === 1 ? "row" : "rows"}</span>
          </button>
        ) : (
          <span className="db-inline-form grow">
            <input
              className="db-inline-input nodrag" autoFocus value={renaming} spellCheck={false}
              onChange={(e) => setRenaming(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") { e.preventDefault(); void submitRename(); }
                else if (e.key === "Escape") setRenaming(null);
              }}
            />
            <button type="button" className="db-inline-ok nodrag" onClick={() => void submitRename()}>save</button>
            <button type="button" className="db-inline-cancel nodrag" onClick={() => setRenaming(null)}>cancel</button>
          </span>
        )}
        {renaming === null && !confirmDrop && (
          <span className="db-card-ic">
            <button type="button" className="nodrag" onClick={() => setRenaming(table.name)} disabled={disabled} title="rename table">
              <Icon name="pencil" />
            </button>
            <button type="button" className="nodrag del" onClick={() => setConfirmDrop(true)} disabled={disabled} title="drop table">
              <Icon name="trash-outline" />
            </button>
          </span>
        )}
        {confirmDrop && (
          <span className="db-confirm">
            <span className="db-confirm-lbl">drop {table.rows} {table.rows === 1 ? "row" : "rows"}?</span>
            <button type="button" className="db-inline-ok del nodrag"
              onClick={async () => { setConfirmDrop(false); await mutate("DELETE", "table", { table: table.name }); }}>yes</button>
            <button type="button" className="db-inline-cancel nodrag" onClick={() => setConfirmDrop(false)}>no</button>
          </span>
        )}
      </div>

      {open && (
        <div className="db-cols">
          {table.columns.map((c: DbColumn) => (
            <ColumnRow key={c.name} table={table.name} col={c} disabled={disabled} mutate={mutate} />
          ))}
          {addingCol ? (
            <div className="db-col-add-form">
              <input
                className="db-inline-input nodrag" autoFocus placeholder="column name" value={colName} spellCheck={false}
                onChange={(e) => setColName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") { e.preventDefault(); void submitColumn(); }
                  else if (e.key === "Escape") setAddingCol(false);
                }}
              />
              <Select value={colType} options={COL_TYPES} onChange={setColType} className="db-type-sel" />
              <button type="button" className="db-inline-ok nodrag" onClick={() => void submitColumn()}>add</button>
              <button type="button" className="db-inline-cancel nodrag" onClick={() => setAddingCol(false)}>cancel</button>
            </div>
          ) : (
            <button type="button" className="db-add-col nodrag" onClick={() => setAddingCol(true)} disabled={disabled}>
              <Icon name="add-outline" /> column
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function ColumnRow({
  table, col, disabled, mutate,
}: {
  table: string;
  col: DbColumn;
  disabled: boolean;
  mutate: (method: string, path: string, body: unknown) => Promise<boolean>;
}) {
  const [renaming, setRenaming] = useState<string | null>(null);
  const [confirmDrop, setConfirmDrop] = useState(false);

  const submitRename = async () => {
    const next = (renaming ?? "").trim();
    if (next && next !== col.name) await mutate("PATCH", "column", { table, old: col.name, new: next });
    setRenaming(null);
  };

  if (renaming !== null) {
    return (
      <div className="db-col">
        <span className="db-inline-form grow">
          <input
            className="db-inline-input nodrag" autoFocus value={renaming} spellCheck={false}
            onChange={(e) => setRenaming(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); void submitRename(); }
              else if (e.key === "Escape") setRenaming(null);
            }}
          />
          <button type="button" className="db-inline-ok nodrag" onClick={() => void submitRename()}>save</button>
          <button type="button" className="db-inline-cancel nodrag" onClick={() => setRenaming(null)}>cancel</button>
        </span>
      </div>
    );
  }

  return (
    <div className="db-col">
      <span className="db-cname">{col.name}</span>
      {col.pk && <span className="db-cpk">pk</span>}
      <span className="db-ctype" style={{ ["--ctc" as string]: typeChipColor(col.type) } as CSSProperties}>
        {col.type.toLowerCase()}
      </span>
      {confirmDrop ? (
        <span className="db-confirm">
          <span className="db-confirm-lbl">drop?</span>
          <button type="button" className="db-inline-ok del nodrag"
            onClick={async () => { setConfirmDrop(false); await mutate("DELETE", "column", { table, name: col.name }); }}>yes</button>
          <button type="button" className="db-inline-cancel nodrag" onClick={() => setConfirmDrop(false)}>no</button>
        </span>
      ) : (
        <>
          <button type="button" className="db-col-del nodrag" onClick={() => setRenaming(col.name)} disabled={disabled} title="rename column">
            <Icon name="pencil" />
          </button>
          <button type="button" className="db-col-del nodrag del" onClick={() => setConfirmDrop(true)}
            disabled={disabled || col.pk} title={col.pk ? "primary key column" : "drop column"}>
            <Icon name="close-outline" />
          </button>
        </>
      )}
    </div>
  );
}
