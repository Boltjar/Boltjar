// ============================================================================
// StoreSelect: the store-aware dropdown for a widget that declares
// `options_from` ("db.tables" -> the wired db's tables, "kv.keys" -> the wired
// kv's keys). It renders the shared `Select` (dark popup, the one dropdown
// template). The list is read when the store is wired, again every time the
// dropdown opens, and again when the schema editor changes the same store, so a
// table created later (a DB exec at run time, the schema editor) shows up.
// A saved value the list lacks stays the selection, marked "not found"; it is
// never cleared. The text box is only for a kv "new key" or a {tag} value, and
// its back button returns to the list with the value kept. The decisions live
// in lib/storeSelect.ts. When no store is wired it shows a disabled hint.
// ============================================================================
import { useCallback, useEffect, useRef, useState } from "react";
import { Icon } from "../../lib/icons";
import { onStoreChanged } from "../../lib/storeEvents";
import {
  isMissing,
  refreshesOn,
  storeListFrom,
  storeSelectMode,
  type StoreKind,
  type StoreList,
} from "../../lib/storeSelect";
import { Select } from "./Select";

interface StoreSelectProps {
  /** the widget's options_from, e.g. "db.tables" or "kv.keys". */
  source: string;
  /** the connected store key (resolved by storeKeyForInput); null = unwired. */
  storeKey: string | null;
  value: string;
  placeholder?: string;
  onChange: (v: string) => void;
}

export function StoreSelect({ source, storeKey, value, placeholder, onChange }: StoreSelectProps) {
  const [kind, listName] = source.split(".") as [StoreKind, string];
  // null until the first read of this store lands: nothing is "not found" before.
  const [list, setList] = useState<StoreList | null>(null);
  // what the picker's own controls chose: "new key" -> true, back -> false,
  // null while neither has been used (the value decides).
  const [typingOverride, setTypingOverride] = useState<boolean | null>(null);
  // only the latest read may land, so a slow early read never overwrites a newer one
  const readSeq = useRef(0);

  const refresh = useCallback(() => {
    if (!storeKey) return;
    const seq = ++readSeq.current;
    fetch(`/api/store/${kind}/${encodeURIComponent(storeKey)}/info`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (seq === readSeq.current && data) setList(storeListFrom(listName, data));
      })
      // a failed read says nothing about the value: keep the last list
      .catch(() => {});
  }, [kind, listName, storeKey]);

  // a different store (or none): forget the old list and read the new one.
  useEffect(() => {
    setList(null);
    refresh();
    return () => { readSeq.current += 1; };   // drop a read still in flight
  }, [refresh]);

  // the schema editor changed this store: read the list again.
  useEffect(
    () => onStoreChanged((changedKind, changedKey) => {
      if (refreshesOn({ kind, key: storeKey }, { kind: changedKind, key: changedKey })) refresh();
    }),
    [kind, storeKey, refresh],
  );

  if (!storeKey) {
    return (
      <div className="store-select store-select--unwired">
        wire a {kind === "db" ? "database" : "kv store"} into the {kind} port
      </div>
    );
  }

  // type mode: a {tag} value, or the user chose "new key". A text input with a
  // back-to-the-list button that keeps whatever was typed.
  if (storeSelectMode(value, typingOverride) === "typing") {
    return (
      <span className="store-select typing nodrag">
        <input
          type="text"
          autoFocus={typingOverride === true}
          value={value}
          placeholder={placeholder}
          spellCheck={false}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => e.stopPropagation()}
        />
        <button
          type="button"
          className="store-select-back"
          title="back to the list"
          onClick={() => setTypingOverride(false)}
        >
          <Icon name="chevron-expand-outline" />
        </button>
      </span>
    );
  }

  return (
    <Select
      value={value}
      options={list?.names ?? []}
      placeholder={placeholder}
      onChange={onChange}
      onOpen={refresh}
      missing={isMissing(value, list)}
      // tables are created/managed in the schema editor, so the table picker is
      // pick-only. kv keys can be created on the fly (a `set`), so keys allow it.
      onNew={listName === "keys" ? () => setTypingOverride(true) : undefined}
      newLabel="new key"
    />
  );
}
