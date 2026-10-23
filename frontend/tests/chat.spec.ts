import { expect, type Page, test } from "@playwright/test"

async function mockCodeWorkspace(page: Page) {
  let content = 'print("original")'
  const workspace = () => ({
    files: [{ path: "main.py", size_bytes: content.length }],
    total_bytes: content.length,
  })
  await page.route("**/api/v1/code/config", (route) =>
    route.fulfill({
      json: {
        api_format: "openai_chat_completions",
        model: "ui-test",
        configured: true,
      },
    }),
  )
  await page.route("**/api/v1/code/workspace", (route) =>
    route.fulfill({ json: workspace() }),
  )
  await page.route("**/api/v1/code/workspace/files/main.py", (route) =>
    route.fulfill({ json: { path: "main.py", content } }),
  )
  await page.route("**/api/v1/code/workspace/files", (route) => {
    content = 'print("uploaded")'
    return route.fulfill({ json: workspace() })
  })
  await page.route("**/api/v1/code/generate", (route) =>
    route.fulfill({
      json: {
        plan: ["更新 main.py"],
        summary: "更新输出",
        edits: [{ path: "main.py", content: 'print("applied")' }],
        request_id: "ui-code-edit",
      },
    }),
  )
  await page.route("**/api/v1/code/workspace/apply", (route) => {
    content = route.request().postDataJSON().edits[0].content
    return route.fulfill({ json: workspace() })
  })
}

async function mockSecondAccountLogin(page: Page) {
  await page.route("**/api/v1/users/me", (route) => {
    const secondAccount =
      route.request().headers().authorization === "Bearer account-b-token"
    return route.fulfill({
      json: {
        id: secondAccount
          ? "44444444-4444-4444-4444-444444444444"
          : "11111111-1111-1111-1111-111111111111",
        email: secondAccount ? "b@example.com" : "a@example.com",
        full_name: secondAccount ? "账号 B" : "账号 A",
        is_active: true,
        is_superuser: false,
      },
    })
  })
  await page.route("**/api/v1/login/access-token", (route) =>
    route.fulfill({
      json: { access_token: "account-b-token", token_type: "bearer" },
    }),
  )
}

