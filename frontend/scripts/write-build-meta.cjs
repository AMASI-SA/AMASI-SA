"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

const {
  clientEnvAllowlist,
  expectedNodeVersion,
  expectedYarnVersion,
} = require("../build-contract.cjs");

const SCHEMA_VERSION = 1;
const NON_PUBLIC_BUILD_FILES = new Set(["_headers", "_headers.json"]);
const META_NAME = "build-meta.json";

const frontendRoot = path.resolve(__dirname, "..");
const repoRoot = path.resolve(frontendRoot, "..");
const buildRoot = path.join(frontendRoot, "build");

function sha256Bytes(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function sha256File(filePath) {
  return sha256Bytes(fs.readFileSync(filePath));
}

function gitBlobOid(value) {
  const header = Buffer.from(`blob ${value.length}\0`, "utf8");
  return crypto.createHash("sha1").update(header).update(value).digest("hex");
}

function relativePosix(filePath) {
  return path.relative(buildRoot, filePath).split(path.sep).join("/");
}

function canonicalBuildTreeSha256(records) {
  const input = records
    .map((record) => `${record.sha256}\0${record.bytes}\0${record.path}\n`)
    .join("");
  return sha256Bytes(Buffer.from(input, "utf8"));
}

function canonicalSourceTreeSha256(records) {
  const input = records
    .map(
      (record) =>
        `${record.git_blob}\0${record.mode}\0${record.sha256}\0${record.bytes}\0${record.path}\n`,
    )
    .join("");
  return sha256Bytes(Buffer.from(input, "utf8"));
}

function runGit(args, options = {}) {
  return execFileSync("git", ["-C", repoRoot, ...args], options);
}

function collectTrackedFrontendSource() {
  const objectFormat = runGit(["rev-parse", "--show-object-format"], {
    encoding: "utf8",
  }).trim();
  if (objectFormat !== "sha1") {
    throw new Error(`Unsupported Git object format: ${objectFormat}`);
  }
  const dirty = runGit(
    ["status", "--porcelain=v1", "-z", "--untracked-files=normal", "--", "frontend"],
  );
  if (dirty.length) {
    throw new Error("Frontend build requires a clean frontend source tree");
  }
  const gitTreeOid = runGit(["rev-parse", "HEAD:frontend"], {
    encoding: "utf8",
  }).trim().toLowerCase();
  const output = runGit(["ls-tree", "-rz", "--full-tree", "HEAD", "--", "frontend"]);
  const entries = output.toString("utf8").split("\0").filter(Boolean);
  if (!entries.length) throw new Error("Git HEAD has no tracked frontend source files");
  const records = entries.map((entry) => {
    const match = entry.match(/^(100644|100755) blob ([0-9a-f]{40})\t(frontend\/(.+))$/);
    if (!match) throw new Error(`Unsupported Git-tracked frontend entry: ${entry}`);
    const [, mode, expectedGitBlob, repoRelative, relative] = match;
    const absolute = path.resolve(repoRoot, repoRelative);
    if (!absolute.startsWith(`${frontendRoot}${path.sep}`)) {
      throw new Error(`Tracked frontend path escapes its root: ${repoRelative}`);
    }
    const stat = fs.lstatSync(absolute);
    if (stat.isSymbolicLink() || !stat.isFile()) {
      throw new Error(`Tracked frontend input must be a regular file: ${repoRelative}`);
    }
    const actualMode = stat.mode & 0o111 ? "100755" : "100644";
    const content = fs.readFileSync(absolute);
    const actualGitBlob = gitBlobOid(content);
    if (actualMode !== mode || actualGitBlob !== expectedGitBlob) {
      throw new Error(`Tracked frontend input differs from Git HEAD: ${repoRelative}`);
    }
    return {
      path: relative.split(path.sep).join("/"),
      mode,
      git_blob: expectedGitBlob,
      bytes: content.length,
      sha256: sha256Bytes(content),
    };
  }).sort((left, right) => (left.path < right.path ? -1 : left.path > right.path ? 1 : 0));
  return {
    scope: "git_head_frontend_tree_v1",
    git_tree_oid: gitTreeOid,
    file_count: records.length,
    files: records,
    tree_sha256: canonicalSourceTreeSha256(records),
  };
}

function collectBuildFiles(directory = buildRoot) {
  const files = [];
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const absolute = path.join(directory, entry.name);
    if (entry.isSymbolicLink()) {
      throw new Error(`Frontend build must not contain symlinks: ${absolute}`);
    }
    if (entry.isDirectory()) {
      files.push(...collectBuildFiles(absolute));
    } else if (entry.isFile() && relativePosix(absolute) !== META_NAME) {
      files.push({
        path: relativePosix(absolute),
        bytes: fs.statSync(absolute).size,
        sha256: sha256File(absolute),
      });
    }
  }
  return files.sort((left, right) => (left.path < right.path ? -1 : left.path > right.path ? 1 : 0));
}

