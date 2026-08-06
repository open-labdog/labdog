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
import { apiFetch, ApiError } from "@/lib/api"
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
    "The Claude Code CLI installed on the LabDog host. Single-shot only: it can write reports but cannot run tools.",
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
  monthly_budget_usd: string
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
  monthly_budget_usd: "0",
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
      monthly_budget_usd: String(provider.monthly_budget_usd),
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
        monthly_budget_usd: Number(form.monthly_budget_usd),
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
                  onValueChange={(v) =>
                    setForm({ ...form, provider_type: v as AIProviderType })
                  }
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
              </div>

              {form.provider_type !== "claude_cli" && (
                <div>
                  <Label htmlFor="base_url">Base URL</Label>
                  <Input
                    id="base_url"
                    value={form.base_url}
                    onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                    placeholder="http://localhost:11434/v1"
                  />
                </div>
              )}

              <div>
                <Label htmlFor="model">Model</Label>
                <Input
                  id="model"
                  value={form.model}
                  onChange={(e) => setForm({ ...form, model: e.target.value })}
                  placeholder={
                    form.provider_type === "anthropic" ? "claude-opus-5" : "llama3.1:8b"
                  }
                />
              </div>

              {form.provider_type !== "claude_cli" && (
                <div>
                  <Label htmlFor="api_key">API key</Label>
                  <Input
                    id="api_key"
                    type="password"
                    value={form.api_key}
                    onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                    placeholder={
                      editing?.has_api_key
                        ? "Stored — leave blank to keep it"
                        : "Leave blank for an unauthenticated local server"
                    }
                  />
                </div>
              )}

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <Label htmlFor="input_cost">Input $/M tokens</Label>
                  <Input
                    id="input_cost"
                    value={form.input_cost_per_mtok}
                    onChange={(e) =>
                      setForm({ ...form, input_cost_per_mtok: e.target.value })
                    }
                  />
                </div>
                <div>
                  <Label htmlFor="output_cost">Output $/M tokens</Label>
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
                report its own pricing. Leave both at 0 for a self-hosted model,
                which makes the USD budgets a no-op for it.
              </p>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <Label htmlFor="max_tokens">Max tokens per turn</Label>
                  <Input
                    id="max_tokens"
                    value={form.max_tokens}
                    onChange={(e) => setForm({ ...form, max_tokens: e.target.value })}
                  />
                </div>
                <div>
                  <Label htmlFor="monthly_budget">Monthly cap (USD)</Label>
                  <Input
                    id="monthly_budget"
                    value={form.monthly_budget_usd}
                    onChange={(e) =>
                      setForm({ ...form, monthly_budget_usd: e.target.value })
                    }
                  />
                </div>
              </div>

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
