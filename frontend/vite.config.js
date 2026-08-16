import { defineConfig, loadEnv, transformWithOxc } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

const LEGAL_NOINDEX_PATHS = new Set(["/privacy-policy", "/data-deletion", "/terms"]);
const LEGAL_NOINDEX_DIRECTIVES = "noindex, nofollow, noarchive, nosnippet, noimageindex";

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

function legalNoIndexHeaders() {
  return {
    name: "mezan-legal-noindex-headers",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const pathname = new URL(req.url || "/", "http://localhost").pathname.replace(/\/$/, "") || "/";
        if (LEGAL_NOINDEX_PATHS.has(pathname)) res.setHeader("X-Robots-Tag", LEGAL_NOINDEX_DIRECTIVES);
        next();
      });
    },
    configurePreviewServer(server) {
      server.middlewares.use((req, res, next) => {
        const pathname = new URL(req.url || "/", "http://localhost").pathname.replace(/\/$/, "") || "/";
        if (LEGAL_NOINDEX_PATHS.has(pathname)) res.setHeader("X-Robots-Tag", LEGAL_NOINDEX_DIRECTIVES);
        next();
      });
    },
  };
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  return {
    plugins: [legacyJsxLoader(), react(), legalNoIndexHeaders()],
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
    },
    build: {
      outDir: "build",
      emptyOutDir: true,
      sourcemap: false,
    },
  };
});
