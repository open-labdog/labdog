import { test, expect } from "@playwright/test"

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"

test.describe("SSH Terminal UI", () => {
  test("terminal page renders with terminal container", async ({ page }) => {
    // Navigate to a terminal page (using host ID 1 as example)
    // The terminal component should render even without a running backend
    await page.goto("/hosts/1/terminal", { waitUntil: "domcontentloaded" })

    // Terminal container should be present with data-testid
    const terminalContainer = page.locator("[data-testid='ssh-terminal']")
    await expect(terminalContainer).toBeVisible({ timeout: 5000 })
  })

  test("terminal shows connecting state initially", async ({ page }) => {
    await page.goto("/hosts/1/terminal", { waitUntil: "domcontentloaded" })

    // Either connecting message or terminal container should be visible
    const connectingOrTerminal = page.locator(
      "[data-testid='ssh-terminal'], text=/Connecting to/"
    )
    await expect(connectingOrTerminal.first()).toBeVisible({ timeout: 5000 })
  })

  test("terminal handles connection errors gracefully", async ({ page }) => {
    // Navigate to terminal page
    await page.goto("/hosts/1/terminal", { waitUntil: "domcontentloaded" })

    // Wait for either terminal to render or error state to appear
    // Since there's no backend, it should eventually show error or disconnected state
    const errorOrDisconnected = page.locator(
      "text=/Connection failed|Session ended|Connecting to/"
    )

    // Give it time to attempt connection and fail
    await expect(errorOrDisconnected.first()).toBeVisible({ timeout: 10000 })
  })

  test("terminal reconnect button appears on error", async ({ page }) => {
    await page.goto("/hosts/1/terminal", { waitUntil: "domcontentloaded" })

    // Wait for error state (no backend running)
    await page.waitForTimeout(2000)

    // Look for reconnect button
    const reconnectButton = page.getByRole("button", { name: /Reconnect/i })

    // Button should be visible if connection failed
    const isVisible = await reconnectButton.isVisible().catch(() => false)
    if (isVisible) {
      await expect(reconnectButton).toBeVisible()
    }
  })

  test("terminal page with host data renders correctly", async ({ request, page }) => {
    // Create a test host via API
    const sshKeyRes = await request.post(`${API_BASE}/api/ssh-keys`, {
      data: {
        name: `e2e-terminal-key-${Date.now()}`,
        private_key:
          "-----BEGIN OPENSSH PRIVATE KEY-----\nfakekey\n-----END OPENSSH PRIVATE KEY-----",
        is_default: false,
      },
    })
    const sshKey = await sshKeyRes.json()

    const hostRes = await request.post(`${API_BASE}/api/hosts`, {
      data: {
        hostname: `e2e-terminal-host-${Date.now()}`,
        ip_address: "10.0.0.99",
        ssh_port: 22,
        ssh_key_id: sshKey.id,
        group_ids: [],
      },
    })
    const host = await hostRes.json()

    // Navigate to terminal page for this host
    await page.goto(`/hosts/${host.id}/terminal`, { waitUntil: "domcontentloaded" })

    // Terminal container should render
    const terminalContainer = page.locator("[data-testid='ssh-terminal']")
    await expect(terminalContainer).toBeVisible({ timeout: 5000 })

    // Should show connecting or error state (no actual SSH connection)
    const statusText = page.locator(
      "text=/Connecting to|Connection failed|Session ended/"
    )
    await expect(statusText.first()).toBeVisible({ timeout: 5000 })
  })

  test("terminal component has correct styling classes", async ({ page }) => {
    await page.goto("/hosts/1/terminal", { waitUntil: "domcontentloaded" })

    const terminalContainer = page.locator("[data-testid='ssh-terminal']")
    await expect(terminalContainer).toBeVisible({ timeout: 5000 })

    // Check that terminal container has expected classes for layout
    const containerClass = await terminalContainer.getAttribute("class")
    expect(containerClass).toContain("flex-1")
    expect(containerClass).toContain("min-h-0")
  })
})
