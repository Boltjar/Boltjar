// ============================================================================
// storeEvents: an in-session notice that one store's tables or keys just
// changed. The schema editor sends it after each mutation that succeeds (and
// the undo path after it restores a store's tables); every store picker listens
// and reads its list again when the change is to the store it lists from.
// ============================================================================
import type { StoreKind } from "./storeSelect";

export type StoreChangeListener = (kind: StoreKind, key: string) => void;

const listeners = new Set<StoreChangeListener>();

/** Tell every listener that the store `kind`/`key` changed. */
export function notifyStoreChanged(kind: StoreKind, key: string): void {
  // a copy, so a listener that unsubscribes while being told cannot skip another
  for (const listener of [...listeners]) listener(kind, key);
}

/** Listen for store changes. Returns the function that stops listening. */
export function onStoreChanged(listener: StoreChangeListener): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
