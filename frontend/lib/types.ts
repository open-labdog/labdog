export type FirewallBackend = "nftables" | "iptables" | "unknown"
export type SyncStatus = "pending" | "in_sync" | "out_of_sync" | "unknown" | "error"
export type GitOpsStatus = "disconnected" | "synced" | "error" | "importing"

export interface HostGroup {
  id: number; name: string; description: string | null; category: string | null; priority: number
  input_policy: string | null; output_policy: string | null
  created_at: string; updated_at: string
  gitops_enabled: boolean
  gitops_status: GitOpsStatus | null
  gitops_error_message: string | null
  gitops_last_import_at: string | null
  gitops_file_path: string | null
  git_repository_id: number | null
}

export interface ModuleCounts {
  firewall: number
  hosts_file: number
  services: number
  users: number
  cron: number
  packages: number
  resolver: number
  ca_certs: number
}

export interface GroupSummary {
  id: number; name: string; description: string | null; category: string | null; priority: number
  gitops_enabled: boolean; gitops_status: string | null
  created_at: string | null; updated_at: string | null
  host_count: number
  has_shared_hosts: boolean
  module_counts: ModuleCounts
}

export interface ChainPolicies {
  input: "accept" | "drop"
  output: "accept" | "drop"
  input_source_group_id: number | null
  input_source_group_name: string | null
  output_source_group_id: number | null
  output_source_group_name: string | null
}
export interface Host {
  id: number; hostname: string; ip_address: string; ssh_port: number; ssh_user: string
  firewall_backend: FirewallBackend; sync_status: SyncStatus
  labdog_source_ip: string | null
  drift_check_enabled: boolean; last_sync_at: string | null
  last_drift_check_at: string | null; ssh_key_id: number | null
  group_ids: number[]
  created_at: string; updated_at: string
  os_codename: string | null
  os_pretty_name: string | null
  os_family: string | null
  default_nic: string | null
  kernel_version: string | null
  kernel_release: string | null
  os_facts_collected_at: string | null
}

export interface HostSummary extends Host {
  override_counts: ModuleCounts
}

export interface WorkflowLastRun {
  id: number
  status: string
  started_at: string | null
  completed_at: string | null
  created_at: string | null
}

export interface WorkflowSummary {
  id: number
  group_id: number
  group_name: string
  group_category: string | null
  batch_size: number
  schedule_cron: string | null
  pre_update_snapshot: boolean
  auto_rollback: boolean
  auto_reboot: boolean
  enabled: boolean
  host_count: number
  last_run: WorkflowLastRun | null
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

export interface FirewallRule {
  id: number
  group_id: number
  action: string
  protocol: string
  direction: string
  source_cidr: string | null
  destination_cidr: string | null
  source_host_id: number | null
  destination_host_id: number | null
  port_start: number | null
  port_end: number | null
  comment: string | null
  priority: number
  is_system: boolean
  created_at: string
  updated_at: string
}

export interface EffectiveFirewallRule {
  action: string
  protocol: string
  direction: string
  source_cidr: string | null
  destination_cidr: string | null
  source_host_id: number | null
  destination_host_id: number | null
  source_host_name: string | null
  destination_host_name: string | null
  port_start: number | null
  port_end: number | null
  comment: string | null
  priority: number
  is_system: boolean
  group_id: number | null
  group_name: string | null
  rule_id: number | null
  group_priority: number | null
  source: "group" | "host" | "system"
  source_id: number | null
  source_name: string | null
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
  unit_content?: string | null
  deploy_mode: "full" | "override"
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
  unit_content?: string | null
  deploy_mode: "full" | "override"
}

export interface HostsEntry {
  id: number
  ip_address: string | null
  hostname: string | null
  host_ref_id: number | null
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
  is_system: boolean
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
  action_key: string
  action_parameters: Record<string, string>
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

// ── CA certificates ─────────────────────────────────────────────────────────

export interface CACertRule {
  id: number
  group_id: number | null
  host_id: number | null
  name: string
  fingerprint_sha256: string
  subject: string | null
  issuer: string | null
  not_before: string | null
  not_after: string | null
  state: "present" | "absent"
  comment: string | null
}

export interface EffectiveCACert {
  name: string
  fingerprint_sha256: string
  subject: string | null
  issuer: string | null
  not_before: string | null
  not_after: string | null
  state: "present" | "absent"
  pem_content: string
  source: "group" | "host"
  source_id: number
  source_name: string
}

export interface CACertActionRun {
  id: number
  host_id: number
  hostname: string | null
  group_id: number | null
  status: "pending" | "running" | "success" | "failed" | "cancelled"
  started_at: string | null
  completed_at: string | null
  ansible_output: string | null
  error_message: string | null
  triggered_by_user_id: number | null
  created_at: string
}

export interface ScanConfig {
  id: number
  name: string
  cidrs: string[]
  ssh_key_id: number
  ssh_port: number
  default_group_ids: number[]
  interval_minutes: number | null
  cron_expression: string | null
  enabled: boolean
  auto_add: boolean
  last_run_at: string | null
  last_run_status: string | null
  last_run_hosts_added: number
  last_run_hosts_pending: number
  last_run_error: string | null
  created_at: string
  updated_at: string
  pending_count: number | null
}

export interface PendingSummary {
  total: number
}

export interface PendingHost {
  id: number
  scan_config_id: number
  ip_address: string
  hostname: string | null
  ssh_verified: boolean
  ssh_error: string | null
  discovered_at: string
}

export interface PendingHostFleet {
  id: number
  scan_config_id: number
  scan_config_name: string
  ip_address: string
  hostname: string | null
  ssh_verified: boolean
  ssh_error: string | null
  discovered_at: string
}

export interface ActionParameter {
  key: string
  label: string
  type: "string" | "int" | "bool" | "choice"
  default: unknown
  required: boolean
  choices: string[] | null
  help_text: string | null
}

export interface ActionDefinition {
  key: string
  name: string
  description: string
  icon: string
  version: string
  estimated_duration: string
  destructive: boolean
  supports_group: boolean
  supports_host: boolean
  parameters: ActionParameter[]
}

export interface ActionHostRun {
  id: number
  action_run_id: number
  host_id: number
  status: string
  started_at: string | null
  finished_at: string | null
  exit_code: number | null
  error_message: string | null
  snapshot_name: string | null
}

export interface ActionRun {
  id: number
  action_key: string
  action_version: string
  host_id: number | null
  group_id: number | null
  parameters: Record<string, unknown>
  parallelism: number
  status: string
  triggered_by_user_id: number | null
  started_at: string | null
  finished_at: string | null
  error_message: string | null
  created_at: string
  host_runs: ActionHostRun[]
}
