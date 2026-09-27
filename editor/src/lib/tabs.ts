// ============================================================================
// tabs: the workflow tab strip's state and where an opened tab's graph comes
// from. A tab is a graph slug. New workflow and Clone mint a slug that no open
// tab, local draft or server graph uses, so the tab holds a graph the server
// has never seen until its first Save. It is marked unsaved: opening it reads
// the local draft or starts empty and never asks the server, which would only
// answer 404. The strip, marks included, is kept in localStorage so a refresh
// reopens it. Pure, so the node tests drive it.
//
// Every other tab asks the server, and its local draft only wins when it holds
// edits made on top of the very copy the server still has (openPlan). Each
// browser keeps its own drafts, so a draft made before the graph was saved
// elsewhere is never shown in place of that save: the saved copy opens and the
// draft is offered back.
// ============================================================================
import type { Graph, GraphNode } from "../types/protocol";

export interface TabsState {
  /** the open tabs' slugs, in strip order. */
  open: string[];
  /** the tab on the canvas, null when none is open. */
  active: string | null;
  /** open tabs whose graph has never been saved to the server. */
  unsaved: string[];
}

/** The strip as saved in localStorage, or null when nothing readable is there.
 *  A strip saved before `unsaved` existed reads with no unsaved tabs. */
export function parseTabs(raw: string | null): TabsState | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<Record<keyof TabsState, unknown>>;
    const slugs = (v: unknown): string[] =>
      Array.isArray(v) ? v.filter((s): s is string => typeof s === "string" && s.length > 0) : [];
    const open = slugs(parsed.open);
    const active = typeof parsed.active === "string" && open.includes(parsed.active) ? parsed.active : (open[0] ?? null);
    const unsaved = slugs(parsed.unsaved).filter((s) => open.includes(s));
    return { open, active, unsaved };
  } catch {
    return null;
  }
}

/** `slug` open and active (added at the end when it was not open). A tab for a
 *  graph that exists only here (New workflow, Clone) is opened `unsaved`. */
export function withTabOpened(state: TabsState, slug: string, opts: { unsaved?: boolean } = {}): TabsState {
  const open = state.open.includes(slug) ? state.open : [...state.open, slug];
  const unsaved = opts.unsaved && !state.unsaved.includes(slug) ? [...state.unsaved, slug] : state.unsaved;
  if (open === state.open && unsaved === state.unsaved && state.active === slug) return state;
  return { open, active: slug, unsaved };
}

/** `slug` off the strip. When it was active, the tab before it (or after it,
 *  or none when the strip empties) becomes active. */
export function withTabClosed(state: TabsState, slug: string): TabsState {
  const idx = state.open.indexOf(slug);
  if (idx < 0) return state;
  const open = state.open.filter((s) => s !== slug);
  const active = state.active === slug ? (open[idx - 1] ?? open[idx] ?? open[0] ?? null) : state.active;
  return { open, active, unsaved: state.unsaved.filter((s) => s !== slug) };
}

/** Tab `from` renamed to `to` in place (refused when `to` is already open). An
 *  unsaved mark moves with it until the renamed graph is saved. */
export function withTabRenamed(state: TabsState, from: string, to: string): TabsState {
  if (!state.open.includes(from) || state.open.includes(to)) return state;
  return {
    open: state.open.map((s) => (s === from ? to : s)),
    active: state.active === from ? to : state.active,
    unsaved: state.unsaved.map((s) => (s === from ? to : s)),
  };
}

/** The server now holds `slug`'s graph: it loads like any saved graph again. */
export function withTabSaved(state: TabsState, slug: string): TabsState {
  return state.unsaved.includes(slug) ? { ...state, unsaved: state.unsaved.filter((s) => s !== slug) } : state;
}

/** `base`, else `base-2`, `base-3`...: the first slug not in `taken`. */
export function freeSlug(base: string, taken: ReadonlySet<string>): string {
  let slug = base;
  for (let n = 2; taken.has(slug); n += 1) slug = `${base}-${n}`;
  return slug;
}

/** Where an opened tab's graph comes from before anything is asked: a graph
 *  never saved opens its local draft when that has nodes, else empty, and is
 *  never fetched; any other tab asks the server (openPlan decides then). */
export type GraphSource = "draft" | "empty" | "server";

export function graphSource(state: TabsState, slug: string, draft: { nodes?: unknown } | null): GraphSource {
  if (!state.unsaved.includes(slug)) return "server";
  return hasNodes(draft) ? "draft" : "empty";
}

function hasNodes(g: { nodes?: unknown } | null | undefined): boolean {
  return !!g && Array.isArray(g.nodes) && g.nodes.length > 0;
}

/** A tab's local draft: the canvas as it was left, the version of the saved
 *  copy it started from (null when there was none) and whether the person
 *  changed anything since that copy was loaded or saved. A draft written before
 *  drafts carried these reads with both unknown (undefined). */
