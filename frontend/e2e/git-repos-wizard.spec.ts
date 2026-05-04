import { test, expect, type Page, type Route } from "@playwright/test"

// Mock backend payloads. Tests that exercise the wizard intercept all
// repo-onboarding endpoints with page.route() so the suite is fully
// deterministic and never touches a real git remote.

const FAKE_REPO_ID = 9001
const FAKE_REPO_NAME = "e2e-fixture-repo"

type ScanShape = {
  packs: Array<{
    path: string
    name: string
    contributed_keys: string[]
    pack_yml_present: boolean
    errors: Array<{ file: string; message: string }>
  }>
  gitops_files: Array<{
    path: string
    group_name: string | null
    errors: Array<{ file: string; message: string }>
  }>
  existing_key_winners: Record<
    string,
    { key: string; source: "bundled" | "db_pack"; pack_name: string; pack_id: number | null }
  >
  intra_repo_key_conflicts: Array<{ key: string; contributing_packs: string[] }>
  scan_errors: Array<{ file: string; message: string }>
  head_sha: string | null
}

const HAPPY_SCAN: ScanShape = {
  packs: [
    {
      path: "actions/upgrade",
      name: "upgrade-pack",
      contributed_keys: ["linux-upgrade"],
      pack_yml_present: true,
      errors: [],
    },
    {
      path: "actions/k8s",
      name: "k8s-pack",
      contributed_keys: ["k8s-rollout"],
      pack_yml_present: true,
      errors: [],
    },
  ],
  gitops_files: [
    { path: "groups/web.yaml", group_name: "web", errors: [] },
    { path: "groups/db.yaml", group_name: "db", errors: [] },
  ],
  existing_key_winners: {},
  intra_repo_key_conflicts: [],
  scan_errors: [],
  head_sha: "abcdef0123456789abcdef0123456789abcdef01",
}

const CONFLICT_SCAN: ScanShape = {
  packs: [
    {
      path: "actions/a",
      name: "pack-a",
      contributed_keys: ["shared-key"],
      pack_yml_present: true,
      errors: [],
    },
    {
      path: "actions/b",
      name: "pack-b",
      contributed_keys: ["shared-key"],
      pack_yml_present: true,
      errors: [],
    },
  ],
  gitops_files: [],
  existing_key_winners: {},
  intra_repo_key_conflicts: [{ key: "shared-key", contributing_packs: ["actions/a", "actions/b"] }],
  scan_errors: [],
  head_sha: "0000000000000000000000000000000000000001",
}

const ERROR_SCAN: ScanShape = {
  packs: [
    {
      path: "actions/broken",
      name: "broken-pack",
      contributed_keys: [],
      pack_yml_present: true,
      errors: [{ file: "actions/broken/actions/foo.manifest.yml", message: "missing required key" }],
    },
    {
      path: "actions/healthy",
      name: "healthy-pack",
      contributed_keys: ["healthy-key"],
      pack_yml_present: true,
      errors: [],
    },
  ],
  gitops_files: [],
  existing_key_winners: {},
  intra_repo_key_conflicts: [],
  scan_errors: [],
  head_sha: "0000000000000000000000000000000000000002",
}

async function mockGroups(page: Page, payload: unknown[] = []) {
  await page.route("**/api/groups", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({ contentType: "application/json", body: JSON.stringify(payload) })
    }
    return route.fallback()
  })
}

async function mockSshKeys(page: Page, payload: unknown[] = []) {
  await page.route("**/api/ssh-keys", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({ contentType: "application/json", body: JSON.stringify(payload) })
    }
    return route.fallback()
  })
}

async function mockCreateRepo(page: Page) {
  await page.route(/\/api\/git-repos(\?.*)?$/, async (route) => {
    if (route.request().method() === "POST") {
      const now = new Date().toISOString()
      return route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: FAKE_REPO_ID,
          name: FAKE_REPO_NAME,
          url: "https://example.com/fake.git",
          branch: "main",
          auth_type: "none",
          ssh_key_id: null,
          webhook_secret: null,
          last_commit_sha: null,
          last_sync_at: null,
          created_at: now,
          updated_at: now,
        }),
      })
    }
    if (route.request().method() === "GET") {
      return route.fulfill({ contentType: "application/json", body: "[]" })
    }
    return route.fallback()
  })
}

async function mockScan(page: Page, scan: ScanShape) {
  await page.route(`**/api/git-repos/${FAKE_REPO_ID}/scan`, (route) => {
    if (route.request().method() === "POST") {
      return route.fulfill({ contentType: "application/json", body: JSON.stringify(scan) })
    }
    return route.fallback()
  })
}

async function mockActivate(page: Page, capture?: { lastBody?: unknown }) {
  await page.route(`**/api/git-repos/${FAKE_REPO_ID}/activate`, async (route) => {
    if (route.request().method() === "POST") {
      if (capture) capture.lastBody = JSON.parse(route.request().postData() || "{}")
      return route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          activated_packs: [],
          activated_gitops_bindings: [],
          head_sha: "abcdef0123456789abcdef0123456789abcdef01",
        }),
      })
    }
    return route.fallback()
  })
}

