// ============================================================================
// Every icon name the editor or a shipped node asks for is in the icon
// registry (src/lib/icons.tsx). An unknown name renders as a hollow circle
// (Icon's fallback), which is easy to miss: Settings > AI Providers showed
// circles for "library-outline" and "open-outline" until this check existed.
// Run from editor/: `node test/icon-names.test.mjs`.
//
// Reads with the TypeScript parser, not regular expressions:
//  - the registry: the keys of the REGISTRY object literal and every
//    REGISTRY["name"] = ... assignment;
//  - the editor: every string literal inside the `name` of an <Icon>, inside
//    an `icon` JSX attribute, and inside the value of an `icon` property (menu
//    items, palette actions, provider rows), across src/;
//  - the nodes: the `icon` of every served core node (fixtures/core-nodes.json,
//    kept equal to the live registry by tests/test_node_look.py) and of the
//    example custom nodes (examples/custom_nodes/*/nodes.py, `icon="..."`).
// ============================================================================
import { readFileSync, readdirSync, statSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve, join, relative } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const srcDir = resolve(here, "../src");

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.(ts|tsx)$/.test(name) && !/\.d\.ts$/.test(name)) out.push(p);
  }
  return out;
}
const parse = (file) =>
  ts.createSourceFile(file, readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true,
    file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);

/** Every string literal inside `node` (a ternary or a list gives all of its). */
function strings(node, out = []) {
  if (!node) return out;
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) out.push(node.text);
  ts.forEachChild(node, (c) => strings(c, out));
  return out;
}
const propName = (n) => (n && (ts.isIdentifier(n) || ts.isStringLiteral(n)) ? n.text : null);

// ── the registry ──
const registered = new Set();
{
  const sf = parse(resolve(srcDir, "lib/icons.tsx"));
  const visit = (n) => {
    if (ts.isVariableDeclaration(n) && propName(n.name) === "REGISTRY" && n.initializer && ts.isObjectLiteralExpression(n.initializer)) {
      for (const p of n.initializer.properties) {
        const key = propName(p.name);
        if (key) registered.add(key);
      }
    }
    if (ts.isBinaryExpression(n) && n.operatorToken.kind === ts.SyntaxKind.EqualsToken &&
        ts.isElementAccessExpression(n.left) && propName(n.left.expression) === "REGISTRY" &&
        ts.isStringLiteral(n.left.argumentExpression)) {
      registered.add(n.left.argumentExpression.text);
    }
    ts.forEachChild(n, visit);
  };
  visit(sf);
}

// ── what the editor asks for ──
const used = new Map(); // name -> where
const use = (name, where) => { if (!used.has(name)) used.set(name, where); };
for (const file of walk(srcDir)) {
  const sf = parse(file);
  const rel = relative(resolve(here, ".."), file).replace(/\\/g, "/");
  const visit = (n) => {
    if (ts.isJsxAttribute(n) && n.initializer) {
      const attr = propName(n.name);
      const el = n.parent?.parent; // JsxAttributes -> opening/self-closing element
      const tag = el && (ts.isJsxOpeningElement(el) || ts.isJsxSelfClosingElement(el)) ? el.tagName.getText(sf) : "";
      if ((attr === "name" && tag === "Icon") || attr === "icon") {
        for (const s of strings(n.initializer)) use(s, `${rel}:${sf.getLineAndCharacterOfPosition(n.getStart()).line + 1}`);
      }
    }
    if (ts.isPropertyAssignment(n) && propName(n.name) === "icon") {
      for (const s of strings(n.initializer)) use(s, `${rel}:${sf.getLineAndCharacterOfPosition(n.getStart()).line + 1}`);
    }
    ts.forEachChild(n, visit);
  };
  visit(sf);
}

// ── what the shipped nodes ask for ──
const core = JSON.parse(readFileSync(resolve(here, "fixtures/core-nodes.json"), "utf8"));
for (const d of core) if (d.icon) use(d.icon, `node ${d.id}`);
const customNodes = resolve(here, "../../examples/custom_nodes");
if (existsSync(customNodes)) {
  for (const folder of readdirSync(customNodes)) {
    const f = join(customNodes, folder, "nodes.py");
    if (!existsSync(f)) continue;
    for (const m of readFileSync(f, "utf8").matchAll(/\bicon\s*=\s*"([^"]+)"/g)) use(m[1], `examples/custom_nodes/${folder}/nodes.py`);
  }
}

let failures = 0;
function check(label, ok, detail = "") {
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  ${detail}`}`);
}

check("the registry was read", registered.size > 100, `(only ${registered.size} names)`);
check("the call sites were read", used.size > 80, `(only ${used.size} names)`);
check("the registry knows the names this check was written for",
  ["library-outline", "open-outline"].every((n) => registered.has(n)));
const missing = [...used].filter(([name]) => !registered.has(name));
check(`every icon asked for is registered (${used.size} names)`, missing.length === 0,
  "\n" + missing.map(([n, w]) => `      ${n}  (${w})`).join("\n"));

if (failures) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nicon names: all checks passed");
