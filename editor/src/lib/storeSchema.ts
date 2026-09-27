// ============================================================================
// storeSchema: the tables a store node declares with the graph. A node keeps
// its declaration in a hidden widget of kind "schema" (the Database node's
// `schema`), in the shape the schema endpoints return minus the row counts:
// [{name, columns: [{name, type, pk}]}]. The schema editor writes it after each
// change it makes and when it reads what the database holds (adoptLiveSchema),
// and the editor asks the server to create what a graph declares (POST
// /api/stores/ensure) when the graph opens and whenever a declaration changes
// (a pasted, duplicated or restored node). Found by widget kind, never by node
// id. Its only imports are types, so the tests load it as is.
// ============================================================================
import type { DbTable, Graph, NodeDef, Widget } from "../types/protocol";

export interface DeclaredColumn {
  name: string;
  type: string;
  pk: boolean;
}

export interface DeclaredTable {
  name: string;
  columns: DeclaredColumn[];
}

/** What POST /api/stores/ensure answers: per store, what it created or added;
 *  per declared column the database cannot match, a warning naming the node. */
export interface EnsureResult {
  stores: Array<{ node: string; key: string; created: string[]; added: string[] }>;
  warnings: Array<{ node: string; message: string }>;
}

/** The widget a node keeps its declared store schema in, if it declares one. */
export function schemaWidget(def: NodeDef | undefined): Widget | undefined {
  return def?.widgets.find((w) => w.kind === "schema");
}

/** A live schema as a declaration: the tables and their columns, in order,
 *  without the row counts (rows are data, a declaration is structure). */
export function declaredSchema(tables: DbTable[]): DeclaredTable[] {
  return tables.map((t) => ({
    name: t.name,
    columns: t.columns.map((c) => ({ name: c.name, type: c.type, pk: !!c.pk })),
  }));
}

/** Whether a saved declaration already says what `next` says (so writing
 *  `next` would change nothing and must not mark the graph edited). */
export function sameDeclaration(saved: unknown, next: DeclaredTable[]): boolean {
  const current = Array.isArray(saved) ? saved : [];
  return JSON.stringify(current) === JSON.stringify(next);
}

/** A saved declaration as tables, whatever the graph file holds: entries that
 *  are not a named table, and columns that are not a named column, are left out. */
function savedTables(saved: unknown): DeclaredTable[] {
  if (!Array.isArray(saved)) return [];
  const out: DeclaredTable[] = [];
  for (const t of saved) {
    if (!t || typeof t !== "object" || typeof t.name !== "string" || !t.name.trim()) continue;
    const columns: DeclaredColumn[] = [];
    for (const c of Array.isArray(t.columns) ? t.columns : []) {
      if (!c || typeof c !== "object" || typeof c.name !== "string" || !c.name.trim()) continue;
      columns.push({ name: c.name, type: String(c.type ?? ""), pk: !!c.pk });
    }
    out.push({ name: t.name, columns });
  }
  return out;
}

/** The declaration to write when the schema editor reads the live schema, or
 *  null when the saved one already says it. The database as it is wins: each
 *  live table with its live columns, in order. What the saved declaration adds
 *  is kept after them (a declared column or table the database lacks), because
 *  the server creates what a graph declares and a read that lands before it
 *  has must not drop it. Names match ignoring case, as SQLite matches them.
 *  So a graph whose tables were made another way (before tables were declared,
 *  or by SQL in a DB node) comes to carry them, and a declaration an undo took
 *  back is written again. */
export function adoptLiveSchema(saved: unknown, live: DbTable[]): DeclaredTable[] | null {
  const next = declaredSchema(live);
  const byName = new Map(next.map((t) => [t.name.toLowerCase(), t]));
  for (const t of savedTables(saved)) {
    const table = byName.get(t.name.toLowerCase());
    if (!table) {
      next.push(t);
      byName.set(t.name.toLowerCase(), t);
      continue;
    }
    const have = new Set(table.columns.map((c) => c.name.toLowerCase()));
    for (const c of t.columns) {
      if (have.has(c.name.toLowerCase())) continue;
      table.columns.push(c);
      have.add(c.name.toLowerCase());
    }
  }
  return sameDeclaration(saved, next) ? null : next;
}

/** One string that changes whenever a node that declares a store schema is
 *  added, removed, renamed or reconfigured; "" when no node declares one. */
export function declarationSignature(
  nodes: ReadonlyArray<{ id: string; data: { typeId: string; config: Record<string, unknown> } }>,
  defs: ReadonlyMap<string, NodeDef>,
): string {
  const parts: Array<[string, Record<string, unknown>]> = [];
  for (const n of nodes) {
    const w = schemaWidget(defs.get(n.data.typeId));
    const declared = w ? n.data.config?.[w.name] : undefined;
    if (Array.isArray(declared) && declared.length > 0) parts.push([n.id, n.data.config]);
  }
  return parts.length ? JSON.stringify(parts) : "";
}

/** The stores whose tables or columns the server just created, so the table
 *  pickers wired to them read their lists again. */
export function changedStores(result: EnsureResult): string[] {
  return result.stores
    .filter((s) => s.created.length > 0 || s.added.length > 0)
    .map((s) => s.key);
}

/** One console line per declared column the database could not match. */
export function ensureNotices(result: EnsureResult): string[] {
  return result.warnings.map((w) => `${w.node}: ${w.message}`);
}

/** Ask the server to create what `graph` declares. Null when it could not. */
export async function ensureDeclaredStores(graph: Graph): Promise<EnsureResult | null> {
  try {
    const res = await fetch("/api/stores/ensure", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(graph),
    });
    if (!res.ok) return null;
    return (await res.json()) as EnsureResult;
  } catch {
    return null;
  }
}
