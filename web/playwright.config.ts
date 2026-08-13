import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  use: {
    baseURL: "http://127.0.0.1:8765",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "uv run python scripts/e2e_product_server.py",
    cwd: "..",
    url: "http://127.0.0.1:8765/api/status",
    reuseExistingServer: false,
  },
});
