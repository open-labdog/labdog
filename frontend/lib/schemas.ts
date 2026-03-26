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
    .max(2147483647, "Priority must be at most 2147483647")
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
  source_cidr: cidrOrEmpty,
  destination_cidr: cidrOrEmpty,
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
  priority: z.number().int().min(0, "Priority must be non-negative"),
  comment: z.string().optional(),
})
export type ServiceInput = z.infer<typeof serviceSchema>

// Hosts entry schema
export const hostsEntrySchema = z.object({
  ip_address: ipAddress,
  hostname: z.string().min(1, "Hostname is required"),
  aliases: z.string().optional(),
  comment: z.string().optional(),
  priority: z.number().int().min(0, "Priority must be non-negative"),
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
export type PackageInput = z.infer<typeof packageSchema>

// Linux user schema
export const linuxUserSchema = z.object({
  username: z.string().min(1, "Username is required"),
  uid: z.number().int().optional().nullable(),
  shell: z.string().default("/bin/bash"),
  home_dir: z.string().optional(),
  comment: z.string().optional(),
})
export type LinuxUserInput = z.infer<typeof linuxUserSchema>
