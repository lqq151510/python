import { expect, type Page, test } from "@playwright/test"

const DOCUMENT_ID = "11111111-1111-4111-8111-111111111111"
const NOTE_ID = "22222222-2222-4222-8222-222222222222"
const CHUNK_ID = "33333333-3333-4333-8333-333333333333"

const document = {
  id: DOCUMENT_ID,
  filename: "rag-eval.md",
  size_bytes: 520,
  chunk_count: 1,
  created_at: "2026-10-29T01:00:00Z",
}

const chunk = {
  id: CHUNK_ID,
  position: 0,
  heading: "召回与排序",
  line_start: 1,
  line_end: 3,
  content:
    "评估要分两问：召回到了没有，召回到的内容能不能支撑答案。只回答第一个问题会明显高估系统表现。",
}

const source = {
  citation: "S1",
  document_id: DOCUMENT_ID,
  chunk_id: CHUNK_ID,
  filename: document.filename,
  heading: chunk.heading,
  line_start: chunk.line_start,
  line_end: chunk.line_end,
  content: chunk.content,
  score: 1.2,
}

const NOTE_BODY =
  "三个问题：这周最有价值的一件事是什么？哪里卡住了？下周第一件事做什么？"

const codeFile = { path: "examples/retrieval.py", size_bytes: 320 }
const codeContent = `from app.knowledge import rank_chunks


def search(query: str, chunks: list[str]):
    """先按关键词召回，再按分数排序。"""
    return rank_chunks(query, chunks, limit=5)
`

let chatBody: Record<string, unknown> | undefined

/**
 * 工作台一次加载四类资料，测试里用固定响应替代真实库，
 * 避免依赖数据库里的账号数据，同时保证断言针对页面行为。
 */
async function mockWorkspace(page: Page) {
  let notes = [
    {
      id: NOTE_ID,
      title: "每周复盘模板",
      body: NOTE_BODY,
      created_at: "2026-10-29T01:00:00Z",
      updated_at: "2026-10-29T01:00:00Z",
    },
  ]

  await page.route(/\/api\/v1\/documents(\?.*)?$/, async (route) => {
    await route.fulfill({
      json: { data: [document], count: 1 },
    })
  })

  await page.route("**/api/v1/documents/search", async (route) => {
    const body = route.request().postDataJSON() as { query?: string }
    await route.fulfill({
      json: {
        query: body.query ?? "",
        method: "bm25",
        sources: [source],
      },
    })
  })

  await page.route(`**/api/v1/documents/${DOCUMENT_ID}`, async (route) => {
    await route.fulfill({
      json: { document, chunks: [chunk] },
    })
  })

  await page.route(/\/api\/v1\/notes$/, async (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON() as {
        title: string
        body: string
      }
      const created = {
        id: "44444444-4444-4444-8444-444444444444",
        title: body.title,
        body: body.body,
        created_at: "2026-10-29T02:00:00Z",
        updated_at: "2026-10-29T02:00:00Z",
      }
      notes = [created, ...notes]
      await route.fulfill({ status: 201, json: created })
      return
    }
    await route.fulfill({ json: { data: notes, count: notes.length } })
  })

  await page.route(/\/api\/v1\/images$/, async (route) => {
    await route.fulfill({ json: { data: [], count: 0 } })
  })

  await page.route(/\/api\/v1\/code\/workspace$/, async (route) => {
    await route.fulfill({
      json: { files: [codeFile], total_bytes: codeFile.size_bytes },
    })
  })

  await page.route(/\/api\/v1\/code\/workspace\/files\/.*/, async (route) => {
    await route.fulfill({
      json: {
        path: codeFile.path,
        content: codeContent,
        base_version: `sha256:${"0".repeat(64)}`,
      },
    })
  })

  await page.route("**/api/v1/chat", async (route) => {
    chatBody = route.request().postDataJSON() as Record<string, unknown>
    await route.fulfill({
      json: {
        answer: "评估要分两问：召回，以及召回内容能否支撑答案。[S1]",
        request_id: "workspace-test",
        sources: [source],
        retrieval_method: "bm25",
      },
    })
  })
}

async function emptySourcesForSecondAccount(page: Page) {
  await page.route(
    /\/api\/v1\/(documents|notes|images|code\/workspace)(\?.*)?$/,
    async (route) => {
      if (
        route.request().headers().authorization !== "Bearer account-b-token"
      ) {
        await route.fallback()
        return
      }
      const code = /\/code\/workspace$/.test(
        new URL(route.request().url()).pathname,
      )
      await route.fulfill({
        json: code ? { files: [], total_bytes: 0 } : { data: [], count: 0 },
      })
    },
  )
}

