import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"

/**
 * The `scheduling.timezone` setting: the zone LabDog reads every schedule's
 * cron expression in. Shares the settings page's query, so a change saved
 * there shows up here without a reload.
 */
export function useScheduleTimezone(enabled = true): string | undefined {
  const { data } = useQuery<{ key: string; value: string }[]>({
    queryKey: ["settings"],
    queryFn: () => apiFetch("/api/settings"),
    enabled,
  })
  return data?.find((s) => s.key === "scheduling.timezone")?.value
}

/**
 * An instant as wall-clock time in `timeZone`, weekday included so a weekly
 * schedule reads at a glance. Falls back to the browser's zone if its
 * time-zone data lacks the name, rather than throwing mid-render.
 */
export function formatInZone(iso: string, timeZone: string): string {
  const opts: Intl.DateTimeFormatOptions = {
    weekday: "short",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }
  try {
    return new Date(iso).toLocaleString(undefined, { ...opts, timeZone })
  } catch {
    return new Date(iso).toLocaleString(undefined, opts)
  }
}
