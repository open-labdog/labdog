"use client"

import { useState } from "react"
import Link from "next/link"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { UsagePanel } from "@/components/ai/usage-panel"
import { Banner, Confirm, Field, Help, Modal, PageHead, Panel, Table, Tag } from "@/components/ld"
import { MODEL_PRESETS, type ModelPreset } from "@/lib/ai-presets"
import { apiFetch, ApiError } from "@/lib/api"
import type { AIProvider, AIProviderTestResult, AIProviderType } from "@/lib/types"

const CRUMBS = [
  { label: "settings", href: "/settings" },
  { label: "ai", href: "/settings?section=ai" },
]

const TYPE_LABEL: Record<AIProviderType, string> = {
  openai_compat: "OpenAI-compatible",
  anthropic: "Anthropic",
  claude_agent: "Claude Code (agentic)",
  claude_cli: "Claude Code (single-shot)",
}

const TYPE_HELP: Record<AIProviderType, string> = {
  openai_compat:
    "Any server speaking the OpenAI chat-completions API — Ollama, vLLM, LM Studio, OpenRouter, or OpenAI itself.",
  anthropic: "The Anthropic Messages API. Leave the base URL blank for the public API.",
  claude_agent:
    "Claude Code driven through the Claude Agent SDK. Runs tools, so it can drive assistant sessions and scheduled checks — billed against your Claude subscription rather than per token. Bundled in the LabDog container image.",
  claude_cli:
    "Claude Code called once per prompt. Cannot run tools. Kept for AI verify steps and written reports; for anything that has to look something up, pick the agentic option above.",
}

/**
 * Backends that drive Claude Code rather than an HTTP API.
 *
 * Both authenticate with a subscription token from `claude setup-token`,
 * neither has a base URL, and neither is billed per token — so the form
 * treats them alike everywhere except capability.
 */
const CLAUDE_CODE_TYPES = new Set<AIProviderType>(["claude_cli", "claude_agent"])

function isClaudeCode(type: AIProviderType): boolean {
  return CLAUDE_CODE_TYPES.has(type)
}

/**
 * A suggested name per type.
 *
 * A single placeholder cannot serve all of them: "ollama-local" suggested on
 * a Claude Code provider is exactly the kind of leftover that makes a form
 * look like it was not built for the option you picked.
 */
const NAME_PLACEHOLDER: Record<AIProviderType, string> = {
  openai_compat: "ollama-local",
  anthropic: "claude-api",
  claude_agent: "claude-subscription",
  claude_cli: "claude-cli",
}

/**
 * Types that reach Anthropic no matter how they are configured, so the
 * egress warning can be shown without re-deriving the backend's
 * is_local_endpoint() URL logic here — duplicating that in the browser is
 * how the two drift apart.
 *
 * An openai_compat provider is judged by its base URL instead, which is
 * usually a local Ollama, so it gets no warning. If one is pointed at a
 * hosted endpoint the refusal names the setting, and the provider list
 * flags it as off-network.
 */
const ALWAYS_SENDS_OFFSITE = new Set<AIProviderType>([
  "anthropic",
  "claude_cli",
  "claude_agent",
])

/**
 * What the Pricing column says.
 *
 * Zero rates used to render as "free", which was wrong in two different
 * directions. A subscription-billed CLI provider is not free — it is
 * simply not metered per token, and LabDog cannot see the cost. A hosted
 * provider left at zero is not free either; nobody entered its rates, and
 * the consequence is that the money budgets silently do nothing.
 *
 * Only a local endpoint — one that does not send data off the network,
 * which the row already tells us — genuinely costs nothing per token.
 */
function pricingLabel(provider: AIProvider, currency: string): string {
  if (isClaudeCode(provider.provider_type)) return "subscription"
  const { input_cost_per_mtok: input, output_cost_per_mtok: output } = provider
  if (input === 0 && output === 0) {
    return provider.sends_data_offsite ? "rates not set" : "free"
  }
  return `${input} in / ${output} out per M ${currency}`
}

/** Inside this window, the expiry stops being trivia and becomes a task. */
const CREDENTIAL_WARN_DAYS = 30

function daysUntilExpiry(provider: AIProvider): number | null {
  if (!provider.credential_expires_at) return null
  const ms = new Date(provider.credential_expires_at).getTime() - Date.now()
  return Math.floor(ms / 86_400_000)
}

