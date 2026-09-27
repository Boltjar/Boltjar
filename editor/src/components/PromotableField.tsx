// ============================================================================
// PromotableField: wraps any editable widget surface (code textarea, secret
// input, color picker, ...) with the universal right-click "Convert to input"
// + "Reset to default" menu. The Knob component already does this for its own
// kinds (bool/select/number/text); this is the same recipe for everything else.
//
// Usage:
//   <PromotableField onPromote={fn} onReset={fn}>
//     <textarea ... />
//   </PromotableField>
// ============================================================================
import { useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { ContextMenu } from "./ContextMenu";

interface PromotableFieldProps {
  /** the field's wrapper class (e.g. "field" for inspector, "ib code" for node). */
  className?: string;
  /** Optional: when given, the menu offers "Convert to input". */
  onPromote?: () => void;
  /** Optional: when given, the menu offers "Reset to default". */
  onReset?: () => void;
  children: ReactNode;
}

export function PromotableField({ className, onPromote, onReset, children }: PromotableFieldProps) {
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);

  const openMenu = (e: React.MouseEvent) => {
    if (!onPromote && !onReset) return;
    e.preventDefault();
    e.stopPropagation();
    e.nativeEvent.stopImmediatePropagation();
    setMenu({ x: e.clientX, y: e.clientY });
  };

  const items = [
    ...(onPromote ? [{ id: "convert", label: "Convert to input", icon: "enter-outline", run: onPromote }] : []),
    ...(onReset ? [{ id: "reset", label: "Reset to default", icon: "refresh-outline", run: onReset }] : []),
  ];

  return (
    <div className={className} onContextMenu={openMenu}>
      {children}
      {menu && createPortal(
        <ContextMenu x={menu.x} y={menu.y} items={items} onClose={() => setMenu(null)} />,
        document.body,
      )}
    </div>
  );
}
