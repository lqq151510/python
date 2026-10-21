import { expect, test } from "@playwright/test"

const document = {
  id: "22222222-2222-2222-2222-222222222222",
  filename: "guide.md",
  chunk_count: 2,
  size_bytes: 120,
  created_at: "2026-10-08T01:00:00Z",
}
const source = {
  citation: "S1",
  document_id: document.id,
  chunk_id: "33333333-3333-3333-3333-333333333333",
  filename: document.filename,
  heading: "异步接口",
  line_start: 2,
  line_end: 4,
  content: "FastAPI 可以等待网络响应。\n<script>window.bad=true</script>",
  score: 1.2,
}

test("hybrid retrieval shows the actual fallback and carries its selection into chat", async ({
  page,
}) => {
  await page.route("**/api/v1/documents/search", (route) => {
    expect(route.request().postDataJSON()).toMatchObject({ method: "hybrid" })
    return route.fulfill({
      json: {
        query: "缓存失效",
        method: "bm25",
        warning: "vector_timeout",
        sources: [source],
      },
    })
  })
  await page.goto("/knowledge")
  await page.getByLabel("检索方式", { exact: true }).selectOption("hybrid")
  await page.getByLabel("检索内容", { exact: true }).fill("缓存失效")
  await page.getByRole("button", { name: "检索文档" }).click()
  await expect(
    page.getByText("语义检索超时，本次已改用关键词检索。", { exact: false }),
  ).toBeVisible()
  await page.getByRole("link", { name: "带着这个问题去问答" }).click()
  await expect(page.getByLabel("检索方式", { exact: true })).toHaveValue(
    "hybrid",
  )
  await page.getByRole("button", { name: "只检索原文" }).click()
  await expect(
    page.getByText("语义检索超时，本次已改用关键词检索。", { exact: false }),
  ).toBeVisible()
})

test("chat sends the semantic selection and shows the returned retrieval method", async ({
  page,
}) => {
  await page.route("**/api/v1/chat", (route) => {
    expect(route.request().postDataJSON()).toMatchObject({
      use_documents: true,
      retrieval_method: "vector",
    })
    return route.fulfill({
      json: {
        answer: "模拟语义检索回答 [S1]",
        request_id: "vector-ui",
        sources: [source],
        retrieval_method: "vector",
      },
    })
  })
  await page.goto("/chat?question=缓存失效&method=vector")
  await expect(page.getByLabel("检索方式", { exact: true })).toHaveValue(
    "vector",
  )
  await page.getByRole("button", { name: "发送问题" }).click()
  await expect(
    page.getByText("模拟语义检索回答 [S1]", { exact: true }),
  ).toBeVisible()
  await expect(
    page.getByText("本次使用：语义检索。", { exact: false }),
  ).toBeVisible()
})

test.use({ storageState: { cookies: [], origins: [] } })

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() =>
    localStorage.setItem("access_token", "knowledge-ui-token"),
  )
  await page.route("**/api/v1/users/me", (route) =>
    route.fulfill({
      json: {
        id: "11111111-1111-1111-1111-111111111111",
        email: "ui@example.com",
        is_active: true,
        is_superuser: false,
      },
    }),
  )
  await page.route("**/api/v1/documents", (route) =>
    route.fulfill({ json: { data: [], count: 0 } }),
  )
})

test("uploads multipart with authentication, persists listing and reports duplicates", async ({
  page,
}) => {
  let uploaded = false
  let uploads = 0
  await page.route("**/api/v1/documents", (route) => {
    if (route.request().method() === "GET")
      return route.fulfill({
        json: { data: uploaded ? [document] : [], count: uploaded ? 1 : 0 },
      })
    expect(route.request().headers().authorization).toBe(
      "Bearer knowledge-ui-token",
    )
    expect(route.request().headers()["content-type"]).toContain(
      "multipart/form-data; boundary=",
    )
    uploads += 1
    uploaded = true
    return route.fulfill({
      status: uploads === 1 ? 201 : 200,
      json: { document, duplicate: uploads > 1 },
    })
  })
  await page.goto("/knowledge")
  await expect(page.getByText("还没有文档，先上传一份项目说明。")).toBeVisible()
  await page.getByLabel("导入文档", { exact: true }).setInputFiles({
    name: "guide.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# Async\nFastAPI"),
  })
  await page.getByRole("button", { name: "上传文档" }).click()
  await expect(page.getByRole("status")).toContainText("导入成功")
  await expect(page.getByRole("region", { name: "已导入文档" })).toContainText(
    "guide.md",
  )
  await page.reload()
  await expect(page.getByRole("region", { name: "已导入文档" })).toContainText(
    "guide.md",
  )
  await page.getByLabel("导入文档", { exact: true }).setInputFiles({
    name: "guide.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# Async\nFastAPI"),
  })
  await page.getByRole("button", { name: "上传文档" }).click()
  await expect(page.getByRole("status")).toContainText("没有重复导入")
})

