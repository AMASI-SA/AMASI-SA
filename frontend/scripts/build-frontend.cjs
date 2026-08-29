"use strict";

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const {
  captureBuildInputs,
  createBuildChildEnvironment,
  writeBuildMetadata,
} = require("./write-build-meta.cjs");

const frontendRoot = path.resolve(__dirname, "..");

async function buildOnce() {
  fs.rmSync(path.join(frontendRoot, "build", "build-meta.json"), {
    force: true,
  });
  const before = await captureBuildInputs();
  const viteBin = path.join(
    path.dirname(require.resolve("vite/package.json")),
    "bin",
    "vite.js",
  );
  const result = spawnSync(process.execPath, [viteBin, "build", "--mode", "production"], {
    cwd: frontendRoot,
    env: createBuildChildEnvironment(),
    stdio: "inherit",
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`Vite build failed with exit code ${result.status}`);
  }
  const after = await captureBuildInputs();
  if (JSON.stringify(after) !== JSON.stringify(before)) {
    throw new Error("Frontend source, toolchain, or governed environment changed during build");
  }
  writeBuildMetadata(after);
}

if (require.main === module) {
  buildOnce().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  });
}

module.exports = { buildOnce };
