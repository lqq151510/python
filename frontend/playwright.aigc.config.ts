import { defineConfig } from "@playwright/test"

import chatConfig from "./playwright.chat.config"

const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173"

export default defineConfig({
  ...chatConfig,
  testDir: "./tests",
  testMatch: "aigc.spec.ts",
  fullyParallel: false,
  workers: 1,
  outputDir: "test-results/aigc",
  use: {
    ...chatConfig.use,
    baseURL,
  },
  webServer: undefined,
})
