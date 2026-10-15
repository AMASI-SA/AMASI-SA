"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { spawn: spawnChild } = require("node:child_process");

const frontendRoot = path.resolve(__dirname, "..");
const buildRoot = path.join(frontendRoot, "build");
const metadataName = "build-meta.json";
const nonPublicFiles = new Set(["_headers", "_headers.json"]);
const runtimeEnvironmentAllowlist = [
  "HOME",
  "LANG",
  "LC_ALL",
  "PATH",
  "TEMP",
  "TMP",
  "TMPDIR",
  "TZ",
];

function sha256(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function relativePosix(root, target) {
  return path.relative(root, target).split(path.sep).join("/");
}

function validateRecord(value, label) {
  if (
    value === null
    || typeof value !== "object"
    || Array.isArray(value)
    || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify(["bytes", "path", "sha256"])
    || typeof value.path !== "string"
    || !value.path
    || value.path.includes("\\")
    || value.path.startsWith("/")
    || value.path.split("/").some((part) => !part || part === "." || part === "..")
    || !Number.isSafeInteger(value.bytes)
    || value.bytes < 0
    || typeof value.sha256 !== "string"
    || !/^[0-9a-f]{64}$/.test(value.sha256)
  ) {
    throw new Error(`Governed frontend runtime ${label} record is invalid`);
  }
  return value;
}

function validateRecords(value, label) {
  if (!Array.isArray(value) || value.length === 0) {
    throw new Error(`Governed frontend runtime ${label} records are missing`);
  }
  const records = value.map((record) => validateRecord(record, label));
  const paths = records.map((record) => record.path);
  if (
    new Set(paths).size !== paths.length
    || JSON.stringify(paths) !== JSON.stringify([...paths].sort())
  ) {
    throw new Error(`Governed frontend runtime ${label} records are not canonical`);
  }
  return records;
}

function collectArtifactRecords(root, directory = root) {
  const records = [];
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const absolute = path.join(directory, entry.name);
    const relative = relativePosix(root, absolute);
    const info = fs.lstatSync(absolute);
    if (info.isSymbolicLink()) {
      throw new Error(`Governed frontend runtime artifact contains a symlink: ${relative}`);
    }
    if (info.isDirectory()) records.push(...collectArtifactRecords(root, absolute));
    else if (info.isFile() && relative !== metadataName) {
      const bytes = fs.readFileSync(absolute);
      records.push({ path: relative, bytes: bytes.length, sha256: sha256(bytes) });
    } else if (!info.isFile()) {
      throw new Error(`Governed frontend runtime artifact entry is unsafe: ${relative}`);
    }
  }
  return records.sort((left, right) => (
    left.path < right.path ? -1 : left.path > right.path ? 1 : 0
  ));
}

function canonicalArtifactTree(records) {
  const canonical = records
    .map((record) => `${record.sha256}\0${record.bytes}\0${record.path}\n`)
    .join("");
  return sha256(Buffer.from(canonical, "utf8"));
}

function createRuntimeEnvironment(parent = process.env) {
  const environment = { NODE_ENV: "production" };
  for (const name of runtimeEnvironmentAllowlist) {
    if (Object.prototype.hasOwnProperty.call(parent, name)) {
      environment[name] = String(parent[name]);
    }
  }
  return environment;
}

function readRegularFile(filePath, label) {
  let info;
  try {
    info = fs.lstatSync(filePath);
  } catch (error) {
    if (error && error.code === "ENOENT") {
      throw new Error(`Governed frontend runtime is missing ${label}`);
    }
    throw error;
  }
  if (info.isSymbolicLink() || !info.isFile()) {
    throw new Error(`Governed frontend runtime ${label} must be a regular file`);
  }
  const bytes = fs.readFileSync(filePath);
  if (!bytes.length) {
    throw new Error(`Governed frontend runtime ${label} must not be empty`);
  }
  return bytes;
}