export interface StoredDraft {
  graph: Graph;
  base: string | null | undefined;
  dirty: boolean | undefined;
}

/** What is stored for a draft. */
export function draftRecord(graph: Graph, base: string | null, dirty: boolean): string {
  return JSON.stringify({ graph, base, dirty });
}

/** A stored draft read back, or null when nothing readable is there. An old
 *  draft (the bare graph) reads with its base and dirty unknown. */
export function parseDraft(raw: string | null): StoredDraft | null {
  if (!raw) return null;
  let parsed: unknown;
  try { parsed = JSON.parse(raw); } catch { return null; }
  if (!parsed || typeof parsed !== "object") return null;
  const rec = parsed as { graph?: unknown; base?: unknown; dirty?: unknown; nodes?: unknown };
  if (rec.graph && typeof rec.graph === "object" && Array.isArray((rec.graph as { nodes?: unknown }).nodes)) {
    return {
      graph: rec.graph as Graph,
      base: typeof rec.base === "string" ? rec.base : null,
      dirty: rec.dirty === true,
    };
  }
  if (Array.isArray(rec.nodes)) return { graph: parsed as Graph, base: undefined, dirty: undefined };
  return null;
}

/** What opening a tab shows, once the server has answered:
 *   - "draft": the local draft, marked unsaved (edits on the saved copy the
 *     server still holds, or a graph the server has never held);
 *   - "empty": an empty canvas (nothing saved, nothing drafted);
 *   - "server": the saved copy (no draft, or one with no edits, or an old one
 *     that holds the same graph);
 *   - "server-offer": the saved copy, with the draft kept aside and offered
 *     back: it holds edits made on an older saved copy (the graph was saved
 *     since, in another browser or by another tool), or it is an old draft that
 *     differs from the saved copy and cannot say which it started from.
 *  `server` is null when the server has no such graph. `sameAsSaved` tells
 *  whether a graph matches the saved copy (only asked for an old draft). */
export type OpenPlan = "draft" | "empty" | "server" | "server-offer";

export function openPlan(
  draft: StoredDraft | null,
  server: { version: string | null } | null,
  sameAsSaved: (g: Graph) => boolean,
): OpenPlan {
  if (!server) return hasNodes(draft?.graph) ? "draft" : "empty";
  if (!draft) return "server";
  if (draft.dirty === undefined) {
    // an old draft: an empty one never hid the saved copy, nor does one that matches it
    if (!hasNodes(draft.graph) || sameAsSaved(draft.graph)) return "server";
    return "server-offer";
  }
  if (!draft.dirty) return "server";
  if (draft.base !== null && draft.base !== undefined && draft.base === server.version) return "draft";
  return "server-offer";
}

/** Whether two graphs hold the same workflow: the same nodes (type, config,
 *  position, size, disabled), wires and groups, whatever their order or name.
 *  `sizeOf` gives the size a node shows at (a node saved without one takes
 *  its default), so a graph read from a file compares with one the canvas
 *  wrote; without it a size counts only when both sides have one. */
export function sameGraph(
  a: Graph,
  b: Graph,
  sizeOf?: (n: GraphNode) => [number, number] | null,
): boolean {
  return canonical(a, b, sizeOf) === canonical(b, a, sizeOf);
}

function canonical(g: Graph, other: Graph, sizeOf?: (n: GraphNode) => [number, number] | null): string {
  const otherSized = new Set((other.nodes ?? []).filter((n) => n.size).map((n) => n.id));
  const nodes = [...(g.nodes ?? [])]
    .map((n) => {
      const size = sizeOf ? sizeOf(n) : (n.size && otherSized.has(n.id) ? n.size : null);
      return {
        id: n.id,
        type: n.type,
        config: n.config ?? {},
        pos: (n.pos ?? [0, 0]).map((v) => Math.round(v)),
        size: size ? size.map((v) => Math.round(v)) : null,
        disabled: !!n.disabled,
      };
    })
    .sort((x, y) => (x.id < y.id ? -1 : x.id > y.id ? 1 : 0));
  const edges = (g.edges ?? []).map((e) => JSON.stringify([e.src, e.src_port, e.dst, e.dst_port])).sort();
  const groups = [...(g.groups ?? [])]
    .map((x) => ({ ...x, members: [...x.members].sort() }))
    .sort((x, y) => (x.id < y.id ? -1 : x.id > y.id ? 1 : 0));
  return stable({ nodes, edges, groups });
}

/** JSON with every object's keys sorted, so key order never counts. */
function stable(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(stable).join(",")}]`;
  if (v && typeof v === "object") {
    const o = v as Record<string, unknown>;
    return `{${Object.keys(o).filter((k) => o[k] !== undefined).sort().map((k) => `${JSON.stringify(k)}:${stable(o[k])}`).join(",")}}`;
  }
  return JSON.stringify(v) ?? "null";
}
