import type { Tone } from "@/lib/fleet"

/* Status vocabulary — one mapping, used everywhere a tone is painted. */
const TONE: Record<Tone, string> = {
  ok: "--ok",
  warn: "--warn",
  danger: "--danger",
  sync: "--sync",
  idle: "--idle",
  hold: "--hold",
  accent: "--accent",
  add: "--add",
  del: "--del",
}

const base = (t: Tone | undefined) => TONE[t ?? "idle"] ?? "--idle"

export const toneVar = (t?: Tone) => `var(${base(t)})`
export const toneSoft = (t?: Tone) => `var(${base(t)}-soft)`
/* Text painted ON a tone's tint uses that tone's ink grade — the tone itself
   is tuned for the page surface, not for its own tint. */
export const toneInk = (t?: Tone) => `var(${base(t)}-ink)`
/* Paint a tint and its text together — never a raw tone on its own tint. */
export const tint = (t?: Tone) => ({ background: toneSoft(t), color: toneInk(t) })

export type { Tone }