async function loginAsSecondAccount(page: Page) {
  await expect(page).toHaveURL(/\/login$/)
  await page.getByTestId("email-input").fill("b@example.com")
  await page.getByPlaceholder("Password", { exact: true }).fill("test-password")
  await page.getByRole("button", { name: "Log In", exact: true }).click()
  await expect(page).toHaveURL("/")
  await expect(page.getByText("Hi, 账号 B", { exact: false })).toBeVisible()
}

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
      // Seed only the initial page; a real logout redirect must not restore auth.
      if (sessionStorage.getItem("chat-ui-test-initialized")) return
      localStorage.setItem("access_token", "chat-ui-test-token")
      sessionStorage.setItem("chat-ui-test-initialized", "true")
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
      detail.answer,
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

  test("refreshes an open file preview after uploading replacement content", async ({
    page,
  }) => {
    await mockCodeWorkspace(page)
    await page.goto("/chat")
    await page.getByRole("button", { name: "编程智能体", exact: true }).click()
    const preview = page.getByRole("region", { name: "main.py 文件预览" })
    await expect(preview).toContainText('print("original")')
    await page.locator('input[type="file"][multiple]').setInputFiles({
      name: "main.py",
      mimeType: "text/x-python",
      buffer: Buffer.from('print("uploaded")'),
    })
    await expect(preview).toContainText('print("uploaded")')
    await expect(preview).not.toContainText('print("original")')
  })

  test("refreshes an open file preview after applying reviewed edits", async ({
    page,
  }) => {
    await mockCodeWorkspace(page)
    await page.goto("/chat")
    await page.getByRole("button", { name: "编程智能体", exact: true }).click()
    const preview = page.getByRole("region", { name: "main.py 文件预览" })
    await expect(preview).toContainText('print("original")')
    await page.getByRole("radio", { name: /工作区修改/ }).check()
    await page.getByLabel("当前任务", { exact: true }).fill("更新输出")
    await page
      .getByRole("button", { name: "生成改动预览", exact: true })
      .click()
    await page.getByRole("article").getByRole("checkbox").check()
    await page
      .getByRole("button", { name: "确认写入隔离工作区", exact: true })
      .click()
    await expect(preview).toContainText('print("applied")')
    await expect(preview).not.toContainText('print("original")')
  })

  test("opens knowledge deep links in RAG despite a saved code mode", async ({
    page,
  }) => {
    const documentId = "22222222-2222-2222-2222-222222222222"
    await page.addInitScript(() => {
      localStorage.setItem(
        "devassist-chat-mode:11111111-1111-1111-1111-111111111111",
        "code",
      )
    })
    await page.route("**/api/v1/documents", (route) =>
      route.fulfill({
        json: {
          data: [
            {
              id: documentId,
              filename: "guide.md",
              size_bytes: 30,
              chunk_count: 1,
              created_at: "2026-10-23T00:00:00Z",
            },
          ],
          count: 1,
        },
      }),
    )
    await page.goto(
      `/chat?question=${encodeURIComponent("如何设置超时")}&document=${documentId}&method=hybrid`,
    )
    await expect(page.getByLabel("我的账号菜单")).toContainText("UI 测试用户")
    await expect(
      page.getByRole("button", { name: "RAG 知识问答", exact: true }),
    ).toHaveAttribute("aria-pressed", "true")
    await expect(page.getByLabel("你的问题", { exact: true })).toHaveValue(
      "如何设置超时",
    )
    await expect(page.getByLabel("检索范围", { exact: true })).toHaveValue(
      documentId,
    )
    await expect(page.getByLabel("检索方式", { exact: true })).toHaveValue(
      "hybrid",
    )
    await page.getByRole("button", { name: "编程智能体", exact: true }).click()
    await expect(
      page.getByRole("main", { name: "编程智能体工作区" }),
    ).toBeVisible()
  })

  test("switches accounts without reusing identity or late document responses", async ({
    page,
  }) => {
    let releaseOldDocuments = () => {}
    const oldDocumentsReady = new Promise<void>((resolve) => {
      releaseOldDocuments = resolve
    })
    let releaseNewIdentity = () => {}
    const newIdentityReady = new Promise<void>((resolve) => {
      releaseNewIdentity = resolve
    })
    let oldDocumentRequests = 0
    let newIdentityRequests = 0
    await page.route("**/api/v1/documents", async (route) => {
      const oldAccount =
        route.request().headers().authorization === "Bearer chat-ui-test-token"
      if (oldAccount) {
        oldDocumentRequests += 1
        if (oldDocumentRequests > 1) await oldDocumentsReady
      }
      await route.fulfill({
        json: {
          data: [
            {
              id: oldAccount
                ? "22222222-2222-2222-2222-222222222222"
                : "33333333-3333-3333-3333-333333333333",
              filename: oldAccount ? "A-private.md" : "B-private.md",
              size_bytes: 30,
              chunk_count: 1,
              created_at: "2026-10-23T00:00:00Z",
            },
          ],
          count: 1,
        },
      })
    })
    await page.route("**/api/v1/users/me", async (route) => {
      const newAccount =
        route.request().headers().authorization === "Bearer account-b-token"
      if (newAccount) {
        newIdentityRequests += 1
        await newIdentityReady
      }
      await route.fulfill({
        json: {
          id: newAccount
            ? "44444444-4444-4444-4444-444444444444"
            : "11111111-1111-1111-1111-111111111111",
          email: newAccount ? "b@example.com" : "a@example.com",
          full_name: newAccount ? "账号 B" : "账号 A",
          is_active: true,
          is_superuser: false,
        },
      })
    })
    await page.route("**/api/v1/login/access-token", (route) =>
      route.fulfill({
        json: { access_token: "account-b-token", token_type: "bearer" },
      }),
    )
    await page.goto("/chat")
    await expect(page.getByLabel("我的账号菜单")).toContainText("账号 A")
    await expect.poll(() => oldDocumentRequests).toBe(1)
    await page.getByRole("link", { name: "我的知识库", exact: true }).click()
    await expect(
      page
        .getByRole("region", { name: "已导入文档" })
        .getByText("A-private.md", { exact: true }),
    ).toBeVisible()
    await expect.poll(() => oldDocumentRequests).toBe(2)
    await page.getByLabel("我的账号菜单").click()
    await page.getByRole("button", { name: "退出登录", exact: true }).click()
    await expect(page).toHaveURL(/\/login$/)
    await page.getByTestId("email-input").fill("b@example.com")
    await page
      .getByPlaceholder("Password", { exact: true })
      .fill("test-password")
    await page.getByRole("button", { name: "Log In", exact: true }).click()
    await expect.poll(() => newIdentityRequests).toBe(1)
    await expect(page).toHaveURL(/\/login$/)
    await expect(page.getByText("账号 A", { exact: true })).toHaveCount(0)
    releaseOldDocuments()
    releaseNewIdentity()
    await expect(page).toHaveURL("/")
    await expect(page.getByText("Hi, 账号 B", { exact: false })).toBeVisible()
    await expect(page.getByText("账号 A", { exact: false })).toHaveCount(0)
    await page.getByRole("link", { name: "文档知识库", exact: true }).click()
    await expect(
      page
        .getByRole("region", { name: "已导入文档" })
        .getByText("B-private.md", { exact: true }),
    ).toBeVisible()
    await expect(page.getByText("A-private.md", { exact: true })).toHaveCount(0)
  })

  test("keeps the new account signed in when an old mutation returns unauthorized", async ({
    page,
  }) => {
    await mockSecondAccountLogin(page)
    let releaseOldRequest = () => {}
    const oldRequestReady = new Promise<void>((resolve) => {
      releaseOldRequest = resolve
    })
    let oldRequests = 0
    await page.route("**/api/v1/chat", async (route) => {
      expect(route.request().headers().authorization).toBe(
        "Bearer chat-ui-test-token",
      )
      oldRequests += 1
      await oldRequestReady
      await route.fulfill({
        status: 401,
        json: { detail: "Old session expired" },
      })
    })
    await page.goto("/chat")
    await page
      .getByLabel("你的问题", { exact: true })
      .fill("旧账号未完成的问题")
    await page.getByRole("button", { name: "发送问题", exact: true }).click()
    await expect.poll(() => oldRequests).toBe(1)
    await page.getByLabel("我的账号菜单").click()
    await page.getByRole("button", { name: "退出登录", exact: true }).click()
    await loginAsSecondAccount(page)
    const oldResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/v1/chat") && response.status() === 401,
    )
    releaseOldRequest()
    await (await oldResponse).finished()
    await page.evaluate(
      () =>
        new Promise<void>((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
        ),
    )
    await expect(page).toHaveURL("/")
    expect(
      await page.evaluate(() => localStorage.getItem("access_token")),
    ).toBe("account-b-token")
    await expect(page.getByText("Hi, 账号 B", { exact: false })).toBeVisible()
  })

  test("ignores a logout callback from an old account deletion", async ({
    page,
  }) => {
    await mockSecondAccountLogin(page)
    let releaseDeletion = () => {}
    const deletionReady = new Promise<void>((resolve) => {
      releaseDeletion = resolve
    })
    let deletions = 0
    await page.route("**/api/v1/users/me", async (route) => {
      if (route.request().method() !== "DELETE") return route.fallback()
      expect(route.request().headers().authorization).toBe(
        "Bearer chat-ui-test-token",
      )
      deletions += 1
      await deletionReady
      await route.fulfill({ json: { message: "User deleted successfully" } })
    })
    await page.goto("/settings")
    await page.getByRole("tab", { name: "Danger zone", exact: true }).click()
    await page
      .getByRole("button", { name: "Delete Account", exact: true })
      .click()
    await page.getByRole("button", { name: "Delete", exact: true }).click()
    await expect.poll(() => deletions).toBe(1)
    await page.keyboard.press("Escape")
    await page.getByTestId("user-menu").click()
    await page.getByRole("menuitem", { name: "Log Out", exact: true }).click()
    await loginAsSecondAccount(page)
    releaseDeletion()
    await expect(
      page.getByText("Your account has been successfully deleted", {
        exact: true,
      }),
    ).toBeVisible()
    await expect(page).toHaveURL("/")
    expect(
      await page.evaluate(() => localStorage.getItem("access_token")),
    ).toBe("account-b-token")
    await expect(page.getByText("Hi, 账号 B", { exact: false })).toBeVisible()
  })

  test("logs out when the current session receives an unauthorized response", async ({
    page,
  }) => {
    await page.route("**/api/v1/chat", (route) =>
      route.fulfill({ status: 401, json: { detail: "Session expired" } }),
    )
    await page.goto("/chat")
    await page.getByLabel("你的问题", { exact: true }).fill("验证当前会话过期")
    await page.getByRole("button", { name: "发送问题", exact: true }).click()
    await expect(page).toHaveURL(/\/login$/)
    expect(
      await page.evaluate(() => localStorage.getItem("access_token")),
    ).toBeNull()
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
