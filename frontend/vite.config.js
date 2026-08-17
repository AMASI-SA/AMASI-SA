import { defineConfig, loadEnv, transformWithOxc } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

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
  const env = loadEnv(mode, process.cwd(), "");
  return {
    plugins: [legacyJsxLoader(), react()],
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "src"),
      },
    },
    define: {
      "process.env": JSON.stringify({
        ...env,
        NODE_ENV: mode === "production" ? "production" : "development",
      }),
    },
    server: {
      host: "0.0.0.0",
      port: 3000,
      headers: FRONTEND_SECURITY_HEADERS,
    },
    preview: {
      headers: FRONTEND_SECURITY_HEADERS,
    },
    build: {
      outDir: "build",
      emptyOutDir: true,
      sourcemap: false,
    },
  };
});
