import { defineConfig } from "@playwright/test";

// Read-only smoke tests against a running web server (default: `npm run start -- -p 3100`).
export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3100",
    screenshot: "only-on-failure",
    // Optional: point at an already-installed Chromium when `playwright install` is unavailable.
    launchOptions: { executablePath: process.env.E2E_CHROMIUM || undefined },
  },
});
