// ============================================================================
// viewport: how the canvas frames a graph it opens. The canvas keeps one React
// Flow instance across tabs, so without this a graph would open at whatever
// zoom and pan the previous one was left at (25% after a large graph, and the
// first node added to a new workflow would be tiny). The first time a graph is
// opened in a session, one with nodes is fitted to the canvas, never above
// OPEN_FIT.maxZoom (100%), and an empty one opens at 100% with the flow origin
// at the canvas's top-left. A graph opened again (its tab clicked, its draft
// loaded back) comes back where it was left. Pure, so the node tests drive it.
// ============================================================================

export interface Viewport {
  x: number;
  y: number;
  zoom: number;
}

/** The fit a graph gets the first time it opens: never above 100%, so a graph
 *  of one or two nodes opens at its real size. */
export const OPEN_FIT = { padding: 0.22, maxZoom: 1 } as const;

/** The canvas's Fit graph button: the same frame, allowed up to 110%. */
export const FIT_VIEW = { padding: 0.22, maxZoom: 1.1 } as const;

/** An empty graph's view: 100% zoom at the flow origin. */
export const EMPTY_VIEW: Viewport = { x: 0, y: 0, zoom: 1 };

export type OpeningView =
  | { kind: "fit"; options: typeof OPEN_FIT }
  | { kind: "set"; viewport: Viewport };

/** How a graph with `nodeCount` nodes is framed when the canvas opens it:
 *  where it was left (`remembered`), else fitted, else (empty) at 100%. */
export function openingView(nodeCount: number, remembered?: Viewport | null): OpeningView {
  if (remembered) return { kind: "set", viewport: { ...remembered } };
  return nodeCount > 0 ? { kind: "fit", options: OPEN_FIT } : { kind: "set", viewport: { ...EMPTY_VIEW } };
}

/** Each graph's view, by slug, for the session: `leave` records where a graph
 *  was left as the canvas moves to another, `open` frames the one it opens. */
export interface ViewMemory {
  leave(slug: string, viewport: Viewport): void;
  open(slug: string, nodeCount: number): OpeningView;
}

export function viewMemory(): ViewMemory {
  const views = new Map<string, Viewport>();
  return {
    leave(slug, viewport) {
      views.set(slug, { x: viewport.x, y: viewport.y, zoom: viewport.zoom });
    },
    open(slug, nodeCount) {
      return openingView(nodeCount, views.get(slug));
    },
  };
}