test("keeps upload locked until the refreshed document list is ready", async ({
  page,
}) => {
  let uploads = 0
  let release: () => void = () => {}
  const refreshed = new Promise<void>((resolve) => {
    release = resolve
  })
  await page.route("**/api/v1/documents", async (route) => {
    if (route.request().method() === "POST") {
      uploads += 1
      return route.fulfill({
        status: 201,
        json: { document, duplicate: false },
      })
    }
    if (uploads) await refreshed
    return route.fulfill({
      json: { data: uploads ? [document] : [], count: uploads ? 1 : 0 },
    })
  })
  await page.goto("/knowledge")
  await expect(page.getByText("还没有文档，先上传一份项目说明。")).toBeVisible()
  const fileInput = page.getByLabel("导入文档", { exact: true })
  await fileInput.setInputFiles({
    name: "guide.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("FastAPI"),
  })
  await page
    .locator("form")
    .first()
    .evaluate((form: HTMLFormElement) => {
      form.requestSubmit()
      form.requestSubmit()
    })
  try {
    await expect.poll(() => uploads).toBe(1)
    await expect(page.getByRole("button", { name: "正在导入…" })).toBeDisabled()
    await expect(page.getByLabel("导入文档", { exact: true })).toBeDisabled()
  } finally {
    release()
  }
  await expect(page.getByRole("button", { name: "上传文档" })).toBeEnabled()
  await expect(page.getByRole("region", { name: "已导入文档" })).toContainText(
    "guide.md",
  )
  expect(uploads).toBe(1)
})

test("rejects unsupported or oversized files before sending", async ({
  page,
}) => {
  let requests = 0
  await page.route("**/api/v1/documents", (route) => {
    if (route.request().method() === "POST") requests += 1
    return route.fulfill({ json: { data: [], count: 0 } })
  })
  await page.goto("/knowledge")
  await page.getByRole("button", { name: "上传文档" }).click()
  await expect(page.getByRole("alert")).toContainText("请选择")
  await page.getByLabel("导入文档", { exact: true }).setInputFiles({
    name: "x.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("PDF"),
  })
  await page.getByRole("button", { name: "上传文档" }).click()
  await expect(page.getByRole("alert")).toContainText(".md 或 .txt")
  await page.getByLabel("导入文档", { exact: true }).setInputFiles({
    name: "x.txt",
    mimeType: "text/plain",
    buffer: Buffer.alloc(256 * 1024 + 1, "x"),
  })
  await page.getByRole("button", { name: "上传文档" }).click()
  await expect(page.getByRole("alert")).toContainText("256 KiB")
  expect(requests).toBe(0)
})

test("searches with trimmed query and displays safe source lines and empty state", async ({
  page,
}) => {
  let requests = 0
  await page.route("**/api/v1/documents/search", (route) => {
    requests += 1
    expect(route.request().postDataJSON()).toEqual({
      query: requests === 1 ? "FastAPI" : "unmatched",
      limit: 5,
    })
    return route.fulfill({
      json: {
        query: requests === 1 ? "FastAPI" : "unmatched",
        method: "bm25",
        sources: requests === 1 ? [source] : [],
      },
    })
  })
  await page.goto("/knowledge")
  await page.getByLabel("检索内容", { exact: true }).fill("   ")
  await page.getByRole("button", { name: "检索文档" }).click()
  await expect(page.getByRole("alert")).toContainText("1～1000")
  await page.getByLabel("检索内容", { exact: true }).fill("  FastAPI  ")
  await page.getByRole("button", { name: "检索文档" }).click()
  const results = page.getByRole("region", { name: "检索结果" })
  await expect(results).toContainText("[S1] guide.md")
  await expect(results).toContainText("第 2～4 行")
  await results.getByText("查看原文片段", { exact: true }).click()
  await expect(results).toContainText("<script>window.bad=true</script>")
  expect(await results.locator("script").count()).toBe(0)
  await page.getByLabel("检索内容", { exact: true }).fill("unmatched")
  await page.getByRole("button", { name: "检索文档" }).click()
  await expect(results).toContainText("没有匹配的片段")
})

