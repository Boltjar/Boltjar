// ============================================================================
// viewport: how the canvas frames a graph it opens. The canvas keeps one React
// Flow instance across tabs, so without this a graph would open at whatever
// zoom and pan the previous one was left at (25% after a large graph, and the
// first node added to a new workflow would be tiny). A graph with nodes is
// fitted to the canvas, never above FIT_VIEW.maxZoom; an empty one opens at
// 100% with the flow origin at the canvas's top-left. Pure, so the node tests
// drive it.
// ============================================================================

/** The fit the canvas uses on open and for its Fit graph button. */
export const FIT_VIEW = { padding: 0.22, maxZoom: 1.1 } as const;

/** An empty graph's view: 100% zoom at the flow origin. */
export const EMPTY_VIEW = { x: 0, y: 0, zoom: 1 } as const;

export type OpeningView =
  | { kind: "fit"; options: typeof FIT_VIEW }
  | { kind: "set"; viewport: typeof EMPTY_VIEW };

/** How a graph with `nodeCount` nodes is framed when the canvas opens it. */
export function openingView(nodeCount: number): OpeningView {
  return nodeCount > 0 ? { kind: "fit", options: FIT_VIEW } : { kind: "set", viewport: EMPTY_VIEW };
}
