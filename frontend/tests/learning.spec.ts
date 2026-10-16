import { expect, test } from "@playwright/test"

test.use({ storageState: { cookies: [], origins: [] } })

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() =>
    localStorage.setItem("access_token", "learning-ui-token"),
  )
  await page.route("**/api/v1/users/me", (route) =>
    route.fulfill({
      json: {
        id: "11111111-1111-1111-1111-111111111111",
        email: "student@example.com",
        is_active: true,
        is_superuser: false,
      },
    }),
  )
  await page.route("**/api/v1/documents", (route) =>
    route.fulfill({ json: { data: [], count: 0 } }),
  )
  await page.route("**/api/v1/learning/cache/stats", (route) =>
    route.fulfill({ json: { available: true, hit: 2, miss: 2 } }),
  )
})

test("student can save a learning note and observe its persisted listing", async ({
  page,
}) => {
  const records: unknown[] = []
  await page.route("**/api/v1/learning", (route) => {
    if (route.request().method() === "GET")
      return route.fulfill({ json: { data: records } })
    expect(route.request().headers().authorization).toBe(
      "Bearer learning-ui-token",
    )
    const body = route.request().postDataJSON()
    const record = {
      id: "note-1",
      kind: "note",
      ...body,
      result: {},
      created_at: "2026-10-16T08:00:00Z",
    }
    records.push(record)
    return route.fulfill({ status: 201, json: record })
  })
  await page.goto("/learning")
  for (const name of ["PostgreSQL", "MySQL", "Redis", "Milvus", "Chroma"])
    await expect(page.getByRole("heading", { name, exact: true })).toBeVisible()
  await page.getByLabel("今天学了什么").fill("我学会了缓存")
  await page.getByLabel("我的理解或疑问").fill("Redis 缓存会过期。")
  await page.getByRole("button", { name: "保存到学习记录" }).click()
  await expect(
    page.getByRole("region", { name: "我的学习记录" }),
  ).toContainText("Redis 缓存会过期。")
  await page.reload()
  await expect(
    page.getByRole("region", { name: "我的学习记录" }),
  ).toContainText("我学会了缓存")
  await expect(page.getByRole("region", { name: "缓存观察" })).toContainText(
    "命中 2 次",
  )
})

test("comparison results display both vector stores and are saved as a learning record", async ({
  page,
}) => {
  let record: unknown = null
  await page.route("**/api/v1/learning", (route) =>
    route.fulfill({ json: { data: record ? [record] : [] } }),
  )
  await page.route("**/api/v1/learning/compare", (route) => {
    expect(route.request().postDataJSON()).toEqual({
      query: "登录凭据失效怎么办",
    })
    record = {
      id: "comparison-1",
      kind: "comparison",
      title: "向量检索对照实验",
      notes: "登录凭据失效怎么办",
      created_at: "2026-10-16T08:00:00Z",
      result: {
        shared_hits: 1,
        stores: {
          milvus: {
            elapsed_ms: 35,
            cache_status: "miss",
            hits: [{ chunk_id: "1", filename: "tokens.md", score: 0.8 }],
          },
          chroma: {
            elapsed_ms: 20,
            cache_status: "miss",
            hits: [{ chunk_id: "1", filename: "tokens.md", score: 0.8 }],
          },
        },
      },
    }
    return route.fulfill({ json: record })
  })
  await page.goto("/learning")
  await page.getByLabel("实验问题").fill("登录凭据失效怎么办")
  await page.getByRole("button", { name: "对照检索并保存" }).click()
  await expect(
    page.getByRole("region", { name: "milvus 实验结果" }),
  ).toContainText("tokens.md")
  await expect(
    page.getByRole("region", { name: "chroma 实验结果" }),
  ).toContainText("tokens.md")
  await expect(
    page.getByText("共有 1 个相同片段。", { exact: false }),
  ).toBeVisible()
})
