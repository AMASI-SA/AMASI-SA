"use strict";

const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const frontendRoot = path.resolve(__dirname, "..");
const expectedSha256 = "c81e48cc4257fc1af42c731588f47dfe3a784591bd60f1b603d46d3856f2cebd";
const workerPaths = ["sw.js", "service-worker.js"];

function loadWorker(source, { claimRejects = false } = {}) {
  const listeners = new Map();
  const calls = { skipWaiting: 0, claim: 0, unregister: 0 };
  const self = {
    addEventListener(name, listener) { listeners.set(name, listener); },
    skipWaiting() { calls.skipWaiting += 1; return Promise.resolve(); },
    clients: {
      claim() {
        calls.claim += 1;
        return claimRejects ? Promise.reject(new Error("claim failed")) : Promise.resolve();
      },
    },
    registration: {
      unregister() { calls.unregister += 1; return Promise.resolve(true); },
    },
  };
  vm.runInNewContext(source, { self }, { filename: "retirement-worker.js" });
  return { calls, listeners };
}

for (const workerPath of workerPaths) {
  test(`${workerPath} has the exact governed retirement bytes`, () => {
    const bytes = fs.readFileSync(path.join(frontendRoot, "public", workerPath));
    assert.equal(bytes.length, 574);
    assert.equal(crypto.createHash("sha256").update(bytes).digest("hex"), expectedSha256);
    const source = bytes.toString("utf8");
    const executable = source.replace(/\/\*[\s\S]*?\*\//g, "");
    assert.doesNotMatch(executable, /addEventListener\(["']fetch["']/);
    assert.doesNotMatch(executable, /\bcaches\s*\.|navigate\s*\(|delete\s*\(/);
  });

  test(`${workerPath} activates, claims when possible, and unregisters`, async () => {
    const source = fs.readFileSync(path.join(frontendRoot, "public", workerPath), "utf8");
    for (const claimRejects of [false, true]) {
      const { calls, listeners } = loadWorker(source, { claimRejects });
      assert.deepEqual([...listeners.keys()].sort(), ["activate", "install"]);
      let install;
      listeners.get("install")({ waitUntil(promise) { install = promise; } });
      await install;
      let activate;
      listeners.get("activate")({ waitUntil(promise) { activate = promise; } });
      await activate;
      assert.deepEqual(calls, { skipWaiting: 1, claim: 1, unregister: 1 });
    }
  });
}

test("retirement paths have exact edge MIME and no-cache policy before wildcard", () => {
  const expectedHeaders = {
    "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
    "Content-Type": "application/javascript; charset=utf-8",
    "X-Content-Type-Options": "nosniff",
  };
  const manifest = JSON.parse(
    fs.readFileSync(path.join(frontendRoot, "public", "_headers.json"), "utf8"),
  );
  const wildcardIndex = manifest.rules.findIndex((rule) => rule.path === "/*");
  for (const workerPath of workerPaths) {
    const requestPath = `/${workerPath}`;
    const index = manifest.rules.findIndex((rule) => rule.path === requestPath);
    assert.ok(index >= 0 && index < wildcardIndex);
    assert.deepEqual(manifest.rules[index].headers, expectedHeaders);
  }

  const textHeaders = fs.readFileSync(
    path.join(frontendRoot, "public", "_headers"),
    "utf8",
  );
  const wildcardTextIndex = textHeaders.indexOf("\n/*\n");
  for (const workerPath of workerPaths) {
    const block = [
      `/${workerPath}`,
      "  Cache-Control: no-cache, no-store, must-revalidate, max-age=0",
      "  Content-Type: application/javascript; charset=utf-8",
      "  X-Content-Type-Options: nosniff",
    ].join("\n");
    const index = textHeaders.indexOf(block);
    assert.ok(index >= 0 && index < wildcardTextIndex);
  }
});

test("build-meta is reserved as JSON before the SPA wildcard", () => {
  const expectedHeaders = {
    "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
    "Content-Type": "application/json; charset=utf-8",
    "X-Content-Type-Options": "nosniff",
  };
  const manifest = JSON.parse(
    fs.readFileSync(path.join(frontendRoot, "public", "_headers.json"), "utf8"),
  );
  const metadataIndex = manifest.rules.findIndex(
    (rule) => rule.path === "/build-meta.json",
  );
  const wildcardIndex = manifest.rules.findIndex((rule) => rule.path === "/*");
  assert.ok(metadataIndex >= 0 && metadataIndex < wildcardIndex);
  assert.deepEqual(manifest.rules[metadataIndex].headers, expectedHeaders);

  const textHeaders = fs.readFileSync(
    path.join(frontendRoot, "public", "_headers"),
    "utf8",
  );
  const block = [
    "/build-meta.json",
    "  Cache-Control: no-cache, no-store, must-revalidate, max-age=0",
    "  Content-Type: application/json; charset=utf-8",
    "  X-Content-Type-Options: nosniff",
  ].join("\n");
  assert.ok(textHeaders.indexOf(block) < textHeaders.indexOf("\n/*\n"));
});