async function switchAccountInOtherTab(page: Page) {
  const other = await page.context().newPage()
  try {
    await other.route("**/api/**", (route) => route.abort())
    await other.route("**/session-switch-test", (route) =>
      route.fulfill({ contentType: "text/html", body: "<!doctype html>" }),
    )
    await other.goto(new URL("/session-switch-test", page.url()).href)
    await other.evaluate(() =>
      localStorage.setItem("access_token", "account-b-token"),
    )
  } finally {
    await other.close()
  }
}

test.use({ storageState: { cookies: [], origins: [] } })

test.beforeEach(async ({ page }) => {
  chatBody = undefined
  await page.addInitScript(() => {
    if (sessionStorage.getItem("workspace-ui-test-initialized")) return
    localStorage.setItem("access_token", "workspace-ui-test-token")
    sessionStorage.setItem("workspace-ui-test-initialized", "true")
  })
  await mockWorkspace(page)
})

test("工作台一次展示知识源、结果列表与代码工作台", async ({ page }) => {
  await page.goto("/workspace")

  await expect(page.getByRole("heading", { name: "全部资料" })).toBeVisible()
  await expect(page.getByRole("heading", { name: "知识源" })).toBeVisible()
  await expect(page.getByRole("heading", { name: "我的速记" })).toBeVisible()
  await expect(page.getByRole("region", { name: "代码工作台" })).toBeVisible()
  await expect(page.locator(".zm-tree__row").first()).toContainText(
    "retrieval.py",
  )
  await expect(page.locator(".zm-status")).toContainText("已索引 3 条资料")
})

test("代码服务不可用时文档仍可检索，并提供重试", async ({ page }) => {
  let attempts = 0
  await page.route(/\/api\/v1\/code\/workspace$/, async (route) => {
    attempts += 1
    if (attempts === 1) {
      await route.fulfill({ status: 500, json: { detail: "unavailable" } })
      return
    }
    await route.fulfill({
      json: { files: [codeFile], total_bytes: codeFile.size_bytes },
    })
  })
  await page.goto("/workspace")
  await expect(page.getByRole("alert")).toContainText("代码加载失败")
  await expect(page.getByRole("button", { name: "重试加载" })).toBeVisible()
  await page.locator("#zm-search-input").fill("评估")
  await expect(page.locator(".zm-result").first()).toContainText("rag-eval.md")
  await page.locator(".zm-result").first().click()
  await expect(page.locator(".zm-reader__title")).toHaveText("rag-eval.md")
  await page.getByRole("button", { name: "重试加载" }).click()
  await expect(page.getByRole("alert")).toHaveCount(0)
  await expect(page.locator(".zm-tree__row").first()).toContainText(
    "retrieval.py",
  )
})

test("代码服务请求尚未返回时文档仍可检索和阅读", async ({ page }) => {
  let release!: () => void
  const pending = new Promise<void>((resolve) => {
    release = resolve
  })
  let requestStarted!: () => void
  const started = new Promise<void>((resolve) => {
    requestStarted = resolve
  })
  await page.route(/\/api\/v1\/code\/workspace$/, async (route) => {
    requestStarted()
    await pending
    await route.fulfill({ json: { files: [], total_bytes: 0 } })
  })
  try {
    await page.goto("/workspace")
    await started
    await page.locator("#zm-search-input").fill("评估")
    const result = page.locator(".zm-result").first()
    await expect(result).toContainText("rag-eval.md", { timeout: 5000 })
    await result.click()
    await expect(page.locator(".zm-reader__title")).toHaveText("rag-eval.md")
  } finally {
    release()
  }
})

test("文档列表失败时仍可打开文档搜索结果", async ({ page }) => {
  await page.route(/\/api\/v1\/documents(\?.*)?$/, async (route) => {
    await route.fulfill({ status: 500, json: { detail: "unavailable" } })
  })
  await page.goto("/workspace")
  await expect(page.getByRole("alert")).toContainText("文档加载失败")
  await page.locator("#zm-search-input").fill("评估")
  const result = page.locator(".zm-result").first()
  await expect(result).toContainText("rag-eval.md")
  await result.click()
  await expect(page.locator(".zm-reader__title")).toHaveText("rag-eval.md")
  await expect(page.getByText("召回与排序", { exact: true })).toBeVisible()
})

