// ============================================================================
// useVersion: fetch GET /api/version once for the Help menu (bug report
// prefill, Copy diagnostics, About). Resilient: a server without the endpoint,
// or one that cannot be reached, yields null and the Help actions still work
// without the version fields.
// ============================================================================
import { useEffect, useState } from "react";
import { parseVersionInfo, type VersionInfo } from "../lib/help";

export function useVersion(): VersionInfo | null {
  const [info, setInfo] = useState<VersionInfo | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetch("/api/version");
        if (!res.ok) return;
        const parsed = parseVersionInfo(await res.json());
        if (alive) setInfo(parsed);
      } catch {
        /* unreachable or not JSON: stay null */
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  return info;
}
