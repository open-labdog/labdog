"use client"

import { ActionsTab } from "@/components/actions-tab"
import { ScheduledActionsSection } from "@/components/scheduled-actions/scheduled-actions-section"
import type { Host } from "@/lib/types"

export function ActivityTab({ hostId, host, view }: { hostId: number; host: Host | undefined; view: "actions" | "schedules" }) {
  if (view === "schedules") return <ScheduledActionsSection scope="host" targetId={hostId} />
  return <ActionsTab scope="host" targetId={hostId} host={host} />
}
