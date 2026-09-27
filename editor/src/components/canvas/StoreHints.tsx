// ============================================================================
// StoreHints: a row of clickable chips under the DB `table` / KV `key` field
// showing the REAL tables (or keys) in the connected store. Click a chip to
// drop its name into the field. The field still accepts {tag} / {{secret}}
// templates by hand; the chips are discovery, not a replacement. Fetches the
// same /api/store/{kind}/{key}/info endpoint the source-node body uses.
// ============================================================================
import { useEffect, useState } from "react";

interface StoreHintsProps {
  kind: "db" | "kv";
  /** The connected store key (resolved by storeKeyForInput); null = unwired. */
  storeKey: string | null;
  /** Drop a picked table/key name into the field. */
  onPick: (name: string) => void;
}

export function StoreHints({ kind, storeKey, onPick }: StoreHintsProps) {
  const [names, setNames] = useState<string[]>([]);

  useEffect(() => {
    if (!storeKey) {
      setNames([]);
      return;
    }
    let cancelled = false;
    fetch(`/api/store/${kind}/${encodeURIComponent(storeKey)}/info`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (cancelled || !data) return;
        const list: string[] = kind === "db"
          ? (data.tables ?? []).map((t: { name: string }) => t.name)
          : (data.sample ?? []);
        setNames(list);
      })
      .catch(() => { if (!cancelled) setNames([]); });
    return () => { cancelled = true; };
  }, [kind, storeKey]);

  if (!storeKey) {
    return (
      <div className="store-hints store-hints--unwired">
        wire a {kind === "db" ? "database" : "kv store"} into the {kind} port
      </div>
    );
  }
  if (names.length === 0) return null;

  return (
    <div className="store-hints">
      <span className="store-hints-lbl">{kind === "db" ? "tables" : "keys"}</span>
      {names.map((n) => (
        <button
          key={n}
          type="button"
          className="store-hint nodrag"
          title={`Use ${n}`}
          onClick={() => onPick(n)}
        >
          {n}
        </button>
      ))}
    </div>
  );
}
