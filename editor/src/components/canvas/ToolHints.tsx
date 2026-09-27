// ============================================================================
// ToolHints: an inline body hint under a Tool node that teaches the
// call -> compute -> result loop. Mirrors StoreHints' unwired-hint pattern (the
// same inline body-hint template, not a new one): it shows until both the `call`
// output and the `result` input are wired, so the contract is obvious the first
// time you drop a Tool. Once the loop is closed it disappears.
// ============================================================================

interface ToolHintsProps {
  /** the Tool's `call` output has a downstream wire (the tool body is started). */
  callWired: boolean;
  /** the Tool's `result` input has an upstream wire (the loop is closed). */
  resultWired: boolean;
}

export function ToolHints({ callWired, resultWired }: ToolHintsProps) {
  if (callWired && resultWired) return null;
  const text = !callWired
    ? "wire call -> compute the tool -> back into result"
    : "close the loop: feed the result back into result";
  return <div className="store-hints store-hints--unwired">{text}</div>;
}
