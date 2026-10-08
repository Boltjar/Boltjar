// ============================================================================
// The toast stack (src/lib/toasts.ts) and where the editor uses it. A choice
// the editor asks comes as a toast with its buttons: at most three cards show,
// more wait their turn, × hides a card without answering it, and an answer
// from anywhere takes the card away. Drives the REAL module (transpiled with
// the installed TypeScript compiler; its one import is a type), then reads
// the socket hook, the status bar, the canvas and editor.css. Run from
// editor/: `node test/toasts.test.mjs`.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const read = (rel) => readFileSync(resolve(here, rel), "utf8");
const js = ts.transpileModule(read("../src/lib/toasts.ts"), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const { raise, hide, settle, shownToasts, queuedToasts, createToastStore, EMPTY_TOASTS, TOAST_STACK, PLAIN_TOAST_MS } =
  await import("data:text/javascript," + encodeURIComponent(js));

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

const ask = (offer, message = offer) => ({ level: "warn", message, offer, actions: [{ label: "Yes", run() {} }, { label: "No", run() {} }] });
const words = (list) => list.map((t) => t.message);

// ---- the stack
check("three cards show at once", TOAST_STACK, 3);
check("a plain toast shows for about four seconds", PLAIN_TOAST_MS, 4000);
{
  let s = EMPTY_TOASTS;
  for (const o of ["a", "b", "c", "d", "e"]) s = raise(s, ask(o));
  check("the first three show, oldest first (the newest at the bottom)", words(shownToasts(s)), ["a", "b", "c"]);
  check("a fourth and a fifth wait, in order", words(queuedToasts(s)), ["d", "e"]);
  const firstId = s.list[0].id;
  s = hide(s, firstId);
  check("a card leaving lets the next waiting one show", words(shownToasts(s)), ["b", "c", "d"]);
  check("and the rest keep waiting", words(queuedToasts(s)), ["e"]);
  check("every card has its own id", new Set(s.list.map((t) => t.id)).size, s.list.length);
}

// ---- × hides, an answer settles
{
  let s = raise(EMPTY_TOASTS, ask("aside:x"));
  const id = s.list[0].id;
  const hidden = hide(s, id);
  check("× hides the card", hidden.list.length, 0);
  check("hiding an unknown card returns the same state", hide(hidden, 999) === hidden, true);
  s = raise(raise(s, ask("aside:y")), { level: "bad", message: "text ✗ number" });
  const settled = settle(s, "aside:x");
  check("settling an offer takes its card away", words(settled.list), ["aside:y", "text ✗ number"]);
  check("settling an offer nothing carries returns the same state", settle(settled, "nope") === settled, true);
  const again = raise(s, ask("aside:x", "asked again"));
  check("the same offer asked again takes its card's place", words(again.list), ["asked again", "aside:y", "text ✗ number"]);
  check("with a new id, so it draws anew", again.list[0].id !== s.list[0].id, true);
}

// ---- plain toasts
{
  let s = raise(EMPTY_TOASTS, { level: "bad", message: "text ✗ number" });
  const first = s.list[0].id;
  s = raise(s, { level: "bad", message: "text ✗ number" });
  check("the same plain words raised again replace their card", s.list.length, 1);
  check("and start its clock again (a new id)", s.list[0].id !== first, true);
  s = raise(s, { level: "bad", message: "cannot wire a node to itself" });
  check("different words are a card of their own", s.list.length, 2);
}

// ---- the store
{
  const store = createToastStore();
  let calls = 0;
  const off = store.subscribe(() => { calls += 1; });
  store.raise(ask("aside:z"));
  store.raise({ level: "info", message: "hello" });
  const id = store.get().list[1].id;
  store.hide(id);
  store.settle("aside:z");
  check("the store runs the same rules", store.get().list.length, 0);
  check("each change tells the subscribers", calls, 4);
  store.settle("aside:z");
  check("a change that changes nothing tells nobody", calls, 4);
  off();
  store.raise({ level: "info", message: "after" });
  check("an unsubscribed listener hears nothing", calls, 4);
}

// ---- wiring: every notice with actions raises a toast, and answers settle
const hook = read("../src/hooks/useRunSocket.ts");
check("a notice with actions raises a toast", /toasts\.raise\(\{ level, message, offer: offer\.id, actions \}\)/.test(hook), true);
check("each button settles the offer before it runs", /run: \(\) => \{ settleNotice\(offer\.id\); a\.run\(\); \}/.test(hook), true);
check("settling the offer takes the toast away", /settleNotice = useCallback\(\(id: string\) => \{[\s\S]*?toasts\.settle\(id\);/.test(hook), true);
const app = read("../src/App.tsx");
check("the editor mounts one toast stack", (app.match(/<Toaster \/>/g) || []).length, 1);
check("no call site raises an offer toast of its own", /toasts\.raise\([^)]*offer/.test(app), false);
check("a refused wire is a plain toast", /toasts\.raise\(\{ level: "bad", message: rejection\.reason \}\)/.test(app), true);

const toast = read("../src/components/Toast.tsx");
check("a toast with buttons is an alertdialog, a plain one a status", /role=\{asks \? "alertdialog" : "status"\}/.test(toast), true);
check("the card is labelled by its message", /aria-labelledby=\{msgId\}/.test(toast) && /id=\{msgId\}/.test(toast), true);
check("Esc on a focused toast hides it", /e\.key === "Escape"[\s\S]{0,80}onHide\(\)/.test(toast), true);
check("the first button is primary, the rest quiet", /className=\{i === 0 \? "confirm-ok" : "confirm-cancel"\}/.test(toast), true);
check("a plain toast's clock pauses while the pointer is on it", /onMouseEnter=\{\(\) => setHeld\(true\)\}/.test(toast) && /if \(asks \|\| held\) return;/.test(toast), true);

// ---- the status bar is words only; the canvas has no toast of its own
const bar = read("../src/components/StatusBar.tsx");
const lineJsx = bar.slice(bar.indexOf('<div className="sb-tail"'), bar.indexOf('<div className="sb-mid">'));
check("the status line has no buttons", /<button|OfferButtons/.test(lineJsx), false);
check("the status line has no 'N choices' collapse", /choicesLabel|offerFits|statusFit|sb-measure|setCollapsed/.test(bar), false);
check("the console rows keep their buttons", /\{entry\.actions && <OfferButtons actions=\{entry\.actions\} \/>\}/.test(bar), true);
check("the counters keep their words in titles when they drop them", (bar.match(/className="tl"/g) || []).length, 4);
const canvas = read("../src/components/canvas/Canvas.tsx");
check("the canvas has no reject-toast of its own", /reject-toast|rejectionReason/.test(canvas), false);

const css = read("../src/styles/editor.css");
check("no .reject-toast style is left", /\.reject-toast/.test(css), false);
check("the line clips inside its own box, never over the counters", /\.sb-tail\{[^}]*min-width:0;[^}]*overflow:hidden;/.test(css), true);
check("the counters and the right side keep their room", /\.sb-mid, \.sb-right\{flex:none; white-space:nowrap;\}/.test(css), true);
check("the message ellipsizes", /\.sb-tail \.msg\{[^}]*text-overflow:ellipsis;[^}]*min-width:0;/.test(css), true);
check("a narrow bar sheds the cursor, then the words",
  /@container statusline \(max-width: 1180px\)\{ \.sb-right \.coord\.cursor\{display:none;\} \}/.test(css)
  && /@container statusline \(max-width: 940px\)\{ \.sb-mid \.tl, \.sb-right \.sb-btn \.tl\{display:none;\}/.test(css), true);
const toastCss = css.slice(css.indexOf(".toasts{"), css.indexOf("@media (prefers-reduced-motion: reduce){ .toast{animation:none;} }"));
check("a toast has a full border in its level's colour", /\.toast\{[^}]*border:1px solid rgba\(var\(--lvl-rgb\),0\.5\);/.test(toastCss), true);
check("never a single-side stripe", /border-(left|right|top|bottom)\s*:/.test(toastCss), false);
check("the message takes the card's width (no max-width on it)", /\.toast-msg\{[^}]*max-width/.test(toastCss), false);
check("the card is about 360 px wide", /\.toasts\{[^}]*width:360px;/.test(toastCss), true);
check("it enters with the 6 px rise in 160 ms", /animation:fadeup var\(--dur-2\)/.test(toastCss) && /--dur-2: 160ms;/.test(read("../src/styles/tokens.css")), true);
check("still for reduced motion", /@media \(prefers-reduced-motion: reduce\)\{ \.toast\{animation:none;\} \}/.test(css), true);

if (failures) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\ntoasts: all checks passed");
