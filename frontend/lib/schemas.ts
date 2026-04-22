import { z } from "zod"

// Reusable validators
const ipRegex = /^(\d{1,3}\.){3}\d{1,3}$/
const cidrRegex = /^(\d{1,3}\.){3}\d{1,3}\/\d{1,2}$/
const cronFieldRegex = /^[\d\*\/,\-]+$/

const ipAddress = z.string().regex(ipRegex, "Invalid IP address format")
const cidrOrEmpty = z.string().refine(
  (v) => v === "" || cidrRegex.test(v),
  "Invalid CIDR format (e.g., 10.0.0.0/8)"
).optional()

// Group schema
export const groupSchema = z.object({
  name: z.string().min(1, "Name is required").max(100, "Name too long"),
  description: z.string().optional(),
  category: z.string().max(100, "Category too long").optional(),
  priority: z.number()
    .min(1, "Priority must be at least 1")
    .max(1000, "Priority must be at most 1000")
    .refine((v) => Number.isInteger(v), "Priority must be a whole number"),
})
export type GroupInput = z.infer<typeof groupSchema>

// Host schema
export const hostSchema = z.object({
  hostname: z.string().optional(),
  ip_address: ipAddress,
  ssh_port: z.number().int().min(1).max(65535),
  ssh_user: z.string().min(1, "SSH user is required").max(32),
  ssh_key_id: z.string().optional(),
  group_ids: z.array(z.string()).optional(),
})
export type HostInput = z.infer<typeof hostSchema>

// Firewall rule schema
export const ruleSchema = z.object({
  action: z.enum(["allow", "deny", "reject"]),
  protocol: z.enum(["tcp", "udp", "icmp", "any"]),
  direction: z.enum(["input", "output"]),
  source_mode: z.enum(["cidr", "host"]),
  destination_mode: z.enum(["cidr", "host"]),
  source_cidr: cidrOrEmpty,
  destination_cidr: cidrOrEmpty,
  source_host_id: z.number().int().nullable().optional(),
  destination_host_id: z.number().int().nullable().optional(),
  port_start: z.number().int().min(1).max(65535).optional().nullable(),
  port_end: z.number().int().min(1).max(65535).optional().nullable(),
  comment: z.string().optional(),
})
export type RuleInput = z.infer<typeof ruleSchema>

// Service schema
export const serviceSchema = z.object({
  service_name: z.string().min(1, "Service name is required"),
  state: z.enum(["running", "stopped"]),
  enabled: z.boolean(),
  unit_content: z.string().optional(),
  deploy_mode: z.enum(["full", "override"]),
  priority: z.number().int().min(0, "Priority must be non-negative"),
  comment: z.string().optional(),
})
export type ServiceInput = z.infer<typeof serviceSchema>

// Hosts entry schema
export const hostsEntrySchema = z.object({
  mode: z.enum(["literal", "host"]),
  ip_address: z.string().optional(),
  hostname: z.string().optional(),
  host_ref_id: z.number().int().nullable().optional(),
  aliases: z.string().optional(),
  comment: z.string().optional(),
  priority: z.number().int().min(0, "Priority must be non-negative"),
}).superRefine((v, ctx) => {
  if (v.mode === "literal") {
    if (!v.ip_address || !ipRegex.test(v.ip_address)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Invalid IP address format", path: ["ip_address"] })
    }
    if (!v.hostname || v.hostname.length === 0) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Hostname is required", path: ["hostname"] })
    }
  } else if (v.mode === "host") {
    if (v.host_ref_id == null) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Pick a host", path: ["host_ref_id"] })
    }
  }
})
export type HostsEntryInput = z.infer<typeof hostsEntrySchema>

// SSH key schema (upload form — public key is derived server-side)
export const sshKeySchema = z.object({
  name: z.string().min(1, "Name is required"),
  private_key: z.string().min(1, "Private key is required"),
  ssh_user: z.string().min(1, "SSH user is required").max(32),
  is_default: z.boolean(),
})
export type SshKeyInput = z.infer<typeof sshKeySchema>