test("confirms deletion before removing a document and clears stale sources", async ({
  page,
}) => {
  let deleted = false
  await page.route("**/api/v1/documents", (route) =>
    route.fulfill({
      json: { data: deleted ? [] : [document], count: deleted ? 0 : 1 },
    }),
  )
  await page.route(`**/api/v1/documents/${document.id}`, (route) => {
    expect(route.request().method()).toBe("DELETE")
    deleted = true
    return route.fulfill({ json: { message: "deleted" } })
  })
  await page.goto("/knowledge")
  await page.getByRole("button", { name: "删除 guide.md" }).click()
  expect(deleted).toBe(false)
  await page.getByRole("button", { name: "取消", exact: true }).click()
  expect(deleted).toBe(false)
  await page.getByRole("button", { name: "删除 guide.md" }).click()
  await page.getByRole("button", { name: "确认删除", exact: true }).click()
  await expect(page.getByRole("status")).toContainText("片段已删除")
  await expect(
    page.getByRole("region", { name: "已导入文档" }),
  ).not.toContainText("guide.md")
})

test("renders backend upload failures without clearing the selected file", async ({
  page,
}) => {
  await page.route("**/api/v1/documents", (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ json: { data: [], count: 0 } })
      : route.fulfill({
          status: 422,
          json: {
            detail: {
              code: "invalid_encoding",
              message: "文件需要使用 UTF-8 编码。",
            },
          },
        }),
  )
  await page.goto("/knowledge")
  await page.getByLabel("导入文档", { exact: true }).setInputFiles({
    name: "guide.md",
    mimeType: "text/plain",
    buffer: Buffer.from("text"),
  })
  await page.getByRole("button", { name: "上传文档" }).click()
  await expect(page.getByRole("alert")).toContainText("UTF-8 编码")
  await expect(page.getByRole("button", { name: "上传文档" })).toBeEnabled()
})

test("chat sends document mode and displays retrieved sources", async ({
  page,
}) => {
  await page.route("**/api/v1/chat", (route) => {
    expect(route.request().postDataJSON()).toEqual({
      message: "FastAPI",
      use_documents: true,
    })
    return route.fulfill({
      json: {
        answer: "等待网络响应。 [S1]",
        request_id: "grounded-ui",
        sources: [source],
      },
    })
  })
  await page.goto("/chat")
  await page.getByRole("checkbox", { name: "使用我的知识库回答" }).check()
  await page.getByLabel("你的问题", { exact: true }).fill("FastAPI")
  await page.getByRole("button", { name: "发送问题" }).click()
  const answer = page.getByRole("region", { name: "AI 回答" })
  await expect(answer).toContainText("等待网络响应。 [S1]")
  await expect(
    page.getByRole("complementary", { name: "本次来源" }),
  ).toContainText("[S1] guide.md")
})

test("retrieves real-contract sources without asking the model and switches excerpts", async ({
  page,
}) => {
  let modelRequests = 0
  await page.route("**/api/v1/chat", (route) => {
    modelRequests += 1
    return route.fulfill({
      status: 503,
      json: { detail: { message: "未配置" } },
    })
  })
  await page.route("**/api/v1/documents/search", (route) => {
    expect(route.request().headers().authorization).toBe(
      "Bearer knowledge-ui-token",
    )
    expect(route.request().postDataJSON()).toEqual({
      query: "FastAPI",
      limit: 5,
    })
    return route.fulfill({
      json: {
        query: "FastAPI",
        method: "bm25",
        sources: [
          source,
          {
            ...source,
            citation: "S2",
            chunk_id: "44444444-4444-4444-4444-444444444444",
            heading: "第二片段",
            content: "只读检索的另一段原文。",
          },
        ],
      },
    })
  })
  await page.goto("/chat")
  await page.getByRole("checkbox", { name: "使用我的知识库回答" }).check()
  await page.getByLabel("你的问题", { exact: true }).fill("  FastAPI  ")
  await page.getByRole("button", { name: "只检索原文", exact: true }).click()
  const panel = page.getByRole("complementary", { name: "本次来源" })
  await expect(panel.getByLabel("原文片段 S1")).toContainText(
    "<script>window.bad=true</script>",
  )
  expect(await panel.locator("script").count()).toBe(0)
  await panel.getByRole("button", { name: /\[S2\]/ }).click()
  await expect(panel.getByLabel("原文片段 S2")).toContainText("另一段原文")
  expect(modelRequests).toBe(0)
})

