// ============================================================================
// NodeModal: the one shared modal shell. Any node that needs more room than its
// body (the DB schema builder, a large editor, a future surface:"modal" widget)
// mounts its content here, so every "open in a modal" reads identically: a
// centered panel over a scrim, a titled header with a close button, Esc to
// dismiss, click-the-scrim to dismiss. Portaled to <body> so it floats above the
// canvas regardless of where it is invoked.
// ============================================================================
import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Icon } from "../../lib/icons";

interface NodeModalProps {
  /** Header title (usually the node id + what is being edited). */
  title: string;
  /** Optional quiet subtitle to the right of the title. */
  subtitle?: string;
  /** Optional ionicon name shown before the title. */
  icon?: string;
  onClose: () => void;
  children: ReactNode;
}

export function NodeModal({ title, subtitle, icon, onClose, children }: NodeModalProps) {
  // Esc closes, matching the command palette / connections window.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return createPortal(
    <div className="node-modal-scrim" onClick={onClose}>
      <div className="node-modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className="node-modal-head">
          {icon && <Icon name={icon} className="nm-icon" />}
          <span className="nm-title">{title}</span>
          {subtitle && <span className="nm-sub">{subtitle}</span>}
          <button type="button" className="nm-close" onClick={onClose} title="Close (Esc)">
            <Icon name="close-outline" />
          </button>
        </div>
        <div className="node-modal-body nowheel">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
