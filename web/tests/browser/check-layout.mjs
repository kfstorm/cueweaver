import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const url = new URL(process.argv[2] ?? "http://127.0.0.1:5173");
if (!["127.0.0.1", "localhost", "[::1]"].includes(url.hostname))
  throw new Error("Use a local development server for synthetic layout checks");
const fixture = fileURLToPath(new URL("./layout-fixture.js", import.meta.url));
const contract = readFileSync(new URL("./layout-contract.js", import.meta.url), "utf8");
const session = execFileSync(
  "agent-browser",
  ["session", "id", "--scope", "worktree", "--prefix", "layout-check"],
  { encoding: "utf8" },
).trim();
const browser = (...args) =>
  execFileSync("agent-browser", ["--session", session, ...args], {
    encoding: "utf8",
    timeout: 60_000,
  });
const failures = [];
let checks = 0;
function audit(context) {
  browser("wait", "--fn", "!!document.querySelector('main h1')");
  const result = JSON.parse(browser("--json", "eval", contract));
  if (!result.success) throw new Error(JSON.stringify(result));
  for (const failure of result.data.result.failures)
    failures.push(`${context}: ${failure}`);
  checks += 1;
}
function open(path) {
  browser("open", new URL(path, url).href);
}
try {
  browser("--init-script", fixture, "open", new URL("/translate", url).href);
  for (const locale of ["zh-CN", "de"]) {
    for (const theme of ["light", "dark"]) {
      browser(
        "eval",
        `sessionStorage.setItem('layout-locale', '${locale}'); sessionStorage.setItem('layout-theme', '${theme}')`,
      );
      for (const width of [390, 767, 768, 769, 1024, 1280]) {
        browser("set", "viewport", String(width), width < 768 ? "844" : "800");
        for (const path of [
          "/translate",
          "/jobs",
          "/jobs/example-job-0123456789abcdef0123456789abcdef",
          "/settings/general",
          "/settings/model-profiles",
          "/settings/term-maps",
          "/settings/term-maps/automatic",
        ]) {
          open(path);
          audit(`${locale}/${theme}/${width}${path}`);
          if (path === "/translate") {
            browser("click", ".checkbox-field input");
            audit(`${locale}/${theme}/${width}/translate/batch`);
          }
          if (path === "/settings/term-maps") {
            browser("wait", ".term-map-item");
            browser("click", ".term-map-item");
            browser("wait", "table tbody tr");
            audit(`${locale}/${theme}/${width}/term-map-detail`);
          }
          if (path === "/settings/term-maps/automatic") {
            browser("wait", ".directory-rule-list button");
            browser("click", ".directory-rule-list button");
            audit(`${locale}/${theme}/${width}/directory-rule-editor`);
          }
        }
      }
    }
  }
  browser(
    "eval",
    "sessionStorage.setItem('layout-locale','zh-CN'); sessionStorage.setItem('layout-theme','light')",
  );
  for (const width of [390, 1280]) {
    browser("set", "viewport", String(width), "844");
    for (const state of ["loading", "empty", "error"]) {
      browser("eval", `sessionStorage.setItem('layout-state', '${state}')`);
      for (const path of [
        "/translate",
        "/jobs",
        "/settings/model-profiles",
        "/settings/term-maps",
        "/settings/term-maps/automatic",
      ]) {
        open(path);
        audit(`${width}/${state}${path}`);
      }
    }
    browser("eval", "sessionStorage.setItem('layout-state','populated')");
    for (const state of ["loading", "empty", "error"]) {
      browser("eval", `sessionStorage.setItem('layout-profiles', '${state}')`);
      open("/translate");
      audit(`${width}/translate/model-profiles-${state}`);
    }
    browser("eval", "sessionStorage.removeItem('layout-profiles')");
    open("/settings/model-profiles/new");
    browser("wait", "#profile-provider");
    browser("select", "#profile-provider", "OpenAI");
    browser("wait", "#profile-option-api_key");
    audit(`${width}/model-profile-editor`);
    browser("focus", "#profile-option-api_key");
    browser("press", "Tab");
    audit(`${width}/model-profile-editor/keyboard`);
    browser("press", "Tab");
    audit(`${width}/model-profile-editor/save-keyboard`);
    browser("press", "Tab");
    audit(`${width}/model-profile-editor/cancel-keyboard`);
    open("/settings/term-maps");
    browser("wait", ".term-map-library-toolbar button");
    browser("click", ".term-map-library-toolbar button");
    browser("wait", "#term-map-content");
    audit(`${width}/term-map-upload`);
    browser("eval", "sessionStorage.setItem('layout-theme','system')");
    open("/settings/general");
    for (const appearance of ["dark", "light"]) {
      browser("set", "media", appearance);
      browser(
        "wait",
        "--fn",
        `document.documentElement.dataset.theme === '${appearance}'`,
      );
      audit(`${width}/system-${appearance}`);
    }
    browser("eval", "sessionStorage.setItem('layout-theme','light')");
  }
} finally {
  browser("close");
}
if (failures.length) throw new Error(failures.join("\n"));
console.log(`Passed ${checks} rendered layout checks with Agent Browser.`);
