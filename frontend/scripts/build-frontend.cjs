"use strict";

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const {
  captureBuildInputs,
  writeBuildMetadata,
} = require("./write-build-meta.cjs");

const frontendRoot = path.resolve(__dirname, "..");

async function main() {
  fs.rmSync(path.join(frontendRoot, "build", "build-meta.json"), {
    force: true,
  });
  const before = await captureBuildInputs();
  const viteBin = path.join(
    frontendRoot,
    "node_modules",
    ".bin",
    process.platform === "win32" ? "vite.cmd" : "vite",
  );
  const result = spawnSync(viteBin, ["build"], {
    cwd: frontendRoot,
    env: process.env,
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

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
});
