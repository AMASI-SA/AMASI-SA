// Isolated visual review: mounts production components against a local test API.
// Usage: node frontend/testing/bundles/start.mjs <frontend-node_modules> [api-origin]
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createRequire } from "node:module";
import fs from "node:fs";

const here = path.dirname(fileURLToPath(import.meta.url));
const frontend = path.resolve(here, "../..");
const modules = path.resolve(process.argv[2] || path.join(frontend, "node_modules"));
const apiOrigin = process.argv[3] || "http://127.0.0.1:8017";
if (!/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(apiOrigin)) throw new Error("Visual review API must be local");
const require = createRequire(path.join(modules, "package.json"));
const { createServer } = await import(pathToFileURL(require.resolve("vite")).href);
const { default: react } = await import(pathToFileURL(require.resolve("@vitejs/plugin-react")).href);
const manifest = JSON.parse(fs.readFileSync(path.join(frontend, "package.json"), "utf8"));
const alias = Object.fromEntries(Object.keys(manifest.dependencies).filter((name) => fs.existsSync(path.join(modules, name))).map((name) => [name, path.join(modules, name)]));
const server = await createServer({
    configFile: false, root: here, envDir: false, plugins: [react()], resolve: { alias },
    define: { "process.env.REACT_APP_BACKEND_URL": JSON.stringify("") },
    css: { postcss: { plugins: [require("tailwindcss")({ content: [path.join(frontend, "src/components/products/Bundle*.jsx"), path.join(frontend, "src/components/orders/Bundle*.jsx"), path.join(here, "review.jsx")], theme: { extend: {} } }), require("autoprefixer")()] } },
    server: { host: "127.0.0.1", port: 4317, strictPort: true, fs: { allow: [frontend, modules] }, proxy: { "/api": apiOrigin } },
});
await server.listen();
server.printUrls();