function validateGovernedRuntimeArtifact({ artifactRoot = buildRoot } = {}) {
  let artifactRootInfo;
  try {
    artifactRootInfo = fs.lstatSync(artifactRoot);
  } catch (error) {
    if (error && error.code === "ENOENT") {
      throw new Error("Governed frontend runtime is missing build directory");
    }
    throw error;
  }
  if (artifactRootInfo.isSymbolicLink() || !artifactRootInfo.isDirectory()) {
    throw new Error("Governed frontend runtime build directory must be a real directory");
  }
  const indexBytes = readRegularFile(path.join(artifactRoot, "index.html"), "build/index.html");
  const metadataBytes = readRegularFile(
    path.join(artifactRoot, "build-meta.json"),
    "build/build-meta.json",
  );
  if (indexBytes.equals(metadataBytes)) {
    throw new Error("Governed frontend build-meta.json must not be the SPA HTML shell");
  }
  let metadata;
  try {
    metadata = JSON.parse(metadataBytes.toString("utf8"));
  } catch (error) {
    throw new Error(`Governed frontend build-meta.json is not valid JSON: ${error.message}`);
  }
  if (metadata === null || typeof metadata !== "object" || Array.isArray(metadata)) {
    throw new Error("Governed frontend build-meta.json must contain a JSON object");
  }
  if (
    metadata.schema_version !== 1
    || typeof metadata.git_sha !== "string"
    || !/^[0-9a-f]{40}$/.test(metadata.git_sha)
    || JSON.stringify(metadata.build) !== JSON.stringify({
      mode: "production",
      output_dir: "frontend/build",
    })
  ) {
    throw new Error("Governed frontend build metadata contract is invalid");
  }
  const actualFiles = collectArtifactRecords(artifactRoot);
  const reviewedFiles = validateRecords(metadata.files, "file");
  if (JSON.stringify(actualFiles) !== JSON.stringify(reviewedFiles)) {
    throw new Error("Governed frontend artifact bytes differ from build metadata");
  }
  if (metadata.artifact_tree_sha256 !== canonicalArtifactTree(actualFiles)) {
    throw new Error("Governed frontend artifact tree SHA256 is invalid");
  }
  const byPath = new Map(actualFiles.map((record) => [record.path, record]));
  if (JSON.stringify(metadata.index) !== JSON.stringify(byPath.get("index.html"))) {
    throw new Error("Governed frontend index record is invalid");
  }
  const expectedAssets = actualFiles.filter((record) => record.path.startsWith("assets/"));
  if (
    expectedAssets.length === 0
    || JSON.stringify(validateRecords(metadata.assets, "asset"))
      !== JSON.stringify(expectedAssets)
  ) {
    throw new Error("Governed frontend asset records are invalid");
  }
  const expectedPublic = actualFiles.filter((record) => !nonPublicFiles.has(record.path));
  if (
    JSON.stringify(validateRecords(metadata.public_files, "public file"))
      !== JSON.stringify(expectedPublic)
  ) {
    throw new Error("Governed frontend public file records are invalid");
  }
  const entrypoints = validateRecords(metadata.entrypoints, "entrypoint");
  const assetsByPath = new Map(expectedAssets.map((record) => [record.path, record]));
  if (
    !entrypoints.some((record) => record.path.endsWith(".js"))
    || entrypoints.some((record) => (
      JSON.stringify(record) !== JSON.stringify(assetsByPath.get(record.path))
      || !/\.(?:js|css)$/.test(record.path)
    ))
  ) {
    throw new Error("Governed frontend entrypoint records are invalid");
  }
  const toolchain = metadata.toolchain;
  const declaredVite = require("../package.json").dependencies.vite;
  if (
    toolchain === null
    || typeof toolchain !== "object"
    || Array.isArray(toolchain)
    || toolchain.node !== "22.23.2"
    || toolchain.yarn !== "1.22.22"
    || toolchain.vite !== declaredVite
  ) {
    throw new Error("Governed frontend build toolchain proof is invalid");
  }
  const backendUrl = metadata.environment?.values?.REACT_APP_BACKEND_URL;
  const expectedBackendHash = sha256(Buffer.from("https://mezansalla.com", "utf8"));
  if (
    metadata.environment?.mode !== "production"
    || JSON.stringify(metadata.environment?.effective) !== JSON.stringify({
      NODE_ENV: "production",
      VITE_USER_NODE_ENV_present: false,
      VITE_prefixed_keys: [],
    })
    || JSON.stringify(metadata.environment?.allowed_client_keys)
      !== JSON.stringify(["REACT_APP_BACKEND_URL"])
    || JSON.stringify(Object.keys(metadata.environment?.values || {}))
      !== JSON.stringify(["REACT_APP_BACKEND_URL"])
    || backendUrl?.present !== true
    || backendUrl?.sha256 !== expectedBackendHash
  ) {
    throw new Error("Governed frontend client environment proof is invalid");
  }
  const source = metadata.source;
  if (
    source === null
    || typeof source !== "object"
    || Array.isArray(source)
    || source.scope !== "git_head_frontend_tree_v1"
    || typeof source.git_tree_oid !== "string"
    || !/^[0-9a-f]{40}$/.test(source.git_tree_oid)
    || !Number.isSafeInteger(source.file_count)
    || source.file_count <= 0
    || !Array.isArray(source.files)
    || source.files.length !== source.file_count
    || typeof source.tree_sha256 !== "string"
    || !/^[0-9a-f]{64}$/.test(source.tree_sha256)
  ) {
    throw new Error("Governed frontend source proof is invalid");
  }
  return metadata;
}

