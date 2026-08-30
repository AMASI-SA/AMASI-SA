"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const crypto = require("node:crypto");
const { EventEmitter } = require("node:events");
const http = require("node:http");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");
const test = require("node:test");

const {
  canonicalArtifactTree,
  createRuntimeEnvironment,
  startGovernedRuntime,
  validateGovernedRuntimeArtifact,
} = require("./start-governed-runtime.cjs");
const {
  governedPreviewCacheHeaders,
} = require("./governed-preview.cjs");

test("runtime child environment excludes Cloud and provider secrets", () => {
  const environment = createRuntimeEnvironment({
    PATH: "/usr/bin:/bin",
    HOME: "/tmp/example-home",
    DB_PASSWORD: "must-not-reach-runtime",
    SNAPCHAT_ACCESS_TOKEN: "must-not-reach-runtime",
    REACT_APP_BACKEND_URL: "https://evil.example.test",
  });
  assert.deepEqual(environment, {
    NODE_ENV: "production",
    HOME: "/tmp/example-home",
    PATH: "/usr/bin:/bin",
  });
  assert.doesNotMatch(JSON.stringify(environment), /must-not-reach|evil|SNAPCHAT|PASSWORD/);
});

function fixture({
  index = "<!doctype html><html><body>governed SPA</body></html>\n",
  metadata,
} = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mezan-governed-runtime-"));
  const build = path.join(root, "build");
  fs.mkdirSync(build, { recursive: true });
  if (index !== null) fs.writeFileSync(path.join(build, "index.html"), index, "utf8");
  fs.mkdirSync(path.join(build, "assets"), { recursive: true });
  fs.writeFileSync(path.join(build, "assets", "app.js"), "console.log('ok');\n", "utf8");
  if (metadata === undefined && index === null) metadata = null;
  if (metadata === undefined && index !== null) {
    const record = (relative) => {
      const bytes = fs.readFileSync(path.join(build, relative));
      return {
        path: relative,
        bytes: bytes.length,
        sha256: crypto.createHash("sha256").update(bytes).digest("hex"),
      };
    };
    const app = record("assets/app.js");
    const indexRecord = record("index.html");
    const files = [app, indexRecord];
    metadata = `${JSON.stringify({
      schema_version: 1,
      git_sha: "a".repeat(40),
      source: {
        scope: "git_head_frontend_tree_v1",
        git_tree_oid: "b".repeat(40),
        file_count: 1,
        files: [{
          path: "package.json",
          mode: "100644",
          git_blob: "d".repeat(40),
          bytes: 1,
          sha256: "e".repeat(64),
        }],
        tree_sha256: "c".repeat(64),
      },
      toolchain: { node: "22.23.2", yarn: "1.22.22", vite: "8.2.1" },
      environment: {
        mode: "production",
        effective: {
          NODE_ENV: "production",
          VITE_USER_NODE_ENV_present: false,
          VITE_prefixed_keys: [],
        },
        allowed_client_keys: ["REACT_APP_BACKEND_URL"],
        values: {
          REACT_APP_BACKEND_URL: {
            present: true,
            sha256: crypto.createHash("sha256")
              .update("https://mezansalla.com")
              .digest("hex"),
          },
        },
      },
      build: { mode: "production", output_dir: "frontend/build" },
      index: indexRecord,
      entrypoints: [app],
      assets: [app],
      public_files: files,
      files,
      artifact_tree_sha256: canonicalArtifactTree(files),
    }, null, 2)}\n`;
  }
  if (metadata !== null) {
    fs.writeFileSync(path.join(build, "build-meta.json"), metadata, "utf8");
  }
  return {
    root,
    build,
    cleanup() { fs.rmSync(root, { recursive: true, force: true }); },
  };
}

