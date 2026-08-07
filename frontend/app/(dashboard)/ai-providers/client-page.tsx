"use client"

import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { UsagePanel } from "@/components/ai/usage-panel"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { InfoPopover } from "@/components/ui/info-popover"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { MODEL_PRESETS, type ModelPreset } from "@/lib/ai-presets"
import { apiFetch, ApiError } from "@/lib/api"
import { cn } from "@/lib/utils"
import type { AIProvider, AIProviderTestResult, AIProviderType } from "@/lib/types"

const TYPE_LABEL: Record<AIProviderType, string> = {
  openai_compat: "OpenAI-compatible",
  anthropic: "Anthropic",
  claude_cli: "Claude CLI",
}

const TYPE_HELP: Record<AIProviderType, string> = {
  openai_compat:
    "Any server speaking the OpenAI chat-completions API — Ollama, vLLM, LM Studio, OpenRouter, or OpenAI itself.",
  anthropic: "The Anthropic Messages API. Leave the base URL blank for the public API.",
  claude_cli:
    "The Claude Code CLI. Bundled in the LabDog container image; on a package install you install it yourself. Authenticates with your Claude subscription rather than metered API billing.",
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
    "Single-shot only — it cannot run tools, so it cannot drive an investigation. Use it for AI verify steps and written reports; pick an OpenAI-compatible or Anthropic provider for assistant sessions and scheduled checks.",
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
  allow_cloud_egress: boolean
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
  allow_cloud_egress: false,
  verify_ssl: true,
}

