// ============================================================================
// nodeSearch: which nodes the ⌘K palette offers for a query, best match first.
// A node matches on its name, id, category or summary, and ranks by where the
// query lands: the exact name, then a name that starts with it, a word in the
// name that starts with it, a name that contains it, the id, the category, a
// word in the summary that starts with it, and last a summary that merely
// contains it. Nodes of one rank keep the registry's order, and the row limit
// cuts the list only after ranking, so "LLM" lists the LLM node first rather
// than whichever node's summary mentions an LLM earlier in the registry. Pure,
// so the node tests drive it.
// ============================================================================

/** The fields of a node definition the search reads. */
export interface SearchableNode {
  id: string;
  name: string;
  category: string;
  summary: string;
}

/** Where a query lands in a node, best first; null when it lands nowhere. */
export function matchRank(node: SearchableNode, query: string): number | null {
  const q = query.trim().toLowerCase();
  if (!q) return 0;
  const name = node.name.toLowerCase();
  const summary = node.summary.toLowerCase();
  if (name === q) return 0;
  if (name.startsWith(q)) return 1;
  if (words(name).some((w) => w.startsWith(q))) return 2;
  if (name.includes(q)) return 3;
  if (node.id.toLowerCase().includes(q)) return 4;
  if (node.category.toLowerCase().includes(q)) return 5;
  if (words(summary).some((w) => w.startsWith(q))) return 6;
  if (summary.includes(q)) return 7;
  // a query that runs across fields ("llm core") still finds the node, last
  if (`${name} ${node.id} ${node.category} ${summary}`.toLowerCase().includes(q)) return 8;
  return null;
}

/** The nodes that match `query`, best match first, at most `limit` of them. */
export function rankNodes<T extends SearchableNode>(nodes: readonly T[], query: string, limit: number): T[] {
  return nodes
    .map((node, order) => ({ node, order, rank: matchRank(node, query) }))
    .filter((hit): hit is { node: T; order: number; rank: number } => hit.rank !== null)
    .sort((a, b) => a.rank - b.rank || a.order - b.order)
    .slice(0, limit)
    .map((hit) => hit.node);
}

/** The words of a lower-cased text: runs of letters and digits ("for-each" is
 *  "for" and "each", "http request" is "http" and "request"). */
function words(text: string): string[] {
  return text.split(/[^\p{L}\p{N}]+/u).filter(Boolean);
}