function normalizeEntrypoint(value, { allowExternal }) {
  if (/^https?:\/\//i.test(value) || value.startsWith("//")) {
    if (allowExternal) return null;
    throw new Error(`Frontend script entrypoint must be same-origin: ${value}`);
  }
  const withoutQuery = value.split(/[?#]/, 1)[0].replace(/^\.\//, "").replace(/^\/+/, "");
  if (!withoutQuery.startsWith("assets/") || !/\.(?:js|css)$/.test(withoutQuery)) {
    throw new Error(`Unexpected frontend entrypoint: ${value}`);
  }
  return withoutQuery;
}

function entrypointsFromIndex(indexHtml, recordsByPath) {
  const values = [];
  const scriptPattern = /<script\b[^>]*\bsrc=["']([^"']+)["'][^>]*>/gi;
  const stylesheetPattern = /<link\b(?=[^>]*\brel=["'][^"']*stylesheet[^"']*["'])[^>]*\bhref=["']([^"']+)["'][^>]*>/gi;
  for (const [pattern, allowExternal] of [
    [scriptPattern, false],
    [stylesheetPattern, true],
  ]) {
    for (const match of indexHtml.matchAll(pattern)) {
      const normalized = normalizeEntrypoint(match[1], { allowExternal });
      if (normalized) values.push(normalized);
    }
  }
  const unique = [...new Set(values)].sort();
  if (!unique.some((value) => value.endsWith(".js"))) {
    throw new Error("Frontend index has no JavaScript entrypoint");
  }
  return unique.map((entrypoint) => {
    const record = recordsByPath.get(entrypoint);
    if (!record) throw new Error(`Frontend entrypoint is missing: ${entrypoint}`);
    return record;
  });
}

function toolchain() {
  const node = process.versions.node;
  if (node !== expectedNodeVersion) {
    throw new Error(`Frontend build requires Node ${expectedNodeVersion}; found ${node}`);
  }
  const yarnMatch = String(process.env.npm_config_user_agent || "").match(/(?:^|\s)yarn\/([^\s]+)/);
  const yarn = yarnMatch ? yarnMatch[1] : "";
  if (yarn !== expectedYarnVersion) {
    throw new Error(`Frontend build requires Yarn ${expectedYarnVersion}; found ${yarn || "unknown"}`);
  }
  const vite = JSON.parse(
    fs.readFileSync(path.join(frontendRoot, "node_modules", "vite", "package.json"), "utf8"),
  ).version;
  return { node, yarn, vite };
}

async function governedEnvironment() {
  const { loadEnv } = await import("vite");
  const loaded = loadEnv("production", frontendRoot, clientEnvAllowlist);
  const values = {};
  for (const name of clientEnvAllowlist) {
    const present = Object.prototype.hasOwnProperty.call(loaded, name);
    values[name] = {
      present,
      sha256: present
        ? sha256Bytes(Buffer.from(String(loaded[name]), "utf8"))
        : null,
    };
  }
  return {
    mode: "production",
    allowed_client_keys: [...clientEnvAllowlist],
    values,
  };
}

async function captureBuildInputs() {
  const gitSha = runGit(["rev-parse", "HEAD"], {
    encoding: "utf8",
  }).trim().toLowerCase();
  if (!/^[0-9a-f]{40}$/.test(gitSha)) throw new Error("Git HEAD is not a full SHA");
  return {
    git_sha: gitSha,
    source: collectTrackedFrontendSource(),
    toolchain: toolchain(),
    environment: await governedEnvironment(),
  };
}

function writeBuildMetadata(inputs) {
  if (!fs.existsSync(path.join(buildRoot, "index.html"))) {
    throw new Error("Vite output is missing build/index.html");
  }
  const files = collectBuildFiles();
  const recordsByPath = new Map(files.map((record) => [record.path, record]));
  const index = recordsByPath.get("index.html");
  if (!index) throw new Error("Frontend build record for index.html is missing");
  const indexHtml = fs.readFileSync(path.join(buildRoot, "index.html"), "utf8");
  const entrypoints = entrypointsFromIndex(indexHtml, recordsByPath);
  const assets = files.filter((record) => record.path.startsWith("assets/"));
  if (!assets.length) throw new Error("Frontend build has no assets");
  const publicFiles = files.filter((record) => !NON_PUBLIC_BUILD_FILES.has(record.path));
  const metadata = {
    schema_version: SCHEMA_VERSION,
    git_sha: inputs.git_sha,
    source: inputs.source,
    toolchain: inputs.toolchain,
    environment: inputs.environment,
    build: {
      mode: "production",
      output_dir: "frontend/build",
    },
    index,
    entrypoints,
    assets,
    public_files: publicFiles,
    files,
    artifact_tree_sha256: canonicalBuildTreeSha256(files),
  };
  fs.writeFileSync(
    path.join(buildRoot, META_NAME),
    `${JSON.stringify(metadata, null, 2)}\n`,
    "utf8",
  );
  process.stdout.write(`Frontend build identity: ${metadata.artifact_tree_sha256}\n`);
}

module.exports = {
  captureBuildInputs,
  governedEnvironment,
  writeBuildMetadata,
};
