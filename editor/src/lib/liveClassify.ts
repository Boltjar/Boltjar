// ============================================================================
// liveClassify: the pure draft-vs-live rules for the editor's live surfaces.
// Framework-free + side-effect-free so they are trivially unit-testable (see
// liveClassify.test.mjs) and shared by EVERY consumer, so the classification is
// declared once, never re-derived per component.
//
// The product rule: while a graph is powered ON, only nodes/wires that are
// part of the RUNNING graph (the server-authoritative live set) may show or pulse
// live data. A structural edit made since the last Save & Restart (a new Preview,
// a new wire) is draft-only and must NOT appear live; it shows a clear
// "Save & Restart to tap" affordance instead. When power is OFF nothing is live,
// so the last-captured values stay visible (unchanged behaviour).
// ============================================================================

export type Power = "on" | "off";

/** The canonical edge key, IDENTICAL to graphAdapter's `edgeId`:
 *  `src:src_port->dst:dst_port`. The server sends live edges as tuples; the editor
 *  tests each drawn wire's id against the live set with this exact format. */
export function liveEdgeKey(src: string, srcPort: string, dst: string, dstPort: string): string {
  return `${src}:${srcPort}->${dst}:${dstPort}`;
}

/** Is this wire/edge part of the running graph? Pure set membership. */
export function isEdgeLive(liveEdges: ReadonlySet<string>, edgeId: string): boolean {
  return liveEdges.has(edgeId);
}

/**
 * The one gate every live surface shares. Returns true when live data MAY flow
 * through this wire/node right now:
 *   - power off  -> true  (show the last-captured values; nothing is running)
 *   - power on   -> only when the wire is in the live set (a live edge)
 * A draft-only wire while power is on returns false (suppress: "Save & Restart").
 */
export function liveGatePasses(power: Power, live: boolean): boolean {
  return power !== "on" || live;
}