test("全部列表仍在加载时已返回的搜索结果可以阅读", async ({ page }) => {
  let release!: () => void
  const pending = new Promise<void>((resolve) => {
    release = resolve
  })
  let allStarted!: () => void
  const started = new Promise<void>((resolve) => {
    allStarted = resolve
  })
  let count = 0
  for (const pattern of [
    /\/api\/v1\/documents(\?.*)?$/,
    /\/api\/v1\/notes$/,
    /\/api\/v1\/images$/,
    /\/api\/v1\/code\/workspace$/,
  ]) {
    await page.route(pattern, async (route) => {
      count += 1
      if (count === 4) allStarted()
      await pending
      await route.fulfill({ status: 503, json: { detail: "unavailable" } })
    })
  }
  try {
    await page.goto("/workspace")
    await started
    await page.locator("#zm-search-input").fill("评估")
    const result = page.locator(".zm-result").first()
    await expect(result).toContainText("rag-eval.md", { timeout: 5000 })
    await result.click()
    await expect(page.getByText("召回与排序", { exact: true })).toBeVisible()
  } finally {
    release()
  }
})

test("文档列表失败时可以打开回答中的另一篇来源", async ({ page }) => {
  const otherId = "55555555-5555-4555-8555-555555555555"
  const otherDocument = { ...document, id: otherId, filename: "other.md" }
  const otherSource = { ...source, document_id: otherId, filename: "other.md" }
  await page.route(/\/api\/v1\/documents(\?.*)?$/, async (route) => {
    await route.fulfill({ status: 500, json: { detail: "unavailable" } })
  })
  await page.route(`**/api/v1/documents/${otherId}`, async (route) => {
    await route.fulfill({ json: { document: otherDocument, chunks: [chunk] } })
  })
  await page.route("**/api/v1/chat", async (route) => {
    await route.fulfill({
      json: {
        answer: "请查看来源。",
        request_id: "test",
        sources: [otherSource],
      },
    })
  })
  await page.goto("/workspace")
  await expect(page.getByRole("alert")).toContainText("文档加载失败")
  await page.locator("#zm-ask-input").fill("查看其他资料")
  await page.getByRole("button", { name: "发送", exact: true }).click()
  await page.locator(".zm-turn__source").filter({ hasText: "other.md" }).click()
  await expect(page.locator(".zm-reader__title")).toHaveText("other.md")
  await expect(page.getByText("召回与排序", { exact: true })).toBeVisible()
})

async function typeSearchBurst(page: Page, text: string) {
  await page.locator("#zm-search-input").evaluate((element, value) => {
    const field = element as HTMLInputElement
    const setter = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )?.set
    if (!setter) throw new Error("无法写入检索框")
    let current = ""
    for (const char of value) {
      current += char
      setter.call(field, current)
      field.dispatchEvent(
        new InputEvent("input", { bubbles: true, data: char }),
      )
    }
  }, text)
}

test("快速连续输入只发送最后一次文档检索，笔记和代码仍即时过滤", async ({
  page,
}) => {
  const queries: string[] = []
  await page.route("**/api/v1/documents/search", async (route) => {
    const body = route.request().postDataJSON() as {
      query?: string
      method?: string
    }
    queries.push(`${body.method ?? "bm25"}:${body.query ?? ""}`)
    await route.fulfill({
      json: {
        query: body.query ?? "",
        method: body.method ?? "bm25",
        sources: body.query === "评估报告" ? [source] : [],
      },
    })
  })

  await page.goto("/workspace")
  await expect(page.getByRole("heading", { name: "全部资料" })).toBeVisible()
  const input = page.locator("#zm-search-input")
  await input.pressSequentially("复盘", { delay: 20 })
  expect(queries).toEqual([])
  await expect(
    page.locator(".zm-result").filter({ hasText: "每周复盘模板" }),
  ).toBeVisible()
  await expect(
    page.locator(".zm-result").filter({ hasText: "retrieval.py" }),
  ).toHaveCount(0)

  await expect
    .poll(() => queries.filter((item) => item === "bm25:复盘").length, {
      timeout: 2000,
    })
    .toBeGreaterThan(0)
  await page.waitForTimeout(400)
  expect(queries.every((item) => item === "bm25:复盘")).toBe(true)
  expect(queries.length).toBeLessThanOrEqual(2)

  await input.fill("")
  await expect(page.getByRole("heading", { name: "全部资料" })).toBeVisible()
  await typeSearchBurst(page, "评估报告")
  await expect
    .poll(() => queries.filter((item) => item === "bm25:评估报告").length, {
      timeout: 2000,
    })
    .toBeGreaterThan(0)
  await page.waitForTimeout(400)
  const reportQueries = queries.filter((item) => item !== "bm25:复盘")
  expect(reportQueries.every((item) => item === "bm25:评估报告")).toBe(true)
  expect(reportQueries.length).toBeLessThanOrEqual(2)
  await expect(
    page.locator(".zm-result").filter({ hasText: "rag-eval.md" }),
  ).toBeVisible()
  await expect(
    page.locator(".zm-result").filter({ hasText: "每周复盘模板" }),
  ).toHaveCount(0)
})

