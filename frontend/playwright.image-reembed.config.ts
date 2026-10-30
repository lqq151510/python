import { defineConfig, devices } from "@playwright/test"

const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:5197"

export default defineConfig({
  testDir: "./tests",
  testMatch: "image-reembed.spec.ts",
  workers: 1,
  retries: 0,
  reporter: "list",
  outputDir: "../.local/playwright/image-reembed",
  use: {
    baseURL,
    ...devices["Desktop Chrome"],
    channel:
      process.env.PLAYWRIGHT_BROWSER_CHANNEL === "chrome"
        ? "chrome"
        : undefined,
  },
  webServer: process.env.PLAYWRIGHT_BASE_URL
    ? undefined
    : {
        command:
          "../node_modules/.bin/vite --host 127.0.0.1 --port 5197 --strictPort",
        url: baseURL,
        reuseExistingServer: false,
      },
})
