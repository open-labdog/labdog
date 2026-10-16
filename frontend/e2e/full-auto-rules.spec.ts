import { test, expect, API_BASE } from "./fixtures"
import type { APIRequestContext } from "@playwright/test"

// Which alerts may change a host without asking: the labdog_ai_automate label
// by default, or rules naming alerts, labels, hosts and groups. The host
// page shows the host's side; the settings page refuses a rule naming a
// host LabDog does not have, and flags one whose host has since gone.

const RULES = "ai.alert_full_auto_rules"

async function set(request: APIRequestContext, key: string, value: string) {
  const res = await request.patch(`${API_BASE}/api/settings/${key}`, { data: { value } })
  expect(res.status(), await res.text()).toBe(200)
}

async function newHost(request: APIRequestContext, name: string) {
  const res = await request.post(`${API_BASE}/api/hosts`, {
    data: { hostname: name, ip_address: "10.0.0.1", ssh_port: 22, group_ids: [] },
  })
  expect(res.status(), await res.text()).toBeLessThan(300)
  return (await res.json()) as { id: number; hostname: string }
}

test.describe("Full-auto rules", () => {
  test.beforeEach(async ({ request }) => {
    await set(request, "ai.enabled", "1")
  })
  test.afterEach(async ({ request }) => {
    await set(request, RULES, "labdog_ai_automate=true")
    await set(request, "ai.alert_full_auto_rules_enabled", "0")
    await set(request, "ai.enabled", "0")
  })

  test("the host page shows the label rule by default", async ({ request, page }) => {
    const host = await newHost(request, `e2e-fa-label-${Date.now()}`)
    await page.goto(`/hosts/${host.id}`)
    const panel = page.getByTestId("host-full-auto")
    await expect(panel).toContainText("Alerts Grafana labels like this may change this host without asking")
    await expect(panel).toContainText("labdog_ai_automate=true")
    // Nothing is configured in this backend, so every alert is held back.
    await expect(panel).toContainText("None can right now")
    await expect(panel).toContainText("alert intake is off")
  })

  test("rules name hosts; the host page lists the ones that cover it", async ({ request, page }) => {
    const host = await newHost(request, `e2e-fa-web-${Date.now()}`)
    const other = await newHost(request, `e2e-fa-db-${Date.now()}`)
    await set(request, "ai.alert_full_auto_rules_enabled", "1")
    await set(request, RULES, `NginxDown, host:${host.hostname}\nDiskFull, host:${other.hostname}`)

    await page.goto(`/hosts/${host.id}`)
    const panel = page.getByTestId("host-full-auto")
    await expect(panel).toContainText("Alerts matching these rules may change this host without asking")
    await expect(panel).toContainText(`NginxDown, host:${host.hostname}`)
    await expect(panel).not.toContainText("DiskFull")

    // Deleting the host leaves its rule matching nothing; the settings page says so.
    expect((await request.delete(`${API_BASE}/api/hosts/${host.id}`)).status()).toBeLessThan(300)
    await page.goto("/settings?section=ai")
    await expect(page.getByTestId(`setting-warning-${RULES}`)).toHaveText(
      `no host named '${host.hostname.toLowerCase()}': rules naming it match nothing`,
    )
  })

  test("saving a rule for a host LabDog does not have is refused", async ({ request, page }) => {
    await set(request, "ai.alert_full_auto_rules_enabled", "1")
    await page.goto("/settings?section=ai")
    const box = page.getByTestId(`setting-${RULES}`)
    await box.fill("NginxDown, host:no-such-host")
    await box.locator("xpath=..").getByRole("button", { name: "Save" }).click()
    await expect(page.getByRole("alert").filter({ hasText: "no host named 'no-such-host'" })).toBeVisible()
  })
})
