// useSecrets: fetch the secret NAMES (never values) from the backend, refresh
// on demand. Used by the {{secret.NAME}} autocomplete in HTTP / Template-style
// fields. The backend's /api/secrets returns [{name, source, hasValue}] and
// never carries a value, so the UI only ever shows names + masks.
import { useEffect, useState, useCallback } from "react";

interface SecretEntry {
  name: string;
  source: "env" | "user";
  hasValue: boolean;
}

export function useSecrets() {
  const [names, setNames] = useState<string[]>([]);

  const refetch = useCallback(async () => {
    try {
      const r = await fetch("/api/secrets");
      if (!r.ok) return;
      const data = await r.json() as { secrets: SecretEntry[] };
      setNames((data.secrets ?? []).map((s) => s.name));
    } catch {
      /* offline: keep what we had. */
    }
  }, []);

  useEffect(() => { refetch(); }, [refetch]);

  return { names, refetch };
}
