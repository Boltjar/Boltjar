// ============================================================================
// ToolHints: an inline body hint under a Tool node that teaches the
// call -> compute -> result loop. Mirrors StoreHints' unwired-hint pattern (the
// same inline body-hint template, not a new one): it shows until the `call`
// output and both `result` and `result_trigger` are wired, so the contract is
// obvious the first time you drop a Tool. Once the loop is closed it disappears.
// ============================================================================

interface ToolHintsProps {
  /** the Tool's `call` output has a downstream wire (the tool body is started). */
  callWired: boolean;
  /** the Tool's `result` and `result_trigger` inputs both have an upstream
   *  wire (the loop is closed). */
  resultWired: boolean;
}

export function ToolHints({ callWired, resultWired }: ToolHintsProps) {
  if (callWired && resultWired) return null;
  const text = !callWired
    ? "wire call -> compute the tool -> back into result"
    : "close the loop: the answer into result, its trigger into result_trigger";
  return <div className="store-hints store-hints--unwired">{text}</div>;
}
