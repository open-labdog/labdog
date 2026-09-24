"use client"

import { useEffect, useState } from "react"
import { useApiMutation } from "@/lib/mutations"
import { apiFetch } from "@/lib/api"
import { Banner, Field, Modal } from "@/components/ld"
import { GroupMultiSelect } from "@/components/group-multi-select"
import type { Host, HostGroup, SSHKey } from "@/lib/types"

export function EditHostDialog({
  hostId,
  host,
  sshKeys,
  groups,
  onClose,
}: {
  hostId: number
  host: Host
  sshKeys: SSHKey[] | undefined
  groups: HostGroup[] | undefined
  onClose: () => void
}) {
  const [hostname, setHostname] = useState(host.hostname)
  const [ip, setIp] = useState(host.ip_address)
  const [sshPort, setSshPort] = useState(host.ssh_port)
  const [sshUser, setSshUser] = useState(host.ssh_user)
  const [sshKeyId, setSshKeyId] = useState<number | null>(host.ssh_key_id)
  const [groupIds, setGroupIds] = useState<number[]>(host.group_ids ?? [])

  useEffect(() => {
    setHostname(host.hostname)
    setIp(host.ip_address)
    setSshPort(host.ssh_port)
    setSshUser(host.ssh_user)
    setSshKeyId(host.ssh_key_id)
    setGroupIds(host.group_ids ?? [])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [host.id])

  const saveMutation = useApiMutation({
    mutationFn: (data: Record<string, unknown>) => apiFetch(`/api/hosts/${hostId}`, { method: "PUT", body: JSON.stringify(data) }),
    invalidateKeys: [["host", hostId], ["host-effective-rules", hostId]],
    onSuccess: onClose,
  })

  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    saveMutation.mutate({ hostname, ip_address: ip, ssh_port: sshPort, ssh_user: sshUser, ssh_key_id: sshKeyId, group_ids: groupIds })
  }

  return (
    <Modal
      title="Edit host"
      w={520}
      onClose={onClose}
      onSubmit={onSubmit}
      footer={
        <>
          <button type="button" className="btn ml-auto" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={saveMutation.isPending}>{saveMutation.isPending ? "Saving…" : "Save changes"}</button>
        </>
      }
    >
      <Field label="hostname" htmlFor="edit-hostname">
        <input id="edit-hostname" className="inp mono" value={hostname} onChange={(e) => setHostname(e.target.value)} required />
      </Field>
      <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[1fr_100px]">
        <Field label="ip address" htmlFor="edit-ip">
          <input id="edit-ip" className="inp mono" value={ip} onChange={(e) => setIp(e.target.value)} required />
        </Field>
        <Field label="ssh port" htmlFor="edit-ssh-port">
          <input id="edit-ssh-port" type="number" min={1} max={65535} className="inp mono num" value={sshPort} onChange={(e) => setSshPort(Number(e.target.value))} required />
        </Field>
      </div>
      <Field label="ssh user" htmlFor="edit-ssh-user">
        <input id="edit-ssh-user" className="inp mono" value={sshUser} onChange={(e) => setSshUser(e.target.value)} required />
      </Field>
      <Field as="div" label="ssh key">
        <select id="edit-ssh-key" className="inp" value={sshKeyId ?? ""} onChange={(e) => setSshKeyId(e.target.value ? Number(e.target.value) : null)}>
          <option value="">No SSH key</option>
          {sshKeys?.map((key) => <option key={key.id} value={key.id}>{key.name}{key.is_default ? " (default)" : ""}</option>)}
        </select>
      </Field>

      {groups && groups.length > 0 && <GroupMultiSelect groups={groups} selected={groupIds} onChange={setGroupIds} />}

      {saveMutation.error && <Banner tone="danger">{saveMutation.error.message}</Banner>}
    </Modal>
  )
}
