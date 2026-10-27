import type { AIAutonomyLevel } from "@/lib/types"

/**
 * Statuses where the owning Celery task has stopped for good.
 *
 * Mirrors TERMINAL_STATES in app/api/ai.py, which refuses to delete a
 * session that is still live — the run owns the row and is writing to it.
 * Disabling the button here means the operator learns that from the UI
 * rather than from a 409.
 *
 * `waiting_approval` is deliberately absent. No task is running, but the
 * session is not over either: a decision restarts it. Deleting it is
 * refused for the same reason cancelling it is offered.
 */
export const TERMINAL_STATES = new Set(["succeeded", "failed", "cancelled"])

/**
 * How a session that the operator did not start is labelled.
 *
 * `chat` is absent on purpose: a session you opened from this page needs
 * no explanation. The others arrive on their own — a scheduled check, or
 * a verify step an action ran after changing a host — and a list of
 * sessions nobody remembers starting is confusing without this.
 */
export const MODE_LABEL: Record<string, string> = {
  scheduled: "scheduled",
  verify: "verify",
  alert_investigation: "alert",
}

/**
 * How a session's host scope reads in one line.
 *
 * An empty list is not "no hosts" — it is a session created without a
 * target, which the tools treat as "refuse rather than roam". Saying "no
 * hosts" would suggest nothing was investigated; "no host selected" says
 * what the operator actually did.
 */
export function describeScope(names: string[]): string {
  if (names.length === 0) return "no host selected"
  if (names.length <= 2) return names.join(", ")
  return `${names[0]}, ${names[1]} +${names.length - 2} more`
}

export const AUTONOMY_HELP: Record<AIAutonomyLevel, string> = {
  read_only: "The assistant may only run commands that read state. Anything that would change a host is refused.",
  approval: "Reads run immediately. Anything that would change a host pauses the session and waits for you to approve or reject it.",
  full_auto: "The assistant may change hosts on its own. A denylist of destructive commands still applies.",
}
