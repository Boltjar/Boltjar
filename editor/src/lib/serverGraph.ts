// ============================================================================
// serverGraph: what the server's answer for a saved graph means to the editor.
//
// Only a 404 (no saved graph and no example under that slug) is a new graph
// that opens on an empty canvas. Any other failure is a graph that may exist
// but cannot be shown: a 422 for one this Boltjar cannot read (saved by a newer
// Boltjar), a server error, or no answer at all. The editor reports those and
// leaves the slug closed, so a Save never writes an empty canvas over a file
// it could not read.
// ============================================================================
import type { Graph } from "../types/protocol";

/** A slug's saved graph, the news that there is none, or why it cannot open. */
export type ServerGraph =
  | { kind: "graph"; graph: Graph }
  | { kind: "missing" }
  | { kind: "unreadable"; error: string };

type Get = (url: string) => Promise<Response>;

/** GET /api/graphs/<slug>, read as a ServerGraph. Never throws. */
export async function fetchServerGraph(slug: string, get: Get = (url) => fetch(url)): Promise<ServerGraph> {
  let res: Response;
  try {
    res = await get(`/api/graphs/${encodeURIComponent(slug)}`);
  } catch {
    return { kind: "unreadable", error: "the server did not answer" };
  }
  if (res.status === 404) return { kind: "missing" };
  if (!res.ok) return { kind: "unreadable", error: await serverError(res) };
  try {
    return { kind: "graph", graph: (await res.json()) as Graph };
  } catch {
    return { kind: "unreadable", error: "the server's answer is not a graph" };
  }
}

/** GET /api/graphs: every slug the server can load (the saved graphs and the
 *  shipped examples), or null when the list cannot be read. Never throws. */
export async function fetchSavedSlugs(get: Get = (url) => fetch(url)): Promise<Set<string> | null> {
  try {
    const res = await get("/api/graphs");
    if (!res.ok) return null;
    const body = (await res.json()) as { graphs?: unknown };
    if (!Array.isArray(body.graphs)) return null;
    return new Set(body.graphs.filter((s): s is string => typeof s === "string"));
  } catch {
    return null;
  }
}

/** The reason a failed response gives: the server's own `error` when it sent
 *  one (a graph from a newer Boltjar, a save it refused), else the status. */
export async function serverError(res: Response): Promise<string> {
  try {
    const body: unknown = await res.json();
    if (body && typeof body === "object" && typeof (body as { error?: unknown }).error === "string") {
      return (body as { error: string }).error;
    }
  } catch { /* not JSON: fall back to the status */ }
  return `the server answered ${res.status}`;
}

/** The console line for a graph the editor did not open. */
export function unreadableNotice(slug: string, error: string): string {
  return `did not open ${slug}: ${error}`;
}
