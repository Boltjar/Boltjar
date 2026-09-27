// ============================================================================
// TypedEdge: a typed bezier wire. Coloured by the SOURCE
// port's data type. Layers: a dark under-stroke (machined channel), the main
// type-coloured stroke, an accent selection glow, and a flow-pulse overlay that
// animates when THIS wire carries a value (a `carry` event names it, see
// lib/wirePulse), never because its source has other wires that did.
// ============================================================================
import { memo, useEffect, useRef, useState, type CSSProperties } from "react";
import { getBezierPath, type EdgeProps } from "@xyflow/react";
import type { WFEdgeData } from "../../lib/graphAdapter";
import { typeColorVar } from "../../lib/types";
import { useEditor } from "../../lib/editorContext";
import { liveGatePasses } from "../../lib/liveClassify";
import { onWirePulse } from "../../lib/wirePulse";

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

  // a clean ~1s flow pulse, fired ONLY when this wire carries a value; it then
  // clears itself, so the wire never animates perpetually and a pan never
  // resets it mid-stroke. While power is on, only a wire in the live set may
  // pulse: a draft wire drawn onto a still-live source must NOT appear to carry
  // live data.
  const mayPulse = useRef(false);
  mayPulse.current = liveGatePasses(power, liveEdges.has(id));
  const [active, setActive] = useState(false);
  useEffect(() => {
    let t = 0;
    const off = onWirePulse(id, () => {
      if (!mayPulse.current) return;
      setActive(true);
      window.clearTimeout(t);
      t = window.setTimeout(() => setActive(false), 1000);
    });
    return () => {
      off();
      window.clearTimeout(t);
    };
  }, [id]);

  return (
    <g className="wf-edge-group">
      <path className="wf-edge-under" d={path} />
      {selected && <path className="wf-edge-glow" d={path} />}
      <path
        className="wf-edge-main"
        d={path}
        style={{ stroke: color } as CSSProperties}
      />
      {active && (
        <path
          className="wf-edge-flow"
          d={path}
          style={{ stroke: `color-mix(in srgb, ${color} 70%, white)` } as CSSProperties}
        />
      )}
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
