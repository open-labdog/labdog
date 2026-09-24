"use client"

import { Panel } from "@/components/ld"
import { HostMetricsSection } from "@/components/host-metrics-section"

export function MetricsTab({ hostId }: { hostId: number }) {
  return (
    <div className="scroll flex flex-1 flex-col gap-3 p-3.5">
      <Panel title="resource usage" pad={11}>
        <HostMetricsSection hostId={hostId} />
      </Panel>
    </div>
  )
}
