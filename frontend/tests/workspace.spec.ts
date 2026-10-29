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

test.beforeEach(async ({ page }) => {
  chatBody = undefined
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
  })
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
