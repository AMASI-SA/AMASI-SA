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
const releaseBuildContract = require("../build-contract.cjs");

const frontendRoot = path.resolve(__dirname, "..");
const repositoryRoot = path.resolve(frontendRoot, "..");

test("Emergent host compatibility does not relax governed releases", () => {
  const packageManifest = JSON.parse(
    fs.readFileSync(path.join(frontendRoot, "package.json"), "utf8"),
  );
  assert.equal(
    packageManifest.scripts.start,
    "node scripts/start-governed-runtime.cjs",
  );
  assert.equal(packageManifest.scripts["start:dev"], "vite --host 0.0.0.0");
  assert.equal(
    packageManifest.scripts.build,
    "node scripts/build-entry.cjs",
  );
  assert.equal(
    packageManifest.scripts["build:release"],
    "node scripts/build-release-frontend.cjs",
  );
  assert.equal(
    packageManifest.engines.node,
    ">=20.18.1 <21 || >=22.23.2 <23",
  );
  assert.equal(packageManifest.engines.yarn, ">=1.22.22 <2");
  assert.equal(releaseBuildContract.expectedNodeVersion, "22.23.2");
  assert.equal(releaseBuildContract.expectedYarnVersion, "1.22.22");
  assert.match(packageManifest.packageManager, /^yarn@1\.22\.22(?:\+|$)/);

  const governedWorkflowPaths = [
    ".github/workflows/emergent-preview-hosts.yml",
    ".github/workflows/legal-pages-noindex.yml",
    ".github/workflows/mezan-production-release.yml",
    ".github/workflows/security-gate.yml",
    ".github/workflows/store-delivery-v1.yml",
  ];
  const workflowsRoot = path.join(repositoryRoot, ".github", "workflows");
  const releaseWorkflowPaths = fs.readdirSync(workflowsRoot)
    .filter((name) => /\.ya?ml$/.test(name))
    .filter((name) => fs.readFileSync(path.join(workflowsRoot, name), "utf8")
      .includes("yarn build:release"))
    .map((name) => `.github/workflows/${name}`)
    .sort();
  assert.deepEqual(releaseWorkflowPaths, [...governedWorkflowPaths].sort());

  for (const relativePath of governedWorkflowPaths) {
    const workflow = fs.readFileSync(
      path.join(repositoryRoot, relativePath),
      "utf8",
    );
    assert.match(workflow, /yarn build:release/);
    if (relativePath === ".github/workflows/mezan-production-release.yml") {
      const hostAdapterInvocations = workflow.match(
        /^\s*env -u GITHUB_ACTIONS -u GITHUB_SHA -u GITHUB_WORKSPACE \\\n\s*yarn build \| tee /gm,
      ) || [];
      assert.equal(hostAdapterInvocations.length, 1);
      assert.match(workflow, /node-version:\s*["']?20\.20\.2["']?/);
      assert.match(workflow, /Emergent Host Node 20 clean-clone adapter rehearsal/);
      assert.match(workflow, /MEZAN_RELEASE_USE_REVIEWED_INTENT=1/);
      assert.match(workflow, /MEZAN_RELEASE_BOOTSTRAP_INTENT=1/);
      assert.match(workflow, /release-v5-reviewed-intent-candidate/);
    } else {
      assert.doesNotMatch(workflow, /yarn build(?:\s|$)/m);
      assert.doesNotMatch(workflow, /MEZAN_RELEASE_USE_REVIEWED_INTENT/);
    }
    assert.match(workflow, /node-version:\s*["']?22\.23\.2["']?/);
    assert.match(workflow, /yarn@1\.22\.22/);
    assert.match(workflow, /yarn install --frozen-lockfile --non-interactive/);
  }
});

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
