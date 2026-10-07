// ============================================================================
// A resizable node with no saved size (a freshly dropped Database node, a graph
// written through the API) renders at its width floor, not as narrow as its
// content: its card fills a React Flow wrapper (width:100%) that has no width
// of its own until a size is saved. Reads WorkflowNode.tsx and editor.css.
// The floor is rendered, never written to the node, so opening a graph does not
// mark it unsaved. Run from editor/: `node test/node-floor.test.mjs`.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const node = readFileSync(resolve(here, "../src/components/canvas/WorkflowNode.tsx"), "utf8");
const css = readFileSync(resolve(here, "../src/styles/editor.css"), "utf8");

let failures = 0;
function check(label, ok) {
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}`);
}

check("a resizable card fills its wrapper", /\.node\.resizable\{width:100%;/.test(css));
check("a resizable card renders at least its width floor",
  /style=\{\{[^}]*\.\.\.\(resizable \? \{ minWidth: minNodeW \} : \{\}\)/.test(node));
check("the resizer drags down to the same floor", /<NodeResizer[^>]*minWidth=\{minNodeW\}/.test(node));
// the clamp writes a size only when a SAVED one is under the floor
const clamp = node.slice(node.indexOf("Enforce the min at RENDER"), node.indexOf("the Router is a tiny pill"));
check("the clamp writes only when a size is under the floor", /if \(w < minNodeW \|\| h < minNodeH\)/.test(clamp));
check("a node with no size reads as at the floor, so the clamp leaves it unsaved",
  /typeof n\.width === "number" \? n\.width : minNodeW/.test(clamp) && /typeof n\.height === "number" \? n\.height : minNodeH/.test(clamp));

if (failures) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nnode floor: all checks passed");