test("runtime refuses missing, malformed, non-object, or SPA metadata", async (t) => {
  const cases = [
    { name: "missing index", values: { index: null }, error: /missing build\/index\.html/ },
    { name: "missing metadata", values: { metadata: null }, error: /missing build\/build-meta\.json/ },
    { name: "HTML metadata", values: { metadata: "<!doctype html><p>SPA</p>\n" }, error: /not valid JSON/ },
    { name: "malformed metadata", values: { metadata: "{broken\n" }, error: /not valid JSON/ },
    { name: "array metadata", values: { metadata: "[]\n" }, error: /must contain a JSON object/ },
  ];
  for (const scenario of cases) {
    await t.test(scenario.name, () => {
      const value = fixture(scenario.values);
      try {
        assert.throws(
          () => validateGovernedRuntimeArtifact({ artifactRoot: value.build }),
          scenario.error,
        );
      } finally {
        value.cleanup();
      }
    });
  }

  await t.test("metadata cannot equal the SPA shell even when both are JSON", () => {
    const value = fixture({ index: "{}\n", metadata: "{}\n" });
    try {
      assert.throws(
        () => validateGovernedRuntimeArtifact({ artifactRoot: value.build }),
        /must not be the SPA HTML shell/,
      );
    } finally {
      value.cleanup();
    }
  });
});

test("runtime validates first then launches only local Vite preview with exact argv", async () => {
  const value = fixture();
  const calls = [];
  const fakeProcess = new EventEmitter();
  const child = new EventEmitter();
  child.kill = () => true;
  try {
    const completion = startGovernedRuntime({
      artifactRoot: value.build,
      root: value.root,
      nodeBin: "/governed/node",
      viteBin: "/governed/node_modules/vite/bin/vite.js",
      spawn(command, args, options) {
        calls.push({ command, args, options });
        return child;
      },
      processObject: fakeProcess,
    });
    child.emit("exit", 0, null);
    const result = await completion;
    assert.equal(result.code, 0);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].command, "/governed/node");
    assert.deepEqual(calls[0].args, [
      "/governed/node_modules/vite/bin/vite.js",
      "preview",
      "--host",
      "0.0.0.0",
      "--port",
      "3000",
      "--strictPort",
    ]);
    assert.equal(calls[0].options.cwd, value.root);
    assert.equal(calls[0].options.stdio, "inherit");
    assert.equal(calls[0].options.env.NODE_ENV, "production");
    assert.notEqual(calls[0].options.env, process.env);
    assert.equal(Object.prototype.hasOwnProperty.call(calls[0].options.env, "DB_PASSWORD"), false);
    assert.doesNotMatch(JSON.stringify(calls), /\bbuild:release\b|deployment_adapter|vite build/);
  } finally {
    value.cleanup();
  }
});

test("runtime forwards termination signals and removes listeners", async () => {
  const value = fixture();
  const fakeProcess = new EventEmitter();
  const child = new EventEmitter();
  const signals = [];
  child.kill = (signal) => {
    signals.push(signal);
    return true;
  };
  try {
    const completion = startGovernedRuntime({
      artifactRoot: value.build,
      root: value.root,
      nodeBin: "/governed/node",
      viteBin: "/governed/vite.js",
      spawn() { return child; },
      processObject: fakeProcess,
    });
    fakeProcess.emit("SIGTERM");
    assert.deepEqual(signals, ["SIGTERM"]);
    child.emit("exit", null, "SIGTERM");
    const result = await completion;
    assert.equal(result.forwardedSignal, "SIGTERM");
    assert.equal(fakeProcess.listenerCount("SIGTERM"), 0);
    assert.equal(fakeProcess.listenerCount("SIGINT"), 0);
  } finally {
    value.cleanup();
  }
});

test("runtime rejects a child error or nonzero exit", async () => {
  for (const outcome of ["error", "nonzero"]) {
    const value = fixture();
    const fakeProcess = new EventEmitter();
    const child = new EventEmitter();
    child.kill = () => true;
    try {
      const completion = startGovernedRuntime({
        artifactRoot: value.build,
        root: value.root,
        nodeBin: "/governed/node",
        viteBin: "/governed/vite.js",
        spawn() { return child; },
        processObject: fakeProcess,
      });
      if (outcome === "error") child.emit("error", new Error("spawn failed"));
      else child.emit("exit", 7, null);
      await assert.rejects(
        completion,
        outcome === "error" ? /spawn failed/ : /exit code 7/,
      );
      assert.equal(fakeProcess.listenerCount("SIGTERM"), 0);
      assert.equal(fakeProcess.listenerCount("SIGINT"), 0);
    } finally {
      value.cleanup();
    }
  }
});

