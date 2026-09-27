// ============================================================================
// modelMeta: small, pure view helpers for the capability-driven LLM node:
// formatting a model's context window, labelling a modality, and the icon a
// modality reads as. Kept separate so the picker, the node body and the
// inspector all describe a model the same way.
// ============================================================================
import type { ModelManifest } from "../types/protocol";

/** A compact human label for a context window (8192 → "8K", 200000 → "200K"). */
export function formatContext(context: number): string {
  if (!context || context <= 0) return "n/a";
  if (context >= 1_000_000) return `${(context / 1_000_000).toFixed(context % 1_000_000 ? 1 : 0)}M`;
  if (context >= 1000) return `${Math.round(context / 1000)}K`;
  return String(context);
}

/** The Ionicon name a modality chip reads as (falls back to a neutral dot glyph). */
export function modalityIcon(modality: string): string {
  switch (modality) {
    case "text": return "text-outline";
    case "image": return "image-outline";
    case "audio": return "volume-high-outline";
    case "video": return "videocam-outline";
    default: return "ellipse-outline";
  }
}

/** The provider label shown as a group header / chip (title-cased, known brands kept). */
export function providerLabel(provider: string): string {
  const known: Record<string, string> = {
    mock: "Mock", ollama: "Ollama", xai: "xAI", anthropic: "Anthropic", google: "Google",
    openai: "OpenAI",
  };
  return known[provider] ?? provider.charAt(0).toUpperCase() + provider.slice(1);
}

/** Group manifests by provider, preserving the served order within each group. */
export function groupByProvider(manifests: ModelManifest[]): Array<{ provider: string; models: ModelManifest[] }> {
  const order: string[] = [];
  const buckets = new Map<string, ModelManifest[]>();
  for (const m of manifests) {
    if (!buckets.has(m.provider)) {
      buckets.set(m.provider, []);
      order.push(m.provider);
    }
    buckets.get(m.provider)!.push(m);
  }
  return order.map((provider) => ({ provider, models: buckets.get(provider)! }));
}

// ----------------------------------------------------------- the model picker
// The picker lists only what can run now (the server marks each model
// `available`), for the node's family, grouped by provider in the served order.
// Pure, so the grouping and the lines it shows are testable without React.

/** The special value an LLM picker offers above the list: the runtime runs the
 *  first runnable model (an installed Ollama chat model, else a keyed provider's). */
export const AUTO_MODEL = "auto";

/** The models a picker of `kind` lists: its family, runnable now. A manifest
 *  without a kind is an LLM (older TOMLs). */
export function runnableModels(manifests: readonly ModelManifest[], kind: string): ModelManifest[] {
  return manifests.filter((m) => (m.kind ?? "llm") === kind && m.available !== false);
}

/** The models matching a search (label, id, provider, summary), case-insensitive. */
export function searchModels(manifests: readonly ModelManifest[], query: string): ModelManifest[] {
  const q = query.trim().toLowerCase();
  if (!q) return [...manifests];
  return manifests.filter((m) => `${m.label} ${m.id} ${m.provider} ${m.summary}`.toLowerCase().includes(q));
}

/** What a picker of `kind` shows for `query`: the runnable models, grouped by
 *  provider, each group and model in the served order. */
export function pickerGroups(
  manifests: readonly ModelManifest[],
  kind: string,
  query: string,
): Array<{ provider: string; models: ModelManifest[] }> {
  return groupByProvider(searchModels(runnableModels(manifests, kind), query));
}

/** The live list's state, as GET /api/models reports it (times are ISO UTC). */
export interface ModelsMeta {
  auto: string | null;
  updated: string | null;
  refreshing: boolean;
  providers: Record<string, { ok: boolean; updated: string | null; error: string | null }>;
}

/** How long ago an ISO UTC time was, in words ("just now", "5 min ago"). */
export function ago(iso: string, nowMs: number): string {
  const seconds = Math.max(0, Math.round((nowMs - Date.parse(iso)) / 1000));
  if (!Number.isFinite(seconds) || seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.floor(hours / 24)} days ago`;
}

/** The picker's "last updated" line: the age of the newest successful refresh,
 *  plus each provider that did not answer the last time it was asked. */
export function updatedLine(meta: ModelsMeta, nowMs: number): string {
  if (meta.refreshing) return "refreshing…";
  const head = meta.updated ? `updated ${ago(meta.updated, nowMs)}` : "not checked yet";
  const down = Object.entries(meta.providers)
    .filter(([, p]) => !p.ok)
    .map(([name]) => providerLabel(name));
  return down.length ? `${head} · ${down.join(", ")} unreachable` : head;
}

/** What the picker's button says about the picked model: `ok`, `auto` (with
 *  the model it runs now), `unavailable` (with the server's reason), `missing`
 *  (no longer in the list) or `none` (nothing picked). */
export function modelStatus(
  selectedId: string,
  models: ReadonlyMap<string, ModelManifest>,
  auto: string | null,
): { state: "ok" | "auto" | "unavailable" | "missing" | "none"; note: string } {
  if (!selectedId) return { state: "none", note: "" };
  if (selectedId === AUTO_MODEL) {
    const runs = auto ? models.get(auto) : undefined;
    return {
      state: "auto",
      note: runs ? `runs ${runs.label}` : auto ? `runs ${auto}` : "runs the mock until a model is connected",
    };
  }
  const manifest = models.get(selectedId);
  if (!manifest) return { state: "missing", note: "not in the model list any more" };
  if (manifest.available === false) return { state: "unavailable", note: manifest.reason ?? "not available now" };
  return { state: "ok", note: "" };
}
