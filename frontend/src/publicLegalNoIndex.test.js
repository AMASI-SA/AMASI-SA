import {
  applyPublicLegalNoIndex,
  isNoIndexLegalPath,
  LEGAL_NOINDEX_DIRECTIVES,
  normalizePublicLegalPath,
} from "./publicLegalNoIndex";

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const viteSource = fs.readFileSync(path.join(__dirname, "..", "vite.config.js"), "utf8");

function configuredViteHeaders() {
  // Evaluate the delivered configuration; stub build plugins, not header policy.
  const sandbox = {
    module: { exports: null }, process: { env: {} }, __dirname: path.join(__dirname, ".."), path,
    defineConfig: (configure) => configure, react: () => ({}), transformWithOxc: () => {},
    buildContract: { clientEnvAllowlist: [] },
    governedPreview: { governedPreviewCacheHeaders: () => ({}) },
  };
  vm.runInNewContext(viteSource.replace(/^import .*;\r?$/gm, "")
    .replace("export default defineConfig", "module.exports = defineConfig"), sandbox);
  return sandbox.module.exports({ mode: "production" });
}

afterEach(() => {
  document.head
    .querySelectorAll('meta[data-mezan-legal-noindex="true"]')
    .forEach((node) => node.remove());
  document.documentElement.removeAttribute("data-mezan-search-index");
});

test("only the three public legal routes are marked noindex", () => {
  expect(isNoIndexLegalPath("/privacy-policy")).toBe(true);
  expect(isNoIndexLegalPath("/privacy-policy/?from=meta")).toBe(true);
  expect(isNoIndexLegalPath("/data-deletion#instructions")).toBe(true);
  expect(isNoIndexLegalPath("/terms/")).toBe(true);
  expect(isNoIndexLegalPath("/")).toBe(false);
  expect(isNoIndexLegalPath("/login")).toBe(false);
  expect(normalizePublicLegalPath("/terms/")).toBe("/terms");
});

test("legal routes receive robots, Googlebot, and Bingbot noindex metadata", () => {
  expect(applyPublicLegalNoIndex("/privacy-policy")).toBe(true);

  for (const name of ["robots", "googlebot", "bingbot"]) {
    const tag = document.head.querySelector(`meta[name="${name}"]`);
    expect(tag).not.toBeNull();
    expect(tag.getAttribute("content")).toBe(LEGAL_NOINDEX_DIRECTIVES);
  }
  expect(document.documentElement.getAttribute("data-mezan-search-index")).toBe("blocked");
});

test("ordinary application routes remain untouched", () => {
  expect(applyPublicLegalNoIndex("/dashboard-v2")).toBe(false);
  expect(document.head.querySelector('meta[name="robots"]')).toBeNull();
});

test("Vite development and preview servers retain global noindex security headers including legal paths", () => {
  const config = configuredViteHeaders();
  for (const surface of [config.server, config.preview]) {
    expect(surface.headers["X-Robots-Tag"]).toBe(LEGAL_NOINDEX_DIRECTIVES);
    expect(surface.headers["X-Content-Type-Options"]).toBe("nosniff");
  }
  // Browser metadata remains restricted to these three routes; the delivered
  // server header is deliberately global, including ordinary application URLs.
  for (const route of ["/privacy-policy", "/data-deletion", "/terms"]) {
    expect(isNoIndexLegalPath(route)).toBe(true);
  }
});