test("runtime refuses artifact byte drift and symlinks", () => {
  const drifted = fixture();
  try {
    fs.writeFileSync(path.join(drifted.build, "assets", "app.js"), "tampered\n", "utf8");
    assert.throws(
      () => validateGovernedRuntimeArtifact({ artifactRoot: drifted.build }),
      /artifact bytes differ/,
    );
  } finally {
    drifted.cleanup();
  }

  const linked = fixture();
  try {
    fs.symlinkSync("index.html", path.join(linked.build, "linked.html"));
    assert.throws(
      () => validateGovernedRuntimeArtifact({ artifactRoot: linked.build }),
      /contains a symlink/,
    );
  } finally {
    linked.cleanup();
  }

  const rootLinked = fixture();
  const linkedRootPath = `${rootLinked.build}-link`;
  try {
    fs.symlinkSync(rootLinked.build, linkedRootPath, "dir");
    assert.throws(
      () => validateGovernedRuntimeArtifact({ artifactRoot: linkedRootPath }),
      /build directory must be a real directory/,
    );
  } finally {
    fs.rmSync(linkedRootPath, { force: true });
    rootLinked.cleanup();
  }
});

test("governed Vite preview explicitly allows production and Emergent preview hosts", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "vite.config.js"), "utf8");
  const previewSource = fs.readFileSync(
    path.join(__dirname, "governed-preview.cjs"),
    "utf8",
  );
  assert.match(source, /["']mezansalla\.com["']/);
  assert.match(source, /["']\.preview\.emergentagent\.com["']/);
  assert.match(previewSource, /public, max-age=31536000, immutable/);
  assert.match(previewSource, /mezan-governed-preview-cache-headers/);
  assert.match(previewSource, /pathname === ["']\/build-meta\.json["']/);
  assert.match(previewSource, /application\/json; charset=utf-8/);
  assert.doesNotMatch(source, /allowedHosts:\s*true/);
});

test("build-meta middleware never falls through to the SPA", () => {
  const value = fixture();
  let middleware;
  governedPreviewCacheHeaders({ artifactRoot: value.build })
    .configurePreviewServer({
      middlewares: { use(handler) { middleware = handler; } },
    });

  function invoke(url, { method = "GET" } = {}) {
    const headers = {};
    let body = null;
    let nextCalled = false;
    const response = {
      statusCode: 200,
      setHeader(name, content) { headers[name.toLowerCase()] = String(content); },
      end(content) { body = content === undefined ? Buffer.alloc(0) : Buffer.from(content); },
    };
    middleware(
      { url, method, headers: { accept: "*/*" } },
      response,
      () => { nextCalled = true; },
    );
    return { status: response.statusCode, headers, body, nextCalled };
  }

  try {
    const expected = fs.readFileSync(path.join(value.build, "build-meta.json"));
    const found = invoke("/build-meta.json?release_check=test");
    assert.equal(found.status, 200);
    assert.equal(found.headers["content-type"], "application/json; charset=utf-8");
    assert.deepEqual(found.body, expected);
    assert.equal(found.nextCalled, false);

    const method = invoke("/build-meta.json", { method: "POST" });
    assert.equal(method.status, 405);
    assert.equal(method.nextCalled, false);

    fs.rmSync(path.join(value.build, "build-meta.json"));
    const missing = invoke("/build-meta.json");
    assert.equal(missing.status, 404);
    assert.equal(missing.headers["content-type"], "application/json; charset=utf-8");
    assert.doesNotMatch(missing.body.toString("utf8"), /governed SPA/);
    assert.equal(missing.nextCalled, false);

    const deepRoute = invoke("/snapchat/campaign/example");
    assert.equal(deepRoute.nextCalled, true);
    assert.match(deepRoute.headers["cache-control"], /no-store/);
    const asset = invoke("/assets/app.js");
    assert.equal(asset.nextCalled, true);
    assert.match(asset.headers["cache-control"], /immutable/);
  } finally {
    value.cleanup();
  }
});

function unusedPort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close((error) => {
        if (error) reject(error);
        else resolve(address.port);
      });
    });
  });
}