function isCredentialUrgent(provider: AIProvider): boolean {
  const days = daysUntilExpiry(provider)
  return days !== null && days <= CREDENTIAL_WARN_DAYS
}

/**
 * When a subscription token runs out.
 *
 * `claude setup-token` mints a one-year token, and Anthropic's own docs
 * warn that an unattended session "stops making progress once the
 * credential expires and can't recover". LabDog's scheduled checks are
 * exactly that, so the first sign of an expired token would otherwise be
 * nightly runs quietly failing. API-key providers get nothing here —
 * their keys do not expire on a schedule.
 */
function describeCredentialExpiry(provider: AIProvider): string | null {
  const days = daysUntilExpiry(provider)
  if (days === null) return null
  if (days < 0) return "token expired — run claude setup-token again"
  if (days <= CREDENTIAL_WARN_DAYS) return `token expires in ${days} day${days === 1 ? "" : "s"}`
  return `token expires in ${Math.round(days / 30)} months`
}

/**
 * A capability limit, not a tip — shown prominently when the type is chosen.
 *
 * Picking the CLI backend for the assistant looks fine until a session starts
 * and refuses to run a single command. Saying so at selection time is the
 * difference between an informed choice and a surprise.
 */
const TYPE_LIMITATION: Partial<Record<AIProviderType, string>> = {
  claude_cli:
    "Single-shot only — it cannot run tools, so it cannot drive an investigation. Use it for AI verify steps and written reports. For subscription billing that can also investigate, pick Claude Code (agentic) instead.",
}

/**
 * Base URL each provider type starts with.
 *
 * Anthropic is blank on purpose: the client falls back to the public API,
 * and a URL here is only for proxies. The CLI has no HTTP endpoint at all
 * and hides the field.
 */
const BASE_URL_DEFAULT: Record<AIProviderType, string> = {
  openai_compat: "http://localhost:11434/v1",
  anthropic: "",
  claude_agent: "",
  claude_cli: "",
}

interface FormState {
  name: string
  provider_type: AIProviderType
  base_url: string
  model: string
  api_key: string
  max_tokens: string
  input_cost_per_mtok: string
  output_cost_per_mtok: string
  monthly_budget: string
  is_default: boolean
  verify_ssl: boolean
}

const EMPTY_FORM: FormState = {
  name: "",
  provider_type: "openai_compat",
  base_url: "http://localhost:11434/v1",
  model: "",
  api_key: "",
  max_tokens: "8192",
  input_cost_per_mtok: "0",
  output_cost_per_mtok: "0",
  monthly_budget: "0",
  is_default: false,
  verify_ssl: true,
}

/**
 * AI providers — the LLM backends the assistant can use. One is the
 * default; each is tested from here. AI stays off until `ai.enabled` is
 * on under Settings › AI, and cloud providers until
 * `ai.allow_cloud_providers` is.
 */
