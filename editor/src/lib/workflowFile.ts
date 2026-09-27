// ============================================================================
// workflowFile: a workflow as a file, for the file menu under the logo.
//
// Export writes the workflow the editor holds (unsaved edits included) the way
// the server saves one: the current graph `format` first, pretty-printed with
// two spaces. A graph holds no secret values by construction: a knob that uses
// one holds its {{secret.NAME}} reference, and the value stays in the
// server's secret store. So an exported file carries references only.
//
// Open reads a .json file picked in the browser. It is a workflow when it is a
// JSON object with a `nodes` list (each node an object with a text `id` and
// `type`) and an `edges` list (each edge naming `src`, `src_port`, `dst` and
// `dst_port`); one from a newer Boltjar is refused, one from an older one is
// migrated (lib/graphAdapter migrateGraph). It opens under a slug made from its
// `name` (else the file name), numbered past every slug in use.
//
// Save as asks for a name. The name becomes a slug the way the server names a
// graph's file (letters, digits, `-` and `_`), and a slug already in use is
// refused, never overwritten. Pure, so the node tests drive it.
// ============================================================================
import type { Graph } from "../types/protocol";
import { GRAPH_FORMAT, migrateGraph } from "./graphAdapter";
import { freeSlug } from "./tabs";

/** The file an exported workflow downloads as. */
export function exportFileName(slug: string): string {
  return `${slug}.json`;
}

/** The text of an exported workflow: `graph` as the server saves it, named
 *  after its slug (a workflow's identity is its slug). */
export function exportText(graph: Graph, slug: string): string {
  const { format, name: _name, ...rest } = graph;
  return JSON.stringify({ format: format ?? GRAPH_FORMAT, name: slug, ...rest }, null, 2);
}

/**
 * A name as a workflow slug: lower case, each run of spaces and dots a single
 * dash, only letters, digits, `-` and `_` kept (what the server keeps in a
 * graph's file name), no dash at either end. "My Flow.v2" -> "my-flow-v2".
 * Empty when nothing usable is left.
 */
export function workflowSlug(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[\s.]+/g, "-")
    .replace(/[^\p{L}\p{N}_-]/gu, "")
    .replace(/-{2,}/g, "-")
    .replace(/^-+|-+$/g, "");
}

/** A file name without its folder and its extension. */
function fileStem(fileName: string): string {
  const base = fileName.split(/[\\/]/).pop() ?? "";
  const dot = base.lastIndexOf(".");
  return dot > 0 ? base.slice(0, dot) : base;
}

export type ParsedWorkflow = { ok: true; graph: Graph } | { ok: false; error: string };

const isText = (v: unknown): v is string => typeof v === "string";
const isObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

/** The workflow in a file's text, in the current format, or why it is not one. */
export function parseWorkflowFile(text: string): ParsedWorkflow {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return { ok: false, error: "it is not JSON" };
  }
  if (!isObject(data) || !Array.isArray(data.nodes) || !Array.isArray(data.edges)) {
    return { ok: false, error: "it is not a workflow (no nodes and edges)" };
  }
  if (!data.nodes.every((n) => isObject(n) && isText(n.id) && isText(n.type))) {
    return { ok: false, error: "a node has no id or type" };
  }
  if (!data.edges.every((e) => isObject(e) && isText(e.src) && isText(e.src_port) && isText(e.dst) && isText(e.dst_port))) {
    return { ok: false, error: "a wire does not name both of its ends" };
  }
  const format = data.format ?? 0;
  if (typeof format !== "number" || !Number.isInteger(format) || format < 0) {
    return { ok: false, error: `its format ${JSON.stringify(format)} is not a format number` };
  }
  if (format > GRAPH_FORMAT) {
    return {
      ok: false,
      error: `it was saved by a newer Boltjar (format ${format}); this one reads formats up to ${GRAPH_FORMAT}, update Boltjar to open it`,
    };
  }
  if (data.groups !== undefined && !Array.isArray(data.groups)) {
    return { ok: false, error: "its groups are not a list" };
  }
  return { ok: true, graph: migrateGraph(data as unknown as Graph).graph };
}

/** The slug an opened file takes: from its `name`, else its file name, else
 *  "untitled", numbered past `taken` (the open tabs, drafts and saved slugs). */
export function openedSlug(graph: Graph, fileName: string, taken: ReadonlySet<string>): string {
  const base = workflowSlug(isText(graph.name) ? graph.name : "") || workflowSlug(fileStem(fileName)) || "untitled";
  return freeSlug(base, taken);
}

/** The console line for a file that did not open. */
export function openFailedNotice(fileName: string, error: string): string {
  return `did not open ${fileName}: ${error}`;
}

export type SaveAsName = { ok: true; slug: string } | { ok: false; error: string };

/** The slug a Save as name saves under, or why it cannot. `taken` is every
 *  slug in use (open tabs, drafts, saved graphs and examples). */
export function saveAsName(input: string, taken: ReadonlySet<string>): SaveAsName {
  if (!input.trim()) return { ok: false, error: "Type a name for the copy." };
  const slug = workflowSlug(input);
  if (!slug) return { ok: false, error: "Use letters or numbers in the name." };
  if (taken.has(slug)) return { ok: false, error: `"${slug}" is already a workflow. Pick another name.` };
  return { ok: true, slug };
}
