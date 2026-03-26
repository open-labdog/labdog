export type FirewallBackend = "nftables" | "firewalld" | "ufw" | "unknown"
export type SyncStatus = "pending" | "in_sync" | "out_of_sync" | "unknown" | "error"
export type GitOpsStatus = "disconnected" | "synced" | "error" | "importing"

export interface HostGroup {
  id: number; name: string; description: string | null; category: string | null; priority: number
  created_at: string; updated_at: string
  gitops_enabled: boolean
  gitops_status: GitOpsStatus | null
  gitops_error_message: string | null
  gitops_last_import_at: string | null
  gitops_file_path: string | null
  git_repository_id: number | null
}
export interface Host {
  id: number; hostname: string; ip_address: string; ssh_port: number; ssh_user: string
  firewall_backend: FirewallBackend; sync_status: SyncStatus
  barricade_source_ip: string | null
  drift_check_enabled: boolean; last_sync_at: string | null
  last_drift_check_at: string | null; ssh_key_id: number | null
  group_ids: number[]
  created_at: string; updated_at: string
}

export interface ModuleCurrentState {
  module_type: string
  sync_status: string
  collected_state: unknown
  collected_at: string | null
  drift_check_enabled: boolean
  error_message: string | null
}
export interface SSHKey {
  id: number; name: string; public_key: string | null
  ssh_user: string; is_default: boolean; created_at: string
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

export interface ProxmoxNode {
  id: number
  name: string
  api_url: string
  token_id: string
  verify_ssl: boolean
  created_at: string
  updated_at: string
}

export interface VMMapping {
  id: number
  host_id: number
  proxmox_node_id: number
  pve_node_name: string
  vmid: number
  vm_name: string
  discovered_at: string
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

export interface AdminUser {
  id: number
  email: string
  is_active: boolean
  is_superuser: boolean
  is_verified: boolean
  created_at: string
  updated_at: string
}

export interface ServiceRule {
  id: number
  service_name: string
  state: "running" | "stopped"
  enabled: boolean
  priority: number
  comment: string | null
  group_id: number | null
  host_id: number | null
  created_at: string
  updated_at: string
}

export interface EffectiveService {
  service_name: string
  state: string
  enabled: boolean
  source: "group" | "host"
  source_id: number
  source_name: string
}

export interface HostsEntry {
  id: number
  ip_address: string
  hostname: string
  aliases: string[]
  comment: string | null
  priority: number
  is_system: boolean
  group_id: number | null
  host_id: number | null
  created_at: string
  updated_at: string
}

export interface EffectiveHostsEntry {
  ip_address: string
  hostname: string
  aliases: string[]
  comment: string | null
  is_system: boolean
  source: "group" | "host" | "system"
  source_id: number
  source_name: string
}

export interface LiveService {
  unit: string
  load_state: string
  active_state: string
  sub_state: string
  description: string
  is_managed: boolean
  is_protected: boolean
}

export interface ServiceCommandResult {
  success: boolean
  exit_code: number
  stdout: string
  stderr: string
  service_name: string
  action: string
  is_protected: boolean
}

export interface LinuxUser {
  id: number
  username: string
  uid: number | null
  shell: string
  home_dir: string | null
  state: "present" | "absent"
  comment: string | null
  sudo_rule: string | null
  authorized_keys: string[]
  supplementary_groups: string[]
  priority: number
  group_id: number | null
  host_id: number | null
  created_at: string
  updated_at: string
}

export interface LinuxGroup {
  id: number
  groupname: string
  gid: number | null
  state: "present" | "absent"
  priority: number
  group_id: number | null
  host_id: number | null
  created_at: string
  updated_at: string
}

export interface EffectiveLinuxUser {
  username: string
  uid: number | null
  shell: string
  home_dir: string | null
  state: "present" | "absent"
  sudo_rule: string | null
  authorized_keys: string[]
  supplementary_groups: string[]
  source: "group" | "host"
  source_id: number
  source_name: string
}

export interface EffectiveLinuxGroup {
  groupname: string
  gid: number | null
  state: "present" | "absent"
  source: "group" | "host"
  source_id: number
  source_name: string
}

export interface CronJob {
  id: number
  name: string
  user: string
  schedule: string
  command: string
  environment: Record<string, string>
  state: "present" | "absent"
  priority: number
  comment: string | null
  group_id: number | null
  host_id: number | null
  created_at: string
  updated_at: string
}

export interface EffectiveCronJob {
  name: string
  user: string
  schedule: string
  command: string
  environment: Record<string, string>
  state: "present" | "absent"
  priority: number
  comment: string | null
  source: "group" | "host"
  source_id: number
  source_name: string
}

export interface PackageRule {
  id: number
  group_id: number | null
  host_id: number | null
  package_name: string
  version: string | null
  state: "present" | "absent" | "latest"
  package_manager: "auto" | "apt" | "dnf" | "yum"
  priority: number
  comment: string | null
  hold: boolean
}

export interface PackageRepository {
  id: number
  group_id: number
  name: string
  url: string
  key_url: string | null
  repo_type: "apt" | "yum"
  distribution: string | null
  components: string | null
  state: "present" | "absent"
}

export interface EffectivePackage {
  package_name: string
  version: string | null
  state: "present" | "absent" | "latest"
  package_manager: "auto" | "apt" | "dnf" | "yum"
  priority: number
  hold: boolean
  source: string
  source_id: number
  source_name: string
}

export interface ResolverConfig {
  id: number
  group_id: number | null
  host_id: number | null
  nameservers: string[]
  search_domains: string[]
  options: Record<string, number | string>
  resolver_type: "resolv_conf" | "systemd_resolved" | "networkmanager"
  dns_over_tls: boolean
}

export interface EffectiveResolverConfig extends ResolverConfig {
  source: "group" | "host"
  source_id: number
  source_name: string
}

export interface UpdateWorkflow {
  id: number
  group_id: number
  batch_size: number
  schedule_cron: string | null
  pre_update_snapshot: boolean
  auto_rollback: boolean
  verification_prompt: string | null
  auto_reboot: boolean
  enabled: boolean
  created_at: string
  updated_at: string
}

export interface WorkflowRun {
  id: number
  workflow_id: number
  status: string
  started_at: string | null
  completed_at: string | null
  triggered_by: number | null
  created_at: string
}

export interface WorkflowHostRun {
  id: number
  host_id: number
  hostname: string
  step: string
  status: string
  snapshot_name: string | null
  error_message: string | null
  started_at: string | null
  completed_at: string | null
}

export interface WorkflowRunDetail extends WorkflowRun {
  host_runs: WorkflowHostRun[]
}
