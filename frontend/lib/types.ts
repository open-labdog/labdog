export type FirewallBackend = "nftables" | "firewalld" | "ufw" | "unknown"
export type SyncStatus = "pending" | "in_sync" | "out_of_sync" | "unknown" | "error"

export interface HostGroup {
  id: number; name: string; description: string | null; priority: number
  created_at: string; updated_at: string
}
export interface Host {
  id: number; hostname: string; ip_address: string; ssh_port: number
  firewall_backend: FirewallBackend; sync_status: SyncStatus
  drift_check_enabled: boolean; last_sync_at: string | null
  last_drift_check_at: string | null; ssh_key_id: number | null
  created_at: string; updated_at: string
}
export interface SSHKey {
  id: number; name: string; public_key: string | null
  is_default: boolean; created_at: string
}
