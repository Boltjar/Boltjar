// ============================================================================
// help: what the Help menu and the command palette's Help group point at. The
// project links, the prefilled bug report, and the diagnostics block a user can
// paste into an issue. Everything that reaches a URL or the clipboard is built
// from a fixed set of fields (app version, Python version, platform string,
// browser user agent, node count), so no path, key or graph content can leak.
// ============================================================================

export const DOCS_URL = "https://boltjar.link/docs";
export const FEEDBACK_URL = "https://github.com/Boltjar/Boltjar/discussions/new?category=ideas";
export const SPONSOR_URL = "https://github.com/sponsors/Boltjar";
const NEW_ISSUE_URL = "https://github.com/Boltjar/Boltjar/issues/new";

/** GET /api/version, reduced to the three fields the editor uses. */
export interface VersionInfo {
  version: string;
  python: string;
  platform: string;
}

/** One reported field: a single trimmed line, capped, or "" when absent. */
function field(value: unknown, max = 200): string {
  if (typeof value !== "string") return "";
  return value.replace(/[\u0000-\u001f\u007f]+/g, " ").trim().slice(0, max);
}

/**
 * The /api/version payload as a VersionInfo, keeping only `version`, `python`
 * and `platform` (anything else the server sends is dropped here). null when the
 * payload has no version, e.g. an older server without the endpoint.
 */
export function parseVersionInfo(json: unknown): VersionInfo | null {
  if (!json || typeof json !== "object") return null;
  const o = json as Record<string, unknown>;
  const version = field(o.version);
  if (!version) return null;
  return { version, python: field(o.python), platform: field(o.platform) };
}

/**
 * The OS family from the browser: "Windows", "macOS", "Linux", "ChromeOS",
 * "Android", "iOS", or "" when it cannot tell. `platform` is
 * navigator.userAgentData.platform (or navigator.platform) when available.
 */
export function osName(userAgent: string, platform = ""): string {
  const s = `${platform} ${userAgent}`;
  if (/iphone|ipad|ipod|\bios\b/i.test(s)) return "iOS";
  if (/android/i.test(s)) return "Android";
  if (/\bcros\b|chrome ?os/i.test(s)) return "ChromeOS";
  if (/windows|win32|win64/i.test(s)) return "Windows";
  if (/mac/i.test(s)) return "macOS";
  if (/linux|x11/i.test(s)) return "Linux";
  return "";
}

/**
 * The GitHub new-issue link with the bug report form prefilled: app version,
 * OS and Python version. A value the editor does not know is left out, so the
 * form asks for it instead of receiving a placeholder.
 */
export function bugReportUrl(info: VersionInfo | null, os: string): string {
  const params: Array<[string, string]> = [
    ["template", "bug_report.yml"],
    ["app_version", info?.version ?? ""],
    ["os", field(os)],
    ["python", info?.python ?? ""],
  ];
  const query = params
    .filter(([, v]) => v)
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`)
    .join("&");
  return `${NEW_ISSUE_URL}?${query}`;
}

/** The text "Copy diagnostics" puts on the clipboard, one field per line. */
export function diagnosticsText(d: { info: VersionInfo | null; userAgent: string; nodeCount: number }): string {
  const known = (v: string | undefined) => v || "unknown";
  return [
    `Boltjar: ${known(d.info?.version)}`,
    `Python: ${known(d.info?.python)}`,
    `Platform: ${known(d.info?.platform)}`,
    `Browser: ${known(field(d.userAgent, 400))}`,
    `Nodes in open graph: ${d.nodeCount}`,
  ].join("\n");
}

/** Open a project link in a new tab, with no handle back to the editor. */
export function openExternal(url: string): void {
  window.open(url, "_blank", "noopener,noreferrer");
}

/** Put `text` on the clipboard. The async Clipboard API needs a secure context
 *  (localhost is one, a LAN address over http is not), so fall back to a
 *  hidden textarea and execCommand. Resolves false when both are refused. */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch { /* no Clipboard API here: try the textarea route */ }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  } catch {
    return false;
  }
}