function resolveViteBin(root = frontendRoot) {
  const vitePackage = require.resolve("vite/package.json", { paths: [root] });
  return path.join(path.dirname(vitePackage), "bin", "vite.js");
}

function previewInvocation({
  root = frontendRoot,
  viteBin = resolveViteBin(root),
  nodeBin = process.execPath,
} = {}) {
  return {
    command: nodeBin,
    args: [
      viteBin,
      "preview",
      "--host",
      "0.0.0.0",
      "--port",
      "3000",
      "--strictPort",
    ],
    options: {
      cwd: root,
      env: createRuntimeEnvironment(),
      stdio: "inherit",
    },
  };
}

function startGovernedRuntime({
  artifactRoot = buildRoot,
  root = frontendRoot,
  viteBin,
  nodeBin = process.execPath,
  spawn = spawnChild,
  processObject = process,
} = {}) {
  validateGovernedRuntimeArtifact({ artifactRoot });
  const invocation = previewInvocation({ root, viteBin, nodeBin });
  const child = spawn(invocation.command, invocation.args, invocation.options);
  return new Promise((resolve, reject) => {
    let forwardedSignal = null;
    const forward = (signal) => {
      forwardedSignal = signal;
      child.kill(signal);
    };
    const onSigterm = () => forward("SIGTERM");
    const onSigint = () => forward("SIGINT");
    const cleanup = () => {
      processObject.removeListener("SIGTERM", onSigterm);
      processObject.removeListener("SIGINT", onSigint);
    };
    processObject.once("SIGTERM", onSigterm);
    processObject.once("SIGINT", onSigint);
    child.once("error", (error) => {
      cleanup();
      reject(error);
    });
    child.once("exit", (code, signal) => {
      cleanup();
      if (forwardedSignal !== null || code === 0) {
        resolve({ code, signal, forwardedSignal });
        return;
      }
      const outcome = signal ? `signal ${signal}` : `exit code ${code}`;
      reject(new Error(`Governed frontend runtime stopped with ${outcome}`));
    });
  });
}

if (require.main === module) {
  startGovernedRuntime().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  });
}

module.exports = {
  buildRoot,
  canonicalArtifactTree,
  collectArtifactRecords,
  createRuntimeEnvironment,
  frontendRoot,
  previewInvocation,
  readRegularFile,
  resolveViteBin,
  startGovernedRuntime,
  validateGovernedRuntimeArtifact,
};
