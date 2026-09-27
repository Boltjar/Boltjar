// ============================================================================
// consoleFeed: what the editor's console panel holds and shows.
// Two streams, each bounded on its own: the READABLE one (Log node output,
// warnings, errors, validation problems, power changes, resume and editor
// notices) and the VALUES one (every value a port emits, and a model's tool
// calls). Values already show on the nodes (footers, Previews, badges), so the
// console hides them unless the person turns "values" on: an Interval firing
// every second no longer buries the lines worth reading. A line identical to
// the last line of its source (the node, and the port for a value) is not
// repeated: that entry counts it (×60) and takes its time, where it stands,
// so a Log printing the same text every second is one line while the Log next
// to it prints its changing lines. A changed line starts a new entry.
// Each stream keeps its own last CONSOLE_KEEP entries, so a flood of values
// never pushes an error out. Pure, so the node tests drive it.
// ============================================================================

export type ConsoleLevel = "info" | "ok" | "warn" | "bad";
export type ConsoleStream = "readable" | "values";

/** One line as it arrives. `kind` is the ws event kind it came from, or
 *  "notice" for a line the editor posts itself. */
export interface ConsoleLineIn {
  kind: string;
  ts: string;
  level: ConsoleLevel;
  node?: string;
  /** one console-safe line (lib/mediaSummary): never a raw payload. */
  message: string;
  tag?: string;
}

export interface ConsoleEntry extends Omit<ConsoleLineIn, "kind"> {
  /** stable for the entry's life: the row's key, and the order the two
   *  streams merge in (the order the entries began). */
  id: number;
  /** when the entry last changed, across both streams (the status line shows
   *  the entry changed last). */
  seq: number;
  stream: ConsoleStream;
  /** how many identical lines in a row from its source this entry stands for. */
  count: number;
}

export interface ConsoleFeed {
  readable: ConsoleEntry[];
  values: ConsoleEntry[];
  next: number;
}

/** Entries each stream keeps. */
export const CONSOLE_KEEP = 500;

export const EMPTY_FEED: ConsoleFeed = { readable: [], values: [], next: 0 };

// the event kinds that are data flowing between nodes, not something to read
const VALUE_KINDS = new Set(["value", "tool_call", "tool_result"]);

/** The stream a line of this event kind belongs to. */
export function streamOf(kind: string): ConsoleStream {
  return VALUE_KINDS.has(kind) ? "values" : "readable";
}

/** `feed` with `line` added. When the last line of the same source (node and
 *  tag) in its stream is the same line (level and message), that entry counts
 *  it and takes its time, in place; else the line is a new entry. Only the
 *  changed entry is a new object, and no entry moves, so an unchanged row never
 *  redraws. */
export function appendLine(feed: ConsoleFeed, line: ConsoleLineIn, keep = CONSOLE_KEEP): ConsoleFeed {
  const stream = streamOf(line.kind);
  const list = feed[stream];
  const seq = feed.next;
  let at = list.length - 1;
  while (at >= 0 && (list[at].node !== line.node || list[at].tag !== line.tag)) at -= 1;
  const prior = at >= 0 ? list[at] : undefined;
  let nextList: ConsoleEntry[];
  if (prior && prior.level === line.level && prior.message === line.message) {
    nextList = list.slice();
    nextList[at] = { ...prior, ts: line.ts, seq, count: prior.count + 1 };
  } else {
    const { kind: _kind, ...rest } = line;
    nextList = [...list, { ...rest, id: seq, seq, stream, count: 1 }];
    if (nextList.length > keep) nextList = nextList.slice(nextList.length - keep);
  }
  return { ...feed, [stream]: nextList, next: seq + 1 };
}

/** The entries the panel shows: the readable stream alone (the same array, so
 *  a value arriving changes nothing on screen), or both merged in the order
 *  they began when values are on. */
export function visibleEntries(feed: ConsoleFeed, showValues: boolean): ConsoleEntry[] {
  if (!showValues) return feed.readable;
  const a = feed.readable;
  const b = feed.values;
  const out: ConsoleEntry[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length || j < b.length) {
    if (j >= b.length || (i < a.length && a[i].id < b[j].id)) out.push(a[i++]);
    else out.push(b[j++]);
  }
  return out;
}

/** The entry that changed last (a new line or a count), for the status line. */
export function latestEntry(entries: readonly ConsoleEntry[]): ConsoleEntry | undefined {
  let latest: ConsoleEntry | undefined;
  for (const e of entries) if (!latest || e.seq > latest.seq) latest = e;
  return latest;
}
