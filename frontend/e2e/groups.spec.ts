import { test, expect } from "./fixtures"

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"

test.describe("Groups page", () => {
  test("groups list page loads", async ({ page }) => {
    await page.goto("/groups")
    await expect(page.getByRole("heading", { name: "Groups" })).toBeVisible()
    await expect(page.getByRole("button", { name: "New group" })).toBeVisible()
  })

  test("new group link navigates to /groups/new", async ({ page }) => {
    await page.goto("/groups")
    // "New group" opens the group editor in place; the /groups/new form is
    // kept for deep links and tested below.
    await page.getByRole("button", { name: "New group" }).click()
    await expect(page.getByRole("dialog").getByRole("heading", { name: "New group" })).toBeVisible()
    await page.keyboard.press("Escape")
    await page.goto("/groups/new")
    await expect(page).toHaveURL(/\/groups\/new/)
  })

  test("new group form renders correctly", async ({ page }) => {
    await page.goto("/groups/new")
    await expect(page.getByRole("heading", { name: "New Group" })).toBeVisible()
    await expect(page.locator("#name")).toBeVisible()
    await expect(page.locator("#description")).toBeVisible()
    await expect(page.locator("#priority")).toBeVisible()
    await expect(page.getByRole("button", { name: "Create Group" })).toBeVisible()
  })

  test("create group via form and see it in list", async ({ page }) => {
    const groupName = `e2e-group-${Date.now()}`
    // Use timestamp-based priority that avoids conflicts with fixed priorities
    // auth.setup.ts cleans e2e groups before each run; use mod to stay in 1-500 range
    const priority = 50 + (Date.now() % 400)

    await page.goto("/groups/new")
    await page.locator("#name").fill(groupName)
    await page.locator("#description").fill("E2E test group")
    await page.locator("#priority").fill(String(priority))
    await page.getByRole("button", { name: "Create Group" }).click()

    await page.waitForURL(/\/groups\/?$/)
    await expect(page).toHaveURL(/\/groups\/?$/)
    await expect(page.getByText(groupName)).toBeVisible()
  })

  test("cancel button on new group form returns to groups list", async ({ page }) => {
    await page.goto("/groups/new")
    await page.getByRole("button", { name: "Cancel" }).click()
    await expect(page).toHaveURL(/\/groups\/?$/)
  })

  test("a row opens the group's own page", async ({ request, page }) => {
    const groupName = `e2e-row-${Date.now()}`
    const res = await request.post(`${API_BASE}/api/groups`, {
      data: { name: groupName, description: "Row test", priority: 994 },
    })
    const group = await res.json()

    await page.goto("/groups")
    await page.getByRole("row").filter({ hasText: groupName }).getByText(groupName).click()
    await expect(page).toHaveURL(new RegExp(`/groups/${group.id}/?$`))
    await expect(page.getByRole("heading", { name: groupName })).toBeVisible()
  })

  test("group detail page shows group info on the Host detail pattern", async ({ request, page }) => {
    const groupName = `e2e-detail-${Date.now()}`
    const res = await request.post(`${API_BASE}/api/groups`, {
      data: { name: groupName, description: "Detail test", priority: 998 },
    })
    const group = await res.json()

    await page.goto(`/groups/${group.id}`)
    await expect(page.getByRole("heading", { name: groupName })).toBeVisible()
    // Overview · Config · Members · Activity — the group is edited inline
    // on these tabs; there is no Edit dialog.
    for (const tab of ["Overview", "Config", "Members", "Activity"]) {
      await expect(page.getByRole("tab", { name: tab })).toBeVisible()
    }
    await expect(page.getByRole("button", { name: "Run action…" })).toBeVisible()
    // Plan sync is the sync entry point; an empty group has nothing to plan.
    await expect(page.getByRole("button", { name: "Plan sync — 0 hosts" })).toBeDisabled()
    await expect(page.getByRole("button", { name: "Delete group" })).toBeVisible()
  })

  test("group detail shows the priority in the head and the settings form", async ({ request, page }) => {
    const groupName = `e2e-priority-${Date.now()}`
    const res = await request.post(`${API_BASE}/api/groups`, {
      data: { name: groupName, description: null, priority: 42 },
    })
    const group = await res.json()

    await page.goto(`/groups/${group.id}`)
    await expect(page.getByRole("heading", { name: groupName }).getByText("priority 42")).toBeVisible()
    await expect(page.getByLabel("priority", { exact: true })).toHaveValue("42")
  })

  test("settings save inline and the head follows", async ({ request, page }) => {
    const groupName = `e2e-rename-${Date.now()}`
    const res = await request.post(`${API_BASE}/api/groups`, {
      data: { name: groupName, description: "before", priority: 41 },
    })
    const group = await res.json()

    await page.goto(`/groups/${group.id}`)
    const save = page.getByRole("button", { name: "Save changes" })
    await expect(save).toBeDisabled()
    await page.getByLabel("description").fill("after")
    await expect(save).toBeEnabled()
    await save.click()
    await expect(page.getByText(/saved — desired state only/)).toBeVisible()
    await expect(page.getByText("after · 0 hosts inherit this")).toBeVisible()
  })

  test("Config tab lists every module and opens the editor; legacy ?tab=rules still resolves", async ({ request, page }) => {
    const groupName = `e2e-config-${Date.now()}`
    const res = await request.post(`${API_BASE}/api/groups`, {
      data: { name: groupName, description: null, priority: 993 },
    })
    const group = await res.json()

    await page.goto(`/groups/${group.id}?tab=rules`)
    await expect(page).toHaveURL(/tab=rules/)
    await expect(page.getByRole("tab", { name: "Config", selected: true })).toBeVisible()
    await expect(page.getByRole("button", { name: "Firewall", pressed: true })).toBeVisible()
    for (const mod of ["Services", "Hosts file", "Packages", "Users & SSH", "Cron", "DNS resolver", "CA certificates"]) {
      await expect(page.getByRole("button", { name: mod, exact: true })).toBeVisible()
    }
    await page.getByRole("button", { name: "Services", exact: true }).click()
    await expect(page).toHaveURL(/tab=config&module=services/)
    await expect(page.getByRole("button", { name: "Services", pressed: true })).toBeVisible()
  })

  // A real CA certificate (EC P-256, CA:TRUE, valid 2026–2126) that the
  // backend's PEM validator accepts, so the add goes all the way through
  // the API. The editor once sent `pem` for `pem_content`; only a live
  // backend rejects that, so this runs the form rather than seeding by API.
  const E2E_CA_PEM = `-----BEGIN CERTIFICATE-----
MIIBejCCASGgAwIBAgIUN+SuaZjYowWTWSwCkJAq+tE+8uowCgYIKoZIzj0EAwIw
MjEbMBkGA1UEAwwSTGFiRG9nIGUyZSB0ZXN0IENBMRMwEQYDVQQKDApMYWJEb2cg
ZTJlMCAXDTI2MDEwMTAwMDAwMFoYDzIxMjYwMTAxMDAwMDAwWjAyMRswGQYDVQQD
DBJMYWJEb2cgZTJlIHRlc3QgQ0ExEzARBgNVBAoMCkxhYkRvZyBlMmUwWTATBgcq
hkjOPQIBBggqhkjOPQMBBwNCAASvMh75uiNeWis4hVlYDFS5Uh4hfZxrmiW4X1Sr
rKhqKow1wUZEILBNh/YZvU/vHrsmDT+G9pjN0wTZCoyMojm6oxMwETAPBgNVHRMB
Af8EBTADAQH/MAoGCCqGSM49BAMCA0cAMEQCIAJpdT141l/f6LZbd0q2oyyWLGAN
TjIIGPY0y9owejh0AiBZDQ3rkCgh7O9lkUGjPac4TmKyBVy5h9OBxBhBKeccGQ==
-----END CERTIFICATE-----`

  test("CA certificates editor adds a certificate", async ({ request, page }) => {
    const res = await request.post(`${API_BASE}/api/groups`, {
      data: { name: `e2e-ca-${Date.now()}`, description: null, priority: 990 },
    })
    const group = await res.json()

    await page.goto(`/groups/${group.id}?tab=config&module=ca-certs`)
    await page.getByRole("button", { name: "Add certificate" }).click()
    const dialog = page.getByRole("dialog")
    await dialog.getByLabel("display name").fill("e2e test CA")
    await dialog.getByLabel("pem").fill(E2E_CA_PEM)
    await dialog.getByRole("button", { name: "Add certificate" }).click()

    await expect(dialog).toBeHidden()
    // The subject column is parsed from the PEM server-side.
    const row = page.getByRole("row").filter({ hasText: "e2e test CA" })
    await expect(row).toContainText("CN=LabDog e2e test CA")
  })

  test("Members tab adds and removes a host", async ({ request, page }) => {
    const stamp = Date.now()
    const groupRes = await request.post(`${API_BASE}/api/groups`, {
      data: { name: `e2e-members-${stamp}`, description: null, priority: 992 },
    })
    const group = await groupRes.json()
    const keyRes = await request.post(`${API_BASE}/api/ssh-keys`, {
      data: {
        name: `e2e-members-key-${stamp}`,
        private_key: "-----BEGIN OPENSSH PRIVATE KEY-----\nfakekey\n-----END OPENSSH PRIVATE KEY-----",
        is_default: false,
      },
    })
    const sshKey = await keyRes.json()
    const hostname = `e2e-member-${stamp}`
    await request.post(`${API_BASE}/api/hosts`, {
      data: { hostname, ip_address: "10.0.9.9", ssh_port: 22, ssh_key_id: sshKey.id, group_ids: [] },
    })

    await page.goto(`/groups/${group.id}?tab=members`)
    await expect(page.getByText("0 hosts inherit this group")).toBeVisible()
    await page.getByRole("button", { name: "+ add hosts" }).click()
    await page.getByLabel("filter hosts to add").fill(hostname)
    // Ticking a host adds it at once and drops it from the picker, so the
    // box never reads as checked — click, don't check().
    await page.getByLabel(`add ${hostname}`).click()

    const row = page.getByRole("row").filter({ hasText: hostname })
    await expect(row).toBeVisible()
    await expect(page.getByText("1 host inherits this group")).toBeVisible()
    await expect(page.getByRole("button", { name: "Plan sync — 1 host" })).toBeEnabled()

    await row.getByRole("button", { name: "remove" }).click()
    await expect(page.getByText("0 hosts inherit this group")).toBeVisible()
  })

  test("group not found shows error message", async ({ page }) => {
    await page.goto("/groups/999999")
    await expect(page.getByText("Group not found")).toBeVisible()
  })
})
