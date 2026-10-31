"use strict";
// Runtime compatibility proof for security-patched transitive dependencies.
// Tested only on isolated CI/worktree; no provider, ledger, or production writes.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const ExcelJS = require("exceljs");
const glob = require("glob");
const { minimatch } = require("minimatch");
const archiver = require("archiver");
const { version: minimatchVersion } = require("minimatch/package.json");
const { version: redirectVersion } = require("follow-redirects/package.json");

async function main() {
  assert.equal(minimatchVersion, "11.0.3", "unexpected minimatch version");
  assert.equal(redirectVersion, "1.16.2", "unexpected follow-redirects version");
  assert.equal(minimatch("file.xlsx", "*.xlsx"), true);
  const matches = glob.globSync("src/pages/operational/*.jsx", {cwd: process.cwd()});
  assert.ok(Array.isArray(matches) && matches.length > 0,
    "modern glob must match source files with patched minimatch 11");

  const workbook = new ExcelJS.Workbook();
  workbook.addWorksheet("Balance").addRow(["SAR", 123]);
  const bytes = await workbook.xlsx.writeBuffer();
  const restored = new ExcelJS.Workbook();
  await restored.xlsx.load(bytes);
  assert.equal(restored.getWorksheet("Balance").getCell("B1").value, 123,
    "ExcelJS XLSX round-trip changed");

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "mezan-security-compat-"));
  try {
    fs.writeFileSync(path.join(dir, "sample.txt"), "verified\n");
    const archive = archiver("zip", {zlib: {level: 1}});
    const chunks = [];
    await new Promise((resolve, reject) => {
      archive.on("data", chunk => chunks.push(chunk));
      archive.once("error", reject);
      archive.once("end", resolve);
      archive.directory(dir, "proof");
      Promise.resolve(archive.finalize()).catch(reject);
    });
    assert.ok(Buffer.concat(chunks).length > 100, "archiver ZIP proof missing");
  } finally {
    fs.rmSync(dir, {recursive: true, force: true});
  }
  console.log("OPERATIONAL_DEPENDENCY_COMPAT_PASS",
    JSON.stringify({minimatch: minimatchVersion, redirects: redirectVersion, matched: matches.length}));
}

main().catch(error => {
  console.error("OPERATIONAL_DEPENDENCY_COMPAT_FAIL:", error?.stack || error);
  process.exitCode = 1;
});
