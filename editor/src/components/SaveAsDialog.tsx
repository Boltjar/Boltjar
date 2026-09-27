// ============================================================================
// SaveAsDialog: the body of the file menu's "Save as...", in the shared
// NodeModal with the shared field (.field / .input / .conn-field-error, as in
// Settings). It asks for the copy's name and shows the slug it saves under;
// App checks the name (lib/workflowFile saveAsName, then the server) and saves.
// A refusal (a taken name, a failed save) shows under the field and keeps the
// dialog open, so nothing is ever saved over another workflow.
// ============================================================================
import { useState } from "react";
import { NodeModal } from "./canvas/NodeModal";
import { workflowSlug } from "../lib/workflowFile";

interface SaveAsDialogProps {
  /** the workflow being copied (its slug). */
  from: string;
  /** save a copy under `name`: resolves to null when saved, else the reason. */
  onSave: (name: string) => Promise<string | null>;
  onClose: () => void;
}

export function SaveAsDialog({ from, onSave, onClose }: SaveAsDialogProps) {
  const [name, setName] = useState(from);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const slug = workflowSlug(name);

  const submit = async () => {
    if (busy) return;
    setBusy(true);
    const refused = await onSave(name);
    setBusy(false);
    if (refused) setError(refused);
    else onClose();
  };

  return (
    <NodeModal title="Save as" icon="duplicate-outline" onClose={onClose}>
      <form
        className="confirm-modal"
        onSubmit={(e) => { e.preventDefault(); void submit(); }}
      >
        <p>
          Saves a copy of <b>{from}</b> under a new name and opens it. <b>{from}</b> stays as it was last saved.
        </p>
        <div className="field">
          <div className="field-lbl">Name</div>
          <div className={`input ${error ? "conn-input-err" : ""}`}>
            <input
              type="text"
              value={name}
              autoFocus
              spellCheck={false}
              aria-label="Name of the copy"
              onFocus={(e) => e.currentTarget.select()}
              onChange={(e) => { setName(e.target.value); setError(null); }}
            />
          </div>
          {error
            ? <div className="conn-field-error" role="alert">{error}</div>
            : slug && slug !== name.trim() && <div className="field-hint">saves as {slug}</div>}
        </div>
        <div className="confirm-actions">
          <button type="button" className="confirm-cancel" onClick={onClose}>Cancel</button>
          <button type="submit" className="confirm-ok" disabled={busy || !name.trim()}>
            {busy ? "Saving…" : "Save copy"}
          </button>
        </div>
      </form>
    </NodeModal>
  );
}
