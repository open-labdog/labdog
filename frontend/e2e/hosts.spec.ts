import { test, expect } from "@playwright/test"

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"

test.describe("Hosts page", () => {
  test("hosts list page loads", async ({ page }) => {
    await page.goto("/hosts")
    await expect(page.getByRole("heading", { name: "Hosts" })).toBeVisible()
    await expect(page.getByRole("link", { name: /Add Host/i })).toBeVisible()
  })

  test("add host link navigates to /hosts/new", async ({ page }) => {
    await page.goto("/hosts")
    await page.getByRole("link", { name: /Add Host/i }).click()
    await expect(page).toHaveURL(/\/hosts\/new/)
  })

  test("new host form renders correctly", async ({ page }) => {
    await page.goto("/hosts/new")
    await expect(page.getByRole("heading", { name: "Add Host" })).toBeVisible()
    await expect(page.locator("#hostname")).toBeVisible()
    await expect(page.locator("#ip_address")).toBeVisible()
    await expect(page.locator("#ssh_port")).toBeVisible()
    await expect(page.locator("#ssh_key")).toBeVisible()
    await expect(page.getByRole("button", { name: "Add Host" })).toBeVisible()
  })

  test("cancel button on new host form returns to hosts list", async ({ page }) => {
    await page.goto("/hosts/new")
    await page.getByRole("button", { name: "Cancel" }).click()
    await expect(page).toHaveURL(/\/hosts\/?$/)
  })

  test("add host with SSH key and group", async ({ request, page }) => {
    // Create prerequisite SSH key
    const keyRes = await request.post(`${API_BASE}/api/ssh-keys`, {
      data: {
        name: `e2e-key-${Date.now()}`,
        private_key: "-----BEGIN OPENSSH PRIVATE KEY-----\nfakekey\n-----END OPENSSH PRIVATE KEY-----",
        is_default: false,
      },
    })
    const sshKey = await keyRes.json()

    // Create prerequisite group
    const groupRes = await request.post(`${API_BASE}/api/groups`, {
      data: { name: `e2e-host-group-${Date.now()}`, description: null, priority: 997 },
    })
    const group = await groupRes.json()

    const hostname = `e2e-host-${Date.now()}`

    await page.goto("/hosts/new")
    await page.locator("#hostname").fill(hostname)
    await page.locator("#ip_address").fill("192.168.1.100")
    await page.locator("#ssh_port").fill("22")

    // Select SSH key from native select
    await page.locator("#ssh_key").selectOption({ value: String(sshKey.id) })

    // GroupMultiSelect uses a custom dropdown — click the trigger to open it
    await page.getByText("Select groups...").click()
    // Now the dropdown is open — check the group by its label text
    await page.getByLabel(group.name).check()
    // Close the dropdown by clicking outside the component (hostname field)
    // so the floating dropdown doesn't intercept the submit button click
    await page.locator("#hostname").click()

    await page.getByRole("button", { name: "Add Host" }).click()

    await page.waitForURL(/\/hosts\/?$/)
    await expect(page).toHaveURL(/\/hosts\/?$/)
    await expect(page.getByText(hostname)).toBeVisible()
  })

  test("host detail page shows host info", async ({ request, page }) => {
    const keyRes = await request.post(`${API_BASE}/api/ssh-keys`, {
      data: {
        name: `e2e-detail-key-${Date.now()}`,
        private_key: "-----BEGIN OPENSSH PRIVATE KEY-----\nfakekey\n-----END OPENSSH PRIVATE KEY-----",
        is_default: false,
      },
    })
    const sshKey = await keyRes.json()

    const hostRes = await request.post(`${API_BASE}/api/hosts`, {
      data: {
        hostname: `e2e-detail-host-${Date.now()}`,
        ip_address: "10.0.0.1",
        ssh_port: 22,
        ssh_key_id: sshKey.id,
        group_ids: [],
      },
    })
    const host = await hostRes.json()

    await page.goto(`/hosts/${host.id}`)
    await expect(page.getByText(host.hostname).first()).toBeVisible()
  })
})
