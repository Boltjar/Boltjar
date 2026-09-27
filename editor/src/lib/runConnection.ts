// ============================================================================
// RunConnection: the one /ws connection the editor keeps to the runtime.
// It points at the active tab's graph (`connect(url)`), reconnects with a
// capped backoff when the server drops it, and queues a single payload sent
// on open. Exactly ONE socket is ever current: every handler checks that its
// socket is still the current one before it acts, so a socket closed by a
// tab switch can neither schedule a reconnect nor clear the current socket,
// and a socket being replaced never delivers another frame. (A stale close
// used to open a second socket for the new tab and orphan the first, and
// each socket replayed the latest values: a Chat showed its turns twice.)
// Framework-free: the socket factory and the timers are passed in, so the
// node tests drive it with fakes.
// ============================================================================

/** WebSocket readyState values (the DOM constants, without needing a DOM). */
export const CONNECTING = 0;
export const OPEN = 1;

/** The part of a WebSocket this connection uses. The handlers take `never`
 *  so a DOM WebSocket fits: this module only assigns them, the socket calls them. */
export interface SocketLike {
  readonly readyState: number;
  send(data: string): void;
  close(): void;
  onopen: ((ev: never) => void) | null;
  onclose: ((ev: never) => void) | null;
  onerror: ((ev: never) => void) | null;
  onmessage: ((ev: never) => void) | null;
}

export interface RunConnectionDeps {
  open(url: string): SocketLike;
  setTimer(fn: () => void, ms: number): number;
  clearTimer(id: number): void;
}

export interface RunConnectionHandlers {
  onOpen(): void;
  onClose(): void;
  onError(): void;
  onMessage(data: unknown): void;
}

/** first reconnect delay, and the cap the doubling backoff stops at (ms). */
export const RECONNECT_FIRST = 500;
export const RECONNECT_MAX = 4000;

export class RunConnection {
  private ws: SocketLike | null = null;
  private url: string | null = null;
  private timer: number | null = null;
  private delay = 0;
  private pending: string | null = null;

  constructor(private deps: RunConnectionDeps, private handlers: RunConnectionHandlers) {}

  /** Point the connection at `url`: the current socket (if any) is dropped
   *  and a fresh one opens. A payload queued for the old url is dropped too. */
  connect(url: string): void {
    this.url = url;
    this.pending = null;
    this.drop();
    this.delay = 0;
    this.ensure();
  }

  /** The current socket, opening one for the current url when there is none
   *  (or it closed). Null before the first connect() or after close(). */
  ensure(): SocketLike | null {
    const cur = this.ws;
    if (cur && (cur.readyState === OPEN || cur.readyState === CONNECTING)) return cur;
    if (this.url === null) return null;
    this.cancelTimer();
    const ws = this.deps.open(this.url);
    this.ws = ws;
    ws.onopen = () => {
      if (this.ws !== ws) return;
      this.delay = 0; // a healthy connection resets the backoff
      this.handlers.onOpen();
      if (this.pending !== null) {
        ws.send(this.pending);
        this.pending = null;
      }
    };
    ws.onclose = () => {
      // a socket replaced by connect() or close() is not this connection's any
      // more: its close must never touch the current socket or reconnect.
      if (this.ws !== ws) return;
      this.ws = null;
      this.handlers.onClose();
      // the server dropped the current socket: reconnect with the backoff. The
      // server replays status + latest values on connect, so the editor catches up.
      const delay = this.delay === 0 ? RECONNECT_FIRST : Math.min(this.delay * 2, RECONNECT_MAX);
      this.delay = delay;
      this.timer = this.deps.setTimer(() => {
        this.timer = null;
        this.ensure();
      }, delay);
    };
    ws.onerror = () => {
      if (this.ws !== ws) return;
      this.handlers.onError();
    };
    ws.onmessage = (e: { data: unknown }) => {
      if (this.ws !== ws) return;
      this.handlers.onMessage(e.data);
    };
    return ws;
  }

  /** Send now when the socket is open; false when it is not. */
  send(payload: object): boolean {
    const ws = this.ws;
    if (ws && ws.readyState === OPEN) {
      ws.send(JSON.stringify(payload));
      return true;
    }
    return false;
  }

  /** Send now, or as soon as the socket opens (one payload waits; a newer one
   *  replaces it). */
  sendOrQueue(payload: object): void {
    const ws = this.ensure();
    if (ws && ws.readyState === OPEN) ws.send(JSON.stringify(payload));
    else this.pending = JSON.stringify(payload);
  }

  /** Forget a queued payload (an Off cancels an On still waiting to be sent). */
  clearPending(): void {
    this.pending = null;
  }

  /** Stop for good: no socket, no reconnect, until the next connect(). */
  close(): void {
    this.url = null;
    this.pending = null;
    this.drop();
  }

  private drop(): void {
    this.cancelTimer();
    const prior = this.ws;
    this.ws = null;
    if (prior) {
      try { prior.close(); } catch { /* already closed */ }
    }
  }

  private cancelTimer(): void {
    if (this.timer !== null) {
      this.deps.clearTimer(this.timer);
      this.timer = null;
    }
  }
}
