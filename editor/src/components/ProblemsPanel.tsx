// ============================================================================
// ProblemsPanel: a floating panel listing pre-run validation problems (from
// POST /api/validate and the ws `invalid` event). Each row names the broken node
// and the reason; clicking it selects/centres that node. Turning On is gated on
// this being empty (a broken graph never runs). Dismissible. When the problems
// are why the launch could not turn the graph back On, it also offers Stop
// resuming (lib/resumeNotice).
// ============================================================================
import { Icon } from "../lib/icons";
import { STOP_RESUMING } from "../lib/resumeNotice";
import type { Problem } from "../types/protocol";

interface ProblemsPanelProps {
  problems: Problem[];
  onGoToNode: (id: string) => void;
  onClose: () => void;
  /** the launch could not resume this graph and retries it: stop that */
  onStopResuming?: () => void;
}

const KIND_LABEL: Record<string, string> = {
  "missing-input": "missing input",
  "missing-secret": "missing secret",
  "no-model": "no model",
  "type-mismatch": "type mismatch",
  "no-trigger": "no trigger",
  unknown: "unknown node",
  cycle: "unguarded cycle",
};

export function ProblemsPanel({ problems, onGoToNode, onClose, onStopResuming }: ProblemsPanelProps) {
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
      {onStopResuming && (
        <div className="pp-foot">
          <span className="pp-foot-note">{STOP_RESUMING.note}</span>
          <button type="button" className="conn-action-btn" onClick={onStopResuming}>
            <Icon name={STOP_RESUMING.icon} />
            {STOP_RESUMING.button}
          </button>
        </div>
      )}
    </div>
  );
}
