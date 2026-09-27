// ============================================================================
// TypedEdge: a typed bezier wire. Coloured by the SOURCE
// port's data type. Layers: a dark under-stroke (machined channel), the main
// type-coloured stroke, an accent selection glow, and the comets: each time
// THIS wire carries a value (a `carry` event names it, see lib/wirePulse), a
// comet slides from the source port to the target port, never because its
// source has other wires that did.
// ============================================================================
import { memo, useEffect, useRef, type CSSProperties } from "react";
import { getBezierPath, type EdgeProps } from "@xyflow/react";
import type { WFEdgeData } from "../../lib/graphAdapter";
import { typeColorVar } from "../../lib/types";
import { useEditor } from "../../lib/editorContext";
import { liveGatePasses } from "../../lib/liveClassify";
import { mountComets, onWirePulse } from "../../lib/wirePulse";

function TypedEdgeImpl({
  id,
  source,
  sourceHandleId,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  selected,
  data,
}: EdgeProps) {
  const { effectiveOutput, power, liveEdges } = useEditor();
  // a wire FROM a Wireless Out (real source elsewhere) or a passthrough's `out`
  // (Preview/Chat: type follows `in`) gets its colour + live pulse from the
  // effective source/type, not the stale declared edge type.
  const wl = effectiveOutput(source, sourceHandleId ?? "");

  // the control distance aims for k = max(48, |dx|*0.5). React Flow's bezier uses a
  // curvature factor; 0.35 over the default 0.25 keeps wires leaving ports perpendicular.
  const [path] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    curvature: 0.35,
  });

  const ed = data as WFEdgeData | undefined;
  const color = typeColorVar(wl ? wl.type : ed?.type);

  // a comet per carry on THIS wire, launched straight into the DOM (no React
  // render per carry); it lands and clears itself, so an idle wire shows its
  // normal look. While power is on, only a wire in the live set may pulse: a
  // draft wire drawn onto a still-live source must NOT appear to carry live data.
  const mayPulse = useRef(false);
  mayPulse.current = liveGatePasses(power, liveEdges.has(id));
  const mainRef = useRef<SVGPathElement>(null);
  const cometsRef = useRef<SVGGElement>(null);
  const colorRef = useRef(color);
  colorRef.current = color;
  useEffect(() => {
    const host = cometsRef.current;
    if (!host) return;
    const comets = mountComets(host, () => ({ path: mainRef.current, color: colorRef.current }));
    const off = onWirePulse(id, () => {
      if (mayPulse.current) comets.fire();
    });
    return () => {
      off();
      comets.dispose();
    };
  }, [id]);

  return (
    <g className="wf-edge-group">
      <path className="wf-edge-under" d={path} />
      {selected && <path className="wf-edge-glow" d={path} />}
      <path
        ref={mainRef}
        className="wf-edge-main"
        d={path}
        style={{ stroke: color } as CSSProperties}
      />
      {/* the comets' layer: filled only by mountComets, never by React */}
      <g ref={cometsRef} className="wf-edge-comets" />
      {/* invisible wide hit-path so the edge is easy to select */}
      <path
        d={path}
        fill="none"
        stroke="transparent"
        strokeWidth={14}
        className="react-flow__edge-interaction"
        id={id}
      />
    </g>
  );
}

export const TypedEdge = memo(TypedEdgeImpl);
