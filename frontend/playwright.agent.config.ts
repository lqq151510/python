import { defineConfig } from "@playwright/test"

import chatConfig from "./playwright.chat.config"

const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:5176"

export default defineConfig({
  ...chatConfig,
  testMatch: "agent.spec.ts",
  outputDir: "test-results/agent",
  use: { ...chatConfig.use, baseURL },
  webServer: process.env.PLAYWRIGHT_BASE_URL
    ? undefined
    : {
        command:
          "../node_modules/.bin/vite --host 127.0.0.1 --port 5176 --strictPort",
        url: baseURL,
        reuseExistingServer: false,
      },
})
