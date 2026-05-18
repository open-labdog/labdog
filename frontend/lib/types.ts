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

export type GitAuthType = "none" | "ssh_key" | "https_token"

export interface GitRepository {
  id: number
  name: string
  url: string
  branch: string
  auth_type: GitAuthType
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
  ssh_key_id?: number | null
  https_token?: string | null
  webhook_secret?: string | null
}

export interface GitRepoUpdate {
  name?: string
  url?: string
  branch?: string
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

export type PackSourceType = "git" | "local"

export interface ActionPack {
  id: number
  name: string
  source_type: PackSourceType
  /** For source_type=git: FK to a GitRepository. Null for local packs. */
  git_repository_id: number | null
  /** Display name of the linked GitRepository (server-resolved). */
  git_repository_name: string | null
  /** Subpath within the git repo where the pack lives ("" = repo root). */
  path: string
  /** Absolute filesystem path for source_type=local. Null for git. */
  local_path: string | null
  /** Linear precedence ordering. Higher wins on action-key collisions.
   * Bundled is implicit at 0. */
  position: number
  enabled: boolean
  last_synced_at: string | null
  last_sync_status: "ok" | "failed" | null
  last_sync_error: string | null
  current_sha: string | null
  created_at: string
  updated_at: string
}

export interface ActionPackSyncResponse {
  success: boolean
  message: string
  current_sha: string | null
  last_synced_at: string | null
}

export interface ActionPackReorderRequest {
  /** Top-to-bottom display order. The first id wins on action-key
   * collisions (highest position). Must list every existing pack
   * exactly once; bundled is implicit and never appears here. */
  pack_ids: number[]
}

export interface ResolutionPack {
  pack_id: number | null
  pack_name: string
  position: number
}

export interface ContestedActionKey {
  action_key: string
  candidates: ResolutionPack[]
  current_winner: ResolutionPack
  /** Operator's explicit pin, or null when the winner came from
   * position-based default (no resolution row present). */
  resolution: ResolutionPack | null
  /** True when the live winner came from a freeze decision rather
   * than the position default — the UI surfaces a "needs your
   * decision" badge for these. */
  is_frozen: boolean
  decided_at: string | null
  decided_by_user_id: number | null
}

export interface ActionResolutionRequest {
  /** Null = bundled wins. */
  pack_id: number | null
}

// ---------------------------------------------------------------------------
// Repo onboarding scan / activate
// ---------------------------------------------------------------------------

export interface ScanError {
  file: string
  message: string
}

export interface DetectedPack {
  path: string
  name: string
  contributed_keys: string[]
  pack_yml_present: boolean
  errors: ScanError[]
}

export interface DetectedGitopsFile {
  path: string
  group_name: string | null
  errors: ScanError[]
}

export interface KeyOwner {
  key: string
  source: "bundled" | "db_pack"
  pack_name: string
  pack_id: number | null
}

export interface KeyConflict {
  key: string
  contributing_packs: string[]
}

export interface RepoScanResponse {
  packs: DetectedPack[]
  gitops_files: DetectedGitopsFile[]
  existing_key_winners: Record<string, KeyOwner>
  intra_repo_key_conflicts: KeyConflict[]
  scan_errors: ScanError[]
  head_sha: string | null
}

export interface ActivatePackSelection {
  path: string
  name: string
}

export interface ActivateKeyResolution {
  action_key: string
  /** Path inside the submitted activation set whose pack wins. */
  winner_pack_path?: string | null
  /** An existing DB pack wins (operator kept the prior winner). */
  winner_existing_pack_id?: number | null
  /** Bundled wins. */
  winner_is_bundled?: boolean
}

export interface ActivateGitopsBinding {
  file_path: string
  host_group_id: number
}

export interface RepoActivateRequest {
  packs: ActivatePackSelection[]
  gitops_bindings: ActivateGitopsBinding[]
  /** Per-key winner decisions for keys this activation makes
   * contested. One row per such key — the server rejects otherwise. */
  key_resolutions: ActivateKeyResolution[]
}

export interface ActivatedPackOut {
  pack_id: number
  name: string
  path: string
  position: number
  requested_name: string
  name_was_disambiguated: boolean
}

export interface ActivatedGitopsBindingOut {
  host_group_id: number
  file_path: string
}

export interface RepoActivateResponse {
  activated_packs: ActivatedPackOut[]
  activated_gitops_bindings: ActivatedGitopsBindingOut[]
  head_sha: string | null
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
  /** Whether the action makes sense across the entire fleet. Drives the
   *  Fleet target option on the schedule dialog. Conservative default. */
  supports_fleet: boolean
  parameters: ActionParameter[]
  /** Pack whose manifest provided this action. */
  pack_name: string
  /** Packs whose entries for this key were shadowed. Empty when uncontested. */
  overridden_from: string[]
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
  /** Populated when status='pending' — human-readable string naming
   *  the in-flight op holding the host. NULL otherwise. */
  pending_reason: string | null
}

export interface ActionRun {
  id: number
  action_key: string
  action_version: string
  host_id: number | null
  group_id: number | null
  /** NULL for ad-hoc runs; populated when the run was dispatched by the
   *  unified scheduler or POST /api/scheduled-actions/{id}/run-now. */
  scheduled_action_id: number | null
  parameters: Record<string, unknown>
  parallelism: number
  /** Universal destructive-flow toggles, mirrored from the schedule
   *  at dispatch time. Ignored when the action is non-destructive. */
  snapshot_enabled: boolean
  verify_enabled: boolean
  auto_rollback: boolean
  status: string
  triggered_by_user_id: number | null
  started_at: string | null
  finished_at: string | null
  error_message: string | null
  /** Populated when status='pending' — human-readable string naming
   *  the in-flight op holding the target host. NULL otherwise. */
  pending_reason: string | null
  created_at: string
  host_runs: ActionHostRun[]
}

// ---------------------------------------------------------------------------
// Scheduled actions (unified cron-driven action dispatch)
// ---------------------------------------------------------------------------

export type ScheduledActionTargetKind = "host" | "group" | "fleet"

export interface ScheduledActionRunSummary {
  id: number
  status: string
  started_at: string | null
  finished_at: string | null
  created_at: string
}

export interface ScheduledAction {
  id: number
  target_kind: ScheduledActionTargetKind
  target_id: number | null
  action_key: string
  parameters: Record<string, unknown>
  schedule_cron: string | null
  enabled: boolean
  snapshot_enabled: boolean
  verify_enabled: boolean
  auto_rollback: boolean
  batch_size: number
  last_dispatched_at: string | null
  created_at: string
  updated_at: string
  /** Server-resolved presentation helpers from /api/scheduled-actions. */
  target_name: string | null
  action_name: string | null
  pack_name: string | null
  destructive: boolean | null
  last_run: ScheduledActionRunSummary | null
}

export interface ScheduledActionCreate {
  target_kind: ScheduledActionTargetKind
  target_id: number | null
  action_key: string
  parameters?: Record<string, unknown>
  schedule_cron: string | null
  enabled?: boolean
  snapshot_enabled?: boolean
  verify_enabled?: boolean
  auto_rollback?: boolean
  batch_size?: number
}

export type ScheduledActionUpdate = Partial<
  Omit<ScheduledActionCreate, "action_key" | "target_kind" | "target_id">
>

export interface ValidateCronResponse {
  valid: boolean
  message: string | null
  next_run_at: string[]
}

// ---------------------------------------------------------------------------
// Version / build metadata  (GET /api/version — public, no auth)
// ---------------------------------------------------------------------------

export interface VersionInfo {
  version: string
  commit_sha: string | null
  commit_sha_short: string | null
  build_date: string | null
  license: string
  repo_url: string
}
