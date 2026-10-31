import { defineConfig } from "@playwright/test"

import chatConfig from "./playwright.chat.config"

// This suite never uses a running application API or persisted login state.
const baseURL = "http://127.0.0.1:4189"

export default defineConfig({
  ...chatConfig,
  testMatch: [
    "auth-ui.spec.ts",
    "chat.spec.ts",
    "knowledge.spec.ts",
    "learning.spec.ts",
    "agent.spec.ts",
    "workspace.spec.ts",
    "image-reembed.spec.ts",
  ],
  outputDir: "../.local/playwright/mock",
  use: { ...chatConfig.use, baseURL },
  webServer: {
    command: "bunx --no-install vite --host 127.0.0.1 --port 4189 --strictPort",
    url: baseURL,
    reuseExistingServer: false,
    // Any unmocked API request must fail instead of reaching the user's backend.
    env: { VITE_API_URL: "http://127.0.0.1:9" },
  },
})
