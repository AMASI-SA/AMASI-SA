"use strict";

// A private dependency is not cleared by registry audit alone. Bind every
// installed copy to the reviewed source and verify all affected consumers.
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");
const root = path.resolve(__dirname, "..");
const vendor = path.join(root, "vendor/braces");
const provenance = require("../vendor/braces/SECURITY-PROVENANCE.json");

function verifyCopy(directory) {
  const pkg = JSON.parse(fs.readFileSync(path.join(directory, "package.json"), "utf8"));
  assert.equal(pkg.name, "@mezan/braces");
  assert.equal(pkg.version, "3.0.3-mezan.1");
  for (const [name, digest] of Object.entries(provenance.patched_files_sha256)) {
    const file = path.join(directory, name);
    assert.ok(fs.lstatSync(file).isFile(), `Missing backport file: ${file}`);
    const bytes = fs.readFileSync(file, "utf8").replace(/\r\n/g, "\n");
    assert.equal(crypto.createHash("sha256").update(bytes).digest("hex"), digest, `Backport integrity: ${file}`);
  }
}

function verifyInstalled() {
  verifyCopy(vendor);
  const copies = [];
  function inspectPackage(directory) {
    const file = path.join(directory, "package.json");
    if (fs.existsSync(file)) {
      const pkg = JSON.parse(fs.readFileSync(file, "utf8"));
      if (path.basename(directory) === "braces" || ["braces", "@mezan/braces"].includes(pkg.name)) {
        verifyCopy(directory);
        copies.push(directory);
      }
    }
    scan(path.join(directory, "node_modules"));
  }
  function scan(directory) {
    if (!fs.existsSync(directory)) return;
    for (const item of fs.readdirSync(directory, { withFileTypes: true })) {
      if (item.name.startsWith(".") || !item.isDirectory()) continue;
      const entry = path.join(directory, item.name);
      if (item.name.startsWith("@")) {
        for (const scoped of fs.readdirSync(entry)) inspectPackage(path.join(entry, scoped));
      } else inspectPackage(entry);
    }
  }
  scan(path.join(root, "node_modules"));
  assert.ok(copies.length, "No installed braces backport was verified");
  const appRequire = createRequire(path.join(root, "package.json"));
  for (const parent of ["micromatch", "chokidar"]) {
    const consumer = createRequire(appRequire.resolve(`${parent}/package.json`));
    verifyCopy(path.dirname(consumer.resolve("braces/package.json")));
  }
  const fastGlob = createRequire(appRequire.resolve("fast-glob/package.json"));
  const nestedMicromatch = createRequire(fastGlob.resolve("micromatch/package.json"));
  verifyCopy(path.dirname(nestedMicromatch.resolve("braces/package.json")));
  return copies.length;
}

if (require.main === module) console.log(`Verified ${verifyInstalled()} installed braces backport copies; no upstream vulnerable copy accepted.`);
module.exports = { verifyCopy, verifyInstalled };
