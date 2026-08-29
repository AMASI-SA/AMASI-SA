import { defineConfig, transformWithOxc } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import buildContract from "./build-contract.cjs";

const FRONTEND_NOINDEX_DIRECTIVES = "noindex, nofollow, noarchive, nosnippet, noimageindex";
const FRONTEND_CSP = "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data: blob: https:; connect-src 'self' https: wss:; frame-src 'self' https:; form-action 'self' https:; upgrade-insecure-requests";
const FRONTEND_SECURITY_HEADERS = Object.freeze({
  "Content-Security-Policy": FRONTEND_CSP,
  "Strict-Transport-Security": "max-age=63072000; includeSubDomains; preload",
  "X-Frame-Options": "DENY",
  "X-Content-Type-Options": "nosniff",
  "X-Permitted-Cross-Domain-Policies": "none",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "Permissions-Policy": "camera=(self), microphone=(), geolocation=(), payment=(), usb=()",
  "X-Robots-Tag": FRONTEND_NOINDEX_DIRECTIVES,
});

// Emergent Preview hostnames are generated per cluster and can change between
// sessions. Allow only the two Preview suffixes controlled by Emergent rather
// than hard-coding one cluster hostname or disabling Vite's host validation.
const EMERGENT_PREVIEW_ALLOWED_HOSTS = [
  ".preview.emergentcf.cloud",
  ".preview.emergent.host",
];
const { clientEnvAllowlist: CLIENT_ENV_ALLOWLIST } = buildContract;

function legacyJsxLoader() {
  return {
    name: "mezan-legacy-jsx-loader",
    enforce: "pre",
    async transform(code, id) {
      if (!/\/src\/.*\.js$/.test(id) || id.includes(".test.js")) return null;
      return transformWithOxc(code, id, { lang: "jsx" });
    },
  };
}

export default defineConfig(({ mode }) => {
  const clientEnv = Object.fromEntries(
    CLIENT_ENV_ALLOWLIST
      .filter((name) => Object.prototype.hasOwnProperty.call(process.env, name))
      .map((name) => [name, process.env[name]]),
  );
  const nodeEnv = process.env.NODE_ENV || (mode === "production" ? "production" : "development");
  return {
    envDir: false,
    envPrefix: [],
    plugins: [legacyJsxLoader(), react()],
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "src"),
      },
    },
    define: {
      "process.env.NODE_ENV": JSON.stringify(nodeEnv),
      "process.env.REACT_APP_BACKEND_URL": Object.prototype.hasOwnProperty.call(
        clientEnv,
        "REACT_APP_BACKEND_URL",
      )
        ? JSON.stringify(clientEnv.REACT_APP_BACKEND_URL)
        : "undefined",
    },
    server: {
      host: "0.0.0.0",
      port: 3000,
      allowedHosts: EMERGENT_PREVIEW_ALLOWED_HOSTS,
      headers: FRONTEND_SECURITY_HEADERS,
    },
    preview: {
      host: "0.0.0.0",
      allowedHosts: EMERGENT_PREVIEW_ALLOWED_HOSTS,
      headers: FRONTEND_SECURITY_HEADERS,
    },
    build: {
      outDir: "build",
      emptyOutDir: true,
      sourcemap: false,
    },
  };
});
