import { expect, test } from "@playwright/test"
import { firstSuperuser, firstSuperuserPassword } from "./config.ts"
import { createUser } from "./utils/privateApi.ts"
import { randomEmail, randomPassword } from "./utils/random"
import { logInUser, logOutUser } from "./utils/user"

const sections = ["账号资料", "修改密码"]

test("Account profile is visible by default", async ({ page }) => {
  await page.goto("/settings")
  await expect(page.getByRole("heading", { name: "个人中心" })).toBeVisible()
  await expect(page.getByLabel("显示名", { exact: true })).toBeVisible()
})

test("All account sections are visible", async ({ page }) => {
  await page.goto("/settings")
  for (const section of sections) {
    await expect(page.getByRole("heading", { name: section })).toBeVisible()
  }
})

test.describe("Edit user profile", () => {
  test.use({ storageState: { cookies: [], origins: [] } })
  let email: string
  let password: string

  test.beforeAll(async () => {
    email = randomEmail()
    password = randomPassword()
    await createUser({ email, password })
  })

  test.beforeEach(async ({ page }) => {
    await logInUser(page, email, password)
    await page.goto("/settings")
  })

  test("Edit user name with a valid name", async ({ page }) => {
    const updatedName = "Test User 2"

    await page.getByLabel("显示名", { exact: true }).fill(updatedName)
    await page.getByRole("button", { name: "保存资料" }).click()

    await expect(page.getByText("资料已更新。")).toBeVisible()
    await expect(page.getByLabel("显示名", { exact: true })).toHaveValue(
      updatedName,
    )
    await expect(
      page.locator(".da-profile-grid").first().getByText(updatedName),
    ).toBeVisible()
  })

  test("Edit user email with an invalid email shows error", async ({
    page,
  }) => {
    const input = page.getByLabel("邮箱", { exact: true })
    await input.fill("")
    await page.getByRole("button", { name: "保存资料" }).click()

    expect(
      await input.evaluate((element) =>
        (element as HTMLInputElement).checkValidity(),
      ),
    ).toBe(false)
  })
})

test.describe("Edit user email", () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test("Edit user email with a valid email", async ({ page }) => {
    const email = randomEmail()
    const password = randomPassword()
    const updatedEmail = randomEmail()

    await createUser({ email, password })
    await logInUser(page, email, password)
    await page.goto("/settings")

    await page.getByLabel("邮箱", { exact: true }).fill(updatedEmail)
    await page.getByRole("button", { name: "保存资料" }).click()

    await expect(page.getByText("资料已更新。")).toBeVisible()
    await expect(page.getByLabel("邮箱", { exact: true })).toHaveValue(
      updatedEmail,
    )
    await expect(
      page
        .locator(".da-profile-grid")
        .first()
        .getByText(updatedEmail, { exact: true }),
    ).toBeVisible()
  })
})

test.describe("Unsaved edit actions", () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test("Reload restores original name after unsaved changes", async ({
    page,
  }) => {
    const email = randomEmail()
    const password = randomPassword()
    const user = await createUser({ email, password })

    await logInUser(page, email, password)
    await page.goto("/settings")
    await page.getByLabel("显示名", { exact: true }).fill("Test User 2")
    await page.reload()

    await expect(page.getByLabel("显示名", { exact: true })).toHaveValue(
      user.full_name as string,
    )
  })

  test("Reload restores original email after unsaved changes", async ({
    page,
  }) => {
    const email = randomEmail()
    const password = randomPassword()
    await createUser({ email, password })

    await logInUser(page, email, password)
    await page.goto("/settings")
    await page.getByLabel("邮箱", { exact: true }).fill(randomEmail())
    await page.reload()

    await expect(page.getByLabel("邮箱", { exact: true })).toHaveValue(email)
  })
})