test("语义模式下连续输入只发送最后一次关键词和向量检索", async ({ page }) => {
  const queries: string[] = []
  await page.route("**/api/v1/documents/search", async (route) => {
    const body = route.request().postDataJSON() as {
      query?: string
      method?: string
    }
    queries.push(`${body.method ?? "bm25"}:${body.query ?? ""}`)
    await route.fulfill({
      json: {
        query: body.query ?? "",
        method: body.method ?? "bm25",
        sources: [source],
      },
    })
  })

  await page.goto("/workspace")
  await expect(page.getByRole("heading", { name: "全部资料" })).toBeVisible()
  await page.getByRole("button", { name: "关键词检索" }).click()
  await expect(page.getByRole("button", { name: "语义检索" })).toBeVisible()
  await typeSearchBurst(page, "评估")
  await expect
    .poll(() => [...new Set(queries)].sort(), { timeout: 2000 })
    .toEqual(["bm25:评估", "vector:评估"])
  await page.waitForTimeout(400)
  expect(queries.every((item) => item.endsWith(":评估"))).toBe(true)
  expect(queries.length).toBeLessThanOrEqual(4)
  await expect(
    page.locator(".zm-result").filter({ hasText: "rag-eval.md" }),
  ).toBeVisible()
})

test("清空或改词会取消旧检索，过期结果不会覆盖当前词", async ({ page }) => {
  const seen: string[] = []
  const failures: string[] = []
  const waiters: Array<{ key: string; done: boolean; release: () => void }> = []
  page.on("requestfailed", (request) => {
    if (request.url().includes("/documents/search")) {
      failures.push(request.failure()?.errorText ?? "failed")
    }
  })
  await page.route("**/api/v1/documents/search", async (route) => {
    const body = route.request().postDataJSON() as {
      query?: string
      method?: string
    }
    const key = `${body.method ?? "bm25"}:${body.query ?? ""}`
    seen.push(key)
    await new Promise<void>((resolve) => {
      waiters.push({ key, done: false, release: resolve })
    })
    try {
      await route.fulfill({
        json: {
          query: body.query ?? "",
          method: body.method ?? "bm25",
          sources: [
            {
              ...source,
              filename: `${body.query ?? ""}.md`,
              content: `结果 ${body.query ?? ""}`,
            },
          ],
        },
      })
    } catch {
      failures.push(`${key}:fulfill-rejected`)
    }
  })
  const release = (key: string) => {
    for (const waiter of waiters) {
      if (waiter.key === key && !waiter.done) {
        waiter.done = true
        waiter.release()
      }
    }
  }

  try {
    await page.goto("/workspace")
    await expect(page.getByRole("heading", { name: "全部资料" })).toBeVisible()
    const input = page.locator("#zm-search-input")
    await input.fill("甲")
    await expect.poll(() => seen.includes("bm25:甲")).toBe(true)
    await input.fill("乙")
    await expect.poll(() => seen.includes("bm25:乙")).toBe(true)
    release("bm25:乙")
    await expect(
      page.locator(".zm-result").filter({ hasText: "乙.md" }),
    ).toBeVisible()
    release("bm25:甲")
    await page.waitForTimeout(300)
    await expect(
      page.locator(".zm-result").filter({ hasText: "甲.md" }),
    ).toHaveCount(0)
    await expect(page.locator(".zm-results .zm-field__error")).toHaveCount(0)
    await expect(
      page.getByRole("heading", { name: "检索「乙」" }),
    ).toBeVisible()

    await input.fill("评估")
    await expect.poll(() => seen.includes("bm25:评估")).toBe(true)
    await page.getByRole("button", { name: "清除检索词" }).click()
    await expect(page.getByRole("heading", { name: "全部资料" })).toBeVisible()
    release("bm25:评估")
    await page.waitForTimeout(300)
    await expect(page.locator(".zm-results .zm-field__error")).toHaveCount(0)
    await expect(page.getByText("文档检索失败")).toHaveCount(0)
    await expect(page.getByText("无法连接服务")).toHaveCount(0)
    expect(seen).toContain("bm25:甲")
    expect(seen).toContain("bm25:评估")
    expect(
      failures.some((item) => item.startsWith("bm25:甲")) ||
        failures.some((item) => /abort|cancel/i.test(item)),
    ).toBe(true)
  } finally {
    for (const waiter of waiters) {
      if (!waiter.done) {
        waiter.done = true
        waiter.release()
      }
    }
  }
})

