import { expect, test } from "@playwright/test"

test.use({ storageState: { cookies: [], origins: [] } })

test.beforeEach(async ({ page }) => {
  await page.route("**/api/**", (route) => route.abort())
})

test("认证入口显示中文产品身份与可用图标", async ({ page }, testInfo) => {
  await page.goto("/login")
  await expect(page).toHaveTitle("登录 - DevAssist")
  await expect(
    page.getByRole("heading", { name: "登录 DevAssist" }),
  ).toBeVisible()
  await expect(page.getByLabel("邮箱", { exact: true })).toBeVisible()
  await expect(page.getByLabel("密码", { exact: true })).toBeVisible()
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN")
  const favicon = await page.request.get("/devassist.svg")
  expect(favicon.ok()).toBeTruthy()
  expect(favicon.headers()["content-type"]).toContain("image/svg+xml")
  await expect(page.getByText("FastAPI", { exact: false })).toHaveCount(0)
  await page.screenshot({
    path: testInfo.outputPath("login-desktop.png"),
    fullPage: true,
  })
})

test("登录等待时禁止重复发送，失败后可以重试", async ({ page }) => {
  let requests = 0
  let release: () => void = () => {}
  const pending = new Promise<void>((resolve) => {
    release = resolve
  })
  await page.route("**/api/v1/login/access-token", async (route) => {
    requests += 1
    await pending
    await route.fulfill({
      status: 400,
      json: { detail: "Incorrect email or password" },
    })
  })
  await page.goto("/login")
  await page.getByTestId("email-input").fill("demo@example.com")
  await page.getByTestId("password-input").fill("synthetic-password")
  await page.getByRole("button", { name: "登录", exact: true }).click()
  await expect(
    page.getByRole("button", { name: "登录", exact: true }),
  ).toBeDisabled()
  await page.getByTestId("password-input").press("Enter")
  expect(requests).toBe(1)
  release()
  await expect(page.getByText("Incorrect email or password")).toBeVisible()
  await expect(
    page.getByRole("button", { name: "登录", exact: true }),
  ).toBeEnabled()
  await expect(page).toHaveURL(/\/login$/)
})

test("网络失败保持在登录页，不写入登录状态", async ({ page }) => {
  await page.goto("/login")
  await page.getByTestId("email-input").fill("demo@example.com")
  await page.getByTestId("password-input").fill("synthetic-password")
  await page.getByRole("button", { name: "登录", exact: true }).click()
  await expect(page.locator("[data-sonner-toast]")).toBeVisible()
  await expect(
    page.getByRole("button", { name: "登录", exact: true }),
  ).toBeEnabled()
  expect(
    await page.evaluate(() => localStorage.getItem("access_token")),
  ).toBeNull()
})

test("注册校验使用中文，密码不一致时不提交", async ({ page }) => {
  let submitted = false
  await page.route("**/api/v1/users/signup", async (route) => {
    submitted = true
    await route.abort()
  })
  await page.goto("/signup")
  await expect(page).toHaveTitle("注册 - DevAssist")
  await page.getByTestId("full-name-input").fill("演示用户")
  await page.getByTestId("email-input").fill("demo@example.com")
  await page.getByTestId("password-input").fill("password-one")
  await page.getByTestId("confirm-password-input").fill("password-two")
  await page.getByRole("button", { name: "注册", exact: true }).click()
  await expect(page.getByText("两次输入的密码不一致")).toBeVisible()
  expect(submitted).toBe(false)
})

test("找回与重置密码使用中文导航", async ({ page }) => {
  await page.goto("/recover-password")
  await expect(page).toHaveTitle("找回密码 - DevAssist")
  await expect(page.getByRole("button", { name: "发送重置邮件" })).toBeVisible()
  await page.getByRole("link", { name: "返回登录" }).click()
  await expect(page).toHaveURL(/\/login$/)
  await page.goto("/reset-password?token=synthetic-test-token")
  await expect(page).toHaveTitle("重置密码 - DevAssist")
  await expect(page.getByRole("heading", { name: "重置密码" })).toBeVisible()
})

test("未知页面提供中文返回入口", async ({ page }) => {
  await page.goto("/missing-release-test-page")
  await expect(page.getByText("页面不存在", { exact: true })).toBeVisible()
  await expect(
    page.getByRole("link", { name: "返回应用首页" }),
  ).toHaveAttribute("href", "/")
})

test("手机登录页无需横向滚动即可填写", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await page.goto("/login")
  await expect(
    page.getByRole("button", { name: "登录", exact: true }),
  ).toBeInViewport()
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(375)
  await page.screenshot({
    path: testInfo.outputPath("login-mobile.png"),
    fullPage: true,
  })
})
