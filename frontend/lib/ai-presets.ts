import type { AIProviderType } from "@/lib/types"

/**
 * Suggested models per provider type.
 *
 * These are starting points, not a closed list — the Model field stays free
 * text, because a self-hosted server can serve anything and new models ship
 * faster than LabDog releases.
 *
 * Rates are per million tokens and are filled in only where they are a
 * published, knowable number. For a self-hosted model the honest rate is
 * zero; for anything behind someone else's billing the honest answer is "look
 * it up", so those presets leave the fields alone rather than writing a figure
 * that will quietly go stale and then be trusted as if LabDog had checked it.
 */
export interface ModelPreset {
  /** The model identifier sent to the provider. */
  id: string
  /** Short human label for the picker. */
  label: string
  /** One line on when to reach for it. */
  hint: string
  /** Cost per million input tokens, or null when unknown to us. */
  input?: number
  /** Cost per million output tokens, or null when unknown to us. */
  output?: number
}

const ANTHROPIC_PRESETS: ModelPreset[] = [
  {
    id: "claude-opus-5",
    label: "Claude Opus 5",
    hint: "Most capable. Best for investigations that need real reasoning.",
    input: 5,
    output: 25,
  },
  {
    id: "claude-sonnet-5",
    label: "Claude Sonnet 5",
    hint: "Strong and cheaper than Opus. A good default for scheduled checks.",
    input: 3,
    output: 15,
  },
  {
    id: "claude-haiku-4-5",
    label: "Claude Haiku 4.5",
    hint: "Fastest and cheapest. Fine for narrow, well-scoped checks.",
    input: 1,
    output: 5,
  },
]

// Local servers: the model tag is whatever you pulled, and it costs nothing
// to run, so both rates stay at zero and the money budgets become a no-op.
const OPENAI_COMPAT_PRESETS: ModelPreset[] = [
  {
    id: "llama3.1:8b",
    label: "Llama 3.1 8B (Ollama)",
    hint: "Runs on modest hardware. Good starting point for a local setup.",
    input: 0,
    output: 0,
  },
  {
    id: "qwen2.5:14b",
    label: "Qwen 2.5 14B (Ollama)",
    hint: "Stronger reasoning than 8B if you have the VRAM for it.",
    input: 0,
    output: 0,
  },
  {
    id: "mistral-nemo",
    label: "Mistral Nemo (Ollama)",
    hint: "Solid all-rounder with a large context window.",
    input: 0,
    output: 0,
  },
  {
    id: "gpt-4o-mini",
    label: "OpenAI gpt-4o-mini",
    hint: "Hosted and paid — check current pricing and enter the rates yourself.",
  },
]

export const MODEL_PRESETS: Record<AIProviderType, ModelPreset[]> = {
  anthropic: ANTHROPIC_PRESETS,
  openai_compat: OPENAI_COMPAT_PRESETS,
  // The CLI uses whatever model it is already configured with, so there is
  // nothing for LabDog to choose here.
  claude_cli: [],
}
