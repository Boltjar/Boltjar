// ============================================================================
// endpoints: the pure rules behind the Connections window's OpenAI-compatible
// endpoint form (OpenRouter, Groq, LM Studio, llama.cpp, vLLM). The server
// (boltjar/endpoints.py) checks everything again; these give instant feedback
// in the form and say where a typed key will be stored. No runtime imports, so
// the node test script can load this file on its own.
// ============================================================================

/** One custom endpoint, as GET /api/connections/endpoints lists it. */
export interface EndpointInfo {
  name: string;
  base_url: string;
  /** the secret its key lives in; null when the server takes no key. */
  key_secret: string | null;
  has_key: boolean;
}

/** The shape an endpoint name takes on the server: the provider id of its
 *  models (`<name>/<model>`), lowercase, up to 32 characters. */
const NAME_RE = /^[a-z0-9][a-z0-9_-]{0,31}$/;

/** Why `name` cannot name a new endpoint, or null when it can. A name the
 *  server reserves (a built-in provider's) is refused by the server, which
 *  says so; this only checks the shape and the endpoints already added. */
export function endpointNameProblem(name: string, taken: readonly string[]): string | null {
  if (!name) return "Name is required.";
  if (!NAME_RE.test(name)) return "Lowercase letters, digits, - and _ only, starting with a letter or digit.";
  if (taken.includes(name)) return "An endpoint with this name already exists.";
  return null;
}

/** The secret a key typed for endpoint `name` is stored under (openrouter ->
 *  OPENROUTER_API_KEY), the same rule as the server's secret_name_for. */
export function endpointKeySecret(name: string): string {
  return `${name.toUpperCase().replace(/[^A-Z0-9]/g, "_")}_API_KEY`;
}

/** Why a base URL cannot be sent as typed, or null (the server adds /v1 to a
 *  bare host and checks the rest). */
export function baseUrlProblem(url: string): string | null {
  const text = url.trim();
  if (!text) return "Base URL is required.";
  if (!/^https?:\/\/[^/\s]+/i.test(text)) return "Starts with http:// or https://, then the host.";
  return null;
}
