"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

const SCHEMA_VERSION = 1;
const EXPECTED_NODE_MAJOR = 22;
const EXPECTED_YARN_VERSION = "1.22.22";
const SOURCE_FILES = [".nvmrc", "package.json", "vite.config.js", "yarn.lock"];
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

function relativePosix(filePath) {
  return path.relative(buildRoot, filePath).split(path.sep).join("/");
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
      const bytes = fs.statSync(absolute).size;
      files.push({
        path: relativePosix(absolute),
        bytes,
        sha256: sha256File(absolute),
      });
    }
  }
  return files.sort((left, right) => (left.path < right.path ? -1 : left.path > right.path ? 1 : 0));
}

function normalizeEntrypoint(value) {
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
  for (const pattern of [scriptPattern, stylesheetPattern]) {
    for (const match of indexHtml.matchAll(pattern)) {
      values.push(normalizeEntrypoint(match[1]));
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
  if (Number(node.split(".", 1)[0]) !== EXPECTED_NODE_MAJOR) {
    throw new Error(`Frontend build requires Node 22.x; found ${node}`);
  }
  const yarnMatch = String(process.env.npm_config_user_agent || "").match(/(?:^|\s)yarn\/([^\s]+)/);
  const yarn = yarnMatch ? yarnMatch[1] : "";
  if (yarn !== EXPECTED_YARN_VERSION) {
    throw new Error(`Frontend build requires Yarn ${EXPECTED_YARN_VERSION}; found ${yarn || "unknown"}`);
  }
  const vite = JSON.parse(
    fs.readFileSync(path.join(frontendRoot, "node_modules", "vite", "package.json"), "utf8"),
  ).version;
  return { node, yarn, vite };
}

function main() {
  if (!fs.existsSync(path.join(buildRoot, "index.html"))) {
    throw new Error("Vite output is missing build/index.html");
  }
  const gitSha = execFileSync("git", ["-C", repoRoot, "rev-parse", "HEAD"], {
    encoding: "utf8",
  }).trim().toLowerCase();
  if (!/^[0-9a-f]{40}$/.test(gitSha)) throw new Error("Git HEAD is not a full SHA");

  const source = Object.fromEntries(
    SOURCE_FILES.map((relative) => {
      const absolute = path.join(frontendRoot, relative);
      if (!fs.existsSync(absolute)) throw new Error(`Frontend source proof is missing: ${relative}`);
      return [relative, sha256File(absolute)];
    }),
  );
  const files = collectBuildFiles();
  const recordsByPath = new Map(files.map((record) => [record.path, record]));
  const index = recordsByPath.get("index.html");
  if (!index) throw new Error("Frontend build record for index.html is missing");
  const indexHtml = fs.readFileSync(path.join(buildRoot, "index.html"), "utf8");
  const entrypoints = entrypointsFromIndex(indexHtml, recordsByPath);
  const assets = files.filter((record) => record.path.startsWith("assets/"));
  if (!assets.length) throw new Error("Frontend build has no assets");
  const treeInput = files
    .map((record) => `${record.sha256}\0${record.bytes}\0${record.path}\n`)
    .join("");
  const metadata = {
    schema_version: SCHEMA_VERSION,
    git_sha: gitSha,
    source,
    toolchain: toolchain(),
    build: {
      mode: "production",
      output_dir: "frontend/build",
    },
    index,
    entrypoints,
    assets,
    files,
    artifact_tree_sha256: sha256Bytes(Buffer.from(treeInput, "utf8")),
  };
  fs.writeFileSync(
    path.join(buildRoot, META_NAME),
    `${JSON.stringify(metadata, null, 2)}\n`,
    "utf8",
  );
  process.stdout.write(`Frontend build identity: ${metadata.artifact_tree_sha256}\n`);
}

main();
