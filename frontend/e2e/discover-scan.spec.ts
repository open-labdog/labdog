import { test, expect } from "./fixtures"
import type { Page } from "@playwright/test"

// Scan now leaves out the addresses that are already hosts. BUG-111: a
// range whose only SSH host had just been added by a scan schedule came
// back "No new SSH hosts found" with no hint that anything was left out.
// The scan is mocked so no worker or network is needed.

const SKIPPED = [
  { ip: "10.10.10.164", hostname: "tester" },
  { ip: "10.10.10.200", hostname: null },
]

async function scan(page: Page, hostsFound: { ip: string; hostname: string | null; ssh_status: string }[]) {
  await page.route("**/api/discovery/scan", (r) =>
    r.request().method() === "POST" ? r.fulfill({ json: { job_id: "e2e-job", status: "pending", progress: 0, total: 252, hosts_found: [] } }) : r.fallback(),
  )
  await page.route("**/api/discovery/scan/e2e-job", (r) =>
    r.fulfill({ json: { job_id: "e2e-job", status: "done", progress: 252, total: 252, hosts_found: hostsFound, skipped_known: SKIPPED } }),
  )
  await page.goto("/discovery?tab=scan")
  await page.getByLabel("network cidr").fill("10.10.10.0/24")
  await page.getByRole("button", { name: "Scan network" }).click()
}

test.describe("Discover scan", () => {
  test("an empty result names the hosts it left out", async ({ page }) => {
    await scan(page, [])
    await expect(
      page.getByText(
        "No new SSH hosts found on this network. 2 addresses are already in LabDog and were not scanned: tester (10.10.10.164), 10.10.10.200.",
      ),
    ).toBeVisible()
  })

  test("a result with new hosts counts the ones left out", async ({ page }) => {
    await scan(page, [{ ip: "10.10.10.50", hostname: null, ssh_status: "open" }])
    await expect(page.getByText("10.10.10.50")).toBeVisible()
    await expect(page.getByText("2 already in LabDog, not scanned")).toBeVisible()
  })
})