export default function AIProvidersPage() {
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<AIProvider | null>(null)
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
      allow_cloud_egress: provider.allow_cloud_egress,
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
        allow_cloud_egress: form.allow_cloud_egress,
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
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["ai-providers"] }),
  })

  const test = useMutation({
    mutationFn: (id: number) =>
      apiFetch<AIProviderTestResult>(`/api/ai/providers/${id}/test`, { method: "POST" }),
    onSuccess: (result, id) => setTestResults((prev) => ({ ...prev, [id]: result })),
  })

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">AI Providers</h1>
          <p className="mt-1 text-sm text-slate-400">
            Connect a local or hosted LLM for the assistant to use. AI stays off
            until you enable <span className="font-mono">ai.enabled</span> in
            Settings.
          </p>
        </div>
        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogTrigger render={<Button onClick={openCreate}>Add provider</Button>} />
          <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
            <DialogHeader>
              <DialogTitle>{editing ? "Edit provider" : "Add provider"}</DialogTitle>
            </DialogHeader>

            <div className="space-y-4">
              <div>
                <Label htmlFor="name">Name</Label>
                <Input
                  id="name"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder="ollama-local"
                />
              </div>

              <div>
                <Label>Type</Label>
                <Select
                  value={form.provider_type}
                  onValueChange={(v) => v && changeType(v as AIProviderType)}
                >
                  <SelectTrigger>
                    {/* base-ui renders the raw value unless given a
                        formatter, so without this the field reads
                        "openai_compat" rather than "OpenAI-compatible". */}
                    <SelectValue>
                      {(v: AIProviderType) => TYPE_LABEL[v] ?? v}
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {(Object.keys(TYPE_LABEL) as AIProviderType[]).map((t) => (
                      <SelectItem key={t} value={t}>
                        {TYPE_LABEL[t]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="mt-1 text-xs text-slate-400">
                  {TYPE_HELP[form.provider_type]}
                </p>
                {TYPE_LIMITATION[form.provider_type] && (
                  <p className="mt-2 rounded-md border border-amber-700/60 bg-amber-950/40 px-3 py-2 text-xs text-amber-200">
                    {TYPE_LIMITATION[form.provider_type]}
                  </p>
                )}
              </div>

              {form.provider_type !== "claude_cli" && (
                <div>
                  <div className="flex items-center gap-1.5">
                    <Label htmlFor="base_url">
                      Base URL
                      {form.provider_type === "anthropic" && (
                        <span className="ml-1 font-normal text-slate-400">
                          (optional)
                        </span>
                      )}
                    </Label>
                    <InfoPopover title="Base URL">
                      {form.provider_type === "anthropic"
                        ? "Leave blank to use the public Anthropic API. Set it only if you route through a proxy or gateway that speaks the Messages API."
                        : "Where your OpenAI-compatible server listens, including the version path — Ollama uses http://localhost:11434/v1 by default."}
                    </InfoPopover>
                  </div>
                  <Input
                    id="base_url"
                    value={form.base_url}
                    onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                    placeholder={
                      form.provider_type === "anthropic"
                        ? "Blank — uses https://api.anthropic.com"
                        : "http://localhost:11434/v1"
                    }
                  />
                </div>
              )}

              <div>
                <div className="flex items-center gap-1.5">
                  <Label htmlFor="model">Model</Label>
                  <InfoPopover title="Model">
                    The model identifier sent to the provider — for Ollama the
                    tag you pulled, for a hosted API the published model id.
                    Pick a suggestion below to fill this in, or type any name
                    the endpoint serves.
                  </InfoPopover>
                </div>
                <Input
                  id="model"
                  value={form.model}
                  onChange={(e) => setForm({ ...form, model: e.target.value })}
                  placeholder={
                    form.provider_type === "anthropic"
                      ? "claude-opus-5"
                      : form.provider_type === "claude_cli"
                        ? "Blank — uses the CLI's own default model"
                        : "llama3.1:8b"
                  }
                />
                {presets.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {presets.map((preset) => (
                      <button
                        key={preset.id}
                        type="button"
                        title={preset.hint}
                        onClick={() => applyPreset(preset)}
                        className={cn(
                          "rounded-md px-2 py-1 text-xs transition-colors",
                          form.model === preset.id
                            ? "bg-slate-700 text-white"
                            : "bg-slate-800 text-slate-300 hover:bg-slate-700 hover:text-white"
                        )}
                      >
                        {preset.label}
                      </button>
                    ))}
                  </div>
                )}
              </div>

              <div>
                <div className="flex items-center gap-1.5">
                  <Label htmlFor="api_key">
                    {form.provider_type === "claude_cli"
                      ? "Subscription token"
                      : "API key"}
                    {form.provider_type === "anthropic" && (
                      <span className="ml-1 font-normal text-slate-400">
                        (required)
                      </span>
                    )}
                  </Label>
                  {form.provider_type === "anthropic" && (
                    <InfoPopover title="API key">
                      Create one in the Claude Console at{" "}
                      <span className="font-mono">platform.claude.com</span> under
                      API keys. It starts with{" "}
                      <span className="font-mono">sk-ant-</span> and is billed per
                      token. A Pro or Max subscription does not cover API usage —
                      to spend against a subscription instead, use the Claude CLI
                      provider type.
                    </InfoPopover>
                  )}
                  {form.provider_type === "claude_cli" && (
                    <InfoPopover title="Subscription token">
                      Run <span className="font-mono">claude setup-token</span>{" "}
                      on your own machine — it opens a browser, authenticates
                      against your Claude subscription, and prints a token
                      valid for a year. Paste it here rather than on the
                      server: it is stored encrypted, like every other LabDog
                      credential, and injected only into the CLI process.
                      Leave blank to use whatever the host is already logged
                      in as.
                    </InfoPopover>
                  )}
                </div>
                <Input
                  id="api_key"
                  type="password"
                  value={form.api_key}
                  onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                  placeholder={
                    editing?.has_api_key
                      ? "Stored — leave blank to keep it"
                      : form.provider_type === "claude_cli"
                        ? "From `claude setup-token` — blank uses the host's own login"
                        : form.provider_type === "anthropic"
                          ? "sk-ant-… from platform.claude.com (required)"
                          : "Leave blank for an unauthenticated local server"
                  }
                />
                {form.provider_type === "claude_cli" && (
                  <p className="mt-1 text-xs text-slate-400">
                    Billed to your Claude subscription rather than API credits.
                    LabDog removes{" "}
                    <span className="font-mono">ANTHROPIC_API_KEY</span> from the
                    CLI&apos;s environment when a token is set, since it would
                    otherwise take precedence and quietly bill the API account
                    instead.
                  </p>
                )}
              </div>

              {/*
                Every field below is inert for the CLI backend, and one of
                them is actively misleading: the CLI reports no token usage,
                so recorded spend is always zero and a Monthly cap can never
                fire. An operator who set one would believe they were capped
                when they were not. The CLI also has no max-tokens flag, so
                that field does nothing either. Hidden rather than disabled —
                a greyed-out budget still reads as a budget.
              */}
              {form.provider_type === "claude_cli" ? (
                <p className="rounded-md border border-slate-700 bg-slate-900/60 px-3 py-2 text-xs text-slate-400">
                  Cost and token settings do not apply to this backend. A
                  subscription is billed flat rather than per token, and the CLI
                  reports no usage, so LabDog cannot track spend for it — the
                  money budgets under Settings will not act on it either. The
                  per-session iteration, command, and wall-clock caps still
                  apply.
                </p>
              ) : (
              <>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <div className="flex items-center gap-1.5">
                    <Label htmlFor="input_cost">Input {currency}/M tokens</Label>
                    <InfoPopover title="Input cost per million tokens">
                      What the provider charges for the tokens you send it —
                      the prompt, the conversation so far, and every tool
                      result read back on each turn. This is usually the larger
                      share of an investigation&apos;s cost, because the
                      transcript is re-sent every turn.
                    </InfoPopover>
                  </div>
                  <Input
                    id="input_cost"
                    value={form.input_cost_per_mtok}
                    onChange={(e) =>
                      setForm({ ...form, input_cost_per_mtok: e.target.value })
                    }
                  />
                </div>
                <div>
                  <div className="flex items-center gap-1.5">
                    <Label htmlFor="output_cost">Output {currency}/M tokens</Label>
                    <InfoPopover title="Output cost per million tokens">
                      What the provider charges for the tokens it generates —
                      the assistant&apos;s replies and the commands it decides
                      to run. Usually several times the input rate per token,
                      but far fewer tokens, so it is often the smaller half of
                      the bill.
                    </InfoPopover>
                  </div>
                  <Input
                    id="output_cost"
                    value={form.output_cost_per_mtok}
                    onChange={(e) =>
                      setForm({ ...form, output_cost_per_mtok: e.target.value })
                    }
                  />
                </div>
              </div>
              <p className="-mt-2 text-xs text-slate-400">
                Rates are entered by hand — an OpenAI-compatible endpoint cannot
                report its own pricing. Enter them in {currency}; LabDog never
                converts between currencies. Leave both at 0 for a self-hosted
                model, which makes the money budgets a no-op for it.
              </p>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <div className="flex items-center gap-1.5">
                    <Label htmlFor="max_tokens">Max tokens per turn</Label>
                    <InfoPopover title="Max tokens per turn">
                      The ceiling on a single reply. It bounds one message, not
                      a whole session — the session caps under Settings do
                      that. Too low and long answers get cut off mid-sentence;
                      the model must also fit any commands it wants to run
                      inside this budget.
                    </InfoPopover>
                  </div>
                  <Input
                    id="max_tokens"
                    value={form.max_tokens}
                    onChange={(e) => setForm({ ...form, max_tokens: e.target.value })}
                  />
                </div>
                <div>
                  <div className="flex items-center gap-1.5">
                    <Label htmlFor="monthly_budget">Monthly cap ({currency})</Label>
                    <InfoPopover title="Monthly cap">
                      A ceiling for this provider alone, on top of the global
                      daily and monthly budgets in Settings. Useful when a free
                      local model and a paid one are both configured and you
                      want to bound only the paid one. 0 means no per-provider
                      limit. Priced at 0? Then this never triggers — the token
                      and iteration caps still apply.
                    </InfoPopover>
                  </div>
                  <Input
                    id="monthly_budget"
                    value={form.monthly_budget}
                    onChange={(e) =>
                      setForm({ ...form, monthly_budget: e.target.value })
                    }
                  />
                </div>
              </div>
              </>
              )}

              <label className="flex items-center gap-2 text-sm text-slate-300">
                <input
                  type="checkbox"
                  checked={form.is_default}
                  onChange={(e) => setForm({ ...form, is_default: e.target.checked })}
                />
                Use as the default provider
              </label>

              <label className="flex items-start gap-2 text-sm text-slate-300">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={form.allow_cloud_egress}
                  onChange={(e) =>
                    setForm({ ...form, allow_cloud_egress: e.target.checked })
                  }
                />
                <span>
                  Allow this provider to receive host data off my network
                  <span className="block text-xs text-slate-400">
                    Required for any hosted provider, and only takes effect when{" "}
                    <span className="font-mono">ai.allow_cloud_providers</span> is
                    also on.
                  </span>
                </span>
              </label>

              {error && <p className="text-sm text-red-400">{error}</p>}
            </div>

            <DialogFooter>
              <Button variant="ghost" onClick={() => setDialogOpen(false)}>
                Cancel
              </Button>
              <Button onClick={() => save.mutate()} disabled={save.isPending}>
                {save.isPending ? "Saving…" : "Save"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      <UsagePanel />

      {isLoading && <div className="py-8 text-center text-slate-400">Loading…</div>}

      {!isLoading && providers?.length === 0 && (
        <div className="py-8 text-center text-slate-400">
          No providers configured yet.
        </div>
      )}

      {!isLoading && providers && providers.length > 0 && (
        <div className="rounded-lg border border-slate-700 bg-slate-900">
          <Table>
            <TableHeader>
              <TableRow className="border-slate-700">
                <TableHead>Name</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Model</TableHead>
                <TableHead>Pricing</TableHead>
                <TableHead>Data</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {providers.map((provider) => {
                const result = testResults[provider.id]
                return (
                  <TableRow key={provider.id} className="border-slate-700">
                    <TableCell className="font-medium text-white">
                      <div className="flex items-center gap-2">
                        {provider.name}
                        {provider.is_default && (
                          <Badge className="bg-blue-600 text-white">default</Badge>
                        )}
                        {!provider.enabled && (
                          <Badge className="bg-slate-600 text-slate-300">disabled</Badge>
                        )}
                      </div>
                      {result && (
                        <p
                          className={`mt-1 text-xs ${
                            result.ok ? "text-green-400" : "text-red-400"
                          }`}
                        >
                          {result.message}
                        </p>
                      )}
                    </TableCell>
                    <TableCell className="text-xs text-slate-400">
                      {TYPE_LABEL[provider.provider_type]}
                      {provider.base_url && (
                        <span className="block font-mono">{provider.base_url}</span>
                      )}
                    </TableCell>
                    <TableCell className="font-mono text-xs text-slate-300">
                      {provider.model}
                    </TableCell>
                    <TableCell className="text-xs text-slate-400">
                      {provider.input_cost_per_mtok === 0 &&
                      provider.output_cost_per_mtok === 0 ? (
                        "free"
                      ) : (
                        <>
                          ${provider.input_cost_per_mtok} in / $
                          {provider.output_cost_per_mtok} out per M
                        </>
                      )}
                    </TableCell>
                    <TableCell>
                      {provider.sends_data_offsite ? (
                        <Badge className="bg-amber-600 text-white">off-site</Badge>
                      ) : (
                        <Badge className="bg-green-600 text-white">local</Badge>
                      )}
                    </TableCell>
                    <TableCell>
                      <div className="flex justify-end gap-1">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => test.mutate(provider.id)}
                          disabled={test.isPending}
                        >
                          Test
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => openEdit(provider)}>
                          Edit
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          className="text-red-400"
                          onClick={() => remove.mutate(provider.id)}
                        >
                          Delete
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  )
}
