import { test, expect } from "@playwright/test"

test.describe("Git repo onboarding wizard", () => {
  test("renders /git-repos/new with step 1 active", async ({ page }) => {
    await page.goto("/git-repos/new")
    await expect(page.getByRole("heading", { name: "Connect a git repository" })).toBeVisible()

    const connectStep = page.locator('[data-step="auth"]')
    await expect(connectStep).toHaveAttribute("data-active", "true")

    const scanStep = page.locator('[data-step="scanning"]')
    await expect(scanStep).toHaveAttribute("data-active", "false")

    const reviewStep = page.locator('[data-step="review"]')
    await expect(reviewStep).toHaveAttribute("data-active", "false")
  })

  test("step 1 renders the auth form", async ({ page }) => {
    await page.goto("/git-repos/new")
    await expect(page.locator("#repo-name")).toBeVisible()
    await expect(page.locator("#repo-url")).toBeVisible()
    await expect(page.locator("#repo-branch")).toBeVisible()
    await expect(page.getByRole("button", { name: "Connect & scan" })).toBeVisible()
  })

  test("Add Repository on the list page links to the wizard", async ({ page }) => {
    await page.goto("/git-repos")
    await page.getByRole("link", { name: "Add Repository" }).first().click()
    await expect(page).toHaveURL(/\/git-repos\/new$/)
  })
})
