// ============================================================================
// startSession: fetch this browser's session cookie, then start the editor.
//
// The server wants its per-install token on every /api, /ws and /audio
// call, and a browser holds it as an HttpOnly cookie that GET /api/session sets.
// GET / sets it too, but under the Vite dev server the page comes from Vite, so
// the editor asks once, before anything else fetches. It starts whatever the
// outcome: an unreachable server shows up in the editor, never as a blank page.
// ============================================================================

export async function startSession(start: () => void, fetchImpl: typeof fetch = fetch): Promise<void> {
  try {
    await fetchImpl("/api/session");
  } catch {
    // the server is down; the editor's own calls report it once it renders.
  }
  start();
}
