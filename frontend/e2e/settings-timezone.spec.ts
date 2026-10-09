import { test, expect, API_BASE } from "./fixtures"

// scheduling.timezone was a free-text box: a typo was only caught on save,
// and nothing showed which names exist. It is a searchable list of the
// IANA names the server accepts.

const KEY = "scheduling.timezone"

test.describe("Scheduling timezone", () => {
  test.afterEach(async ({ request }) => {
    await request.patch(`${API_BASE}/api/settings/${KEY}`, { data: { value: "UTC" } })
  })

  test("is picked by searching, and saved", async ({ page }) => {
    await page.goto("/settings?section=fleet")
    const input = page.getByTestId(`setting-${KEY}`)
    await expect(input).toHaveValue("UTC")

    await input.click()
    await input.fill("stockh")
    await page.getByRole("option", { name: "Europe/Stockholm" }).click()
    await expect(input).toHaveValue("Europe/Stockholm")

    const saved = page.waitForResponse((r) => r.url().includes(`/api/settings/${KEY}`) && r.request().method() === "PATCH")
    await page.getByRole("button", { name: "Save" }).click()
    expect((await saved).status()).toBe(200)

    await page.reload()
    await expect(page.getByTestId(`setting-${KEY}`)).toHaveValue("Europe/Stockholm")
  })

  test("narrows the list to what was typed", async ({ page }) => {
    await page.goto("/settings?section=fleet")
    const input = page.getByTestId(`setting-${KEY}`)
    await input.click()
    await input.fill("new york")
    await expect(page.getByRole("listbox").getByRole("option")).toHaveText(["America/New_York"])
  })
})
