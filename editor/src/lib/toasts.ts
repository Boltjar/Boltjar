// ============================================================================
// toasts: the cards in the bottom right of the editor (components/Toast.tsx).
//
// A toast with buttons is how the editor asks the person to decide something:
// every console notice that carries actions raises one (hooks/useRunSocket),
// so no call site writes toast code of its own. It stays until a button is
// pressed or its × hides it. × only hides the card: the console line keeps
// its words and its buttons, and the palette keeps its actions, so the choice
// stays reachable. Pressing a button anywhere (toast, console line, palette)
// settles the offer, which takes the toast away everywhere it shows.
//
// A plain toast (no buttons) says something briefly, such as a refused wire;
// the card hides itself after PLAIN_TOAST_MS, paused while the pointer is on
// it. The same plain words raised again replace the card that shows them.
//
// At most TOAST_STACK cards show, oldest first (the newest at the bottom);
// more wait in order and show as cards leave. Pure functions over a state,
// plus one small store the component subscribes to, so the tests drive it.
// ============================================================================
import type { ConsoleLevel } from "./consoleFeed";

/** A button on a toast: one answer to the choice it asks. */
export interface ToastAction {
  label: string;
  run: () => void;
}

export interface ToastIn {
  level: ConsoleLevel;
  message: string;
  /** the answers, in order; the first is the primary one. */
  actions?: ToastAction[];
  /** names the offer, so settling it takes the toast away. */
  offer?: string;
}

export interface Toast extends ToastIn {
  /** stable for the card's life: its key and its timer. */
  id: number;
}

export interface ToastState {
  list: Toast[];
  next: number;
}

/** Cards that show at once; more wait their turn. */
export const TOAST_STACK = 3;
/** How long a plain toast shows, while the pointer is not on it. */
export const PLAIN_TOAST_MS = 4000;

export const EMPTY_TOASTS: ToastState = { list: [], next: 1 };

function isPlain(t: ToastIn): boolean {
  return !t.actions || t.actions.length === 0;
}

/** `state` with `toast` added at the end. A toast for an offer already on the
 *  list takes that card's place, and so do plain words already showing (a new
 *  id, so its timer starts again); everything else waits behind the list. */
export function raise(state: ToastState, toast: ToastIn): ToastState {
  const id = state.next;
  const card: Toast = { ...toast, id };
  const at = state.list.findIndex((t) =>
    toast.offer
      ? t.offer === toast.offer
      : isPlain(t) && isPlain(toast) && !t.offer && t.level === toast.level && t.message === toast.message);
  const list = state.list.slice();
  if (at >= 0) list[at] = card;
  else list.push(card);
  return { list, next: id + 1 };
}

/** `state` without the card `id` (its × or its timer). The offer it carried is
 *  not settled: the console line and the palette still hold the choice. */
export function hide(state: ToastState, id: number): ToastState {
  if (!state.list.some((t) => t.id === id)) return state;
  return { ...state, list: state.list.filter((t) => t.id !== id) };
}

/** `state` with the offer `offer` answered: its toast leaves. */
export function settle(state: ToastState, offer: string): ToastState {
  if (!state.list.some((t) => t.offer === offer)) return state;
  return { ...state, list: state.list.filter((t) => t.offer !== offer) };
}

/** The cards on screen, oldest first (the newest is drawn at the bottom). */
export function shownToasts(state: ToastState): Toast[] {
  return state.list.slice(0, TOAST_STACK);
}

/** The cards waiting for room, in the order they will show. */
export function queuedToasts(state: ToastState): Toast[] {
  return state.list.slice(TOAST_STACK);
}

export interface ToastStore {
  get: () => ToastState;
  subscribe: (fn: () => void) => () => void;
  raise: (toast: ToastIn) => void;
  hide: (id: number) => void;
  settle: (offer: string) => void;
}

/** A store over the pure functions above, for useSyncExternalStore. */
export function createToastStore(): ToastStore {
  let state = EMPTY_TOASTS;
  const subs = new Set<() => void>();
  const set = (next: ToastState) => {
    if (next === state) return;
    state = next;
    for (const fn of subs) fn();
  };
  return {
    get: () => state,
    subscribe: (fn) => {
      subs.add(fn);
      return () => { subs.delete(fn); };
    },
    raise: (toast) => set(raise(state, toast)),
    hide: (id) => set(hide(state, id)),
    settle: (offer) => set(settle(state, offer)),
  };
}

/** The editor's one toast stack. */
export const toasts = createToastStore();
