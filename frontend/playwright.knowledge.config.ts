import { defineConfig } from "@playwright/test"

import chatConfig from "./playwright.chat.config"

export default defineConfig({
  ...chatConfig,
  testMatch: ["chat.spec.ts", "knowledge.spec.ts", "learning.spec.ts"],
  outputDir: "test-results/knowledge",
})
