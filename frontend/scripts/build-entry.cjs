"use strict";

const path = require("node:path");
const { execFileSync, spawnSync } = require("node:child_process");

const frontendRoot = path.resolve(__dirname, "..");
const repoRoot = path.resolve(frontendRoot, "..");

function fullGitSha(value, label) {
  if (typeof value !== "string" || !/^[0-9a-f]{40}$/.test(value)) {
    throw new Error(`${label} must be a full lowercase Git SHA`);
  }
  return value;
}

function githubCurrentHead({
  environment = process.env,
  repositoryRoot = repoRoot,
  gitHead = () => execFileSync(
    "git",
    ["-C", repositoryRoot, "rev-parse", "HEAD"],
    { encoding: "utf8" },
  ).trim().toLowerCase(),
} = {}) {
  const markers = ["GITHUB_ACTIONS", "GITHUB_SHA", "GITHUB_WORKSPACE"];
  const hasMarker = markers.some((name) =>
    Object.prototype.hasOwnProperty.call(environment, name)
    && environment[name] !== ""
  );
  if (!hasMarker) return null;
  if (environment.GITHUB_ACTIONS !== "true") {
    throw new Error("GITHUB_ACTIONS must be exactly true when GitHub markers are present");
  }
  const expected = fullGitSha(environment.GITHUB_SHA, "GITHUB_SHA");
  const workspace = environment.GITHUB_WORKSPACE;
  if (
    typeof workspace !== "string"
    || path.resolve(workspace) !== path.resolve(repositoryRoot)
  ) {
    throw new Error("GITHUB_WORKSPACE does not match the repository root");
  }
  const actual = fullGitSha(gitHead(), "Git HEAD");
  if (actual !== expected) {
    throw new Error(
      `GitHub checkout differs from GITHUB_SHA: head=${actual} expected=${expected}`,
    );
  }
  return actual;
}

function buildInvocation({
  environment = process.env,
  repositoryRoot = repoRoot,
  frontendDirectory = frontendRoot,
  nodeBin = process.execPath,
  pythonBin = "python",
  gitHead = () => execFileSync(
    "git",
    ["-C", repositoryRoot, "rev-parse", "HEAD"],
    { encoding: "utf8" },
  ).trim().toLowerCase(),
  viteBin = () => {
    const manifest = require.resolve("vite/package.json", {
      paths: [frontendDirectory],
    });
    return path.join(path.dirname(manifest), "bin", "vite.js");
  },
} = {}) {
  const githubHead = githubCurrentHead({
    environment,
    repositoryRoot,
    gitHead,
  });
  if (githubHead) {
    return {
      mode: "github_current_head",
      command: nodeBin,
      args: [viteBin(), "build"],
      cwd: frontendDirectory,
    };
  }
  return {
    mode: "emergent_cloud_adapter",
    command: pythonBin,
    args: [
      path.join(repositoryRoot, "scripts", "emergent_deployment_adapter.py"),
      "build",
    ],
    cwd: frontendDirectory,
  };
}

function runBuild(options = {}) {
  const invocation = buildInvocation(options);
  const result = spawnSync(invocation.command, invocation.args, {
    cwd: invocation.cwd,
    env: options.environment || process.env,
    stdio: "inherit",
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`${invocation.mode} build exited with status ${result.status}`);
  }
  return invocation;
}

if (require.main === module) {
  try {
    runBuild();
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  }
}

module.exports = { buildInvocation, fullGitSha, githubCurrentHead, runBuild };