test("检索文档并在阅读视图打开，引用后提问带上文档范围", async ({ page }) => {
  await page.goto("/workspace")
  await page.locator("#zm-search-input").fill("评估")

  const result = page.locator(".zm-result").first()
  await expect(result).toContainText("rag-eval.md")
  await result.click()

  await expect(page.locator(".zm-reader__title")).toHaveText("rag-eval.md")
  await expect(page.getByText("召回与排序", { exact: false })).toBeVisible()

  await page
    .getByRole("main")
    .getByRole("button", { name: "引用到上下文" })
    .click()
  await expect(page.locator(".zm-ctx__count")).toHaveText("1 条")

  await page.locator("#zm-ask-input").fill("评估要分哪两问？")
  await page.getByRole("button", { name: "发送", exact: true }).click()

  await expect(page.locator(".zm-turn__a")).toContainText("评估要分两问")
  await expect(page.locator(".zm-turn__source")).toContainText("rag-eval.md")
  expect(chatBody).toMatchObject({
    use_documents: true,
    document_id: DOCUMENT_ID,
    client_request_id: expect.stringMatching(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    ),
  })
})

test("引用两篇文档时提问只传所选文档范围", async ({ page }) => {
  const otherId = "55555555-5555-4555-8555-555555555555"
  const otherDocument = { ...document, id: otherId, filename: "other.md" }
  await page.route(/\/api\/v1\/documents(\?.*)?$/, async (route) => {
    await route.fulfill({ json: { data: [document, otherDocument], count: 2 } })
  })
  await page.route(`**/api/v1/documents/${otherId}`, async (route) => {
    await route.fulfill({ json: { document: otherDocument, chunks: [chunk] } })
  })
  await page.goto("/workspace")
  for (const name of ["rag-eval.md", "other.md"]) {
    await page.locator(".zm-result").filter({ hasText: name }).first().click()
    await page
      .getByRole("main")
      .getByRole("button", { name: "引用到上下文" })
      .click()
  }
  await expect(page.locator(".zm-ctx__count")).toHaveText("2 条")
  await page.locator("#zm-ask-input").fill("比较两份资料")
  await page.getByRole("button", { name: "发送", exact: true }).click()
  await expect(page.locator(".zm-turn__a")).toBeVisible()
  expect(chatBody).toMatchObject({
    use_documents: true,
    document_ids: [DOCUMENT_ID, otherId],
  })
  expect(chatBody).not.toHaveProperty("document_id")
})

