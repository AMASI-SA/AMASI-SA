import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  return {
    plugins: [react()],
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