test("carries a searched question from knowledge to chat", async ({ page }) => {
  await page.route("**/api/v1/documents/search", (route) =>
    route.fulfill({
      json: { query: "FastAPI", method: "bm25", sources: [source] },
    }),
  )
  await page.goto("/knowledge")
  await page.getByLabel("检索内容", { exact: true }).fill("FastAPI")
  await page.getByRole("button", { name: "检索文档", exact: true }).click()
  await page
    .getByRole("link", { name: "带着这个问题去问答", exact: true })
    .click()
  await expect(page.getByLabel("你的问题", { exact: true })).toHaveValue(
    "FastAPI",
  )
  await expect(
    page.getByRole("checkbox", { name: "使用我的知识库回答" }),
  ).toBeChecked()
})

test("chat distinguishes missing evidence from invalid input", async ({
  page,
}) => {
  await page.route("**/api/v1/chat", (route) =>
    route.fulfill({
      status: 422,
      json: {
        detail: {
          code: "no_document_evidence",
          message: "知识库中没有匹配的片段，请先导入相关文档或调整问题。",
        },
      },
      headers: {
        "X-Request-ID": "no-evidence-ui",
        "Access-Control-Expose-Headers": "X-Request-ID",
      },
    }),
  )
  await page.goto("/chat")
  await page.getByRole("checkbox", { name: "使用我的知识库回答" }).check()
  await page.getByLabel("你的问题", { exact: true }).fill("unmatched")
  await page.getByRole("button", { name: "发送问题" }).click()
  await expect(page.getByRole("alert")).toContainText("没有匹配的片段")
  await expect(page.getByRole("alert")).toContainText("no-evidence-ui")
})

test("opens the source document and highlights its stored chunk as plain text", async ({
  page,
}) => {
  await page.route("**/api/v1/documents/search", (route) =>
    route.fulfill({
      json: { query: "FastAPI", method: "bm25", sources: [source] },
    }),
  )
  await page.route(`**/api/v1/documents/${document.id}`, (route) => {
    expect(route.request().headers().authorization).toBe(
      "Bearer knowledge-ui-token",
    )
    return route.fulfill({
      json: {
        document,
        chunks: [
          {
            id: "44444444-4444-4444-4444-444444444444",
            position: 0,
            heading: "第一段",
            line_start: 1,
            line_end: 1,
            content: "  leading spaces",
          },
          {
            id: source.chunk_id,
            position: 1,
            heading: source.heading,
            line_start: source.line_start,
            line_end: source.line_end,
            content: source.content,
          },
        ],
      },
    })
  })
  await page.goto("/chat")
  await page.getByLabel("你的问题", { exact: true }).fill("FastAPI")
  await page.getByRole("checkbox", { name: /使用我的知识库回答/ }).check()
  await page.getByRole("button", { name: "只检索原文", exact: true }).click()
  await page.getByRole("link", { name: "打开来源文档", exact: true }).click()
  await expect(page).toHaveURL(
    new RegExp(`/knowledge\\?document=${document.id}&chunk=${source.chunk_id}`),
  )
  const preview = page.getByRole("region", {
    name: "文档片段预览",
    exact: true,
  })
  await expect(preview).toContainText("第一段")
  await expect(preview).toContainText("当前来源")
  await expect(
    preview.getByRole("region", { name: "文档片段 2", exact: true }),
  ).toHaveClass(/da-preview-selected/)
  await expect(preview.locator("pre").first()).toHaveText("  leading spaces")
  await expect(preview.locator("script")).toHaveCount(0)
  await expect(preview).toContainText("<script>window.bad=true</script>")
  await page.getByRole("button", { name: "关闭预览" }).click()
  await expect(preview).toHaveCount(0)
})

