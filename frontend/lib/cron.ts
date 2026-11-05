const DAYS = ["Sundays", "Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays"]
const SHORT = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

const at = (h: string, m: string) => `${Number(h)}:${m.padStart(2, "0")}`

/** A weekday field — `0`, `1-5`, `1,3,5` — in words, or null when it is
 *  something this reader does not know (names, steps). */
function weekdays(field: string): string | null {
  if (/^[0-7]$/.test(field)) return DAYS[Number(field)]
  const range = field.match(/^([0-7])-([0-7])$/)
  if (range) {
    const [a, b] = [Number(range[1]), Number(range[2])]
    if (a === 1 && b === 5) return "Weekdays"
    return `${SHORT[a]}–${SHORT[b]}`
  }
  if (/^[0-7](,[0-7])+$/.test(field)) return field.split(",").map((d) => SHORT[Number(d)]).join(", ")
  return null
}

/**
 * A cron expression in words, for the common shapes: every N minutes,
 * hourly, every N hours, daily, on given weekdays, monthly. Anything else
 * comes back unchanged — callers compare against the input to tell.
 */
export function cronToHuman(schedule: string): string {
  const s = schedule.trim()
  if (s === "* * * * *") return "Every minute"
  if (s === "0 * * * *") return "Every hour"
  if (s === "0 0 * * *") return "Every day at midnight"

  // 0 N * * *  => Every day at N:00
  const dailyMatch = s.match(/^0\s+(\d+)\s+\*\s+\*\s+\*$/)
  if (dailyMatch) return `Every day at ${dailyMatch[1]}:00`

  // */N * * * *  => Every N minutes
  const everyNMin = s.match(/^\*\/(\d+)\s+\*\s+\*\s+\*\s+\*$/)
  if (everyNMin) return `Every ${everyNMin[1]} minutes`

  const f = s.split(/\s+/)
  if (f.length !== 5) return s
  const [min, hour, dom, mon, dow] = f
  if (!/^\d{1,2}$/.test(min) || mon !== "*") return s

  // M * * * *  => Every hour at :MM
  if (hour === "*" && dom === "*" && dow === "*") return `Every hour at :${min.padStart(2, "0")}`

  // M */N * * *  => Every N hours
  const everyNHours = hour.match(/^\*\/(\d+)$/)
  if (everyNHours && dom === "*" && dow === "*") {
    return min === "0" ? `Every ${everyNHours[1]} hours` : `Every ${everyNHours[1]} hours at :${min.padStart(2, "0")}`
  }

  if (!/^\d{1,2}$/.test(hour)) return s
  // M H * * *  => Every day at H:MM
  if (dom === "*" && dow === "*") return `Every day at ${at(hour, min)}`
  // M H * * D  => Sundays at H:MM
  if (dom === "*") {
    const days = weekdays(dow)
    return days ? `${days} at ${at(hour, min)}` : s
  }
  // M H D * *  => Monthly on day D at H:MM
  if (/^\d{1,2}$/.test(dom) && dow === "*") return `Monthly on day ${dom} at ${at(hour, min)}`
  return s
}
