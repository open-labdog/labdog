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

  return s
}
