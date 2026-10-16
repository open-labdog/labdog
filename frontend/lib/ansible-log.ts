/**
 * Reads the structure back out of Ansible's plain-text output, so the run
 * log can be coloured. Ansible runs with ANSIBLE_NOCOLOR=1 and older rows
 * have their ANSI stripped, so the colour has to come from the words: the
 * `ok:` / `changed:` / `fatal:` that open a line, the `TASK [...]` headers,
 * `PLAY RECAP`, and LabDog's own `[preflight]` / `[cleanup]` lines.
 *
 * Every line keeps its exact text. Joining `text` with "\n" gives back the
 * input, which is what lets the log colour the text without changing what
 * a copy-and-paste of it produces.
 */

export type LineKind =
  | "play"
  | "task"
  | "recap-head"
  | "recap-row"
  | "ok"
  | "changed"
  | "skipping"
  | "included"
  | "failed"
  | "fatal"
  | "unreachable"
  | "rescued"
  | "ignored"
  | "error"
  | "warning"
  | "labdog"
  | "labdog-failed"
  | "section"
  | "plain"

export interface LogLine {
  text: string
  kind: LineKind
  /** Part of the block a line above opened (a `=> {` result, an
   *  `[ERROR]` explanation): painted like that line, a step quieter. */
  cont?: boolean
}

/** The kinds "jump to first failure" looks for. Only the line that opens a
 *  block counts, so one failure is one stop. */
export const FAILURE_KINDS: ReadonlySet<LineKind> = new Set(["error", "fatal", "failed", "unreachable", "labdog-failed"])

const STATUS = /^(ok|changed|skipping|failed|fatal|unreachable|rescued|ignored|included): /
const PLAY = /^PLAY \[/
const TASK = /^(TASK|RUNNING HANDLER) \[/
const RECAP = /^PLAY RECAP\b/
const RECAP_ROW = /^\S.*\s:\s+ok=\d+/
const ERROR = /^\[ERROR\]:/
const WARNING = /^\[(DEPRECATION )?WARNING\]:/
// LabDog's own step lines: "[preflight] host reachable", "[cleanup] …".
const LABDOG = /^\[[a-z][a-z-]*\] /
const LABDOG_FAILED = /\b(status=failed|passed=False|success=False|failed|unreachable)\b/
// "=== Ansible output ===", and the per-host header the combined view adds.
const SECTION = /^={3,} .* ={3,}$/
const OPENS_BRACKET = /(=> |^ *)[{[]$/

export function parseAnsibleLog(text: string): LogLine[] {
  const out: LogLine[] = []
  // A block opened by a line above: a JSON result until its closing
  // bracket at column 0; a [WARNING] until a blank line; an [ERROR] — its
  // message, a blank line, then the source excerpt Ansible quotes — until
  // the next line Ansible itself starts (a task, a result, the recap).
  let block: { kind: LineKind; until: "bracket" | "blank" | "structure" } | null = null
  let inRecap = false

  for (const line of text.split("\n")) {
    if (block?.until === "bracket") {
      out.push({ text: line, kind: block.kind, cont: true })
      if (line === "}" || line === "]") block = null
      continue
    }
    if (block) {
      const structural = STATUS.test(line) || TASK.test(line) || PLAY.test(line) || RECAP.test(line) || LABDOG.test(line) || SECTION.test(line)
      if (structural || (block.until === "blank" && line.trim() === "")) {
        block = null
      } else {
        out.push({ text: line, kind: block.kind, cont: true })
        continue
      }
    }

    let kind: LineKind = "plain"
    const status = STATUS.exec(line)
    if (RECAP.test(line)) {
      kind = "recap-head"
      inRecap = true
    } else if (inRecap && RECAP_ROW.test(line)) {
      kind = "recap-row"
    } else if (PLAY.test(line)) {
      kind = "play"
    } else if (TASK.test(line)) {
      kind = "task"
    } else if (status) {
      kind = status[1] as LineKind
      if (OPENS_BRACKET.test(line)) block = { kind, until: "bracket" }
    } else if (line === "...ignoring") {
      kind = "ignored"
    } else if (ERROR.test(line)) {
      kind = "error"
      block = { kind, until: "structure" }
    } else if (WARNING.test(line)) {
      kind = "warning"
      block = { kind, until: "blank" }
    } else if (LABDOG.test(line)) {
      kind = LABDOG_FAILED.test(line) ? "labdog-failed" : "labdog"
    } else if (SECTION.test(line)) {
      kind = "section"
    }
    if (kind !== "recap-head" && kind !== "recap-row" && line.trim() !== "") inRecap = false
    out.push({ text: line, kind })
  }
  return out
}
