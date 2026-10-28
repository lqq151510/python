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

test("notes expose loading and retry states and prevent duplicate saves", async ({
  page,
}) => {
  let failList = true
  let holdNextSuccess = false
  let listRequests = 0
  let postRequests = 0
  let releaseFirstList = () => {}
  let releaseRetry = () => {}
  let releasePost = () => {}

  await page.route("**/api/v1/notes", async (route) => {
    if (route.request().method() === "GET") {
      listRequests += 1
      if (failList) {
        if (listRequests === 1) {
          await new Promise<void>((resolve) => {
            releaseFirstList = () => resolve()
          })
        }
        return route.fulfill({ status: 500, json: { detail: "temporary" } })
      }
      if (holdNextSuccess) {
        holdNextSuccess = false
        await new Promise<void>((resolve) => {
          releaseRetry = () => resolve()
        })
      }
      return route.fulfill({ json: { data: [], count: 0 } })
    }

    postRequests += 1
    await new Promise<void>((resolve) => {
      releasePost = () => resolve()
    })
    return route.fulfill({
      status: 201,
      json: {
        id: "note-1",
        ...route.request().postDataJSON(),
        created_at: "2026-10-28T08:00:00Z",
        updated_at: "2026-10-28T08:00:00Z",
      },
    })
  })

  await page.goto("/notes")
  await expect(page.getByText("正在读取笔记…")).toBeVisible()
  await expect.poll(() => listRequests).toBe(1)
  releaseFirstList()
  await expect(page.getByRole("alert")).toContainText("笔记读取失败", {
    timeout: 20_000,
  })

  failList = false
  holdNextSuccess = true
  await page.getByRole("button", { name: "重新加载" }).click()
  await expect(page.getByRole("button", { name: "正在重试…" })).toBeDisabled()
  releaseRetry()
  await expect(page.getByRole("alert")).toHaveCount(0)

  await page.getByLabel("标题", { exact: true }).fill("测试笔记")
  await page.getByLabel("内容", { exact: true }).fill("只保存一次")
  const save = page.getByRole("button", { name: "保存笔记" })
  await save.click()
  await expect(save).toBeDisabled()
  await expect(save).toHaveAttribute("aria-busy", "true")
  await expect.poll(() => postRequests).toBe(1)
  await page.locator("form.da-note-form").evaluate((form) => {
    for (let index = 0; index < 2; index += 1) {
      form.dispatchEvent(
        new Event("submit", { bubbles: true, cancelable: true }),
      )
    }
  })
  expect(postRequests).toBe(1)
  releasePost()
  await expect(page.getByRole("status")).toContainText("笔记已保存")
})

test("tasks expose initial loading and manual retry states", async ({
  page,
}) => {
  let failList = true
  let holdNextSuccess = false
  let listRequests = 0
  let releaseFirstList = () => {}
  let releaseRetry = () => {}

  await page.route("**/api/v1/tasks", async (route) => {
    listRequests += 1
    if (failList) {
      if (listRequests === 1) {
        await new Promise<void>((resolve) => {
          releaseFirstList = () => resolve()
        })
      }
      return route.fulfill({ status: 500, json: { detail: "temporary" } })
    }
    if (holdNextSuccess) {
      holdNextSuccess = false
      await new Promise<void>((resolve) => {
        releaseRetry = () => resolve()
      })
    }
    return route.fulfill({ json: { data: [], count: 0, open_count: 0 } })
  })

  await page.goto("/tasks")
  await expect(page.getByText("正在读取复习清单…")).toBeVisible()
  await expect.poll(() => listRequests).toBe(1)
  releaseFirstList()
  await expect(page.getByRole("alert")).toContainText("复习清单读取失败", {
    timeout: 20_000,
  })

  failList = false
  holdNextSuccess = true
  await page.getByRole("button", { name: "重新加载" }).click()
  await expect(page.getByRole("button", { name: "正在重试…" })).toBeDisabled()
  releaseRetry()
  await expect(page.getByRole("alert")).toHaveCount(0)
  await expect(page.getByText("未完成清单是空的。")).toBeVisible()
})

test("375px layout keeps navigation scrollable and touch targets usable", async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await page.route("**/api/v1/notes", (route) =>
    route.fulfill({ json: { data: [], count: 0 } }),
  )
  await page.route("**/api/v1/tasks", (route) =>
    route.fulfill({ json: { data: [], count: 0, open_count: 0 } }),
  )

  await page.goto("/notes")
  const navigation = page.locator(".da-nav")
  const metrics = await navigation.evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
  }))
  expect(metrics.scrollWidth).toBeGreaterThan(metrics.clientWidth)
  expect(
    await navigation.evaluate((element) => {
      element.scrollLeft = element.scrollWidth
      return element.scrollLeft > 0
    }),
  ).toBe(true)
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true)
  const noteSave = await page
    .getByRole("button", { name: "保存笔记" })
    .boundingBox()
  expect(noteSave?.height).toBeGreaterThanOrEqual(44)

  await page.goto("/tasks")
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true)
  const taskFilter = await page
    .getByRole("button", { name: "未完成", exact: true })
    .boundingBox()
  expect(taskFilter?.height).toBeGreaterThanOrEqual(44)
})
