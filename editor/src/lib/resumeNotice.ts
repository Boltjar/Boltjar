// ============================================================================
// A graph the launch could not turn back On ("Resume workflows after launch").
// The server keeps it recorded and tries it again at every launch, and replays
// why it failed (an `invalid` or `error` event marked `resume`) to each editor
// that opens it, until it turns On or a person turns it Off. The graph is Off,
// so the power toggle offers no Off: STOP_RESUMING is that Off, in the command
// palette and in the Problems panel. Pure, so it is testable without React.
// ============================================================================

/** Whether the open graph shows a launch's "not resumed" notice after `evt`. */
export function resumeNoticeAfter(showing: boolean, evt: { kind: string; resume?: boolean }): boolean {
  // any power change ends it: the graph turned On, or a person turned it Off
  // (on connect the server sends the status first, then replays the notice)
  if (evt.kind === "status") return false;
  if ((evt.kind === "invalid" || evt.kind === "error") && evt.resume === true) return true;
  return showing;
}

/** The action that stops the retries: a person's Off, for a graph that is Off. */
export const STOP_RESUMING = {
  label: "Stop resuming this graph",
  hint: "no launch turns it back On",
  icon: "ban-outline",
  /** the Problems panel's line beside its button */
  note: "Boltjar could not turn this graph back On after the launch, and tries again at each one.",
  button: "Stop resuming",
} as const;
