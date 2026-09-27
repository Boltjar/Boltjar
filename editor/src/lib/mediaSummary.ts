// ============================================================================
// mediaSummary: the console form of a value. Audio, images and video travel
// between nodes as data: URLs (often megabytes of base64) or as links, so the
// console names what a payload is and how big it is, never the payload itself:
//   data:audio/wav;base64,UklGR...   -> "audio/wav · 42 KB"
//   blob:... / https://x/clip.mp3    -> "media · link" / "audio · link"
//   a long reply                     -> its head, "…", then "· 1,532 chars"
// A data: URL inside a larger value (a JSON object, a log line) is replaced in
// place. Players and Preview read the raw value; only console text comes here.
// ============================================================================

// mime, `;key=value` params, the `;base64` flag, then the payload up to the
// first character that cannot belong to it (a quote, a backslash from JSON
// escaping, whitespace, a closing bracket or the runtime's cut marker). `\b`
// keeps "metadata:" out.
const DATA_URL = String.raw`\bdata:([a-z]+\/[a-z0-9.+-]+)?((?:;[a-z0-9.+-]+=[^;,\s"'\\]*)*)(;base64)?,([^\s"'\\<>()[\]{}…]*)`;
const DATA_URL_WHOLE = new RegExp(`^${DATA_URL}$`, "i");
const DATA_URL_ANY = new RegExp(DATA_URL, "gi");

// The runtime sends a value whole only when it is itself a data:/blob:/http
// URL; anything longer than its cap arrives as the head plus this one char
// (runtime._preview). A data: URL that runs into it lost the rest of its
// payload, so its size is unknown.
const CUT_MARK = "…";

/** File extension -> media kind, for links that point at a media file. */
const MEDIA_EXT: Record<string, string> = {
  wav: "audio", mp3: "audio", ogg: "audio", oga: "audio", opus: "audio",
  flac: "audio", m4a: "audio", aac: "audio", weba: "audio",
  png: "image", jpg: "image", jpeg: "image", gif: "image", webp: "image",
  avif: "image", bmp: "image", svg: "image",
  mp4: "video", m4v: "video", mov: "video", webm: "video", mkv: "video",
};

/** A byte count as a short size: "512 B", "1.5 KB", "42 KB", "3.2 MB". */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let v = bytes / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v < 10 ? Number(v.toFixed(1)) : Math.round(v)} ${units[i]}`;
}

/** Decoded size of a data: URL payload, from its length alone (no decoding). */
function payloadBytes(payload: string, base64: boolean): number {
  if (base64) {
    const body = payload.replace(/(?:=|%3d)+$/i, "");
    return Math.floor((body.length * 3) / 4);
  }
  // percent-encoded text: every %XX escape stands for one byte.
  const escapes = payload.match(/%[0-9a-f]{2}/gi)?.length ?? 0;
  return payload.length - escapes * 2;
}

/** "mime · size" for the parts of one data: URL match, or "mime · cut" when
 *  the runtime cut its payload. A data: URL with no mime is text/plain
 *  (RFC 2397). */
function describeDataUrl(mime: string | undefined, base64: string | undefined, payload: string, cut = false): string {
  const size = cut ? "cut" : formatBytes(payloadBytes(payload, !!base64));
  return `${(mime || "text/plain").toLowerCase()} · ${size}`;
}

/** The kind of media a link points at: "media" for a blob: URL (the browser
 *  holds the bytes, the URL says nothing about them), the kind of a known file
 *  extension for an http(s) link, null for any other string. */
function linkKind(s: string): string | null {
  if (/^blob:\S+$/i.test(s)) return "media";
  if (!/^https?:\/\/\S+$/i.test(s)) return null;
  const path = s.split(/[?#]/, 1)[0];
  const ext = /\.([a-z0-9]+)$/i.exec(path)?.[1]?.toLowerCase();
  return (ext && MEDIA_EXT[ext]) || null;
}

/**
 * The summary of a value that IS a piece of media: "audio/wav · 42 KB" for a
 * data: URL, "audio · link" for a link to a media file, "media · link" for a
 * blob: URL. Anything else (plain text, a page link) returns null.
 */
export function mediaSummary(value: string): string | null {
  const s = value.trim();
  const m = DATA_URL_WHOLE.exec(s);
  if (m) return describeDataUrl(m[1], m[3], m[4]);
  const kind = linkKind(s);
  return kind ? `${kind} · link` : null;
}

/** `text` with every data: URL in it replaced by its "mime · size" summary.
 *  The cut marker stays, so the line still shows the value was cut. */
export function summarizeDataUrls(text: string): string {
  const cutAt = text.endsWith(CUT_MARK) ? text.length - CUT_MARK.length : -1;
  return text.replace(
    DATA_URL_ANY,
    (m: string, mime: string | undefined, _params: string, base64: string | undefined, payload: string, offset: number) =>
      describeDataUrl(mime, base64, payload, offset + m.length === cutAt),
  );
}

/**
 * Any value as one console-safe line: media summarized, whitespace collapsed,
 * and text longer than `max` cut to its head plus the total length
 * ("the quick brown… · 1,532 chars"). Non-strings are shown as JSON.
 */
export function consoleText(value: unknown, max = 60): string {
  let s = typeof value === "string" ? value : JSON.stringify(value);
  if (s === undefined) s = String(value);
  const media = mediaSummary(s);
  if (media) return media;
  const full = summarizeDataUrls(s);
  const flat = full.replace(/\s+/g, " ").trim();
  if (flat.length <= max) return flat;
  return `${flat.slice(0, max - 1)}… · ${full.length.toLocaleString("en-US")} chars`;
}
