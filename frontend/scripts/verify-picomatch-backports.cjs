"use strict";

// Registry audit cannot establish the safety of a private fork. Verify the
// reviewed bytes, every installed copy, and each consumer's actual resolution.
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");

const frontendRoot = path.resolve(__dirname, "..");
const variants = Object.freeze({
  v2: Object.freeze({ name: "@mezan/picomatch-v2", version: "2.3.2-mezan.1", upstream: "picomatch@2.3.2" }),
  v4: Object.freeze({ name: "@mezan/picomatch-v4", version: "4.0.7-mezan.1", upstream: "picomatch@4.0.7" }),
});
const advisory = "GHSA-fc5q-39xc-vjpf";
const digest = bytes => crypto.createHash("sha256").update(bytes).digest("hex");

function regularFile(file) {
  assert.ok(fs.lstatSync(file).isFile(), `Backport file must be regular, not a symlink: ${file}`);
  return fs.readFileSync(file);
}

function runtimeFiles(directory) {
  assert.ok(fs.lstatSync(directory).isDirectory(), `Backport directory must not be a symlink: ${directory}`);
  const files = new Set(["LICENSE", "package.json", "index.js"]);
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    if (/\.(?:[cm]?js|json|node)$/.test(entry.name) && entry.name !== "SECURITY-PROVENANCE.json") {
      regularFile(path.join(directory, entry.name));
      files.add(entry.name);
    }
  }
  function visit(relative) {
    const absolute = path.join(directory, relative);
    assert.ok(fs.lstatSync(absolute).isDirectory(), `Backport runtime directory must not be a symlink: ${absolute}`);
    for (const entry of fs.readdirSync(absolute, { withFileTypes: true })) {
      const child = `${relative}/${entry.name}`;
      if (entry.isDirectory()) visit(child);
      else {
        regularFile(path.join(directory, child));
        files.add(child);
      }
    }
  }
  visit("lib");
  for (const file of files) regularFile(path.join(directory, file));
  return [...files].sort();
}

function reviewedProvenance(variant, root) {
  assert.ok(Object.hasOwn(variants, variant), `Unknown picomatch variant: ${variant}`);
  const bytes = regularFile(path.join(root, "vendor", `picomatch-${variant}`, "SECURITY-PROVENANCE.json"));
  const provenance = JSON.parse(bytes);
  assert.equal(provenance.upstream, variants[variant].upstream, "Unexpected upstream provenance");
  assert.equal(provenance.advisory, advisory, "Original picomatch advisory must remain recorded");
  assert.match(provenance.upstream_tarball_integrity, /^sha512-[A-Za-z0-9+/]+={0,2}$/);
  const hashes = provenance.patched_files_sha256;
  assert.ok(hashes && typeof hashes === "object" && !Array.isArray(hashes), "Missing patched-file hashes");
  for (const [file, hash] of Object.entries(hashes)) {
    assert.ok(file && !file.includes("\\") && !path.posix.isAbsolute(file)
      && file.split("/").every(part => part && part !== "." && part !== ".."), "Unsafe provenance file path");
    assert.match(hash, /^[a-f0-9]{64}$/, `Invalid SHA-256 for ${file}`);
  }
  assert.equal(hashes.LICENSE, provenance.original_files_sha256?.LICENSE, "The upstream license must be retained");
  assert.ok(Object.entries(hashes).some(([file, hash]) => file.startsWith("lib/")
    && hash !== provenance.original_files_sha256?.[file]), "A renamed upstream package is not a security backport");
  return { bytes, hashes };
}

function verifyCopy(directory, variant, { root = frontendRoot } = {}) {
  assert.ok(fs.lstatSync(directory).isDirectory(), `Backport directory must not be a symlink: ${directory}`);
  const pkg = JSON.parse(regularFile(path.join(directory, "package.json")));
  const selected = variant || Object.keys(variants).find(key => variants[key].name === pkg.name);
  assert.ok(selected && Object.hasOwn(variants, selected), `Unreviewed picomatch package: ${pkg.name}@${pkg.version}`);
  const expected = variants[selected];
  assert.equal(pkg.name, expected.name, `Unexpected picomatch identity at ${directory}`);
  assert.equal(pkg.version, expected.version, `Unexpected picomatch version at ${directory}`);
  assert.equal(pkg.private, true, "The local backport must declare its private identity");
  assert.equal(pkg.license, "MIT");
  assert.equal(pkg.main, "index.js");
  const { bytes, hashes } = reviewedProvenance(selected, root);
  assert.deepEqual(regularFile(path.join(directory, "SECURITY-PROVENANCE.json")), bytes,
    `Installed provenance differs from reviewed vendor source: ${directory}`);
  const files = runtimeFiles(directory);
  assert.deepEqual(Object.keys(hashes).sort(), files, `Incomplete runtime hash inventory: ${directory}`);
  assert.equal(files.includes("posix.js"), selected === "v4", "Preserve the major-specific public entrypoints");
  for (const file of files) {
    assert.equal(digest(regularFile(path.join(directory, file))), hashes[file], `Backport integrity: ${directory}/${file}`);
  }
  return selected;
}