export default function AIProvidersPage() {
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<AIProvider | null>(null)
  const [deleting, setDeleting] = useState<AIProvider | null>(null)
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [error, setError] = useState<string | null>(null)
  const [testResults, setTestResults] = useState<Record<number, AIProviderTestResult>>({})

  const { data: providers, isLoading } = useQuery<AIProvider[]>({
    queryKey: ["ai-providers"],
    queryFn: () => apiFetch<AIProvider[]>("/api/ai/providers"),
  })

  // The display currency comes from the server (ai.currency) so the label on
  // every money field agrees with the usage panel. It is a unit, not a
  // conversion — rates are stored exactly as typed.
  const { data: usage } = useQuery<{ currency: string }>({
    queryKey: ["ai-usage", "currency"],
    queryFn: () => apiFetch<{ currency: string }>("/api/ai/usage"),
    staleTime: 300_000,
  })
  const currency = usage?.currency ?? "USD"

  const presets = MODEL_PRESETS[form.provider_type] ?? []

  /**
   * Switch provider type, moving the base URL to that type's default.
   *
   * The types disagree about what a base URL even means: an
   * OpenAI-compatible server needs one, while Anthropic wants it blank
   * unless you are proxying. Carrying the previous type's value across is
   * worse than useless — a leftover Ollama URL on an Anthropic provider
   * looks like a filled-in field and sends Anthropic-format requests to
   * Ollama, which fails in a way that points nowhere near the cause.
   *
   * A value the operator actually typed is preserved: only an untouched
   * default is replaced.
   */
  function changeType(next: AIProviderType) {
    setForm((prev) => {
      const untouched =
        !prev.base_url.trim() || prev.base_url === BASE_URL_DEFAULT[prev.provider_type]
      return {
        ...prev,
        provider_type: next,
        base_url: untouched ? BASE_URL_DEFAULT[next] : prev.base_url,
      }
    })
  }

  /**
   * Fill in a suggested model, and its rates when we know them.
   *
   * A preset without rates leaves the cost fields untouched rather than
   * zeroing them: zero is a claim that the model is free, and writing that for
   * a paid hosted model would silently disable the budgets.
   */
  function applyPreset(preset: ModelPreset) {
    setForm((prev) => ({
      ...prev,
      model: preset.id,
      ...(preset.input !== undefined
        ? { input_cost_per_mtok: String(preset.input) }
        : {}),
      ...(preset.output !== undefined
        ? { output_cost_per_mtok: String(preset.output) }
        : {}),
    }))
  }

  function openCreate() {
    setEditing(null)
    setForm(EMPTY_FORM)
    setError(null)
    setDialogOpen(true)
  }

  function openEdit(provider: AIProvider) {
    setEditing(provider)
    setForm({
      name: provider.name,
      provider_type: provider.provider_type,
      base_url: provider.base_url ?? "",
      model: provider.model,
      // Never round-trips the stored key: blank means "leave it alone".
      api_key: "",
      max_tokens: String(provider.max_tokens),
      input_cost_per_mtok: String(provider.input_cost_per_mtok),
      output_cost_per_mtok: String(provider.output_cost_per_mtok),
      monthly_budget: String(provider.monthly_budget),
      is_default: provider.is_default,
      verify_ssl: provider.verify_ssl,
    })
    setError(null)
    setDialogOpen(true)
  }

  const save = useMutation({
    mutationFn: async () => {
      const body: Record<string, unknown> = {
        name: form.name,
        provider_type: form.provider_type,
        base_url: form.base_url || null,
        model: form.model,
        max_tokens: Number(form.max_tokens),
        input_cost_per_mtok: Number(form.input_cost_per_mtok),
        output_cost_per_mtok: Number(form.output_cost_per_mtok),
        monthly_budget: Number(form.monthly_budget),
        is_default: form.is_default,
        verify_ssl: form.verify_ssl,
      }
      // Omitting the key on edit keeps the stored one; sending "" clears it.
      if (form.api_key || !editing) body.api_key = form.api_key || null

      return editing
        ? apiFetch<AIProvider>(`/api/ai/providers/${editing.id}`, {
            method: "PATCH",
            json: body,
          })
        : apiFetch<AIProvider>("/api/ai/providers", { method: "POST", json: body })
    },
    onSuccess: () => {
      setDialogOpen(false)
      queryClient.invalidateQueries({ queryKey: ["ai-providers"] })
    },
    onError: (err: unknown) => {
      setError(err instanceof ApiError ? err.message : "Could not save the provider.")
    },
  })

  const remove = useMutation({
    mutationFn: (id: number) =>
      apiFetch<void>(`/api/ai/providers/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      setDeleting(null)
      queryClient.invalidateQueries({ queryKey: ["ai-providers"] })
    },
  })

  const test = useMutation({
    mutationFn: (id: number) =>
      apiFetch<AIProviderTestResult>(`/api/ai/providers/${id}/test`, { method: "POST" }),
    onSuccess: (result, id) => setTestResults((prev) => ({ ...prev, [id]: result })),
  })

  const all = providers ?? []
  const claude = isClaudeCode(form.provider_type)
  const setField = <K extends keyof FormState>(k: K, v: FormState[K]) => setForm((prev) => ({ ...prev, [k]: v }))

  return (
    <>
      <PageHead
        crumbs={CRUMBS}
        title={
          <>
            AI providers <span className="mono num text-[12.5px] font-normal text-text-faint">{all.length}</span>
          </>
        }
        sub={
          <>
            Connect a local or hosted LLM for the assistant to use. AI stays off until <span className="mono">ai.enabled</span> is on under{" "}
            <Link href="/settings?section=ai" className="text-ld-accent">
              Settings › AI
            </Link>
            .
          </>
        }
        actions={
          <button type="button" className="btn btn-sm btn-primary" onClick={openCreate}>
            Add provider…
          </button>
        }
      />

      <div className="scroll flex flex-1 flex-col gap-3 p-3.5">
        <UsagePanel />

        <Panel title="providers" meta="who answers the assistant">
          <Table<AIProvider>
            cols={[
              {
                k: "name",
                label: "name",
                w: "minmax(160px,1.2fr)",
                sortable: false,
                nowrap: false,
                cell: (provider) => {
                  const result = testResults[provider.id]
                  return (
                    <span className="flex min-w-0 flex-col gap-0.5">
                      <span className="flex items-center gap-1.5">
                        <span className="mono trunc font-medium text-text">{provider.name}</span>
                        {provider.is_default && <Tag tone="accent">default</Tag>}
                        {!provider.enabled && <Tag>disabled</Tag>}
                      </span>
                      {result && (
                        <span className="text-[11px]" style={{ color: result.ok ? "var(--ok)" : "var(--danger)" }}>
                          {result.message}
                        </span>
                      )}
                    </span>
                  )
                },
              },
              {
                k: "type",
                label: "type",
                w: "minmax(150px,1fr)",
                sortable: false,
                cell: (provider) => (
                  <span className="flex min-w-0 flex-col">
                    <span className="trunc text-text-2">{TYPE_LABEL[provider.provider_type]}</span>
                    {provider.base_url && <span className="mono trunc text-[10.5px] text-text-faint">{provider.base_url}</span>}
                  </span>
                ),
              },
              {
                k: "model",
                label: "model",
                w: "minmax(140px,1fr)",
                sortable: false,
                cell: (provider) => {
                  const expiry = describeCredentialExpiry(provider)
                  return (
                    <span className="flex min-w-0 flex-col">
                      <span className="mono trunc text-[11px] text-text">{provider.model || <span className="text-text-faint">provider default</span>}</span>
                      {expiry && <span className={`trunc text-[10.5px] ${isCredentialUrgent(provider) ? "text-warn" : "text-text-faint"}`}>{expiry}</span>}
                    </span>
                  )
                },
              },
              { k: "pricing", label: "pricing", w: "minmax(120px,0.9fr)", sortable: false, cell: (provider) => <span className="mono text-[11px] text-text-3">{pricingLabel(provider, currency)}</span> },
              { k: "data", label: "data", w: "80px", sortable: false, cell: (provider) => (provider.sends_data_offsite ? <Tag tone="warn">off-site</Tag> : <Tag tone="ok">local</Tag>) },
              {
                k: "actions",
                label: "",
                w: "170px",
                right: true,
                sortable: false,
                cell: (provider) => (
                  <span className="flex gap-0.5">
                    <button type="button" className="btn btn-sm btn-ghost" onClick={() => test.mutate(provider.id)} disabled={test.isPending}>
                      test
                    </button>
                    <button type="button" className="btn btn-sm btn-ghost" onClick={() => openEdit(provider)}>
                      edit
                    </button>
                    <button type="button" className="btn btn-sm btn-ghost text-danger" onClick={() => setDeleting(provider)}>
                      delete
                    </button>
                  </span>
                ),
              },
            ]}
            rows={all}
            keyOf={(p) => p.id}
            loading={isLoading}
            empty="No providers yet. Add a local Ollama, an Anthropic API key, or a Claude Code subscription token — the assistant cannot run without one."
          />
        </Panel>
      </div>

      {dialogOpen && (
        <Modal
          title={editing ? "Edit provider" : "Add provider"}
          meta={editing?.name}
          w={560}
          onClose={() => setDialogOpen(false)}
          onSubmit={(e) => {
            e.preventDefault()
            save.mutate()
          }}
          footer={
            <>
              <span className="tt mr-auto">credentials are stored encrypted</span>
              <button type="button" className="btn" onClick={() => setDialogOpen(false)}>
                Cancel
              </button>
              <button type="submit" className="btn btn-primary" disabled={save.isPending}>
                {save.isPending ? "Saving…" : "Save"}
              </button>
            </>
          }
        >
          <div className="grid gap-[11px]" style={{ gridTemplateColumns: "1fr 1fr" }}>
            <Field label="name" htmlFor="name">
              <input id="name" className="inp mono" value={form.name} onChange={(e) => setField("name", e.target.value)} placeholder={NAME_PLACEHOLDER[form.provider_type]} />
            </Field>
            <Field label="type" htmlFor="provider_type">
              <select id="provider_type" className="inp" value={form.provider_type} onChange={(e) => changeType(e.target.value as AIProviderType)}>
                {(Object.keys(TYPE_LABEL) as AIProviderType[]).map((t) => (
                  <option key={t} value={t}>
                    {TYPE_LABEL[t]}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <div className="text-[11.5px] text-text-3">{TYPE_HELP[form.provider_type]}</div>
          {TYPE_LIMITATION[form.provider_type] && <Banner tone="warn">{TYPE_LIMITATION[form.provider_type]}</Banner>}

          {!claude && (
            <Field label="base url" htmlFor="base_url" hint={form.provider_type === "anthropic" ? "optional — blank uses the public API" : "including the version path"}>
              <input
                id="base_url"
                className="inp mono"
                value={form.base_url}
                onChange={(e) => setField("base_url", e.target.value)}
                placeholder={form.provider_type === "anthropic" ? "blank — uses https://api.anthropic.com" : "http://localhost:11434/v1"}
              />
              <Help>
                {form.provider_type === "anthropic"
                  ? "Leave blank to use the public Anthropic API. Set it only if you route through a proxy or gateway that speaks the Messages API."
                  : "Where your OpenAI-compatible server listens, including the version path — Ollama uses http://localhost:11434/v1 by default."}
              </Help>
            </Field>
          )}

          <Field label="model" htmlFor="model" hint="the identifier sent to the provider">
            <input
              id="model"
              className="inp mono"
              value={form.model}
              onChange={(e) => setField("model", e.target.value)}
              placeholder={form.provider_type === "anthropic" ? "claude-opus-5" : claude ? "blank — uses Claude Code's own default model" : "llama3.1:8b"}
            />
            {presets.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {presets.map((preset) => (
                  <Tag key={preset.id} tone={form.model === preset.id ? "accent" : undefined} title={preset.hint} onClick={() => applyPreset(preset)}>
                    {preset.label}
                  </Tag>
                ))}
              </div>
            )}
            <Help>For Ollama the tag you pulled, for a hosted API the published model id. Pick a suggestion to fill this in, or type any name the endpoint serves.</Help>
          </Field>

          <Field label={claude ? "subscription token" : "api key"} htmlFor="api_key" hint={form.provider_type === "anthropic" ? "required" : editing?.has_api_key ? "stored — blank keeps it" : undefined}>
            <input
              id="api_key"
              type="password"
              className="inp mono"
              autoComplete="off"
              value={form.api_key}
              onChange={(e) => setField("api_key", e.target.value)}
              placeholder={
                editing?.has_api_key
                  ? "stored — leave blank to keep it"
                  : claude
                    ? "sk-ant-oat01-… from `claude setup-token` — blank uses the host's own login"
                    : form.provider_type === "anthropic"
                      ? "sk-ant-… from platform.claude.com"
                      : "leave blank for an unauthenticated local server"
              }
            />
            {form.provider_type === "anthropic" && (
              <Help summary="where to get one">
                Create one in the Claude Console at <span className="mono">platform.claude.com</span> under API keys. It starts with <span className="mono">sk-ant-</span> and is billed per token. A Pro or Max
                subscription does not cover API usage — to spend against a subscription instead, use a Claude Code provider type.
              </Help>
            )}
            {claude && (
              <Help summary="how to get one, and what it authenticates as">
                Run <span className="mono">claude setup-token</span> on your own machine, not the server. It shows three strings in turn, and only the last belongs here: an <strong>authorize URL</strong> to open in a
                browser, an <strong>authorization code</strong> to paste back at the terminal, then the <strong>token</strong> starting <span className="mono">sk-ant-oat01-</span> — that is this field. It is stored
                encrypted, like every other LabDog credential, and injected only into the CLI process; leave it blank to use whatever the host is already logged in as. The token lasts a year, and sessions count against
                your plan&apos;s usage limits — the same ones the Claude apps use. Anthropic&apos;s terms allow this for your own use but not for routing other people&apos;s requests through your plan, so use your own
                token on your own instance and pick an API-key provider if you run LabDog for someone else.
                <br />
                <br />
                Two things outrank this token in Claude Code&apos;s own credential order, and both would quietly authenticate as a different account: the <span className="mono">ANTHROPIC_API_KEY</span> and{" "}
                <span className="mono">ANTHROPIC_AUTH_TOKEN</span> environment variables, and a login left on disk by <span className="mono">claude login</span>. When a token is set LabDog neutralises both variables and
                points Claude Code at its own config directory, so neither can shadow what you enter here.
              </Help>
            )}
          </Field>

          {/*
            Every field below is inert on a subscription, and a Monthly
            cap is actively misleading there: it can never fire, so an
            operator who set one would believe they were capped when they
            were not. Hidden rather than disabled — a greyed-out budget
            still reads as a budget.

            The two backends reach that state differently. The single-shot
            CLI reports no usage at all, so recorded spend stays zero. The
            agentic backend does report tokens, so the token and iteration
            caps work normally — but the money is still flat-rate, so a
            USD figure would be fiction either way.
          */}
          {claude ? (
            <Banner tone="idle">
              Cost settings do not apply to a subscription: it is billed flat rather than per token, so the money budgets under Settings will not act on this provider. The per-session iteration, command and wall-clock caps
              still apply.
            </Banner>
          ) : (
            <>
              <div className="grid gap-[11px]" style={{ gridTemplateColumns: "1fr 1fr" }}>
                <Field label={`input ${currency} / M tokens`} htmlFor="input_cost">
                  <input id="input_cost" className="inp mono num" value={form.input_cost_per_mtok} onChange={(e) => setField("input_cost_per_mtok", e.target.value)} />
                  <Help>
                    What the provider charges for the tokens you send it — the prompt, the conversation so far, and every tool result read back on each turn. Usually the larger share of an investigation&apos;s cost,
                    because the transcript is re-sent every turn.
                  </Help>
                </Field>
                <Field label={`output ${currency} / M tokens`} htmlFor="output_cost">
                  <input id="output_cost" className="inp mono num" value={form.output_cost_per_mtok} onChange={(e) => setField("output_cost_per_mtok", e.target.value)} />
                  <Help>
                    What the provider charges for the tokens it generates — the assistant&apos;s replies and the commands it decides to run. Usually several times the input rate per token, but far fewer tokens, so it is
                    often the smaller half of the bill.
                  </Help>
                </Field>
              </div>
              <div className="text-[11.5px] text-text-3">
                Rates are entered by hand — an OpenAI-compatible endpoint cannot report its own pricing. Enter them in {currency}; LabDog never converts between currencies. Leave both at 0 for a self-hosted model,
                which makes the money budgets a no-op for it.
              </div>
              <div className="grid gap-[11px]" style={{ gridTemplateColumns: "1fr 1fr" }}>
                <Field label="max tokens per turn" htmlFor="max_tokens">
                  <input id="max_tokens" className="inp mono num" value={form.max_tokens} onChange={(e) => setField("max_tokens", e.target.value)} />
                  <Help>
                    The ceiling on a single reply. It bounds one message, not a whole session — the session caps under Settings do that. Too low and long answers get cut off mid-sentence; the model must also fit any
                    commands it wants to run inside this budget.
                  </Help>
                </Field>
                <Field label={`monthly cap (${currency})`} htmlFor="monthly_budget" hint="0 = unlimited">
                  <input id="monthly_budget" className="inp mono num" value={form.monthly_budget} onChange={(e) => setField("monthly_budget", e.target.value)} />
                  <Help>
                    A ceiling for this provider alone, on top of the global daily and monthly budgets in Settings. Useful when a free local model and a paid one are both configured and you want to bound only the paid
                    one. Priced at 0? Then this never triggers — the token and iteration caps still apply.
                  </Help>
                </Field>
              </div>
            </>
          )}

          <label className="flex items-center gap-2 text-xs text-text">
            <input type="checkbox" checked={form.is_default} onChange={(e) => setField("is_default", e.target.checked)} />
            use as the default provider
          </label>

          {ALWAYS_SENDS_OFFSITE.has(form.provider_type) && (
            <Banner tone="hold">
              This provider sends host data off your network. It stays blocked until <span className="mono">ai.allow_cloud_providers</span> is enabled under{" "}
              <Link href="/settings?section=ai" className="underline">
                Settings › AI
              </Link>
              , which is off by default.
            </Banner>
          )}

          {error && <Banner tone="danger">{error}</Banner>}
        </Modal>
      )}

      {deleting && (
        <Confirm
          open
          onOpenChange={(open) => !open && setDeleting(null)}
          title="Delete provider"
          description={`${deleting.name} is removed${deleting.is_default ? " — it is the default, so the assistant has no provider until another is made default" : ""}. Sessions that used it keep their history. This cannot be undone.`}
          confirmLabel="Delete"
          variant="destructive"
          loading={remove.isPending}
          onConfirm={() => remove.mutate(deleting.id)}
        />
      )}
    </>
  )
}
