// ============================================================================
// Framework-free harness for the Help menu's data (src/lib/help.ts): the
// /api/version payload is reduced to version / python / platform, the OS is
// read from the browser, the bug report link carries exactly those prefill
// fields (URL-encoded, unknown ones left out), and Copy diagnostics holds
// nothing beyond the fixed fields. Drives the REAL module, transpiled with the
// installed TypeScript compiler. Run from editor/: `node test/help.test.mjs`.
// ============================================================================
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(resolve(here, "../src/lib/help.ts"), "utf8");
const js = ts.transpileModule(src, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const { parseVersionInfo, osName, bugReportUrl, diagnosticsText, DOCS_URL, FEEDBACK_URL, SPONSOR_URL } =
  await import("data:text/javascript," + encodeURIComponent(js));

let failures = 0;
function check(label, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  const ok = g === w;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${g}, want ${w})`}`);
}

// ---- links
check("docs", DOCS_URL, "https://boltjar.link/docs");
check("feedback goes to the ideas category", FEEDBACK_URL, "https://github.com/Boltjar/Boltjar/discussions/new?category=ideas");
check("sponsors", SPONSOR_URL, "https://github.com/sponsors/Boltjar");

// ---- /api/version payload
const info = parseVersionInfo({ version: "0.1.0", python: "3.12.4", platform: "Windows-11-10.0.26200-SP0" });
check("the three fields", info, { version: "0.1.0", python: "3.12.4", platform: "Windows-11-10.0.26200-SP0" });
check("extra fields are dropped",
  parseVersionInfo({ version: "0.1.0", python: "3.12.4", platform: "Linux", root: "C:\\Users\\alice\\boltjar", token: "abc123" }),
  { version: "0.1.0", python: "3.12.4", platform: "Linux" });
check("no version is no info", parseVersionInfo({ python: "3.12.4" }), null);
check("an error body is no info", parseVersionInfo({ detail: "Not Found" }), null);
check("null is no info", parseVersionInfo(null), null);
check("a string is no info", parseVersionInfo("0.1.0"), null);
check("non-string fields read as empty", parseVersionInfo({ version: "0.2.0", python: 3.12, platform: null }),
  { version: "0.2.0", python: "", platform: "" });
check("control characters and newlines flatten to one line",
  parseVersionInfo({ version: " 0.1.0\n", python: "3.12.4\r\n(main)", platform: "Linux\tx86_64" }),
  { version: "0.1.0", python: "3.12.4 (main)", platform: "Linux x86_64" });

// ---- OS from the browser
const UA = {
  winChrome: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
  winEdge: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
  macSafari: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
  linuxFirefox: "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
  android: "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Mobile Safari/537.36",
  iphone: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
  chromeos: "Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
};
check("Windows Chrome", osName(UA.winChrome, "Win32"), "Windows");
check("Windows Edge", osName(UA.winEdge, "Windows"), "Windows");
check("macOS Safari", osName(UA.macSafari, "MacIntel"), "macOS");
check("Linux Firefox", osName(UA.linuxFirefox, "Linux x86_64"), "Linux");
check("Android before Linux", osName(UA.android, "Linux armv81"), "Android");
check("iPhone before macOS", osName(UA.iphone, "iPhone"), "iOS");
check("ChromeOS", osName(UA.chromeos, "Linux x86_64"), "ChromeOS");
check("UA-CH platform alone", osName("", "Chrome OS"), "ChromeOS");
check("unknown", osName("SomeBot/1.0", ""), "");

// ---- bug report link
check("prefilled from the server and the browser",
  bugReportUrl(info, "Windows"),
  "https://github.com/Boltjar/Boltjar/issues/new?template=bug_report.yml&app_version=0.1.0&os=Windows&python=3.12.4");
check("values are URL-encoded",
  bugReportUrl({ version: "0.1.0+dev 2", python: "3.12.4 (main, &x=1)", platform: "Linux" }, "macOS"),
  "https://github.com/Boltjar/Boltjar/issues/new?template=bug_report.yml&app_version=0.1.0%2Bdev%202&os=macOS&python=3.12.4%20(main%2C%20%26x%3D1)");
check("no version endpoint: only what the browser knows",
  bugReportUrl(null, "Linux"),
  "https://github.com/Boltjar/Boltjar/issues/new?template=bug_report.yml&os=Linux");
check("nothing known: the bare form",
  bugReportUrl(null, ""),
  "https://github.com/Boltjar/Boltjar/issues/new?template=bug_report.yml");
const params = [...new URL(bugReportUrl(info, "Windows")).searchParams.keys()];
check("exactly the prefill fields, no labels, no logs", params, ["template", "app_version", "os", "python"]);

// ---- Copy diagnostics
check("diagnostics text",
  diagnosticsText({ info, userAgent: UA.winChrome, nodeCount: 17 }),
  [
    "Boltjar: 0.1.0",
    "Python: 3.12.4",
    "Platform: Windows-11-10.0.26200-SP0",
    `Browser: ${UA.winChrome}`,
    "Nodes in open graph: 17",
  ].join("\n"));
check("diagnostics without the version endpoint",
  diagnosticsText({ info: null, userAgent: "", nodeCount: 0 }),
  "Boltjar: unknown\nPython: unknown\nPlatform: unknown\nBrowser: unknown\nNodes in open graph: 0");
const leaky = parseVersionInfo({ version: "0.1.0", python: "3.12.4", platform: "Linux", root: "/home/alice/boltjar", token: "sk-secret" });
const diag = diagnosticsText({ info: leaky, userAgent: UA.linuxFirefox, nodeCount: 3 });
check("a path or key in the payload never reaches diagnostics",
  diag.includes("/home/alice") || diag.includes("sk-secret"), false);
check("one field per line", diag.split("\n").length, 5);

if (failures) {
  console.log(`\n${failures} help check${failures === 1 ? "" : "s"} failed`);
  process.exit(1);
}
console.log("\nall help checks passed");