test("文档上下文最多 20 篇，移除后可继续添加", async ({ page }) => {
  const documents = Array.from({ length: 21 }, (_, index) => ({
    ...document,
    id: `00000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
    filename: `doc-${String(index + 1).padStart(2, "0")}.md`,
  }))
  await page.route(/\/api\/v1\/documents(\?.*)?$/, (route) =>
    route.fulfill({ json: { data: documents, count: documents.length } }),
  )
  await page.route(/\/api\/v1\/documents\/[^/]+$/, (route) => {
    const id = new URL(route.request().url()).pathname.split("/").pop()
    const selected = documents.find((item) => item.id === id)
    if (!selected) return route.abort()
    return route.fulfill({ json: { document: selected, chunks: [chunk] } })
  })
  await page.goto("/workspace")

  async function addDocument(name: string) {
    await page.locator(".zm-result").filter({ hasText: name }).click()
    await expect(page.locator(".zm-reader__title")).toHaveText(name)
    await page
      .getByRole("main")
      .getByRole("button", { name: "引用到上下文" })
      .click()
  }

  for (const item of documents.slice(0, 20)) {
    await addDocument(item.filename)
  }
  await expect(page.locator(".zm-ctx__count")).toHaveText("20 条")

  await addDocument(documents[20].filename)
  await expect(page.getByRole("status")).toContainText("最多引用 20 篇不同文档")
  await expect(page.locator(".zm-ctx__count")).toHaveText("20 条")

  await addDocument(documents[0].filename)
  await expect(page.locator(".zm-ctx__count")).toHaveText("20 条")
  await page.locator(".zm-result").filter({ hasText: "每周复盘模板" }).click()
  await page
    .getByRole("main")
    .getByRole("button", { name: "引用到上下文" })
    .click()
  await expect(page.locator(".zm-ctx__count")).toHaveText("21 条")

  await page
    .getByRole("button", { name: `移除 ${documents[0].filename}` })
    .click()
  await addDocument(documents[20].filename)
  await expect(page.locator(".zm-ctx__count")).toHaveText("21 条")
  await page.locator("#zm-ask-input").fill("汇总资料")
  await page.getByRole("button", { name: "发送", exact: true }).click()
  await expect(page.locator(".zm-turn__a")).toBeVisible()
  expect(chatBody?.document_ids).toEqual(
    documents.slice(1).map((item) => item.id),
  )
})

test("重试处理中请求复用原始摘录和编号，不覆盖其他回合", async ({ page }) => {
  const bodies: Record<string, unknown>[] = []
  await page.route("**/api/v1/chat", (route) => {
    bodies.push(route.request().postDataJSON())
    if (bodies.length === 1) {
      return route.fulfill({
        status: 409,
        json: {
          detail: { code: "request_in_progress", message: "请求处理中" },
        },
      })
    }
    return route.fulfill({ json: { answer: "已恢复", request_id: "done" } })
  })
  await page.goto("/workspace")
  await page
    .locator(".zm-result")
    .filter({ hasText: "rag-eval.md" })
    .first()
    .click()
  await page
    .getByRole("main")
    .getByRole("button", { name: "引用到上下文" })
    .click()
  await page.locator("#zm-ask-input").fill("第一个问题")
  await page.getByRole("button", { name: "发送", exact: true }).click()
  const first = page.locator(".zm-turn").filter({ hasText: "第一个问题" })
  await expect(first).toContainText("请求处理中")
  await page.getByRole("button", { name: "移除 rag-eval.md" }).click()
  await page.locator("#zm-ask-input").fill("另一个问题")
  await page.getByRole("button", { name: "发送", exact: true }).click()
  await expect(
    page.locator(".zm-turn").filter({ hasText: "另一个问题" }),
  ).toContainText("已恢复")
  await first.getByRole("button", { name: "重试此请求" }).click()
  await expect(first).toContainText("已恢复")
  expect(bodies[0]).toEqual(bodies[2])
  expect(bodies[1].client_request_id).not.toBe(bodies[0].client_request_id)
  await expect(page.locator(".zm-turn")).toHaveCount(2)
})

test("网络失败后重试保留编号，重复点击不会发送两次", async ({ page }) => {
  const bodies: Record<string, unknown>[] = []
  let release!: () => void
  const pending = new Promise<void>((resolve) => {
    release = resolve
  })
  await page.route("**/api/v1/chat", async (route) => {
    bodies.push(route.request().postDataJSON())
    if (bodies.length === 1) {
      await route.abort("failed")
      return
    }
    await pending
    await route.fulfill({ json: { answer: "网络恢复", request_id: "done" } })
  })
  try {
    await page.goto("/workspace")
    await page.locator("#zm-ask-input").fill("网络失败")
    await page.getByRole("button", { name: "发送", exact: true }).click()
    const turn = page.locator(".zm-turn").first()
    const retry = turn.getByRole("button", { name: "重试此请求" })
    await expect(retry).toBeVisible()
    await retry.evaluate((button: HTMLButtonElement) => {
      button.click()
      button.click()
    })
    await expect(turn).toContainText("正在检索并生成回答")
    await expect.poll(() => bodies.length).toBe(2)
    expect(bodies[1]).toEqual(bodies[0])
  } finally {
    release()
  }
  await expect(page.locator(".zm-turn").first()).toContainText("网络恢复")
  expect(bodies).toHaveLength(2)
})

test("模型前失败后用户手动重试使用新编号", async ({ page }) => {
  const bodies: Record<string, unknown>[] = []
  await page.route("**/api/v1/chat", (route) => {
    bodies.push(route.request().postDataJSON())
    if (bodies.length === 1) {
      return route.fulfill({
        status: 409,
        json: { detail: { code: "request_failed", message: "模型前失败" } },
      })
    }
    return route.fulfill({ json: { answer: "新编号完成", request_id: "done" } })
  })
  await page.goto("/workspace")
  await page.locator("#zm-ask-input").fill("模型前失败")
  await page.getByRole("button", { name: "发送", exact: true }).click()
  const turn = page.locator(".zm-turn").first()
  await expect(turn.locator(".zm-field__error")).toHaveText("模型前失败")
  await turn.getByRole("button", { name: "重试此请求" }).click()
  await expect(turn).toContainText("新编号完成")
  expect(bodies[0].client_request_id).not.toBe(bodies[1].client_request_id)
  expect(bodies[0].message).toBe(bodies[1].message)
})

test("结果未知先查询原请求，用户确认后才准备新编号", async ({ page }) => {
  const bodies: Record<string, unknown>[] = []
  await page.route("**/api/v1/chat", (route) => {
    bodies.push(route.request().postDataJSON())
    if (bodies.length < 3) {
      return route.fulfill({
        status: 409,
        json: {
          detail: { code: "request_outcome_unknown", message: "结果未知" },
        },
      })
    }
    return route.fulfill({ json: { answer: "确认后完成", request_id: "done" } })
  })
  await page.goto("/workspace")
  await page.locator("#zm-ask-input").fill("结果未知")
  await page.getByRole("button", { name: "发送", exact: true }).click()
  const turn = page.locator(".zm-turn").first()
  await expect(turn).toContainText("可能产生重复费用")
  await turn
    .getByRole("button", { name: "重试原请求（可能再次调用模型）" })
    .click()
  await expect(turn).toContainText("可能产生重复费用")
  expect(bodies[1]).toEqual(bodies[0])
  await turn.getByRole("button", { name: "确认并准备新请求" }).click()
  expect(bodies).toHaveLength(2)
  await expect(turn).toContainText("已准备新请求")
  await turn.getByRole("button", { name: "重试此请求" }).click()
  await expect(turn).toContainText("确认后完成")
  expect(bodies[2].client_request_id).not.toBe(bodies[1].client_request_id)
  expect(bodies[2].message).toBe(bodies[1].message)
})

test("跨标签切换账号时清空未发送的私有摘录", async ({ page }) => {
  await emptySourcesForSecondAccount(page)
  let requests = 0
  await page.route("**/api/v1/chat", (route) => {
    requests += 1
    return route.fulfill({
      json: { answer: "unexpected", request_id: "unexpected" },
    })
  })
  await page.goto("/workspace")
  await page
    .locator(".zm-result")
    .filter({ hasText: "rag-eval.md" })
    .first()
    .click()
  await page
    .getByRole("main")
    .getByRole("button", { name: "引用到上下文" })
    .click()
  await expect(page.locator(".zm-ctx__count")).toHaveText("1 条")
  await page.locator("#zm-ask-input").fill("账号A未发送的问题")
  const reloaded = page.waitForEvent("load")
  await switchAccountInOtherTab(page)
  await reloaded
  await expect(page.locator(".zm-ctx__count")).toHaveText("0 条")
  await expect(page.locator("#zm-ask-input")).toBeEmpty()
  expect(requests).toBe(0)
})

test("账号切换后旧账号在途回答不会显示给新账号", async ({ page }) => {
  await emptySourcesForSecondAccount(page)
  let release!: () => void
  const pending = new Promise<void>((resolve) => {
    release = resolve
  })
  const authorization: string[] = []
  await page.route("**/api/v1/chat", async (route) => {
    authorization.push(route.request().headers().authorization ?? "")
    await pending
    await route
      .fulfill({ json: { answer: "账号A私有迟到回答", request_id: "late" } })
      .catch(() => {})
  })
  try {
    await page.goto("/workspace")
    await page.locator("#zm-ask-input").fill("账号A的问题")
    await page.getByRole("button", { name: "发送", exact: true }).click()
    await expect.poll(() => authorization.length).toBe(1)
    expect(authorization).toEqual(["Bearer workspace-ui-test-token"])
    const reloaded = page.waitForEvent("load")
    await switchAccountInOtherTab(page)
    await reloaded
    release()
    await expect(page.locator(".zm-turn")).toHaveCount(0)
    await expect(page.getByText("账号A私有迟到回答")).toHaveCount(0)
    expect(authorization).toEqual(["Bearer workspace-ui-test-token"])
  } finally {
    release()
  }
})

test("账号在重试前变化时不发送旧请求", async ({ page }) => {
  await emptySourcesForSecondAccount(page)
  const authorizations: string[] = []
  await page.route("**/api/v1/chat", (route) => {
    authorizations.push(route.request().headers().authorization ?? "")
    return route.fulfill({
      status: 503,
      json: { detail: { message: "临时失败" } },
    })
  })
  await page.goto("/workspace")
  await page.locator("#zm-ask-input").fill("原账号问题")
  await page.getByRole("button", { name: "发送", exact: true }).click()
  const retry = page.getByRole("button", { name: "重试此请求" })
  await expect(retry).toBeVisible()
  const reloaded = page.waitForEvent("load")
  await retry.evaluate((button: HTMLButtonElement) => {
    localStorage.setItem("access_token", "account-b-token")
    button.click()
  })
  await reloaded
  await expect(page.locator(".zm-turn")).toHaveCount(0)
  expect(authorizations).toEqual(["Bearer workspace-ui-test-token"])
})

test("认证回调前会话切换时不会把原问题发给新账号", async ({ page }) => {
  await emptySourcesForSecondAccount(page)
  const authorizations: string[] = []
  await page.route("**/api/v1/chat", (route) => {
    authorizations.push(route.request().headers().authorization ?? "")
    return route.fulfill({
      json: { answer: "unexpected", request_id: "unexpected" },
    })
  })
  await page.goto("/workspace")
  await expect(page.locator(".zm-result").first()).toBeVisible()
  await page.locator("#zm-ask-input").fill("原账号私有问题")
  const reloaded = page.waitForEvent("load")
  await page.evaluate(() => {
    const original = Storage.prototype.getItem
    let tokenReads = 0
    Storage.prototype.getItem = function (key: string) {
      if (this === localStorage && key === "access_token") {
        tokenReads += 1
        if (tokenReads === 3) {
          sessionStorage.setItem("workspace-race-token-read", "3")
          localStorage.setItem("access_token", "account-b-token")
        }
      }
      return original.call(this, key)
    }
    const button = document.querySelector<HTMLButtonElement>(
      ".zm-ask__row button",
    )
    button?.click()
  })
  await reloaded
  expect(
    await page.evaluate(() =>
      sessionStorage.getItem("workspace-race-token-read"),
    ),
  ).toBe("3")
  expect(authorizations).toEqual([])
  await expect(page.locator(".zm-turn")).toHaveCount(0)
  await expect(page.locator("#zm-ask-input")).toBeEmpty()
})

test("另一个标签快速切换后又切回仍清空旧会话", async ({ page }) => {
  await page.goto("/workspace")
  await page
    .locator(".zm-result")
    .filter({ hasText: "rag-eval.md" })
    .first()
    .click()
  await page
    .getByRole("main")
    .getByRole("button", { name: "引用到上下文" })
    .click()
  await expect(page.locator(".zm-ctx__count")).toHaveText("1 条")
  const other = await page.context().newPage()
  try {
    await other.route("**/api/**", (route) => route.abort())
    await other.route("**/session-switch-test", (route) =>
      route.fulfill({ contentType: "text/html", body: "<!doctype html>" }),
    )
    await other.goto(new URL("/session-switch-test", page.url()).href)
    const reloaded = page.waitForEvent("load")
    await other.evaluate(() => {
      localStorage.setItem("access_token", "account-b-token")
      localStorage.setItem("access_token", "workspace-ui-test-token")
    })
    await reloaded
    await expect(page.locator(".zm-ctx__count")).toHaveText("0 条")
  } finally {
    await other.close()
  }
})

test("新建速记后出现在左侧速记列表", async ({ page }) => {
  await page.goto("/workspace")

  await page.getByRole("button", { name: "新建速记" }).click()
  await page.locator("#zm-note-input-title").fill("工作台联调记录")
  await page.locator("#zm-note-input-body").fill("记录一次联调。")
  await page.getByRole("button", { name: "保存速记" }).click()

  await expect(page.locator(".zm-toast")).toHaveText("速记已保存")
  await expect(
    page.locator(".zm-notes__item").filter({ hasText: "工作台联调记录" }),
  ).toBeVisible()
})

test("打开代码文件后定位命中，并能反查相关笔记", async ({ page }) => {
  await page.goto("/workspace")

  await page.locator(".zm-tree__row").first().click()
  await expect(page.locator(".zm-code")).toContainText("rank_chunks")

  await page.locator("#zm-search-input").fill("rank_chunks")
  await page
    .getByRole("region", { name: "代码工作台" })
    .getByRole("button", { name: "在代码中查找" })
    .click()

  await expect(page.locator(".zm-code__line.is-hit")).toHaveCount(2)
  await expect(page.locator(".zm-toast")).toContainText("定位")

  await page
    .getByRole("region", { name: "代码工作台" })
    .getByRole("button", { name: "查找相关笔记" })
    .click()

  await expect(page.locator("#zm-search-input")).toHaveValue("search")
  await expect(page.locator(".zm-toast")).toContainText("代码符号")
})
