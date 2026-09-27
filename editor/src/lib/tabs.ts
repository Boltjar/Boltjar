// ============================================================================
// tabs: the workflow tab strip's state and where an opened tab's graph comes
// from. A tab is a graph slug. New workflow and Clone mint a slug that no open
// tab, local draft or server graph uses, so the tab holds a graph the server
// has never seen until its first Save. It is marked unsaved: opening it reads
// the local draft or starts empty and never asks the server, which would only
// answer 404. Every other tab loads its draft when the draft has nodes, else
// the server's copy. The strip, marks included, is kept in localStorage so a
// refresh reopens it. Pure, so the node tests drive it.
// ============================================================================

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

/** Where an opened tab's graph comes from: its local draft when that has
 *  nodes, an empty canvas for a graph never saved, else the server. */
export type GraphSource = "draft" | "empty" | "server";

export function graphSource(state: TabsState, slug: string, draft: { nodes?: unknown } | null): GraphSource {
  if (draft && Array.isArray(draft.nodes) && draft.nodes.length > 0) return "draft";
  return state.unsaved.includes(slug) ? "empty" : "server";
}
