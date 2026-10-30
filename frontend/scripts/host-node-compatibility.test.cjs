"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const test = require("node:test");

const frontendRoot = path.resolve(__dirname, "..");
const manifest = JSON.parse(fs.readFileSync(path.join(frontendRoot, "package.json"), "utf8"));
const legacyNodeRange = ">=20.18.1 <21 || >=22.23.2 <23";
const marker = "MEZAN_HOST_DISPATCH_OK";

function childEnvironment() {
  const environment = { ...process.env, YARN_IGNORE_ENGINES: "false" };
  for (const name of [
    "GITHUB_ACTIONS", "GITHUB_SHA", "GITHUB_WORKSPACE",
    "GITHUB_EVENT_NAME", "GITHUB_EVENT_PATH",
    "MEZAN_RELEASE_USE_REVIEWED_INTENT", "MEZAN_RELEASE_BOOTSTRAP_INTENT",
  ]) delete environment[name];
  return environment;
}

function dispatchWithEngine(nodeRange) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mezan-host-engine-"));
  try {
    const frontend = path.join(root, "frontend");
    fs.mkdirSync(path.join(frontend, "scripts"), { recursive: true });
    fs.mkdirSync(path.join(root, "scripts"));
    fs.copyFileSync(path.join(__dirname, "build-entry.cjs"), path.join(frontend, "scripts", "build-entry.cjs"));
    // Only the Python boundary is synthetic. Yarn and the actual dispatcher run unchanged.
    fs.writeFileSync(path.join(root, "scripts", "emergent_deployment_adapter.py"), `print(${JSON.stringify(marker)})\n`);
    fs.writeFileSync(path.join(frontend, "package.json"), JSON.stringify({
      name: "mezan-host-engine-fixture", version: "1.0.0", private: true,
      packageManager: manifest.packageManager,
      engines: { node: nodeRange, yarn: manifest.engines.yarn },
      scripts: { build: manifest.scripts.build },
    }));
    return spawnSync("yarn", ["--silent", "build"], {
      cwd: frontend, env: childEnvironment(), encoding: "utf8", timeout: 20000, maxBuffer: 131072,
    });
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}

function assertAdmission(result, admitted) {
  assert.ifError(result.error);
  const output = `${result.stdout}\n${result.stderr}`;
  if (admitted) {
    assert.equal(result.status, 0, output);
    assert.match(result.stdout, new RegExp(marker));
  } else {
    assert.notEqual(result.status, 0);
    assert.match(output, /engine|incompatible/i);
    assert.doesNotMatch(output, new RegExp(marker));
  }
}

test("host admission uses actual Yarn Classic 1.22.22", () => {
  const result = spawnSync("yarn", ["--version"], { encoding: "utf8", timeout: 20000 });
  assert.ifError(result.error);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), "1.22.22");
});

test("current package admits the supported host through the real build dispatcher", () => {
  assertAdmission(dispatchWithEngine(manifest.engines.node), true);
});

test("legacy package demonstrates Node 24 rejection before the dispatcher", () => {
  const major = Number(process.versions.node.split(".")[0]);
  assert.ok([20, 22, 24].includes(major));
  assertAdmission(dispatchWithEngine(legacyNodeRange), major !== 24);
});

test("Yarn still refuses an incompatible engine before dispatch", () => {
  assertAdmission(dispatchWithEngine(">=99 <100"), false);
});

test("host compatibility keeps governed build metadata pinned to Node 22.23.2", () => {
  const script = `require(${JSON.stringify(path.join(__dirname, "write-build-meta.cjs"))})
    .captureBuildInputs().then(value => console.log(JSON.stringify(value.toolchain)))
    .catch(error => { console.error(error.message); process.exitCode = 1; });`;
  const result = spawnSync(process.execPath, ["-e", script], {
    cwd: frontendRoot, encoding: "utf8", timeout: 20000, maxBuffer: 131072,
    env: { ...childEnvironment(), MEZAN_RELEASE_BOOTSTRAP_INTENT: "1", npm_config_user_agent: "yarn/1.22.22" },
  });
  assert.ifError(result.error);
  if (process.versions.node === "22.23.2") {
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(JSON.parse(result.stdout), { node: "22.23.2", yarn: "1.22.22", vite: manifest.dependencies.vite });
  } else {
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /Frontend build requires Node 22\.23\.2; found/);
  }
});
