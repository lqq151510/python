import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"

import { expect, type Page, test } from "@playwright/test"

const EMAIL = process.env.AIGC_UI_EMAIL ?? "aigc-ui@example.com"
const PASSWORD = process.env.AIGC_UI_PASSWORD ?? "ZebaoAigc123!"

const HERE = dirname(fileURLToPath(import.meta.url))
const FIRST_FRAME = resolve(HERE, "../../.local/aigc-demo/input-frame.png")

async function login(page: Page) {
  await page.goto("/login")
  await page.getByTestId("email-input").fill(EMAIL)
  await page.getByTestId("password-input").fill(PASSWORD)
  await page.getByRole("button", { name: "Log In" }).click()
  await page.waitForURL("**/", { timeout: 20_000 })
}

async function openAigc(page: Page) {
  // /aigc 使用 DevAssistLayout 自己的侧栏，首页用的是模板侧栏，所以直接进路由。
  await page.goto("/aigc")
  await expect(page.getByRole("region", { name: "AI 视频生成" })).toBeVisible()
  const navLink = page.getByRole("link", { name: "AI 视频生成" })
  await expect(navLink).toHaveAttribute("aria-current", "page")
}

test.describe.configure({ mode: "serial" })
test.use({ storageState: { cookies: [], origins: [] } })
test.skip(
  process.env.AIGC_ENABLED !== "true",
  "需要显式启用 AIGC_ENABLED 并启动本地 ComfyUI",
)

test("文生视频：提交后自动轮询到已完成并内嵌播放器", async ({ page }) => {
  await login(page)
  await openAigc(page)

  const prompt = `霓虹城市雨夜，镜头缓慢推进（浏览器验证 ${Date.now()}）`
  await page
    .getByPlaceholder("例如：霓虹城市雨夜，镜头缓慢推进，胶片颗粒感")
    .fill(prompt)
  await page.getByRole("button", { name: "提交生成任务" }).click()

  await expect(page.getByText("任务已提交，排队中。")).toBeVisible()
  const job = page.locator("button.da-aigc-job", { hasText: prompt })
  await expect(job).toBeVisible()
  await expect(job).toContainText("已完成", { timeout: 60_000 })

  await job.click()
  const detail = page.getByRole("region", { name: "任务详情" })
  await expect(detail).toBeVisible()
  await expect(detail.locator("video.da-aigc-video")).toBeVisible({
    timeout: 30_000,
  })
  const src = await detail.locator("video.da-aigc-video").getAttribute("src")
  expect(src?.startsWith("blob:")).toBe(true)
  await expect(
    detail.getByRole("button", { name: "删除任务与产物" }),
  ).toBeVisible()
})

test("图生视频：上传首帧后成功出片，任务标记为图生视频", async ({ page }) => {
  await login(page)
  await openAigc(page)

  await page.getByRole("button", { name: "图生视频" }).click()
  const prompt = `首帧缓慢推进（浏览器验证 ${Date.now()}）`
  await page
    .getByPlaceholder("例如：霓虹城市雨夜，镜头缓慢推进，胶片颗粒感")
    .fill(prompt)
  await page.locator('input[type="file"]').setInputFiles(FIRST_FRAME)
  await page.getByRole("button", { name: "提交生成任务" }).click()

  const job = page.locator("button.da-aigc-job", { hasText: prompt })
  await expect(job).toBeVisible()
  await expect(job).toContainText("图生视频")
  await expect(job).toContainText("已完成", { timeout: 60_000 })
})

test("取消任务：慢任务在生成中可以取消", async ({ page }) => {
  await login(page)
  await openAigc(page)

  const prompt = `慢一点：验证取消流程 ${Date.now()}`
  await page
    .getByPlaceholder("例如：霓虹城市雨夜，镜头缓慢推进，胶片颗粒感")
    .fill(prompt)
  await page.getByRole("button", { name: "提交生成任务" }).click()

  const job = page.locator("button.da-aigc-job", { hasText: prompt })
  await expect(job).toBeVisible()
  await job.click()
  const detail = page.getByRole("region", { name: "任务详情" })
  await expect(detail.locator("progress")).toBeVisible()
  await detail.getByRole("button", { name: "取消任务" }).click()

  await expect(page.getByText("已取消该任务。")).toBeVisible()
  await expect(job).toContainText("已取消", { timeout: 30_000 })
})

test("删除任务：终态任务可删除并移出列表", async ({ page }) => {
  await login(page)
  await openAigc(page)

  const job = page.locator("button.da-aigc-job", { hasText: "已完成" }).first()
  await expect(job).toBeVisible()
  const before = await page.locator("button.da-aigc-job").count()
  await job.click()
  const detail = page.getByRole("region", { name: "任务详情" })
  await detail.getByRole("button", { name: "删除任务与产物" }).click()

  await expect(page.getByText("任务和产物已删除。")).toBeVisible()
  await expect(page.locator("button.da-aigc-job")).toHaveCount(before - 1, {
    timeout: 30_000,
  })
})
