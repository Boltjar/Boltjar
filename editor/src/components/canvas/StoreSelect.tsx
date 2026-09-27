// ============================================================================
// StoreSelect: the store-aware dropdown for a widget that declares
// `options_from` ("db.tables" -> the wired db's tables, "kv.keys" -> the wired
// kv's keys). It renders the shared `Select` (dark popup, the one dropdown
// template), with a "new" action that flips to a one-off text input for a value
// that does not exist yet (a `set` on an empty kv) or a custom / {tag} value.
// When no store is wired it shows a disabled hint.
// ============================================================================
import { useEffect, useState } from "react";
import { Icon } from "../../lib/icons";
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
  const [kind, listName] = source.split(".") as ["db" | "kv", string];
  const [names, setNames] = useState<string[]>([]);
  const [typing, setTyping] = useState(false);

  useEffect(() => {
    if (!storeKey) { setNames([]); return; }
    let cancelled = false;
    fetch(`/api/store/${kind}/${encodeURIComponent(storeKey)}/info`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (cancelled || !data) return;
        const list: string[] = listName === "tables"
          ? (data.tables ?? []).map((t: { name: string }) => t.name)
          : (data.sample ?? data.keys ?? []);
        setNames(list);
      })
      .catch(() => { if (!cancelled) setNames([]); });
    return () => { cancelled = true; };
  }, [kind, listName, storeKey]);

  if (!storeKey) {
    return (
      <div className="store-select store-select--unwired">
        wire a {kind === "db" ? "database" : "kv store"} into the {kind} port
      </div>
    );
  }

  // type mode: a custom / {tag} value, or the user chose "new". A one-off text
  // input with a back-to-the-list affordance.
  if (typing || (value !== "" && !names.includes(value))) {
    return (
      <span className="store-select typing nodrag">
        <input
          type="text"
          autoFocus={typing}
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
          onClick={() => { onChange(""); setTyping(false); }}
        >
          <Icon name="chevron-expand-outline" />
        </button>
      </span>
    );
  }

  return (
    <Select
      value={value}
      options={names}
      placeholder={placeholder}
      onChange={onChange}
      // tables are created/managed in the schema editor, so the table picker is
      // pick-only. kv keys can be created on the fly (a `set`), so keys allow it.
      onNew={listName === "keys" ? () => setTyping(true) : undefined}
      newLabel="new key"
    />
  );
}
