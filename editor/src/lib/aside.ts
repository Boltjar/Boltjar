// ============================================================================
// A second copy of a workflow held in this browser beside the one on the
// canvas, and the words for the choice between them.
//
// It happens when a draft in this browser is older than the saved file (the
// file was saved since, in another browser or by another tool). The canvas
// opens the saved file and the other copy is kept, never dropped unasked. The
// choice is always a swap: showing the other copy puts the canvas copy aside
// in its place, so every answer but Delete can be taken back with one click.
//
// Stored in localStorage under `boltjar:aside:<slug>` as {role, graph, shown}.
// `role` says what the kept copy IS, so the buttons can say it plainly:
//   older   an older copy this browser held (a draft from before versions
//           were tracked: whether it held edits cannot be known)
//   edits   unsaved changes made here on a copy that was then saved over
//   saved   the saved file, set aside while one of the others is shown
// `shown` (only with `saved`) says which of the other two is on the canvas.
// An entry written before roles existed (a bare graph) reads as `older`.
// ============================================================================
import type { Graph } from "../types/protocol";

export type CopyRole = "older" | "edits";
export type AsideRole = CopyRole | "saved";

export interface Aside {
  role: AsideRole;
  graph: Graph;
  /** with role `saved`: what the canvas shows instead */
  shown?: CopyRole;
}

function isGraph(g: unknown): g is Graph {
  return !!g && typeof g === "object" && Array.isArray((g as Graph).nodes);
}

export function parseAside(raw: string | null): Aside | null {
  if (!raw) return null;
  let data: unknown;
  try { data = JSON.parse(raw); } catch { return null; }
  if (isGraph(data)) return { role: "older", graph: data };
  if (!data || typeof data !== "object") return null;
  const { role, graph, shown } = data as { role?: unknown; graph?: unknown; shown?: unknown };
  if (!isGraph(graph)) return null;
  if (role === "older" || role === "edits") return { role, graph };
  if (role === "saved") return { role, graph, shown: shown === "older" ? "older" : "edits" };
  return null;
}

export function serializeAside(a: Aside): string {
  return JSON.stringify(a.role === "saved" ? { role: a.role, graph: a.graph, shown: a.shown ?? "edits" } : { role: a.role, graph: a.graph });
}

/** The record after a swap: what was on the canvas goes aside, what was aside
 *  goes on the canvas. `canvas` is the graph leaving the canvas. */
export function swapped(kept: Aside, canvas: Graph): Aside {
  return kept.role === "saved"
    ? { role: kept.shown ?? "edits", graph: canvas }
    : { role: "saved", graph: canvas, shown: kept.role };
}

function nodes(n: number): string {
  return `${n} node${n === 1 ? "" : "s"}`;
}

export interface AsideWords {
  message: string;
  /** shows the kept copy, putting the canvas copy aside in its place */
  swap: string;
  /** ends the choice. With a copy aside: deletes that copy. With the saved
   *  file aside: keeps the canvas as unsaved changes (the file stays on disk). */
  end: string;
}

/** The console line and its two buttons. Both copies are named by what they
 *  are and their size, so neither button can be read as the other. */
export function asideWords(slug: string, kept: Aside, canvasNodes: number): AsideWords {
  const keptNodes = kept.graph.nodes.length;
  if (kept.role === "saved") {
    const what = kept.shown === "older" ? "the older copy" : "your unsaved changes";
    return {
      message: `${slug}: showing ${what} (${nodes(canvasNodes)}), not the saved file (${nodes(keptNodes)})`,
      swap: `Back to the saved file (${nodes(keptNodes)})`,
      end: `Keep ${what === "the older copy" ? "the older copy" : "my changes"} (unsaved)`,
    };
  }
  if (kept.role === "older") {
    return {
      message: `${slug}: showing the saved file (${nodes(canvasNodes)}); this browser also had an older copy (${nodes(keptNodes)})`,
      swap: `Show the older copy (${nodes(keptNodes)})`,
      end: "Delete the older copy",
    };
  }
  return {
    message: `${slug} was saved elsewhere: showing the saved file (${nodes(canvasNodes)}); your unsaved changes (${nodes(keptNodes)}) are kept`,
    swap: `Show my unsaved changes (${nodes(keptNodes)})`,
    end: "Delete my unsaved changes",
  };
}
