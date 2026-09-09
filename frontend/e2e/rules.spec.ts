import { test, expect } from "./fixtures"

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"

test.describe("Rules page", () => {
  test.describe.configure({ mode: "serial" })
  let groupId: number

  test.beforeAll(async ({ request }) => {
    const res = await request.post(`${API_BASE}/api/groups`, {
      data: { name: `e2e-rules-group-${Date.now()}`, description: "Rules test group", priority: 996 },
    })
    const group = await res.json()
    groupId = group.id
  })

  test("rules page loads for a group", async ({ page }) => {
    await page.goto(`/groups/${groupId}/rules`)
    await expect(page.getByRole("heading", { name: "Firewall Rules" })).toBeVisible()
    await expect(page.getByRole("button", { name: "Add Rule" })).toBeVisible()
  })

  test("clicking Add Rule opens dialog", async ({ page }) => {
    await page.goto(`/groups/${groupId}/rules`)
    await page.getByRole("button", { name: "Add Rule" }).click()

    await expect(page.getByRole("dialog")).toBeVisible()
    await expect(page.getByRole("heading", { name: "Add Rule" })).toBeVisible()
    await expect(page.locator("#action")).toBeVisible()
    await expect(page.locator("#protocol")).toBeVisible()
    await expect(page.locator("#direction")).toBeVisible()
  })

  test("create a TCP allow rule via dialog", async ({ page }) => {
    await page.goto(`/groups/${groupId}/rules`)
    await page.getByRole("button", { name: "Add Rule" }).click()

    const dialog = page.getByRole("dialog")
    await expect(dialog).toBeVisible()

    await dialog.locator("#action").selectOption("allow")
    await dialog.locator("#protocol").selectOption("tcp")
    await dialog.locator("#direction").selectOption("input")
    // Source CIDR field has no id — locate by placeholder inside dialog
    await dialog.getByPlaceholder("0.0.0.0/0").first().fill("0.0.0.0/0")
    await dialog.locator("#port_start").fill("80")
    await dialog.locator("#port_end").fill("80")
    await dialog.locator("#comment").fill("Allow HTTP")

    await dialog.getByRole("button", { name: "Add Rule" }).click()

    await expect(dialog).not.toBeVisible()
    await expect(page.getByText("Allow HTTP")).toBeVisible()
  })

  test("create a deny rule and see it in table", async ({ page }) => {
    await page.goto(`/groups/${groupId}/rules`)
    await page.getByRole("button", { name: "Add Rule" }).click()

    const dialog = page.getByRole("dialog")
    await expect(dialog).toBeVisible()

    await dialog.locator("#action").selectOption("deny")
    await dialog.locator("#protocol").selectOption("tcp")
    await dialog.locator("#direction").selectOption("input")
    // Fill port fields to avoid NaN validation errors from empty number inputs
    await dialog.locator("#port_start").fill("443")
    await dialog.locator("#port_end").fill("443")
    await dialog.locator("#comment").fill("E2E deny rule")

    await dialog.getByRole("button", { name: "Add Rule" }).click()

    await expect(dialog).not.toBeVisible()
    await expect(page.getByText("E2E deny rule")).toBeVisible()
  })

  test("cancel button closes dialog without saving", async ({ page }) => {
    await page.goto(`/groups/${groupId}/rules`)
    await page.getByRole("button", { name: "Add Rule" }).click()

    const dialog = page.getByRole("dialog")
    await expect(dialog).toBeVisible()
    await dialog.locator("#comment").fill("Should not be saved")
    await dialog.getByRole("button", { name: "Cancel" }).click()

    await expect(dialog).not.toBeVisible()
    await expect(page.getByText("Should not be saved")).not.toBeVisible()
  })

  // REMOVED: "system rules have disabled Edit and Delete buttons".
  //
  // The test needed a firewall_rules row with is_system = TRUE, and it got
  // one by shelling out to `docker exec ... psql`, which only worked on the
  // author's machine. There is no supported way to create that row: RuleCreate
  // does not accept is_system (deliberately — a client must not be able to
  // mint a rule the API then refuses to let it edit or delete), and no backend
  // code path writes the column either. The only is_system rules LabDog builds
  // are synthesised at merge time in app/rules/merge.py and never persisted.
  //
  // So the disabled-button state this asserted is unreachable through any
  // interface a test can use. Rather than keep a spec that can only pass next
  // to a hand-edited database, it is gone and the gap is recorded in TODO.md.

  test("edit an existing rule", async ({ request, page }) => {
    const ruleRes = await request.post(`${API_BASE}/api/groups/${groupId}/rules`, {
      data: {
        action: "allow",
        protocol: "tcp",
        direction: "input",
        source_cidr: null,
        destination_cidr: null,
        port_start: 443,
        port_end: 443,
        comment: "original-comment",
        is_system: false,
      },
    })
    await ruleRes.json()

    await page.goto(`/groups/${groupId}/rules`)

    // SortableRow renders <tr> with role="button" via dnd-kit — use tr locator
    const ruleRow = page.locator("tr").filter({ hasText: "original-comment" })
    await ruleRow.locator("button", { hasText: "Edit" }).click()

    await expect(page.getByRole("dialog")).toBeVisible()
    await expect(page.getByRole("heading", { name: "Edit Rule" })).toBeVisible()

    await page.locator("#comment").fill("updated-comment")
    await page.getByRole("button", { name: "Save Changes" }).click()

    await expect(page.getByRole("dialog")).not.toBeVisible()
    await expect(page.getByText("updated-comment")).toBeVisible()
  })
})
