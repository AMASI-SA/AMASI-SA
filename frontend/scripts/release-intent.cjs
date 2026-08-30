"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const INTENT_SCHEMA_VERSION = 1;
const INTENT_KIND = "mezan_emergent_release_intent_v1";
const PROTOCOL_VERSION = 5;
const SOURCE_SCOPE = "git_head_frontend_tree_v1";
const FULL_GIT_SHA = /^[0-9a-f]{40}$/;
const SHA256 = /^[0-9a-f]{64}$/;
const GENERATED_TOP_LEVEL_PATHS = new Set([".release", "build", "node_modules"]);

function sha256Bytes(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function gitBlobOid(value) {
  const header = Buffer.from(`blob ${value.length}\0`, "utf8");
  return crypto.createHash("sha1").update(header).update(value).digest("hex");
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

function gitTreeOid(records) {
  const root = { directories: new Map(), files: new Map() };
  for (const record of records) {
    const parts = record.path.split("/");
    let node = root;
    for (const part of parts.slice(0, -1)) {
      if (node.files.has(part)) {
        throw new Error(`Frontend source path collides with a file: ${record.path}`);
      }
      if (!node.directories.has(part)) {
        node.directories.set(part, { directories: new Map(), files: new Map() });
      }
      node = node.directories.get(part);
    }
    const name = parts.at(-1);
    if (node.directories.has(name) || node.files.has(name)) {
      throw new Error(`Duplicate frontend source path: ${record.path}`);
    }
    node.files.set(name, record);
  }

  function encodeTree(node) {
    const entries = [];
    for (const [name, child] of node.directories) {
      const oid = encodeTree(child);
      entries.push({ name, sortName: `${name}/`, mode: "40000", oid });
    }
    for (const [name, record] of node.files) {
      entries.push({ name, sortName: name, mode: record.mode, oid: record.git_blob });
    }
    entries.sort((left, right) =>
      Buffer.compare(Buffer.from(left.sortName, "utf8"), Buffer.from(right.sortName, "utf8")),
    );
    const body = Buffer.concat(entries.map((entry) => Buffer.concat([
      Buffer.from(`${entry.mode} ${entry.name}\0`, "utf8"),
      Buffer.from(entry.oid, "hex"),
    ])));
    return crypto
      .createHash("sha1")
      .update(Buffer.from(`tree ${body.length}\0`, "utf8"))
      .update(body)
      .digest("hex");
  }

  return encodeTree(root);
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function requirePlainObject(value, label) {
  if (!isPlainObject(value)) throw new Error(`${label} must be a JSON object`);
  return value;
}

function requireExactKeys(value, expected, label) {
  const actual = Object.keys(value).sort();
  const canonical = [...expected].sort();
  if (JSON.stringify(actual) !== JSON.stringify(canonical)) {
    throw new Error(`${label} keys are not canonical`);
  }
}

function requireExactInteger(value, label) {
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new Error(`${label} must be a non-negative safe integer`);
  }
  return value;
}

function validateRelativePath(value) {
  if (
    typeof value !== "string"
    || !value
    || value.includes("\\")
    || value.startsWith("/")
    || value.endsWith("/")
    || value.split("/").some((part) => !part || part === "." || part === "..")
    || value.includes("\0")
  ) {
    throw new Error(`Invalid frontend source path: ${String(value)}`);
  }
  return value;
}

function validateSourceRecord(value, frontendRoot) {
  const record = requirePlainObject(value, "frontend_source.files entry");
  requireExactKeys(
    record,
    ["path", "mode", "git_blob", "bytes", "sha256"],
    "frontend_source.files entry",
  );
  const relative = validateRelativePath(record.path);
  if (record.mode !== "100644" && record.mode !== "100755") {
    throw new Error(`Unsupported frontend source mode for ${relative}: ${String(record.mode)}`);
  }
  if (typeof record.git_blob !== "string" || !FULL_GIT_SHA.test(record.git_blob)) {
    throw new Error(`Invalid frontend source git_blob for ${relative}`);
  }
  if (typeof record.sha256 !== "string" || !SHA256.test(record.sha256)) {
    throw new Error(`Invalid frontend source sha256 for ${relative}`);
  }
  requireExactInteger(record.bytes, `frontend source bytes for ${relative}`);

  const absolute = path.resolve(frontendRoot, ...relative.split("/"));
  if (!absolute.startsWith(`${path.resolve(frontendRoot)}${path.sep}`)) {
    throw new Error(`Frontend source path escapes its root: ${relative}`);
  }
  let stat;
  try {
    stat = fs.lstatSync(absolute);
  } catch (error) {
    if (error && error.code === "ENOENT") {
      throw new Error(`Reviewed frontend source file is missing: ${relative}`);
    }
    throw error;
  }
  if (stat.isSymbolicLink() || !stat.isFile()) {
    throw new Error(`Reviewed frontend source must be a regular file: ${relative}`);
  }
  const content = fs.readFileSync(absolute);
  const actual = {
    path: relative,
    mode: stat.mode & 0o111 ? "100755" : "100644",
    git_blob: gitBlobOid(content),
    bytes: content.length,
    sha256: sha256Bytes(content),
  };
  for (const name of ["mode", "git_blob", "bytes", "sha256"]) {
    if (actual[name] !== record[name]) {
      throw new Error(`Reviewed frontend source mismatch for ${relative}: ${name}`);
    }
  }
  return actual;
}

function collectReviewableFrontendPaths(frontendRoot, directory = frontendRoot) {
  const records = [];
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const absolute = path.join(directory, entry.name);
    const relative = path.relative(frontendRoot, absolute).split(path.sep).join("/");
    const topLevel = relative.split("/", 1)[0];
    if (GENERATED_TOP_LEVEL_PATHS.has(topLevel)) continue;
    if (entry.isSymbolicLink()) {
      throw new Error(`Unreviewed frontend source must not be a symlink: ${relative}`);
    }
    if (entry.isDirectory()) records.push(...collectReviewableFrontendPaths(frontendRoot, absolute));
    else if (entry.isFile()) records.push(relative);
    else throw new Error(`Unsupported unreviewed frontend source entry: ${relative}`);
  }
  return records.sort();
}

function validateFrontendSource(value, { frontendRoot }) {
  const source = requirePlainObject(value, "release intent frontend_source");
  requireExactKeys(
    source,
    ["scope", "git_tree_oid", "file_count", "files", "tree_sha256"],
    "release intent frontend_source",
  );
  if (source.scope !== SOURCE_SCOPE) {
    throw new Error(`Unsupported frontend source scope: ${String(source.scope)}`);
  }
  if (typeof source.git_tree_oid !== "string" || !FULL_GIT_SHA.test(source.git_tree_oid)) {
    throw new Error("release intent frontend_source.git_tree_oid must be a full SHA-1");
  }
  requireExactInteger(source.file_count, "release intent frontend_source.file_count");
  if (typeof source.tree_sha256 !== "string" || !SHA256.test(source.tree_sha256)) {
    throw new Error("release intent frontend_source.tree_sha256 must be SHA-256");
  }
  if (!Array.isArray(source.files) || source.files.length === 0) {
    throw new Error("release intent frontend_source.files must be a non-empty array");
  }
  if (source.file_count !== source.files.length) {
    throw new Error("release intent frontend source file_count does not match files");
  }

  const expectedPaths = source.files.map((record) =>
    validateRelativePath(isPlainObject(record) ? record.path : undefined));
  const sortedPaths = [...expectedPaths].sort();
  if (new Set(expectedPaths).size !== expectedPaths.length) {
    throw new Error("release intent frontend source paths must be unique");
  }
  if (JSON.stringify(expectedPaths) !== JSON.stringify(sortedPaths)) {
    throw new Error("release intent frontend source files must be sorted by path");
  }
  const actualPaths = collectReviewableFrontendPaths(frontendRoot);
  if (JSON.stringify(actualPaths) !== JSON.stringify(expectedPaths)) {
    const expected = new Set(expectedPaths);
    const actual = new Set(actualPaths);
    const missing = expectedPaths.filter((item) => !actual.has(item));
    const unreviewed = actualPaths.filter((item) => !expected.has(item));
    throw new Error(
      `Reviewed frontend source membership mismatch (missing=${missing.join(",") || "none"}; `
      + `unreviewed=${unreviewed.join(",") || "none"})`,
    );
  }

  const records = source.files.map((record) => validateSourceRecord(record, frontendRoot));
  const canonicalTreeSha = canonicalSourceTreeSha256(records);
  if (canonicalTreeSha !== source.tree_sha256) {
    throw new Error("release intent frontend source tree_sha256 is not canonical");
  }
  const canonicalGitTreeOid = gitTreeOid(records);
  if (canonicalGitTreeOid !== source.git_tree_oid) {
    throw new Error("release intent frontend source git_tree_oid is not canonical");
  }
  return {
    scope: SOURCE_SCOPE,
    git_tree_oid: canonicalGitTreeOid,
    file_count: records.length,
    files: records,
    tree_sha256: canonicalTreeSha,
  };
}

function loadReviewedReleaseIntent({ intentPath, frontendRoot }) {
  let raw;
  try {
    raw = fs.readFileSync(intentPath, "utf8");
  } catch (error) {
    if (error && error.code === "ENOENT") {
      throw new Error(`Reviewed release intent is missing: ${intentPath}`);
    }
    throw error;
  }
  let intent;
  try {
    intent = JSON.parse(raw);
  } catch (error) {
    throw new Error(`Reviewed release intent is not valid JSON: ${error.message}`);
  }
  requirePlainObject(intent, "release intent");
  if (intent.schema_version !== INTENT_SCHEMA_VERSION) {
    throw new Error(`Unsupported release intent schema_version: ${String(intent.schema_version)}`);
  }
  if (intent.kind !== INTENT_KIND) {
    throw new Error(`Unsupported release intent kind: ${String(intent.kind)}`);
  }
  if (intent.protocol_version !== PROTOCOL_VERSION) {
    throw new Error(`Release intent protocol_version must be ${PROTOCOL_VERSION}`);
  }
  if (typeof intent.source_git_sha !== "string" || !FULL_GIT_SHA.test(intent.source_git_sha)) {
    throw new Error("release intent source_git_sha must be a lowercase full Git SHA");
  }
  if (typeof intent.branch !== "string" || !intent.branch.trim() || intent.branch.length > 255) {
    throw new Error("release intent branch must be a non-empty string");
  }
  if (intent.branch !== intent.branch.trim()) {
    throw new Error("release intent branch must not contain surrounding whitespace");
  }
  return {
    source_git_sha: intent.source_git_sha,
    frontend_source: validateFrontendSource(intent.frontend_source, { frontendRoot }),
  };
}

module.exports = {
  INTENT_KIND,
  INTENT_SCHEMA_VERSION,
  PROTOCOL_VERSION,
  SOURCE_SCOPE,
  canonicalSourceTreeSha256,
  collectReviewableFrontendPaths,
  gitBlobOid,
  gitTreeOid,
  loadReviewedReleaseIntent,
  validateFrontendSource,
};
