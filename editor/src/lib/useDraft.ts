import { useEffect, useRef, useState } from "react";

/**
 * Local draft buffer for a controlled text input whose value round-trips through
 * graph state. Typing updates `draft` synchronously (so the caret never moves)
 * and we propagate up via `onChange`. We only re-seed from the incoming `value`
 * on a genuine external change (undo, a programmatic edit), tracked via the last
 * value we emitted. Without this, the value coming back a tick later makes React
 * re-assign the input's value and snap the caret to the end on every keystroke.
 */
export function useDraft(
  value: string,
  onChange: (v: string) => void,
): [string, (v: string) => void] {
  const [draft, setDraft] = useState(value);
  const lastEmit = useRef(value);
  useEffect(() => {
    if (value !== lastEmit.current) {
      lastEmit.current = value;
      setDraft(value);
    }
  }, [value]);
  const emit = (next: string) => {
    lastEmit.current = next;
    setDraft(next);
    onChange(next);
  };
  return [draft, emit];
}
