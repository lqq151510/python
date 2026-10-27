import { expect, type Page, test } from "@playwright/test"

const sessionA = {
  id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  title: "超时处理",
  turn_count: 1,
  created_at: "2026-10-23T00:00:00Z",
  updated_at: "2026-10-23T00:00:00Z",
}
const sessionB = {
  ...sessionA,
  id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
  title: "重试策略",
}
const turnA = {
  id: "turn-a",
  message: "怎么设置超时？",
  answer: "设置有限的等待时间。",
  steps: [],
  stopped: false,
  created_at: sessionA.created_at,
}
const turnB = {
  ...turnA,
  id: "turn-b",
  message: "怎么限制重试？",
  answer: "设置有限的重试次数。",
}

function gate() {
  let release = () => {}
  const promise = new Promise<void>((resolve) => {
    release = resolve
  })
  return { promise, release }
}

async function mockSessions(page: Page) {
  await page.route("**/api/v1/agent/sessions", (route) =>
    route.fulfill({ json: { data: [sessionA, sessionB], count: 2 } }),
  )
}

test.use({ storageState: { cookies: [], origins: [] } })

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("access_token", "agent-ui-test-token")
  })
  await page.route("**/api/v1/users/me", (route) =>
    route.fulfill({
      json: {
        id: "11111111-1111-1111-1111-111111111111",
        email: "agent-ui@example.com",
        full_name: "Agent 测试用户",
        is_active: true,
        is_superuser: false,
      },
    }),
  )
})

test("separates loading sessions from a genuinely empty library", async ({
  page,
}) => {
  const response = gate()
  await page.route("**/api/v1/agent/sessions", async (route) => {
    await response.promise
    await route.fulfill({ json: { data: [], count: 0 } })
  })
  await page.goto("/agent")
  await expect(page.getByRole("status")).toHaveText("正在加载会话…")
  await expect(page.getByText("还没有会话。提一个问题就会开始。")).toHaveCount(
    0,
  )
  response.release()
  await expect(page.getByText("还没有会话。提一个问题就会开始。")).toBeVisible()
  await expect(page.getByRole("status")).toHaveCount(0)
})

test("shows a session list failure and retries without an empty state", async ({
  page,
}) => {
  let attempts = 0
  await page.route("**/api/v1/agent/sessions", (route) => {
    attempts += 1
    return attempts === 1
      ? route.fulfill({ status: 503, json: { detail: "服务暂时不可用" } })
      : route.fulfill({ json: { data: [sessionA], count: 1 } })
  })
  await page.goto("/agent")
  await expect(page.getByRole("alert")).toContainText("会话列表加载失败")
  await expect(page.getByText("还没有会话。提一个问题就会开始。")).toHaveCount(
    0,
  )
  await page.getByRole("button", { name: "重试加载会话" }).click()
  await expect(
    page.getByRole("button", { name: "超时处理 1 轮" }),
  ).toBeVisible()
  await expect(page.getByRole("alert")).toHaveCount(0)
  expect(attempts).toBe(2)
})

test("blocks sending while the selected conversation is loading", async ({
  page,
}) => {
  const response = gate()
  await mockSessions(page)
  await page.route(`**/api/v1/agent/sessions/${sessionA.id}`, async (route) => {
    await response.promise
    await route.fulfill({ json: { session: sessionA, turns: [turnA] } })
  })
  await page.goto("/agent")
  await page.getByRole("button", { name: "超时处理 1 轮" }).click()
  await expect(page.getByRole("status")).toHaveText("正在加载对话…")
  await expect(page.getByText("从下面输入第一轮问题。")).toHaveCount(0)
  await expect(page.getByLabel("继续这一轮会话")).toBeDisabled()
  await expect(
    page.getByRole("button", { name: "发送", exact: true }),
  ).toBeDisabled()
  response.release()
  await expect(page.getByText(turnA.answer)).toBeVisible()
  await expect(page.getByLabel("继续这一轮会话")).toBeEnabled()
})

test("keeps a failed detail unavailable until retry succeeds", async ({
  page,
}) => {
  let attempts = 0
  await mockSessions(page)
  await page.route(`**/api/v1/agent/sessions/${sessionA.id}`, (route) => {
    attempts += 1
    return attempts === 1
      ? route.fulfill({ status: 503, json: { detail: "稍后再试" } })
      : route.fulfill({ json: { session: sessionA, turns: [] } })
  })
  await page.goto("/agent")
  await page.getByRole("button", { name: "超时处理 1 轮" }).click()
  await expect(page.getByRole("alert")).toContainText("对话加载失败")
  await expect(page.getByText("从下面输入第一轮问题。")).toHaveCount(0)
  await expect(page.getByLabel("继续这一轮会话")).toBeDisabled()
  await expect(
    page.getByRole("button", { name: "发送", exact: true }),
  ).toBeDisabled()
  await page.getByRole("button", { name: "重试加载对话" }).click()
  await expect(page.getByText("从下面输入第一轮问题。")).toBeVisible()
  await expect(page.getByLabel("继续这一轮会话")).toBeEnabled()
  await expect(page.getByRole("alert")).toHaveCount(0)
  expect(attempts).toBe(2)
})

