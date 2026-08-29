"use strict";

const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const test = require("node:test");

const {
  createBuildChildEnvironment,
  governedEnvironment,
} = require("./write-build-meta.cjs");

const frontendRoot = path.resolve(__dirname, "..");

test("only the exact public client allowlist reaches the Vite child and build proof", async () => {
  const names = [
    "NODE_ENV",
    "VITE_USER_NODE_ENV",
    "REACT_APP_BACKEND_URL",
    "DB_PASSWORD",
    "VITE_SECRET",
  ];
  const previous = Object.fromEntries(names.map((name) => [name, process.env[name]]));
  const poisonedParent = {
    NODE_ENV: "development",
    VITE_USER_NODE_ENV: "development",
    REACT_APP_BACKEND_URL: "https://api.example.test",
    DB_PASSWORD: "must-not-reach-browser",
    VITE_SECRET: "must-not-reach-import-meta",
    NODE_OPTIONS: "--require=must-not-reach-child",
  };
  const childEnvironment = createBuildChildEnvironment(poisonedParent);
  assert.equal(childEnvironment.NODE_ENV, "production");
  assert.equal(
    childEnvironment.REACT_APP_BACKEND_URL,
    "https://api.example.test",
  );
  for (const forbidden of [
    "DB_PASSWORD",
    "NODE_OPTIONS",
    "VITE_SECRET",
    "VITE_USER_NODE_ENV",
  ]) {
    assert.equal(Object.prototype.hasOwnProperty.call(childEnvironment, forbidden), false);
  }

  const childProbe = spawnSync(
    process.execPath,
    ["-e", "process.stdout.write(JSON.stringify(process.env))"],
    { encoding: "utf8", env: childEnvironment },
  );
  assert.equal(childProbe.status, 0, childProbe.stderr);
  assert.deepEqual(JSON.parse(childProbe.stdout), childEnvironment);

  const proof = governedEnvironment(poisonedParent);
  assert.deepEqual(
    proof,
    governedEnvironment({ REACT_APP_BACKEND_URL: "https://api.example.test" }),
  );
  assert.deepEqual(proof.effective, {
    NODE_ENV: "production",
    VITE_USER_NODE_ENV_present: false,
    VITE_prefixed_keys: [],
  });
  assert.deepEqual(proof.allowed_client_keys, ["REACT_APP_BACKEND_URL"]);
  assert.deepEqual(Object.keys(proof.values), ["REACT_APP_BACKEND_URL"]);
  assert.equal(
    proof.values.REACT_APP_BACKEND_URL.sha256,
    crypto.createHash("sha256").update("https://api.example.test").digest("hex"),
  );
  assert.doesNotMatch(
    JSON.stringify(proof),
    /must-not-reach-browser|must-not-reach-import-meta|must-not-reach-child/,
  );

  process.env.NODE_ENV = childEnvironment.NODE_ENV;
  delete process.env.VITE_USER_NODE_ENV;
  process.env.REACT_APP_BACKEND_URL = childEnvironment.REACT_APP_BACKEND_URL;
  process.env.DB_PASSWORD = poisonedParent.DB_PASSWORD;
  process.env.VITE_SECRET = poisonedParent.VITE_SECRET;
  const dotEnvPath = path.join(frontendRoot, ".env.production");
  const previousDotEnv = fs.existsSync(dotEnvPath)
    ? fs.readFileSync(dotEnvPath)
    : null;
  fs.writeFileSync(
    dotEnvPath,
    [
      "NODE_ENV=development",
      "VITE_USER_NODE_ENV=development",
      "VITE_SECRET=must-not-reach-from-dotenv",
      "DB_PASSWORD=must-not-reach-from-dotenv",
      "",
    ].join("\n"),
    "utf8",
  );
  try {
    const { loadConfigFromFile } = await import("vite");
    const loaded = await loadConfigFromFile(
      { command: "build", mode: "production" },
      path.join(frontendRoot, "vite.config.js"),
    );
    assert.ok(loaded);
    assert.equal(loaded.config.envDir, false);
    assert.deepEqual(loaded.config.envPrefix, []);
    assert.deepEqual(
      Object.keys(loaded.config.define).sort(),
      ["process.env.NODE_ENV", "process.env.REACT_APP_BACKEND_URL"].sort(),
    );
    const serialized = JSON.stringify(loaded.config.define);
    assert.doesNotMatch(serialized, /DB_PASSWORD|VITE_SECRET|must-not-reach/);
    assert.equal(loaded.config.define["process.env.NODE_ENV"], '"production"');
    assert.match(serialized, /https:\/\/api\.example\.test/);

    const viteConfigSource = fs.readFileSync(
      path.join(frontendRoot, "vite.config.js"),
      "utf8",
    );
    assert.doesNotMatch(viteConfigSource, /\bloadEnv\b/);
    assert.match(viteConfigSource, /envDir:\s*false/);
    const buildWrapperSource = fs.readFileSync(
      path.join(frontendRoot, "scripts", "build-frontend.cjs"),
      "utf8",
    );
    assert.match(buildWrapperSource, /spawnSync\(process\.execPath/);
    assert.match(buildWrapperSource, /createBuildChildEnvironment\(\)/);
    assert.doesNotMatch(buildWrapperSource, /env:\s*process\.env/);
  } finally {
    if (previousDotEnv === null) fs.rmSync(dotEnvPath, { force: true });
    else fs.writeFileSync(dotEnvPath, previousDotEnv);
    for (const name of names) {
      if (previous[name] === undefined) delete process.env[name];
      else process.env[name] = previous[name];
    }
  }
});
