import { defineConfig } from "@playwright/test";

const baseUrl = process.env.CUEWEAVER_E2E_BASE_URL;

if (!baseUrl) {
  throw new Error("Run E2E tests through scripts/test-e2e.sh");
}

export default defineConfig({
  testDir: "./tests/e2e",
  use: {
    baseURL: baseUrl,
    trace: "retain-on-failure",
  },
});
