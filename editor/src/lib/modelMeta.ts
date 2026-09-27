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
