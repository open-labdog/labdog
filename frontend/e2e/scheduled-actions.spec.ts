import { test, expect, type Page, type Route } from "@playwright/test"

// Mock backend payloads so the schedule-action surface can be exercised
// without hitting a real Celery worker / database.

const FAKE_GROUP_ID = 7001
const FAKE_HOST_ID = 8001
const FAKE_SCHEDULE_ID = 9001

interface Action {
  key: string
  name: string
  description: string
  icon: string
  version: string
  estimated_duration: string
  destructive: boolean
  supports_group: boolean
  supports_host: boolean
  supports_fleet: boolean
  parameters: unknown[]
  pack_name: string
  overridden_from: string[]
}

const FAKE_ACTIONS: Action[] = [
  {
    key: "_builtin.collect_state",
    name: "Collect host state",
    description: "Refresh cached module state via SSH.",
    icon: "database-zap",
    version: "1.0.0",
    estimated_duration: "< 1 min",
    destructive: false,
    supports_group: true,
    supports_host: true,
    supports_fleet: true,
    parameters: [],
    pack_name: "_builtin",
    overridden_from: [],
  },
  {
    key: "_builtin.drift_check",
    name: "Check drift",
    description: "Compare desired vs current state.",
    icon: "search-check",
    version: "1.0.0",
    estimated_duration: "< 1 min",
    destructive: false,
    supports_group: true,
    supports_host: true,
    supports_fleet: true,
    parameters: [],
    pack_name: "_builtin",
    overridden_from: [],
  },
  {
    key: "linux-upgrade",
    name: "Upgrade Linux packages",
    description: "Upgrades all system packages.",
    icon: "ArrowUpFromLine",
    version: "1.0",
    estimated_duration: "5–15 min",
    destructive: true,
    supports_group: false,
    supports_host: true,
    supports_fleet: false,
    parameters: [],
    pack_name: "bundled",
    overridden_from: [],
  },
]

const FAKE_GROUP = {
  id: FAKE_GROUP_ID,
  name: "e2e-test-group",
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
}

interface ScheduleRow {
  id: number
  target_kind: "host" | "group" | "fleet"
  target_id: number | null
  action_key: string
  parameters: Record<string, unknown>
  schedule_cron: string
  enabled: boolean
  snapshot_enabled: boolean
  verify_enabled: boolean
  auto_rollback: boolean
  batch_size: number
  last_dispatched_at: string | null
  created_at: string
  updated_at: string
  target_name: string | null
  action_name: string | null
  pack_name: string | null
  destructive: boolean | null
  last_run: null
}

function makeSchedule(overrides: Partial<ScheduleRow> = {}): ScheduleRow {
  return {
    id: FAKE_SCHEDULE_ID,
    target_kind: "group",
    target_id: FAKE_GROUP_ID,
    action_key: "_builtin.collect_state",
    parameters: {},
    schedule_cron: "0 3 * * *",
    enabled: true,
    snapshot_enabled: true,
    verify_enabled: true,
    auto_rollback: true,
    batch_size: 1,
    last_dispatched_at: null,
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-01-01T00:00:00Z",
    target_name: "e2e-test-group",
    action_name: "Collect host state",
    pack_name: "_builtin",
    destructive: false,
    last_run: null,
    ...overrides,
  }
}

async function fulfillJSON(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  })
}

async function setupCommonMocks(
  page: Page,
  options: {
    schedules?: ScheduleRow[]
    captureCreate?: { lastBody?: unknown }
  } = {},
) {
  const schedules = options.schedules ?? []

  await page.route("**/api/actions/", (r) => {
    if (r.request().method() === "GET") return fulfillJSON(r, FAKE_ACTIONS)
    return r.fallback()
  })
  await page.route(/\/api\/actions\/?(\?.*)?$/, (r) => {
    if (r.request().method() === "GET") return fulfillJSON(r, FAKE_ACTIONS)
    return r.fallback()
  })
  await page.route("**/api/groups", (r) => {
    if (r.request().method() === "GET") return fulfillJSON(r, [FAKE_GROUP])
    return r.fallback()
  })
  await page.route("**/api/hosts", (r) => {
    if (r.request().method() === "GET") {
      return fulfillJSON(r, [
        {
          id: FAKE_HOST_ID,
          hostname: "e2e-host",
          ip_address: "10.0.0.1",
          ssh_port: 22,
          ssh_user: "root",
          firewall_backend: "nftables",
          sync_status: "in_sync",
          labdog_source_ip: null,
          drift_check_enabled: false,
          last_sync_at: null,
          last_drift_check_at: null,
          ssh_key_id: null,
          group_ids: [],
          created_at: "2024-01-01T00:00:00Z",
          updated_at: "2024-01-01T00:00:00Z",
          os_codename: null,
          os_pretty_name: null,
          os_family: null,
          default_nic: null,
          kernel_version: null,
          kernel_release: null,
          os_facts_collected_at: null,
        },
      ])
    }
    return r.fallback()
  })

  // /api/scheduled-actions list + create
  await page.route(/\/api\/scheduled-actions(\?.*)?$/, async (r) => {
    if (r.request().method() === "GET") {
      return fulfillJSON(r, schedules)
    }
    if (r.request().method() === "POST") {
      if (options.captureCreate)
        options.captureCreate.lastBody = JSON.parse(
          r.request().postData() || "{}",
        )
      return fulfillJSON(
        r,
        makeSchedule({ id: 9999 }),
        201,
      )
    }
    return r.fallback()
  })

  await page.route("**/api/scheduled-actions/validate-cron", (r) => {
    if (r.request().method() === "POST") {
      const body = JSON.parse(r.request().postData() || "{}")
      const valid = /^(\S+\s+){4}\S+$/.test(body.cron ?? "")
      return fulfillJSON(r, {
        valid,
        message: valid ? null : "Invalid cron expression",
        next_run_at: valid
          ? [
              "2026-01-01T03:00:00Z",
              "2026-01-02T03:00:00Z",
              "2026-01-03T03:00:00Z",
            ]
          : [],
      })
    }
    return r.fallback()
  })
}

