import {
  applyPublicLegalNoIndex,
  isNoIndexLegalPath,
  LEGAL_NOINDEX_DIRECTIVES,
  normalizePublicLegalPath,
} from "./publicLegalNoIndex";

const fs = require("fs");
const path = require("path");
const cracoSource = fs.readFileSync(
  path.join(__dirname, "..", "craco.config.js"),
  "utf8",
);

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

test("frontend server sends X-Robots-Tag only for direct legal paths", () => {
  expect(cracoSource).toContain('res.setHeader("X-Robots-Tag", LEGAL_NOINDEX_DIRECTIVES)');
  expect(cracoSource).toContain('"/privacy-policy"');
  expect(cracoSource).toContain('"/data-deletion"');
  expect(cracoSource).toContain('"/terms"');
  expect(cracoSource).toContain("LEGAL_NOINDEX_PATHS.has(normalizeRequestPath(req.url))");
});
