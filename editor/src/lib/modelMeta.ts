// ============================================================================
// modelMeta: small, pure view helpers for the capability-driven LLM node:
// formatting a model's context window, labelling a modality, and the icon a
// modality reads as. Kept separate so the picker, the node body and the
// inspector all describe a model the same way.
// ============================================================================
import type { ModelManifest, ProviderListing } from "../types/protocol";

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
 *  first runnable model (an installed Ollama chat model, else a model of another
 *  connected provider that answered, curated ones first). */
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

/** The live list's state, as GET /api/models reports it (times are ISO UTC).
 *  `list` says whether a list was read at all: "loading" before the first
 *  answer, "failed" when GET /api/models failed and none was ever read, "ready"
 *  once one was (a later failed read keeps it). Until "ready", no model can be
 *  called missing. */
export interface ModelsMeta {
  list: "loading" | "failed" | "ready";
  auto: string | null;
  updated: string | null;
  refreshing: boolean;
  providers: Record<string, Pick<ProviderListing, "ok" | "checked" | "updated" | "error" | "failure">
    & Partial<Pick<ProviderListing, "count">>>;
}

/** ModelsMeta.list from the reader's state: a list read (even if a later read
 *  failed) is "ready"; no list and a failed read is "failed"; else "loading". */
export function listState(hasList: boolean, loading: boolean, error: string | null): ModelsMeta["list"] {
  return hasList ? "ready" : error && !loading ? "failed" : "loading";
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

/** How the picker words a provider's failed attempt, by its kind. */
const FAILURE_WORDS: Record<string, string> = {
  not_running: "not running",
  unreachable: "unreachable",
  timeout: "timed out",
  key_refused: "refused the key",
  error: "failed",
};

/** How a provider's failed attempt reads, by its kind ("not running", ...). */
export function failureWords(failure: string | null | undefined): string {
  return FAILURE_WORDS[failure ?? "error"] ?? FAILURE_WORDS.error;
}

/** A provider's last listing in a few words, for its row in Settings, AI Providers: how
 *  many models it listed, why its last attempt failed, or that it was never
 *  asked (no listing: the server asks it on its next refresh). */
export function listingNote(listing: ModelsMeta["providers"][string] | undefined): {
  ok: boolean;
  text: string;
} {
  if (!listing) return { ok: false, text: "not checked yet" };
  if (!listing.ok) return { ok: false, text: failureWords(listing.failure) };
  const count = listing.count ?? 0;
  return { ok: true, text: `${count} ${count === 1 ? "model" : "models"}` };
}

/** The picker's "last updated" line: the age of the newest successful refresh
 *  (or of the last attempt, when no provider ever answered), plus each provider
 *  whose last attempt failed and why. Ollama is always asked, so one that never
 *  answered is a setup without it, not news, and is left out. */
export function updatedLine(meta: ModelsMeta, nowMs: number): string {
  if (meta.refreshing) return "refreshing…";
  const listings = Object.entries(meta.providers);
  const checked = listings
    .map(([, p]) => p.checked)
    .filter(Boolean)
    .sort()
    .pop();
  const head = meta.updated
    ? `updated ${ago(meta.updated, nowMs)}`
    : checked
      ? `checked ${ago(checked, nowMs)}`
      : "not checked yet";
  const down = listings
    .filter(([name, p]) => !p.ok && !(name === "ollama" && !p.updated))
    .map(([name, p]) => `${providerLabel(name)} ${failureWords(p.failure)}`);
  return down.length ? `${head} · ${down.join(", ")}` : head;
}

/** What the picker's button says about the picked model: `ok`, `auto` (with
 *  the model it runs now), `unavailable` (with the server's reason), `missing`
 *  (no longer in the list), `unknown` (no list was read yet, so nothing can be
 *  claimed either way) or `none` (nothing picked). A `mock/` model is the
 *  offline mock, which always runs. */
export function modelStatus(
  selectedId: string,
  models: ReadonlyMap<string, ModelManifest>,
  meta: Pick<ModelsMeta, "auto" | "list">,
): { state: "ok" | "auto" | "unavailable" | "missing" | "unknown" | "none"; note: string } {
  if (!selectedId) return { state: "none", note: "" };
  const listed = meta.list === "ready";
  if (selectedId === AUTO_MODEL) {
    const runs = meta.auto ? models.get(meta.auto) : undefined;
    return {
      state: "auto",
      note: !listed
        ? "picks a model that can run when it runs"
        : runs
          ? `runs ${runs.label}`
          : meta.auto
            ? `runs ${meta.auto}`
            : "runs the mock until a model is connected",
    };
  }
  if (selectedId.startsWith("mock/")) return { state: "ok", note: "" };
  if (!listed) {
    return { state: "unknown", note: meta.list === "failed" ? "the model list could not be read" : "" };
  }
  const manifest = models.get(selectedId);
  if (!manifest) return { state: "missing", note: "not in the model list any more" };
  if (manifest.available === false) return { state: "unavailable", note: manifest.reason ?? "not available now" };
  return { state: "ok", note: "" };
}
