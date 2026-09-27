// ============================================================================
// ProblemsPanel: a floating panel listing pre-run validation problems (from
// POST /api/validate and the ws `invalid` event). Each row names the broken node
// and the reason; clicking it selects/centres that node. Turning On is gated on
// this being empty (a broken graph never runs). Dismissible.
// ============================================================================
import { Icon } from "../lib/icons";
import type { Problem } from "../types/protocol";

interface ProblemsPanelProps {
  problems: Problem[];
  onGoToNode: (id: string) => void;
  onClose: () => void;
}

const KIND_LABEL: Record<string, string> = {
  "missing-input": "missing input",
  "type-mismatch": "type mismatch",
  "no-trigger": "no trigger",
  unknown: "unknown node",
  cycle: "unguarded cycle",
};

export function ProblemsPanel({ problems, onGoToNode, onClose }: ProblemsPanelProps) {
  return (
    <div className="problems-panel">
      <div className="pp-head">
        <span className="pp-title">
          <Icon name="warning-outline" />
          {problems.length} problem{problems.length === 1 ? "" : "s"}
        </span>
        <button className="pp-close" onClick={onClose} title="dismiss">
          <Icon name="close-outline" />
        </button>
      </div>
      <div className="pp-body">
        {problems.length === 0 ? (
          <div className="pp-clean">
            <Icon name="checkmark-done-outline" /> no problems, ready to power on
          </div>
        ) : (
          problems.map((p, i) => (
            <button
              key={`${p.node}-${p.kind}-${i}`}
              className="pp-row"
              onClick={() => p.node && onGoToNode(p.node)}
              disabled={!p.node}
            >
              <span className="pp-kind">{KIND_LABEL[p.kind] ?? p.kind}</span>
              {p.node ? <span className="pp-node">{p.node}</span> : <span className="pp-node graph">graph</span>}
              <span className="pp-msg">{p.message}</span>
            </button>
          ))
        )}
      </div>
    </div>
  );
}
