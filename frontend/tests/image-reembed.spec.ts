import { expect, test } from "@playwright/test"

const IMAGE_ID = "11111111-1111-4111-8111-111111111111"
const image = {
  id: IMAGE_ID,
  filename: "sample.jpg",
  size_bytes: 100,
  media_type: "image/jpeg",
  created_at: "2026-10-30T01:00:00Z",
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() =>
    localStorage.setItem("access_token", "test-token"),
  )
  await page.route("**/api/**", (route) => route.abort())
  await page.route("**/api/v1/users/me", (route) =>
    route.fulfill({
      json: {
        id: "owner",
        email: "demo@example.test",
        is_active: true,
        is_superuser: false,
      },
    }),
  )
  await page.route(/\/api\/v1\/images$/, (route) =>
    route.fulfill({ json: { data: [image], count: 1 } }),
  )
})

test("用户逐张点击后才重建，进行中防重复，成功保留原图", async ({ page }) => {
  let calls = 0
  let release!: () => void
  const pending = new Promise<void>((resolve) => {
    release = resolve
  })
  await page.route(`**/api/v1/images/${IMAGE_ID}/reembed`, async (route) => {
    calls += 1
    await pending
    await route.fulfill({ json: { message: "图片检索向量已更新。" } })
  })
  await page.route("**/api/v1/images/search", async (route) => {
    await route.fulfill({
      json: { kind: "text", hits: [], request_id: "offline-test" },
    })
  })
  await page.goto("/images")
  await expect(page.getByText("sample.jpg")).toBeVisible()
  expect(calls).toBe(0)
  await page.getByRole("button", { name: "重新生成检索向量" }).click()
  await expect(
    page.getByRole("button", { name: "正在生成检索向量…" }),
  ).toBeDisabled()
  expect(calls).toBe(1)
  await page.getByLabel("描述你要找的图片").fill("示例")
  await page.getByRole("button", { name: "检索图片" }).click()
  await expect(
    page.getByRole("button", { name: "正在生成检索向量…" }),
  ).toBeDisabled()
  await expect(page.getByRole("button", { name: "删除" })).toBeDisabled()
  expect(calls).toBe(1)
  release()
  await expect(page.getByRole("status")).toContainText("图片检索向量已更新")
  await expect(page.getByText("sample.jpg")).toBeVisible()
})

test("重建失败明确提示且不自动重试、不删除图片", async ({ page }) => {
  let calls = 0
  let deletes = 0
  await page.route(`**/api/v1/images/${IMAGE_ID}/reembed`, async (route) => {
    calls += 1
    await route.fulfill({
      status: 503,
      json: { detail: { code: "offline", message: "模型暂不可用" } },
    })
  })
  await page.route(`**/api/v1/images/${IMAGE_ID}`, (route) => {
    deletes += 1
    return route.abort()
  })
  await page.goto("/images")
  await page.getByRole("button", { name: "重新生成检索向量" }).click()
  await expect(page.getByRole("alert")).toContainText("模型暂不可用")
  await expect(page.getByText("sample.jpg")).toBeVisible()
  expect(calls).toBe(1)
  expect(deletes).toBe(0)
})

test("网络断开时提示结果未知，后续操作不会保留旧错误", async ({ page }) => {
  let calls = 0
  await page.route(`**/api/v1/images/${IMAGE_ID}/reembed`, async (route) => {
    calls += 1
    await route.abort("connectionreset")
  })
  await page.route("**/api/v1/images/search", async (route) => {
    await route.fulfill({
      json: { kind: "text", hits: [], request_id: "offline-test" },
    })
  })
  await page.goto("/images")
  await page.getByRole("button", { name: "重新生成检索向量" }).click()
  await expect(page.getByRole("alert")).toContainText("结果暂不可确认")
  await expect(page.getByText("sample.jpg")).toBeVisible()
  expect(calls).toBe(1)
  await page.getByLabel("描述你要找的图片").fill("示例")
  await page.getByRole("button", { name: "检索图片" }).click()
  await expect(page.getByRole("alert")).toHaveCount(0)
  await expect(
    page.getByText("没有命中你的图片。图库为空时不会去调用嵌入模型。"),
  ).toBeVisible()
})
