"use strict";

const fs = require("node:fs");
const path = require("node:path");

const RELEASE_REVALIDATION_HEADER = "no-cache, no-store, must-revalidate, max-age=0";
const HASHED_ASSET_CACHE_HEADER = "public, max-age=31536000, immutable";
const UNAVAILABLE_METADATA = Buffer.from(
  '{"error":"governed_build_metadata_unavailable"}\n',
  "utf8",
);

function metadataBytes(artifactRoot) {
  const target = path.join(artifactRoot, "build-meta.json");
  const info = fs.lstatSync(target);
  if (info.isSymbolicLink() || !info.isFile()) {
    throw new Error("build-meta.json is not a regular file");
  }
  const bytes = fs.readFileSync(target);
  const parsed = JSON.parse(bytes.toString("utf8"));
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("build-meta.json is not a JSON object");
  }
  return bytes;
}

function sendJson(response, status, bytes, method) {
  response.statusCode = status;
  response.setHeader("Content-Type", "application/json; charset=utf-8");
  response.setHeader("Content-Length", String(bytes.length));
  response.setHeader("Cache-Control", RELEASE_REVALIDATION_HEADER);
  response.setHeader("X-Content-Type-Options", "nosniff");
  if (method === "HEAD") response.end();
  else response.end(bytes);
}

function governedPreviewCacheHeaders({
  artifactRoot = path.resolve(__dirname, "..", "build"),
} = {}) {
  return {
    name: "mezan-governed-preview-cache-headers",
    configurePreviewServer(server) {
      server.middlewares.use((request, response, next) => {
        const pathname = new URL(request.url || "/", "http://localhost").pathname;
        if (pathname === "/build-meta.json") {
          const method = String(request.method || "GET").toUpperCase();
          if (!new Set(["GET", "HEAD"]).has(method)) {
            response.setHeader("Allow", "GET, HEAD");
            sendJson(response, 405, UNAVAILABLE_METADATA, method);
            return;
          }
          try {
            sendJson(response, 200, metadataBytes(artifactRoot), method);
          } catch (_error) {
            sendJson(response, 404, UNAVAILABLE_METADATA, method);
          }
          return;
        }
        if (pathname.startsWith("/assets/")) {
          response.setHeader("Cache-Control", HASHED_ASSET_CACHE_HEADER);
        } else if (
          pathname === "/sw.js"
          || pathname === "/service-worker.js"
          || pathname === "/"
          || pathname === "/index.html"
          || path.posix.extname(pathname) === ""
        ) {
          response.setHeader("Cache-Control", RELEASE_REVALIDATION_HEADER);
        }
        next();
      });
    },
  };
}

module.exports = {
  HASHED_ASSET_CACHE_HEADER,
  RELEASE_REVALIDATION_HEADER,
  governedPreviewCacheHeaders,
  metadataBytes,
};
