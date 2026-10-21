import { expect, test } from "@playwright/test"

test.use({ storageState: { cookies: [], origins: [] } })

test.describe("authenticated chat UI with simulated responses", () => {
  test.beforeEach(async ({ page }) => {
    await page.route("**/api/v1/documents", (route) =>
      route.fulfill({ json: { data: [], count: 0 } }),
    )
    await page.route("**/api/v1/chat/history**", (route) =>
      route.fulfill({ json: { data: [], count: 0 } }),
    )
    await page.addInitScript(() => {
      localStorage.setItem("access_token", "chat-ui-test-token")
    })
    await page.route("**/api/v1/users/me", (route) =>
      route.fulfill({
        json: {
          id: "11111111-1111-1111-1111-111111111111",
          email: "ui-test@example.com",
          full_name: "UI 测试用户",
          is_active: true,
          is_superuser: false,
        },
      }),
    )
  })

  test("rejects empty and overlong questions without sending a request", async ({
    page,
  }) => {
    let requests = 0
    await page.route("**/api/v1/chat", (route) => {
      requests += 1
      return route.fulfill({
        json: { answer: "Unexpected", request_id: "unexpected" },
      })
    })
    await page.goto("/chat")
    const input = page.getByLabel("你的问题", { exact: true })
    await expect(input).toBeEmpty()
    await input.fill("  \n  ")
    await page.getByRole("button", { name: "发送问题" }).click()
    await expect(page.getByRole("alert")).toContainText("1～4000")
    await input.fill("字".repeat(4001))
    await page.getByRole("button", { name: "发送问题" }).click()
    await expect(page.getByRole("alert")).toContainText("1～4000")
    expect(requests).toBe(0)
  })

  test("sends trimmed input with bearer auth, renders plain text, and forgets on refresh", async ({
    page,
  }) => {
    await page.route("**/api/v1/chat", (route) => {
      expect(route.request().postDataJSON()).toMatchObject({
        message: "解释异步接口",
        client_request_id: expect.stringMatching(
          /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
        ),
      })
      expect(route.request().headers().authorization).toBe(
        "Bearer chat-ui-test-token",
      )
      return route.fulfill({
        json: {
          answer:
            "异步接口等待 I/O 时可以处理其他请求。\n<script>window.bad=true</script>",
          request_id: "ui-success-001",
        },
        headers: { "X-Request-ID": "ui-success-001" },
      })
    })
    await page.goto("/chat")
    await page.getByLabel("你的问题", { exact: true }).fill("  解释异步接口  ")
    await page.getByRole("button", { name: "发送问题" }).click()
    const answer = page.getByRole("region", { name: "AI 回答" })
    await expect(answer).toContainText("异步接口等待 I/O")
    await expect(answer).toContainText("<script>window.bad=true</script>")
    await expect(answer).toContainText("ui-success-001")
    expect(await answer.locator("script").count()).toBe(0)
    await page.reload()
    await expect(page.getByLabel("你的问题", { exact: true })).toBeEmpty()
    await expect(answer).toHaveCount(0)
  })

  test("counts Unicode code points at the accepted 4000-character boundary", async ({
    page,
  }) => {
    const message = "😀".repeat(4000)
    await page.route("**/api/v1/chat", (route) => {
      expect(route.request().postDataJSON()).toMatchObject({
        message,
        client_request_id: expect.any(String),
      })
      return route.fulfill({
        json: { answer: "边界输入已接收", request_id: "ui-boundary" },
      })
    })
    await page.goto("/chat")
    await page.getByLabel("你的问题", { exact: true }).fill(message)
    await page.getByRole("button", { name: "发送问题" }).click()
    await expect(page.getByRole("region", { name: "AI 回答" })).toContainText(
      "边界输入已接收",
    )
  })

  test("shows loading and prevents concurrent duplicate submissions", async ({
    page,
  }) => {
    let requests = 0
    let release: () => void = () => {}
    const responseReady = new Promise<void>((resolve) => {
      release = resolve
    })
    await page.route("**/api/v1/chat", async (route) => {
      requests += 1
      await responseReady
      await route.fulfill({
        json: { answer: "已完成", request_id: "ui-pending" },
      })
    })
    await page.goto("/chat")
    await page.getByLabel("你的问题", { exact: true }).fill("测试重复提交")
    await page.locator("form").evaluate((form: HTMLFormElement) => {
      form.requestSubmit()
      form.requestSubmit()
    })
    try {
      await expect(page.getByRole("status")).toContainText("正在生成回答")
      await expect(
        page.getByRole("button", { name: "正在回答…" }),
      ).toBeDisabled()
      await expect(page.getByLabel("你的问题", { exact: true })).toBeDisabled()
      await expect.poll(() => requests).toBe(1)
    } finally {
      release()
    }
    await expect(page.getByRole("region", { name: "AI 回答" })).toContainText(
      "已完成",
    )
    await expect(page.getByRole("button", { name: "发送问题" })).toBeEnabled()
    expect(requests).toBe(1)
  })

  test("displays safe service failure and request identifier and allows a new attempt", async ({
    page,
  }) => {
    let requests = 0
    await page.route("**/api/v1/chat", (route) => {
      requests += 1
      if (requests > 1) {
        return route.fulfill({
          json: { answer: "服务已恢复", request_id: "ui-recovered" },
        })
      }
      return route.fulfill({
        status: 503,
        json: {
          detail: {
            code: "model_not_configured",
            message: "模型服务尚未配置，请联系维护者。",
          },
        },
        headers: {
          "X-Request-ID": "ui-error-001",
          "Access-Control-Expose-Headers": "X-Request-ID",
        },
      })
    })
    await page.goto("/chat")
    await page.getByLabel("你的问题", { exact: true }).fill("测试失败")
    await page.getByRole("button", { name: "发送问题" }).click()
    await expect(page.getByRole("alert")).toContainText("模型服务尚未配置")
    await expect(page.getByRole("alert")).toContainText("ui-error-001")
    await expect(page.getByRole("button", { name: "发送问题" })).toBeEnabled()
    await page.getByRole("button", { name: "发送问题" }).click()
    await expect(page.getByRole("region", { name: "AI 回答" })).toContainText(
      "服务已恢复",
    )
    await expect(page.getByRole("alert")).toHaveCount(0)
    expect(requests).toBe(2)
  })

  test("persists successful answers with citations and restores them from history", async ({
    page,
  }) => {
    const turnId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    let saved = false
    const summary = {
      id: turnId,
      message: "恢复问答历史",
      answer_preview: "备份恢复步骤见部署手册。",
      source_count: 1,
      retrieval_method: "bm25",
      created_at: "2026-10-17T02:00:00Z",
    }
    const detail = {
      ...summary,
      answer: "备份恢复步骤见部署手册 [S1]。",
      request_id: "chat-history-request-1",
      sources: [
        {
          citation: "S1",
          document_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          chunk_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
          filename: "runbook.md",
          heading: "备份恢复",
          line_start: 8,
          line_end: 10,
          content: "备份数据库后执行恢复，并检查 Alembic 版本。",
          score: 1.25,
        },
      ],
    }
    await page.route("**/api/v1/chat/history**", (route) => {
      const pathname = new URL(route.request().url()).pathname
      if (pathname.endsWith(`/history/${turnId}`)) {
        return route.fulfill({ json: detail })
      }
      return route.fulfill({
        json: { data: saved ? [summary] : [], count: saved ? 1 : 0 },
      })
    })
    await page.route("**/api/v1/chat", (route) => {
      expect(route.request().postDataJSON()).toMatchObject({
        message: "恢复问答历史",
        use_documents: true,
        client_request_id: expect.any(String),
      })
      saved = true
      return route.fulfill({
        json: {
          answer: detail.answer,
          request_id: detail.request_id,
          sources: detail.sources,
          retrieval_method: "bm25",
        },
      })
    })
    await page.goto("/chat")
    await page.getByLabel("你的问题", { exact: true }).fill("恢复问答历史")
    await page.getByLabel("使用我的知识库回答").check()
    await page.getByRole("button", { name: "发送问题" }).click()
    await expect(page.getByRole("region", { name: "AI 回答" })).toContainText(
      "恢复问答历史",
    )
    await page.getByText("查看历史", { exact: false }).click()
    const historyEntry = page.getByRole("button", { name: /恢复问答历史/ })
    await expect(historyEntry).toBeVisible()
    await historyEntry.click()
    const historyDetail = page.getByRole("region", { name: "历史问答详情" })
    await expect(historyDetail).toContainText("备份恢复步骤见部署手册 [S1]")
    await expect(historyDetail).toContainText("runbook.md · 备份恢复")
    await page.reload()
    await page.getByText("查看历史", { exact: false }).click()
    await expect(
      page.getByRole("button", { name: /恢复问答历史/ }),
    ).toBeVisible()
  })

  test("handles the FastAPI validation response", async ({ page }) => {
    await page.route("**/api/v1/chat", (route) =>
      route.fulfill({
        status: 422,
        json: {
          detail: [
            {
              loc: ["body", "message"],
              msg: "invalid",
              type: "string_too_long",
            },
          ],
        },
        headers: {
          "X-Request-ID": "ui-validation",
          "Access-Control-Expose-Headers": "X-Request-ID",
        },
      }),
    )
    await page.goto("/chat")
    await page.getByLabel("你的问题", { exact: true }).fill("请求校验示例")
    await page.getByRole("button", { name: "发送问题" }).click()
    await expect(page.getByRole("alert")).toContainText("问题格式不正确")
    await expect(page.getByRole("alert")).toContainText("ui-validation")
  })

  test("shows a network failure without leaking transport details", async ({
    page,
  }) => {
    await page.route("**/api/v1/chat", (route) =>
      route.abort("connectionrefused"),
    )
    await page.goto("/chat")
    await page.getByLabel("你的问题", { exact: true }).fill("网络失败示例")
    await page.getByRole("button", { name: "发送问题" }).click()
    await expect(page.getByRole("alert")).toContainText("无法连接服务")
    await expect(page.getByRole("button", { name: "发送问题" })).toBeEnabled()
  })
})

test("protects the chat route when logged out", async ({ page }) => {
  await page.goto("/chat")
  await expect(page).toHaveURL(/\/login$/)
})
