// ============================================================================
// replayedValues: telling a replayed value from a new one.
// On connect the server replays the latest value event of every wire, each
// with the id it carried live. An editor reconnecting to the graph it already
// shows (a dropped connection) holds most of those values, so a value whose
// id is the last one seen on its wire is a replay and changes nothing: it
// never lands in the port's history twice, so a Chat viewer never shows a
// turn twice. After a tab switch the ids are forgotten with the history, and
// the replay seeds it. Pure, so the node tests drive it.
// ============================================================================

/** Whether a value event on `key` (`node:port`) is one this editor already
 *  holds. Records `id` as the wire's latest otherwise. An event with no id
 *  is always new. */
export function isReplayed(lastIds: Map<string, string>, key: string, id: string | undefined): boolean {
  if (!id) return false;
  if (lastIds.get(key) === id) return true;
  lastIds.set(key, id);
  return false;
}
