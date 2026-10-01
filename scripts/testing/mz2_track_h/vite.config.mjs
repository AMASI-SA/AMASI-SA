import { defineConfig } from "../../../frontend/node_modules/vite/dist/node/index.js";
import react from "../../../frontend/node_modules/@vitejs/plugin-react/dist/index.js";
import tailwind from "../../../frontend/node_modules/tailwindcss/lib/index.js";
import path from "node:path";
import { fileURLToPath } from "node:url";
const root = path.dirname(fileURLToPath(import.meta.url));
const frontend = path.resolve(root, "../../../frontend");
export default defineConfig({
 root, plugins: [react()], envDir: false, envPrefix: [],
 resolve: { alias: [{ find: /^(?:\.\.\/)+lib\/api$/, replacement: path.join(root, "fixture-api.js") }, { find: "@", replacement: path.join(frontend,"src") }, {find:"react",replacement:path.join(frontend,"node_modules/react")},{find:"react-dom",replacement:path.join(frontend,"node_modules/react-dom")},{find:"react-router-dom",replacement:path.join(frontend,"node_modules/react-router-dom")}] },
 css: { postcss: { plugins: [tailwind({ content: [path.join(frontend,"src/pages/accounting/**/*.{js,jsx}"),path.join(root,"*.jsx")], theme: {extend:{}}, plugins:[] })] } },
 server: { host:"127.0.0.1",port:4318,strictPort:true,fs:{allow:[path.resolve(root,"../../..")]} },
 define: {"process.env.NODE_ENV":JSON.stringify("development")},
});