function installedPackages(root) {
  const packages = [];
  function inspect(directory) {
    assert.ok(fs.lstatSync(directory).isDirectory(), `Installed package directory must not be a symlink: ${directory}`);
    const file = path.join(directory, "package.json");
    if (fs.existsSync(file)) packages.push({ directory, pkg: JSON.parse(regularFile(file)) });
    scan(path.join(directory, "node_modules"));
  }
  function scan(directory) {
    if (!fs.existsSync(directory)) return;
    assert.ok(fs.lstatSync(directory).isDirectory(), `Installed dependency directory must not be a symlink: ${directory}`);
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      if (entry.name.startsWith(".") || (!entry.isDirectory() && !entry.isSymbolicLink())) continue;
      const absolute = path.join(directory, entry.name);
      if (entry.name.startsWith("@")) {
        assert.ok(fs.lstatSync(absolute).isDirectory(), `Installed scope must not be a symlink: ${absolute}`);
        for (const child of fs.readdirSync(absolute)) inspect(path.join(absolute, child));
      } else inspect(absolute);
    }
  }
  scan(path.join(root, "node_modules"));
  return packages;
}

function verifyInstalled({ root = frontendRoot } = {}) {
  for (const variant of Object.keys(variants)) verifyCopy(path.join(root, "vendor", `picomatch-${variant}`), variant, { root });
  const packages = installedPackages(root);
  const copies = [];
  for (const row of packages) {
    if (path.basename(row.directory) === "picomatch"
      || row.pkg.name === "picomatch" || Object.values(variants).some(item => item.name === row.pkg.name)) {
      copies.push({ path: path.relative(root, row.directory), variant: verifyCopy(row.directory, undefined, { root }) });
    }
  }
  assert.deepEqual([...new Set(copies.map(copy => copy.variant))].sort(), ["v2", "v4"], "Both installed backport majors must be present");
  const consumers = [];
  for (const row of packages) {
    const range = row.pkg.dependencies?.picomatch || row.pkg.optionalDependencies?.picomatch;
    if (!range) continue;
    const major = typeof range === "string" && range.match(/^[~^]?(2|4)\.\d+\.\d+$/)?.[1];
    assert.ok(major, `Review required for new picomatch dependency range: ${row.pkg.name} ${range}`);
    const variant = `v${major}`;
    const consumer = createRequire(path.join(row.directory, "package.json"));
    const target = path.dirname(consumer.resolve("picomatch/package.json"));
    const relative = path.relative(path.join(root, "node_modules"), target);
    assert.ok(relative && !relative.startsWith("..") && !path.isAbsolute(relative), "Consumer escaped the installed dependency tree");
    verifyCopy(target, variant, { root });
    assert.equal(consumer.resolve("picomatch"), path.join(target, "index.js"), "Consumer entrypoint differs from reviewed source");
    if (row.pkg.name === "micromatch") {
      assert.equal(consumer.resolve("picomatch/lib/utils"), path.join(target, "lib/utils.js"), "Micromatch's deep import changed");
    }
    consumers.push({ name: row.pkg.name, version: row.pkg.version, variant, path: path.relative(root, row.directory),
      resolved: path.relative(root, target) });
  }
  for (const required of ["micromatch", "anymatch", "readdirp", "tinyglobby", "vite"]) {
    assert.ok(consumers.some(item => item.name === required), `Required picomatch consumer was not verified: ${required}`);
  }
  return { copies, consumers };
}

if (require.main === module) {
  const result = verifyInstalled();
  console.log(`Verified ${result.copies.length} installed picomatch backport copies and ${result.consumers.length} consumer resolutions; no upstream copy accepted.`);
}

module.exports = { frontendRoot, variants, runtimeFiles, verifyCopy, verifyInstalled };
