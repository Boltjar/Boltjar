// ============================================================================
// The store picker's decisions (components/canvas/StoreSelect.tsx), pure so they
// are testable without React. The picker lists a wired store's tables (db) or
// keys (kv) in the shared Select; these helpers decide:
//   - what the store's info payload lists, and whether that list is the whole store
//   - which mode the picker is in: the list, or a text box
//   - whether the saved value is missing from a list that has loaded
//   - which store change makes a picker read its list again
// A saved value is what the graph runs with, so nothing here ever clears it. A
// value the list lacks stays the selection and is marked "not found"; the list
// is read again every time the dropdown opens and whenever the schema editor
// changes the same store, so a table created later shows up without a remount.
// ============================================================================

export type StoreKind = "db" | "kv";
export type StoreSelectMode = "list" | "typing";

/** The names a store lists, and whether they are ALL of the store's names. */
export interface StoreList {
  names: string[];
  /** false when the payload is a preview (or unreadable): a name it lacks may
   *  still exist, so it is never called "not found". */
  complete: boolean;
}

/** The tables or keys in a GET /api/store/{kind}/{key}/info payload. The db
 *  payload lists every table; the kv payload lists a sorted preview (`sample`)
 *  of its `count` keys, so a kv list is complete only when the preview holds
 *  every key. `listName` is the second half of the widget's options_from. */
export function storeListFrom(listName: string, data: unknown): StoreList {
  const d = (data && typeof data === "object" ? data : {}) as Record<string, unknown>;
  if (listName === "tables") {
    if (!Array.isArray(d.tables)) return { names: [], complete: false };
    const names = d.tables
      .map((t) => (t && typeof t === "object" ? (t as { name?: unknown }).name : undefined))
      .filter((n): n is string => typeof n === "string");
    return { names, complete: true };
  }
  if (Array.isArray(d.keys)) {
    return { names: d.keys.filter((k): k is string => typeof k === "string"), complete: true };
  }
  if (!Array.isArray(d.sample)) return { names: [], complete: false };
  const names = d.sample.filter((k): k is string => typeof k === "string");
  return { names, complete: typeof d.count === "number" && d.count <= names.length };
}

// A {tag} (the same grammar as a Template's tags) or a {{secret.NAME}} token.
const TEMPLATE_REF = /\{[A-Za-z_]\w*\}|\{\{\s*secret\.[A-Za-z0-9_]+\s*\}\}/;

/** Whether the value is resolved at run time ({tag} or {{secret.NAME}}), so it
 *  is text to edit, never a name to look up in the list. */
export function hasTemplateRef(value: string): boolean {
  return TEMPLATE_REF.test(value);
}

/** Which mode the picker shows. `override` is what the picker's own controls
 *  chose: true after "new key", false after the back button, null while neither
 *  has been used (then a {tag} value opens as text and anything else as the
 *  list). A value the list lacks is NOT a reason to leave the list. */
export function storeSelectMode(value: string, override: boolean | null): StoreSelectMode {
  if (override !== null) return override ? "typing" : "list";
  return hasTemplateRef(value) ? "typing" : "list";
}

/** Whether the saved value is known to be absent: the list has loaded (`list`
 *  is null until then), it is the whole store, and it lacks the value. An empty
 *  value or a {tag} value is never missing. */
export function isMissing(value: string, list: StoreList | null): boolean {
  if (value === "" || list === null || !list.complete) return false;
  if (hasTemplateRef(value)) return false;
  return !list.names.includes(value);
}

/** Whether a change to one store is a change to the store a picker lists from.
 *  The same key names a db and a kv store independently; an unwired picker
 *  (key null) listens to nothing. */
export function refreshesOn(
  watch: { kind: StoreKind; key: string | null },
  change: { kind: StoreKind; key: string },
): boolean {
  return watch.key !== null && watch.kind === change.kind && watch.key === change.key;
}