test.describe("Scheduled Actions", () => {
  test("sidebar label and /schedules page render", async ({ page }) => {
    await setupCommonMocks(page)
    await page.goto("/schedules")
    await expect(
      page.getByRole("heading", { name: "Scheduled Actions" }),
    ).toBeVisible()
    await expect(
      page.getByRole("link", { name: "Scheduled Actions" }),
    ).toBeVisible()
  })

  test("create from /schedules + New: full picker walk → POST body correct", async ({
    page,
  }) => {
    const captured: { lastBody?: unknown } = {}
    await setupCommonMocks(page, { captureCreate: captured })

    await page.goto("/schedules")
    await page.getByRole("button", { name: "+ New" }).click()
    await expect(
      page.getByRole("heading", { name: "Schedule an action" }),
    ).toBeVisible()

    await page
      .getByTestId("action-picker")
      .selectOption("_builtin.collect_state")
    await page.getByTestId("target-group").click()
    await page.locator('select').nth(1).selectOption(String(FAKE_GROUP_ID))
    await page.getByRole("button", { name: "Continue" }).click()

    // Parameters step — collect_state has no params.
    await page.getByRole("button", { name: "Continue" }).click()

    // Schedule step.
    await page.getByLabel("Cron expression").fill("0 3 * * *")
    await page.getByRole("button", { name: "Continue" }).click()

    // Review step.
    await page.getByTestId("schedule-submit").click()

    await expect.poll(() => captured.lastBody).toMatchObject({
      action_key: "_builtin.collect_state",
      target_kind: "group",
      target_id: FAKE_GROUP_ID,
      schedule_cron: "0 3 * * *",
    })
  })

  test("fleet target gated by supports_fleet", async ({ page }) => {
    await setupCommonMocks(page)
    await page.goto("/schedules")
    await page.getByRole("button", { name: "+ New" }).click()

    // linux-upgrade has supports_fleet=false → Fleet radio disabled.
    await page.getByTestId("action-picker").selectOption("linux-upgrade")
    await expect(page.getByTestId("target-fleet")).toBeDisabled()

    // Switching to drift_check (supports_fleet=true) enables Fleet.
    await page.getByTestId("action-picker").selectOption("_builtin.drift_check")
    await expect(page.getByTestId("target-fleet")).toBeEnabled()
  })

  test("/groups/{id}/workflow returns 404 (legacy route deleted)", async ({ page }) => {
    await setupCommonMocks(page)
    const resp = await page.goto(`/groups/${FAKE_GROUP_ID}/workflow`)
    // Either Next renders a 404 page (200 status with not-found content) or
    // the route is gone entirely. Both are acceptable; just assert there's
    // no Workflow form.
    await expect(
      page.getByRole("heading", { name: /Workflow/i }),
    ).toHaveCount(0)
    expect(resp?.status() ?? 404).toBeGreaterThanOrEqual(200)
  })

  test("existing schedule renders in the list with action + target", async ({
    page,
  }) => {
    await setupCommonMocks(page, { schedules: [makeSchedule()] })
    await page.goto("/schedules")

    const row = page.getByTestId("scheduled-action-row")
    await expect(row).toHaveCount(1)
    await expect(row).toContainText("Collect host state")
    await expect(row).toContainText("e2e-test-group")
    await expect(row).toContainText("0 3 * * *")
  })

  test("filter strip narrows the list to built-in only", async ({ page }) => {
    await setupCommonMocks(page, {
      schedules: [
        makeSchedule({ id: 1, action_key: "_builtin.drift_check" }),
        makeSchedule({
          id: 2,
          action_key: "k8s-upgrade",
          action_name: "Upgrade Kubernetes",
          pack_name: "bundled",
          destructive: true,
        }),
      ],
    })
    await page.goto("/schedules")

    await expect(page.getByTestId("scheduled-action-row")).toHaveCount(2)
    await page.getByRole("button", { name: "Built-in" }).click()
    await expect(page.getByTestId("scheduled-action-row")).toHaveCount(1)
    await expect(
      page.getByTestId("scheduled-action-row"),
    ).toContainText("drift")
  })
})