function request(port, pathname, { accept = "*/*", method = "GET" } = {}) {
  return new Promise((resolve, reject) => {
    const call = http.request({
      host: "127.0.0.1",
      port,
      path: pathname,
      method,
      headers: { accept },
    }, (response) => {
      const chunks = [];
      response.on("data", (chunk) => chunks.push(chunk));
      response.on("end", () => resolve({
        status: response.statusCode,
        headers: response.headers,
        body: Buffer.concat(chunks).toString("utf8"),
      }));
    });
    call.once("error", reject);
    call.end();
  });
}

async function waitForServer(port, child) {
  let lastError;
  for (let attempt = 0; attempt < 80; attempt += 1) {
    if (child.exitCode !== null) {
      throw new Error(`Vite preview exited before becoming ready: ${child.exitCode}`);
    }
    try {
      return await request(port, "/build-meta.json", { accept: "application/json" });
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
  }
  throw lastError || new Error("Vite preview did not become ready");
}

test("Vite static runtime serves build-meta as JSON and deep routes as the SPA", async (t) => {
  let viteBin;
  try {
    viteBin = path.join(
      path.dirname(require.resolve("vite/package.json")),
      "bin",
      "vite.js",
    );
  } catch (error) {
    if (error && error.code === "MODULE_NOT_FOUND") {
      t.skip("Vite dependencies are not installed in this source-only test environment");
      return;
    }
    throw error;
  }

  const value = fixture();
  const port = await unusedPort();
  fs.writeFileSync(
    path.join(value.root, "vite.config.js"),
    [
      `const { governedPreviewCacheHeaders } = require(${JSON.stringify(
        path.join(__dirname, "governed-preview.cjs"),
      )});`,
      'module.exports = { plugins: [governedPreviewCacheHeaders({',
      '  artifactRoot: __dirname + "/build",',
      '})],',
      '  build: { outDir: "build" },',
      '};',
      '',
    ].join("\n"),
    "utf8",
  );
  const child = spawn(process.execPath, [
    viteBin,
    "preview",
    "--host",
    "127.0.0.1",
    "--port",
    String(port),
    "--strictPort",
  ], {
    cwd: value.root,
    env: process.env,
    stdio: ["ignore", "pipe", "pipe"],
  });
  try {
    const metadata = await waitForServer(port, child);
    const expectedMetadata = fs.readFileSync(
      path.join(value.build, "build-meta.json"),
      "utf8",
    );
    for (const [pathname, accept] of [
      ["/build-meta.json", "application/json"],
      ["/build-meta.json", "*/*"],
      ["/build-meta.json?release_check=test", "*/*"],
    ]) {
      const response = pathname === "/build-meta.json" && accept === "application/json"
        ? metadata
        : await request(port, pathname, { accept });
      assert.equal(response.status, 200);
      assert.equal(
        String(response.headers["content-type"]).split(";", 1)[0].trim().toLowerCase(),
        "application/json",
      );
      assert.equal(
        response.headers["cache-control"],
        "no-cache, no-store, must-revalidate, max-age=0",
      );
      assert.equal(response.body, expectedMetadata);
      assert.equal(Array.isArray(JSON.parse(response.body)), false);
    }

    const deepRoute = await request(port, "/snapchat/campaigns/example");
    assert.equal(deepRoute.status, 200);
    assert.match(String(deepRoute.headers["content-type"]), /^text\/html\b/i);
    assert.equal(
      deepRoute.headers["cache-control"],
      "no-cache, no-store, must-revalidate, max-age=0",
    );
    assert.match(deepRoute.body, /governed SPA/);
    assert.notEqual(deepRoute.body, metadata.body);

    fs.rmSync(path.join(value.build, "build-meta.json"));
    const missing = await request(port, "/build-meta.json", { accept: "*/*" });
    assert.equal(missing.status, 404);
    assert.equal(
      String(missing.headers["content-type"]).split(";", 1)[0].trim().toLowerCase(),
      "application/json",
    );
    assert.doesNotMatch(missing.body, /governed SPA/);
  } finally {
    child.kill("SIGTERM");
    await new Promise((resolve) => {
      if (child.exitCode !== null) resolve();
      else child.once("close", resolve);
    });
    value.cleanup();
  }
});
