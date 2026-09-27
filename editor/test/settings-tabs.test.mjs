// ============================================================================
// Framework-free checks of the Settings panel's tabs (src/lib/settingsTabs.ts):
// the gear opens GENERAL, every deep link still lands on its own tab, and the
// General tab's Startup section holds the three toggles, all off on a fresh
// install. Drives the REAL module, transpiled with the installed TypeScript
// compiler (it has no imports), then checks that App and the panel use it
// (the gear passes GEAR_TAB, "Add a connection" SETTINGS_LINKS.addConnection).
// Run from editor/: `node test/settings-tabs.test.mjs`.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const read = (path) => readFileSync(resolve(here, path), "utf8");
const js = ts.transpileModule(read("../src/lib/settingsTabs.ts"), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const {
  GEAR_TAB, SETTINGS_TABS, SETTINGS_LINKS, openingTab, GENERAL_SECTIONS, SETTINGS_DEFAULTS, mayChange,
} = await import("data:text/javascript," + encodeURIComponent(js));

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

// ---- the tabs
check("three tabs, General first", SETTINGS_TABS.map((t) => t.id), ["general", "providers", "secrets"]);
check("their labels", SETTINGS_TABS.map((t) => t.label), ["General", "AI Providers", "Secrets"]);

// ---- where each opening lands
check("the gear opens General", GEAR_TAB, "general");
check("an opening that names no tab lands on the gear's", openingTab(), "general");
check("null lands on the gear's too", openingTab(null), "general");
check("Add a connection lands on AI Providers", openingTab(SETTINGS_LINKS.addConnection), "providers");
check("Open AI Providers lands on AI Providers", openingTab(SETTINGS_LINKS.openConnections), "providers");
check("Open Settings lands on General", openingTab(SETTINGS_LINKS.openSettings), "general");
check("Secrets can be asked for", openingTab("secrets"), "secrets");
check("a tab that does not exist lands on the gear's", openingTab("local-ai"), "general");

// ---- the General tab
const startup = GENERAL_SECTIONS.find((s) => s.id === "startup");
check("General opens with the Startup section", GENERAL_SECTIONS[0]?.id, "startup");
check("Startup is titled", startup?.title, "Startup");
check("its three toggles, in order", startup?.rows.map((r) => r.label),
  ["Launch with system", "Start Ollama with Boltjar", "Resume workflows after launch"]);
check("each names the setting the server keeps", startup?.rows.map((r) => r.name),
  ["launch_with_system", "start_ollama", "resume_workflows"]);
check("each explains itself in one line",
  startup?.rows.every((r) => r.hint.length > 20 && !r.hint.includes("\n")), true);
check("no copy holds an em dash",
  GENERAL_SECTIONS.flatMap((s) => [s.title, s.summary, ...s.rows.flatMap((r) => [r.label, r.hint])])
    .some((text) => text.includes(String.fromCharCode(0x2014))), false);
check("every toggle is off on a fresh install", SETTINGS_DEFAULTS,
  { launch_with_system: false, start_ollama: false, resume_workflows: false });
const iconSet = read("../src/lib/icons.tsx");
check("every tab and section icon is a name the icon set has",
  [...SETTINGS_TABS, ...GENERAL_SECTIONS].filter((x) => !iconSet.includes(`"${x.icon}":`)).map((x) => x.icon),
  []);

// ---- the wiring: App and the panel open through this module
const app = read("../src/App.tsx");
check("the gear opens on GEAR_TAB", /onOpenSettings=\{\(\) => setSettingsTab\(GEAR_TAB\)\}/.test(app), true);
check("Add a connection opens on its link's tab",
  /openConnections: \(\) => setSettingsTab\(SETTINGS_LINKS\.addConnection\)/.test(app), true);
check("the palette's Open AI Providers opens on its link's tab",
  /run: \(\) => setSettingsTab\(SETTINGS_LINKS\.openConnections\)/.test(app), true);
check("the panel is handed the tab it opens on", /initialTab=\{settingsTab\}/.test(app), true);
const panel = read("../src/components/ConnectionsWindow.tsx");
check("the panel opens on openingTab(initialTab)", (panel.match(/openingTab\(initialTab\)/g) ?? []).length, 2);
check("the panel draws its tabs from SETTINGS_TABS", /SETTINGS_TABS\.map\(/.test(panel), true);
check("the panel header reads SETTINGS", />SETTINGS</.test(panel), true);
check("its subline", /startup, providers &amp; secrets/.test(panel), true);

// ---- what a browser on another machine may change
const names = ["launch_with_system", "start_ollama", "resume_workflows"];
const localOnly = ["launch_with_system", "start_ollama"];
check("a browser on Boltjar's computer may change every setting",
  names.map((n) => mayChange({ here: true, local_only: localOnly }, n)), [true, true, true]);
check("another machine may change only what starts no program there",
  names.map((n) => mayChange({ here: false, local_only: localOnly }, n)), [false, false, true]);
const general = read("../src/components/GeneralSettings.tsx");
check("the General tab locks each row the server lists", /!mayChange\(info, row\.name\)/.test(general), true);
check("the Start Ollama button waits for a browser on Boltjar's computer",
  /disabled=\{starting \|\| !local\.editable\}/.test(panel), true);

// ---- the Ollama card's states
check("an Ollama at another machine's address that does not answer is its own state",
  /unreachable: "NOT ANSWERING"/.test(panel) && /ollamaState === "unreachable" && info\.local \?/.test(panel),
  true);
check("that state shows why, never the download link",
  /ollamaState === "unreachable" && info\.local \? \([\s\S]*?\{info\.local\.reason\}[\s\S]*?\) : \(/.test(panel)
    && !/ollamaState === "unreachable"[\s\S]{0,400}ollama\.com\/download/.test(panel),
  true);
check("the stops-it-on-exit line shows only for an Ollama Boltjar can start",
  /\{local\.startable && \(\s*<div className="prov-add-key-footer">/.test(panel), true);

if (failures) {
  console.error(`\n${failures} settings tab check(s) failed`);
  process.exit(1);
}
console.log("\nall settings tab checks passed");
