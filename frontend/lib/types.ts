export type FirewallBackend = "nftables" | "firewalld" | "ufw" | "unknown"
export type SyncStatus = "pending" | "in_sync" | "out_of_sync" | "unknown" | "error"
export type GitOpsStatus = "disconnected" | "synced" | "error" | "importing"

export interface HostGroup {
  id: number; name: string; description: string | null; priority: number
  created_at: string; updated_at: string
  gitops_enabled: boolean
  gitops_status: GitOpsStatus | null
  gitops_error_message: string | null
  gitops_last_import_at: string | null
  gitops_file_path: string | null
  git_repository_id: number | null
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

export interface GitRepository {
  id: number
  name: string
  url: string
  branch: string
  auth_type: "ssh_key" | "https_token"
  ssh_key_id: number | null
  webhook_secret: string | null
  last_commit_sha: string | null
  last_sync_at: string | null
  created_at: string
  updated_at: string
}

export interface GitRepoCreate {
  name: string
  url: string
  branch?: string
  auth_type: "ssh_key" | "https_token"
  ssh_key_id?: number | null
  https_token?: string | null
  webhook_secret?: string | null
}

export interface GitRepoUpdate {
  name?: string
  url?: string
  branch?: string
  auth_type?: string
  ssh_key_id?: number | null
  https_token?: string | null
  webhook_secret?: string | null
}

export interface GitOpsStatusResponse {
  gitops_enabled: boolean
  git_repository_id: number | null
  gitops_file_path: string | null
  gitops_status: string
  gitops_error_message: string | null
  gitops_last_import_at: string | null
}

export interface FirewallRule {
  id: number
  group_id: number
  action: string
  protocol: string
  direction: string
  source_cidr: string | null
  destination_cidr: string | null
  port_start: number | null
  port_end: number | null
  comment: string | null
  priority: number
  is_system: boolean
  created_at: string
  updated_at: string
}