// Git repo schema
export const gitRepoSchema = z.object({
  name: z.string().min(1, "Name is required"),
  url: z.string().min(1, "URL is required"),
  branch: z.string().min(1, "Branch is required"),
  auth_type: z.enum(["ssh_key", "https_token"]),
  ssh_key_id: z.string().optional().nullable(),
  https_token: z.string().optional(),
  webhook_secret: z.string().optional().nullable(),
})
export type GitRepoInput = z.infer<typeof gitRepoSchema>

// Cron job schema
export const cronJobSchema = z.object({
  name: z.string().min(1, "Name is required"),
  minute: z.string().regex(cronFieldRegex, "Invalid cron field"),
  hour: z.string().regex(cronFieldRegex, "Invalid cron field"),
  day: z.string().regex(cronFieldRegex, "Invalid cron field"),
  month: z.string().regex(cronFieldRegex, "Invalid cron field"),
  weekday: z.string().regex(cronFieldRegex, "Invalid cron field"),
  command: z.string().min(1, "Command is required"),
  user: z.string().min(1, "User is required"),
  state: z.enum(["present", "absent"]),
  priority: z.number().int().min(0, "Priority must be non-negative"),
  comment: z.string().optional(),
})
export type CronJobInput = z.infer<typeof cronJobSchema>

// Password change schema
export const passwordChangeSchema = z.object({
  new_password: z.string().min(8, "Password must be at least 8 characters"),
  confirm_password: z.string(),
}).refine((data) => data.new_password === data.confirm_password, {
  message: "Passwords do not match",
  path: ["confirm_password"],
})
export type PasswordChangeInput = z.infer<typeof passwordChangeSchema>

// Package schema
export const packageSchema = z.object({
  package_name: z.string().min(1, "Package name is required"),
  version: z.string().optional().nullable(),
  state: z.enum(["present", "absent", "latest"]),
  package_manager: z.enum(["auto", "apt", "dnf", "yum"]).default("auto"),
  comment: z.string().optional(),
})
// 5-field cron: each field may be *, a digit run, range, list, or step.
const cronFieldPat = /^(\*|(\d+(-\d+)?(,\d+(-\d+)?)*)(\/\d+)?|\*\/\d+)$/
const fullCronRegex = new RegExp(
  `^${Array(5).fill(cronFieldPat.source).join("\\s+")}$`
)

// CIDR — accept both strict (192.168.0.0/24) and host-in-network (192.168.0.5/24)
const cidrNetworkRegex =
  /^(\d{1,3}\.){3}\d{1,3}\/\d{1,2}$|^([0-9a-fA-F:]+)\/\d{1,3}$/

export const scanConfigSchema = z.object({
  name: z.string().min(1, "Name is required").max(100, "Name must be 100 chars or fewer"),
  cidrs: z
    .array(z.string())
    .min(1, "At least one CIDR is required"),
  ssh_key_id: z
    .number({ error: "SSH key is required" })
    .int()
    .min(1, "SSH key is required"),
  ssh_port: z
    .number({ error: "SSH port is required" })
    .int()
    .min(1, "Port must be between 1 and 65535")
    .max(65535, "Port must be between 1 and 65535"),
  default_group_ids: z.array(z.number().int()).default([]),
  schedule_type: z.enum(["interval", "cron"]),
  interval_value: z.number().int().min(1).max(10080).nullable().optional(),
  interval_unit: z.enum(["minutes", "hours", "days"]).optional(),
  cron_expression: z
    .string()
    .nullable()
    .optional()
    .refine(
      (v) => !v || fullCronRegex.test(v.trim()),
      "Invalid cron expression (5 space-separated fields required)"
    ),
  enabled: z.boolean().default(true),
  auto_add: z.boolean().default(false),
})
export type ScanConfigInput = z.input<typeof scanConfigSchema>
export type ScanConfigOutput = z.output<typeof scanConfigSchema>

export { cidrNetworkRegex }

// Linux user schema
export const linuxUserSchema = z.object({
  username: z.string().min(1, "Username is required"),
  uid: z.number().int().optional().nullable(),
  shell: z.string().default("/bin/bash"),
  home_dir: z.string().optional(),
  comment: z.string().optional(),
})
