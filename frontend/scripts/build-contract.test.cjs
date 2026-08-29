"use strict";

const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const path = require("node:path");
const test = require("node:test");

const { governedEnvironment } = require("./write-build-meta.cjs");

const frontendRoot = path.resolve(__dirname, "..");

test("only the exact public client allowlist reaches Vite and build proof", async () => {
  const names = ["REACT_APP_BACKEND_URL", "DB_PASSWORD", "VITE_SECRET"];
  const previous = Object.fromEntries(names.map((name) => [name, process.env[name]]));
  process.env.REACT_APP_BACKEND_URL = "https://api.example.test";
  process.env.DB_PASSWORD = "must-not-reach-browser";
  process.env.VITE_SECRET = "must-not-reach-import-meta";
  try {
    const proof = await governedEnvironment();
    assert.deepEqual(proof.allowed_client_keys, ["REACT_APP_BACKEND_URL"]);
    assert.deepEqual(Object.keys(proof.values), ["REACT_APP_BACKEND_URL"]);
    assert.equal(
      proof.values.REACT_APP_BACKEND_URL.sha256,
      crypto.createHash("sha256").update("https://api.example.test").digest("hex"),
    );
    assert.doesNotMatch(JSON.stringify(proof), /must-not-reach-browser|must-not-reach-import-meta/);

    const { loadConfigFromFile } = await import("vite");
    const loaded = await loadConfigFromFile(
      { command: "build", mode: "production" },
      path.join(frontendRoot, "vite.config.js"),
    );
    assert.ok(loaded);
    assert.deepEqual(loaded.config.envPrefix, []);
    assert.deepEqual(
      Object.keys(loaded.config.define).sort(),
      ["process.env.NODE_ENV", "process.env.REACT_APP_BACKEND_URL"].sort(),
    );
    const serialized = JSON.stringify(loaded.config.define);
    assert.doesNotMatch(serialized, /DB_PASSWORD|VITE_SECRET|must-not-reach/);
    assert.match(serialized, /https:\/\/api\.example\.test/);
  } finally {
    for (const name of names) {
      if (previous[name] === undefined) delete process.env[name];
      else process.env[name] = previous[name];
    }
  }
});