test.describe("Change password", () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test("Update password successfully", async ({ page }) => {
    const email = randomEmail()
    const password = randomPassword()
    const newPassword = randomPassword()

    await createUser({ email, password })
    await logInUser(page, email, password)

    await page.goto("/settings")
    await page.getByLabel("当前密码", { exact: true }).fill(password)
    await page.getByLabel("新密码", { exact: true }).fill(newPassword)
    await page.getByLabel("确认新密码", { exact: true }).fill(newPassword)
    await page.getByRole("button", { name: "更新密码" }).click()

    await expect(page.getByText("密码已更新。")).toBeVisible()

    await logOutUser(page)
    await logInUser(page, email, newPassword)
  })
})

test.describe("Change password validation", () => {
  test.use({ storageState: { cookies: [], origins: [] } })
  let email: string
  let password: string

  test.beforeAll(async () => {
    email = randomEmail()
    password = randomPassword()
    await createUser({ email, password })
  })

  test.beforeEach(async ({ page }) => {
    await logInUser(page, email, password)
    await page.goto("/settings")
  })

  test("Update password with weak passwords", async ({ page }) => {
    const weakPassword = "weak"
    const input = page.getByLabel("新密码", { exact: true })

    await page.getByLabel("当前密码", { exact: true }).fill(password)
    await input.fill(weakPassword)
    await page.getByLabel("确认新密码", { exact: true }).fill(weakPassword)
    await page.getByRole("button", { name: "更新密码" }).click()

    expect(
      await input.evaluate((element) =>
        (element as HTMLInputElement).checkValidity(),
      ),
    ).toBe(false)
  })

  test("New password and confirmation password do not match", async ({
    page,
  }) => {
    await page.getByLabel("当前密码", { exact: true }).fill(password)
    await page.getByLabel("新密码", { exact: true }).fill(randomPassword())
    await page.getByLabel("确认新密码", { exact: true }).fill(randomPassword())
    await page.getByRole("button", { name: "更新密码" }).click()

    await expect(page.getByText("两次输入的新密码不一致。")).toBeVisible()
  })

  test("Current password and new password are the same", async ({ page }) => {
    await page.getByLabel("当前密码", { exact: true }).fill(password)
    await page.getByLabel("新密码", { exact: true }).fill(password)
    await page.getByLabel("确认新密码", { exact: true }).fill(password)
    await page.getByRole("button", { name: "更新密码" }).click()

    await expect(page.getByText("新密码不能和当前密码相同。")).toBeVisible()
  })
})

test("Appearance button is visible in sidebar", async ({ page }) => {
  await page.goto("/settings")
  await expect(
    page.getByRole("button", { name: /切换为(柔和|清爽)浅色/ }),
  ).toBeVisible()
})

test("User can switch between appearance modes", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.removeItem("devassist-appearance")
  })
  await page.goto("/settings")

  await page.getByRole("button", { name: "切换为柔和浅色" }).click()
  await expect(page.locator(".da-shell")).toHaveClass(/da-soft/)
  await expect(
    page.getByRole("button", { name: "切换为清爽浅色" }),
  ).toHaveAttribute("aria-pressed", "true")

  await page.getByRole("button", { name: "切换为清爽浅色" }).click()
  await expect(page.locator(".da-shell")).not.toHaveClass(/da-soft/)
  await expect(
    page.getByRole("button", { name: "切换为柔和浅色" }),
  ).toHaveAttribute("aria-pressed", "false")
})

test("Selected appearance is preserved across sessions", async ({ page }) => {
  await page.goto("/settings")
  await page.evaluate(() => localStorage.removeItem("devassist-appearance"))
  await page.reload()

  await page.getByRole("button", { name: "切换为柔和浅色" }).click()
  await expect(page.locator(".da-shell")).toHaveClass(/da-soft/)
  expect(
    await page.evaluate(() => localStorage.getItem("devassist-appearance")),
  ).toBe("soft")

  await logOutUser(page)
  await logInUser(page, firstSuperuser, firstSuperuserPassword)

  // 登录落在工作台（没有 .da-shell 侧栏），回到设置页再断言外观沿用。
  await page.goto("/settings")
  await expect(page.locator(".da-shell")).toHaveClass(/da-soft/)
})