test("cancels old detail on a quick switch and keeps the current conversation", async ({
  page,
}) => {
  const response = gate()
  const oldRequest = gate()
  const oldResponse = gate()
  await mockSessions(page)
  await page.route(`**/api/v1/agent/sessions/${sessionA.id}`, async (route) => {
    oldRequest.release()
    await response.promise
    await route.fulfill({ json: { session: sessionA, turns: [turnA] } })
    oldResponse.release()
  })
  await page.route(`**/api/v1/agent/sessions/${sessionB.id}`, (route) =>
    route.fulfill({ json: { session: sessionB, turns: [turnB] } }),
  )
  await page.goto("/agent")
  await page.getByRole("button", { name: "超时处理 1 轮" }).click()
  await oldRequest.promise
  const cancelled = page.waitForEvent("requestfailed", {
    predicate: (request) => request.url().endsWith(sessionA.id),
  })
  await page.getByRole("button", { name: "重试策略 1 轮" }).click()
  await cancelled
  await expect(page.getByText(turnB.answer)).toBeVisible()
  response.release()
  await oldResponse.promise
  await expect(page.getByText(turnA.answer)).toHaveCount(0)
  await expect(
    page.getByRole("button", { name: "重试策略 1 轮" }),
  ).toHaveAttribute("aria-current", "true")
})

test("locks session switching and creation until a send completes", async ({
  page,
}) => {
  const response = gate()
  const sent = gate()
  let sentMessage = false
  await mockSessions(page)
  await page.route(`**/api/v1/agent/sessions/${sessionA.id}`, (route) =>
    route.fulfill({
      json: {
        session: sessionA,
        turns: sentMessage ? [turnA, turnB] : [turnA],
      },
    }),
  )
  await page.route("**/api/v1/agent/turns", async (route) => {
    expect(route.request().postDataJSON()).toEqual({
      message: "怎么限制重试？",
      session_id: sessionA.id,
    })
    sent.release()
    await response.promise
    sentMessage = true
    await route.fulfill({
      json: { session: sessionA, turn: turnB, request_id: "agent-ui-send" },
    })
  })
  await page.goto("/agent")
  await page.getByRole("button", { name: "超时处理 1 轮" }).click()
  await page.getByLabel("继续这一轮会话").fill("怎么限制重试？")
  await page.getByRole("button", { name: "发送", exact: true }).click()
  await sent.promise
  await expect(page.getByRole("button", { name: "新会话" })).toBeDisabled()
  await expect(
    page.getByRole("button", { name: "超时处理 1 轮" }),
  ).toBeDisabled()
  await expect(
    page.getByRole("button", { name: "重试策略 1 轮" }),
  ).toBeDisabled()
  await expect(page.getByRole("button", { name: "正在思考…" })).toBeDisabled()
  response.release()
  await expect(page.getByText(turnB.answer)).toBeVisible()
  await expect(page.getByRole("button", { name: "新会话" })).toBeEnabled()
  await expect(
    page.getByRole("button", { name: "重试策略 1 轮" }),
  ).toBeEnabled()
  await expect(page.getByLabel("继续这一轮会话")).toBeEmpty()
  await expect(
    page.getByRole("button", { name: "超时处理 1 轮" }),
  ).toHaveAttribute("aria-current", "true")
})

test("cancels pending session queries when leaving the Agent page", async ({
  page,
}) => {
  const response = gate()
  const requested = gate()
  const completed = gate()
  await page.route("**/api/v1/agent/sessions", async (route) => {
    requested.release()
    await response.promise
    await route.fulfill({ json: { data: [], count: 0 } })
    completed.release()
  })
  await page.route("**/api/v1/documents", (route) =>
    route.fulfill({ json: { data: [], count: 0 } }),
  )
  await page.goto("/agent")
  await requested.promise
  const cancelled = page.waitForEvent("requestfailed", {
    predicate: (request) => request.url().endsWith("/agent/sessions"),
  })
  await page.getByRole("link", { name: "我的知识库", exact: true }).click()
  await expect(page).toHaveURL(/\/knowledge$/)
  await cancelled
  response.release()
  await completed.promise
})
