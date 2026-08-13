import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiPort = env.API_PORT || "8000";
  const webPort = Number(env.WEB_PORT || "5173");

  return {
    plugins: [react(), tailwindcss()],
    build: {
      outDir: "dist",
      emptyOutDir: true,
    },
    server: {
      host: env.WEB_HOST || "localhost",
      port: webPort,
      proxy: {
        "/api": `http://127.0.0.1:${apiPort}`,
      },
    },
    test: {
      environment: "jsdom",
      exclude: ["tests/e2e/**", "node_modules/**"],
      setupFiles: "./tests/setup.ts",
    },
  };
});