async function fillAuthStep(page: Page) {
  await page.locator("#repo-name").fill(FAKE_REPO_NAME)
  await page.locator("#repo-url").fill("https://example.com/fake.git")
  await page.getByRole("button", { name: "Connect & scan" }).click()
}

test.describe("Git repo onboarding wizard", () => {
  test("renders /git-repos/new with step 1 active", async ({ page }) => {
    await page.goto("/git-repos/new")
    await expect(page.getByRole("heading", { name: "Connect a git repository" })).toBeVisible()
    await expect(page.locator('[data-step="auth"]')).toHaveAttribute("data-active", "true")
    await expect(page.locator('[data-step="scanning"]')).toHaveAttribute("data-active", "false")
    await expect(page.locator('[data-step="review"]')).toHaveAttribute("data-active", "false")
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

  test("happy path: scan returns findings, operator activates, lands on detail", async ({
    page,
  }) => {
    const groups = [
      {
        id: 1,
        name: "web",
        description: null,
        category: null,
        priority: 100,
        input_policy: null,
        output_policy: null,
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:00:00Z",
        gitops_enabled: false,
        gitops_status: null,
        gitops_error_message: null,
        gitops_last_import_at: null,
        gitops_file_path: null,
        git_repository_id: null,
      },
      {
        id: 2,
        name: "db",
        description: null,
        category: null,
        priority: 100,
        input_policy: null,
        output_policy: null,
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:00:00Z",
        gitops_enabled: false,
        gitops_status: null,
        gitops_error_message: null,
        gitops_last_import_at: null,
        gitops_file_path: null,
        git_repository_id: null,
      },
    ]
    const captured: { lastBody?: unknown } = {}
    await mockGroups(page, groups)
    await mockSshKeys(page)
    await mockCreateRepo(page)
    await mockScan(page, HAPPY_SCAN)
    await mockActivate(page, captured)
    // Detail page also fires; route the lookups too so the redirect doesn't 404.
    await page.route(`**/api/git-repos/${FAKE_REPO_ID}`, (route) => {
      if (route.request().method() === "GET") {
        return route.fulfill({
          contentType: "application/json",
          body: JSON.stringify({
            id: FAKE_REPO_ID,
            name: FAKE_REPO_NAME,
            url: "https://example.com/fake.git",
            branch: "main",
            auth_type: "none",
            ssh_key_id: null,
            webhook_secret: null,
            last_commit_sha: null,
            last_sync_at: null,
            created_at: "2024-01-01T00:00:00Z",
            updated_at: "2024-01-01T00:00:00Z",
          }),
        })
      }
      return route.fallback()
    })
    await page.route("**/api/action-packs", (route) => {
      if (route.request().method() === "GET") {
        return route.fulfill({ contentType: "application/json", body: "[]" })
      }
      return route.fallback()
    })

    await page.goto("/git-repos/new")
    await fillAuthStep(page)

    // Wait for the review step to render.
    await expect(page.locator('[data-step="review"]')).toHaveAttribute("data-active", "true")
    await expect(page.locator("[data-testid=detected-pack-row]")).toHaveCount(2)
    await expect(page.locator("[data-testid=detected-gitops-row]")).toHaveCount(2)

    await page.getByTestId("activate-button").click()

    await expect(page).toHaveURL(new RegExp(`/git-repos/${FAKE_REPO_ID}$`))

    // Activation request shape: two packs default-checked, two gitops bindings.
    expect(captured.lastBody).toMatchObject({
      packs: [
        { path: "actions/upgrade", name: "upgrade-pack", role: "default" },
        { path: "actions/k8s", name: "k8s-pack", role: "default" },
      ],
      gitops_bindings: [
        { file_path: "groups/web.yaml", host_group_id: 1 },
        { file_path: "groups/db.yaml", host_group_id: 2 },
      ],
    })
  })

  test("intra-repo conflict disables Activate until one row is unchecked", async ({ page }) => {
    await mockGroups(page)
    await mockSshKeys(page)
    await mockCreateRepo(page)
    await mockScan(page, CONFLICT_SCAN)
    await mockActivate(page)
    await page.route(`**/api/git-repos/${FAKE_REPO_ID}`, (route) => {
      if (route.request().method() === "GET") {
        return route.fulfill({
          contentType: "application/json",
          body: JSON.stringify({
            id: FAKE_REPO_ID,
            name: FAKE_REPO_NAME,
            url: "https://example.com/fake.git",
            branch: "main",
            auth_type: "none",
            ssh_key_id: null,
            webhook_secret: null,
            last_commit_sha: null,
            last_sync_at: null,
            created_at: "2024-01-01T00:00:00Z",
            updated_at: "2024-01-01T00:00:00Z",
          }),
        })
      }
      return route.fallback()
    })
    await page.route("**/api/action-packs", (route) => {
      if (route.request().method() === "GET") {
        return route.fulfill({ contentType: "application/json", body: "[]" })
      }
      return route.fallback()
    })

    await page.goto("/git-repos/new")
    await fillAuthStep(page)

    await expect(page.locator('[data-step="review"]')).toHaveAttribute("data-active", "true")
    const rowA = page.locator('[data-testid=detected-pack-row][data-path="actions/a"]')
    const rowB = page.locator('[data-testid=detected-pack-row][data-path="actions/b"]')
    await expect(rowA).toHaveAttribute("data-conflict", "true")
    await expect(rowB).toHaveAttribute("data-conflict", "true")
    await expect(page.getByTestId("activate-button")).toBeDisabled()

    // Uncheck pack-b → conflict resolves.
    await rowB.locator("input[type=checkbox]").uncheck()
    await expect(rowA).toHaveAttribute("data-conflict", "false")
    await expect(page.getByTestId("activate-button")).toBeEnabled()

    await page.getByTestId("activate-button").click()
    await expect(page).toHaveURL(new RegExp(`/git-repos/${FAKE_REPO_ID}$`))
  })

  test("manifest errors render as a dimmed, non-checkable row", async ({ page }) => {
    await mockGroups(page)
    await mockSshKeys(page)
    await mockCreateRepo(page)
    await mockScan(page, ERROR_SCAN)
    await mockActivate(page)
    await page.route(`**/api/git-repos/${FAKE_REPO_ID}`, (route) => {
      if (route.request().method() === "GET") {
        return route.fulfill({
          contentType: "application/json",
          body: JSON.stringify({
            id: FAKE_REPO_ID,
            name: FAKE_REPO_NAME,
            url: "https://example.com/fake.git",
            branch: "main",
            auth_type: "none",
            ssh_key_id: null,
            webhook_secret: null,
            last_commit_sha: null,
            last_sync_at: null,
            created_at: "2024-01-01T00:00:00Z",
            updated_at: "2024-01-01T00:00:00Z",
          }),
        })
      }
      return route.fallback()
    })
    await page.route("**/api/action-packs", (route) => {
      if (route.request().method() === "GET") {
        return route.fulfill({ contentType: "application/json", body: "[]" })
      }
      return route.fallback()
    })

    await page.goto("/git-repos/new")
    await fillAuthStep(page)

    await expect(page.locator('[data-step="review"]')).toHaveAttribute("data-active", "true")
    const brokenRow = page.locator('[data-testid=detected-pack-row][data-path="actions/broken"]')
    await expect(brokenRow).toHaveAttribute("data-has-errors", "true")
    await expect(brokenRow.locator("input[type=checkbox]")).toBeDisabled()
    await expect(brokenRow).toContainText("missing required key")

    // The healthy pack is still checkable + activatable.
    await expect(page.getByTestId("activate-button")).toBeEnabled()
    await page.getByTestId("activate-button").click()
    await expect(page).toHaveURL(new RegExp(`/git-repos/${FAKE_REPO_ID}$`))
  })

  test("re-scan from the detail page reuses the review modal", async ({ page }) => {
    const REPO_ID = FAKE_REPO_ID
    const repoPayload = {
      id: REPO_ID,
      name: FAKE_REPO_NAME,
      url: "https://example.com/fake.git",
      branch: "main",
      auth_type: "none",
      ssh_key_id: null,
      webhook_secret: null,
      last_commit_sha: "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
      last_sync_at: "2024-01-02T03:04:05Z",
      created_at: "2024-01-01T00:00:00Z",
      updated_at: "2024-01-02T03:04:05Z",
    }

    await mockGroups(page)
    await page.route(`**/api/git-repos/${REPO_ID}`, (route: Route) => {
      if (route.request().method() === "GET") {
        return route.fulfill({ contentType: "application/json", body: JSON.stringify(repoPayload) })
      }
      return route.fallback()
    })
    await page.route("**/api/action-packs", (route) => {
      if (route.request().method() === "GET") {
        return route.fulfill({ contentType: "application/json", body: "[]" })
      }
      return route.fallback()
    })
    await mockScan(page, HAPPY_SCAN)
    await mockActivate(page)

    await page.goto(`/git-repos/${REPO_ID}`)
    await expect(page.getByRole("heading", { name: FAKE_REPO_NAME })).toBeVisible()

    await page.getByTestId("rescan-button").click()
    await expect(page.getByRole("heading", { name: "Re-scan repository" })).toBeVisible()
    await expect(page.locator("[data-testid=detected-pack-row]")).toHaveCount(2)

    await page.getByTestId("activate-button").click()

    // Modal closes; we stay on the detail page.
    await expect(page.getByRole("heading", { name: "Re-scan repository" })).toBeHidden()
    await expect(page).toHaveURL(new RegExp(`/git-repos/${REPO_ID}$`))
  })
})
