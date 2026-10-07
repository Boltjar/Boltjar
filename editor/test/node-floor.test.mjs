// ============================================================================
// A resizable node with no saved size (a freshly dropped Database node, a graph
// written through the API) renders at its width floor, not as narrow as its
// content: its card fills a React Flow wrapper (width:100%) that has no width
// of its own until a size is saved. Reads WorkflowNode.tsx and editor.css.
// The floor is rendered, never written to the node, so opening a graph does not
// mark it unsaved.
//
// It also sits at its natural height with nothing cut: every text surface is
// as tall as its text (src/lib/useAutoGrow.ts, driven for real below). Before,
// an expand field took a flex share of a height the node did not have, so a
// size-less DB node cut its SQL mid-line at 64 px. Only a node that was GIVEN a
// height (saved, a type default, or dragged) fills it, its text scrolling.
// Run from editor/: `node test/node-floor.test.mjs`.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

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

// ---- natural height: text surfaces grow to their text
const grow = readFileSync(resolve(here, "../src/lib/useAutoGrow.ts"), "utf8");
const js = ts.transpileModule(grow.replace(/^import .*$/m, "const useLayoutEffect = () => {};"), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const { grownHeight, AUTOGROW_CAP } = await import("data:text/javascript," + encodeURIComponent(js));
check("a grown field shows all its text plus its borders", grownHeight(91, 2) === 93);
check("it stops at the cap and scrolls past it", grownHeight(900, 2) === AUTOGROW_CAP);

const field = readFileSync(resolve(here, "../src/components/SecretAutocompleteField.tsx"), "utf8");
const tpl = readFileSync(resolve(here, "../src/components/TemplateField.tsx"), "utf8");
check("the secret-aware field grows with the shared hook", /useAutoGrow\(ref, /.test(field));
check("the Template editor grows with the shared hook", /useAutoGrow\(taRef, /.test(tpl));
check("a plain code field grows with the shared hook", /useAutoGrow\(codeRef, /.test(node));
check("a node fills only a height it was given (not the measured one)",
  /useInternalNode\(id\)\?\.height/.test(node) && /const fill = resizable && typeof givenHeight === "number"/.test(node));
check("an expand field grows unless it fills a given height", /const grows = !\(widget\.expand && fill\)/.test(node));
check("a node with a given height is marked sized", /fill \? "sized" : ""/.test(node));
check("expand fields share the height only on a sized node",
  /\.node\.resizable\.sized \.ib\.code\.expand, \.node\.resizable\.sized \.tplfield\{flex:1 1 0;/.test(css)
  && /\.node\.resizable \.ib\.code\.expand, \.node\.resizable \.tplfield\{flex:none;/.test(css));
check("a secret-aware textarea keeps the height it is given",
  /\.secret-ac textarea\{\s*flex:none;/.test(css));

if (failures) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nnode floor: all checks passed");