test("preserves document scope from search to retrieval and model request", async ({
  page,
}) => {
  const requests: unknown[] = []
  await page.route("**/api/v1/documents", (route) =>
    route.fulfill({ json: { data: [document], count: 1 } }),
  )
  await page.route("**/api/v1/documents/search", (route) => {
    const body = route.request().postDataJSON()
    expect(body).toEqual({
      query: "FastAPI",
      limit: 5,
      document_id: document.id,
    })
    requests.push(body)
    return route.fulfill({
      json: {
        query: body.query,
        document_id: document.id,
        method: "bm25",
        sources: [source],
      },
    })
  })
  let modelBody: unknown
  await page.route("**/api/v1/chat", (route) => {
    modelBody = route.request().postDataJSON()
    return route.fulfill({
      json: {
        answer: "Simulated scoped answer [S1]",
        request_id: "scoped-test",
        sources: [source],
      },
    })
  })
  await page.goto("/knowledge")
  await page.getByLabel("检索范围", { exact: true }).selectOption(document.id)
  await page.getByLabel("检索内容", { exact: true }).fill("FastAPI")
  await page.getByRole("button", { name: "检索文档", exact: true }).click()
  await page.getByRole("link", { name: "带着这个问题去问答" }).click()
  await expect(page.getByLabel("检索范围", { exact: true })).toHaveValue(
    document.id,
  )
  await page.getByRole("button", { name: "只检索原文", exact: true }).click()
  await expect(
    page.getByRole("complementary", { name: "本次来源" }),
  ).toContainText(document.filename)
  expect(requests).toHaveLength(2)
  await page.getByRole("button", { name: "发送问题", exact: true }).click()
  await expect(page.getByRole("region", { name: "AI 回答" })).toContainText(
    "Simulated scoped answer",
  )
  expect(modelBody).toEqual({
    message: "FastAPI",
    use_documents: true,
    document_id: document.id,
  })
  await page.getByRole("checkbox", { name: /使用我的知识库回答/ }).uncheck()
  await page.getByRole("button", { name: "发送问题", exact: true }).click()
  await expect.poll(() => modelBody).toEqual({ message: "FastAPI" })
})

test("shows deleted preview errors and retries without exposing old fragments", async ({
  page,
}) => {
  let available = false
  await page.route("**/api/v1/documents", (route) =>
    route.fulfill({ json: { data: [document], count: 1 } }),
  )
  await page.route(`**/api/v1/documents/${document.id}`, (route) =>
    available
      ? route.fulfill({
          json: {
            document,
            chunks: [
              {
                id: source.chunk_id,
                position: 0,
                heading: source.heading,
                line_start: 2,
                line_end: 4,
                content: source.content,
              },
            ],
          },
        })
      : route.fulfill({
          status: 404,
          json: {
            detail: {
              code: "document_not_found",
              message: "文档不存在或已删除。",
            },
          },
        }),
  )
  await page.goto(`/knowledge?document=${document.id}`)
  const preview = page.getByRole("region", {
    name: "文档片段预览",
    exact: true,
  })
  await expect(preview.getByRole("alert")).toContainText("文档不存在或已删除")
  await expect(preview.locator("pre")).toHaveCount(0)
  available = true
  await preview.getByRole("button", { name: "重新加载片段" }).click()
  await expect(preview).toContainText(source.content)
  await page.getByRole("button", { name: "关闭预览" }).click()
  available = false
  await page.getByRole("link", { name: "预览 guide.md", exact: true }).click()
  await expect(preview.getByRole("alert")).toContainText("文档不存在或已删除")
  await expect(preview.locator("pre")).toHaveCount(0)
})

test("deleting a previewed document closes its preview and resets its search scope", async ({
  page,
}) => {
  let deleted = false
  await page.route("**/api/v1/documents", (route) =>
    route.fulfill({
      json: { data: deleted ? [] : [document], count: deleted ? 0 : 1 },
    }),
  )
  await page.route(`**/api/v1/documents/${document.id}`, (route) => {
    if (route.request().method() === "DELETE") {
      deleted = true
      return route.fulfill({ json: { message: "文档及其片段已删除。" } })
    }
    return route.fulfill({
      json: {
        document,
        chunks: [
          {
            id: source.chunk_id,
            position: 0,
            heading: source.heading,
            line_start: 2,
            line_end: 4,
            content: source.content,
          },
        ],
      },
    })
  })
  await page.goto(`/knowledge?document=${document.id}`)
  const preview = page.getByRole("region", {
    name: "文档片段预览",
    exact: true,
  })
  await expect(preview).toContainText(source.content)
  await page.getByLabel("检索范围", { exact: true }).selectOption(document.id)
  await page.getByRole("button", { name: "删除 guide.md", exact: true }).click()
  await page.getByRole("button", { name: "确认删除", exact: true }).click()
  await expect(preview).toHaveCount(0)
  await expect(page.getByLabel("检索范围", { exact: true })).toHaveValue("")
  await expect(page.getByRole("status")).toContainText("片段已删除")
  await expect(page).toHaveURL(/\/knowledge$/)
})
