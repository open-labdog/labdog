"""Initial schema (squashed).

Consolidates the original 42 alembic migrations into a single
baseline that creates the LabDog schema as it stands at first
public release. Anyone upgrading from a pre-rebrand or
pre-release dev install needs to wipe their database and start
fresh — same as the Barricade -> LabDog rebrand asked for.
Tagged releases will start from this baseline.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-05-07
"""
from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic
revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


SCHEMA_STATEMENTS = (
    """CREATE TYPE public.certstate AS ENUM (
    'present',
    'absent'
)""",
    """CREATE TYPE public.cronstate AS ENUM (
    'present',
    'absent'
)""",
    """CREATE TYPE public.deploymode AS ENUM (
    'full',
    'override'
)""",
    """CREATE TYPE public.firewallbackend AS ENUM (
    'nftables',
    'iptables',
    'unknown'
)""",
    """CREATE TYPE public.gitauthtype AS ENUM (
    'ssh_key',
    'https_token',
    'none'
)""",
    """CREATE TYPE public.gitopsstatus AS ENUM (
    'disconnected',
    'synced',
    'error',
    'importing'
)""",
    """CREATE TYPE public.jobstatus AS ENUM (
    'pending',
    'running',
    'success',
    'failed',
    'cancelled'
)""",
    """CREATE TYPE public.packagemanager AS ENUM (
    'apt',
    'dnf',
    'yum',
    'auto'
)""",
    """CREATE TYPE public.packagestate AS ENUM (
    'present',
    'absent',
    'latest'
)""",
    """CREATE TYPE public.packsourcetype AS ENUM (
    'git',
    'local'
)""",
    """CREATE TYPE public.repotype AS ENUM (
    'apt',
    'yum'
)""",
    """CREATE TYPE public.resolvertype AS ENUM (
    'resolv_conf',
    'systemd_resolved',
    'networkmanager'
)""",
    """CREATE TYPE public.ruleaction AS ENUM (
    'allow',
    'deny',
    'reject'
)""",
    """CREATE TYPE public.ruledirection AS ENUM (
    'input',
    'output'
)""",
    """CREATE TYPE public.ruleprotocol AS ENUM (
    'tcp',
    'udp',
    'icmp',
    'any'
)""",
    """CREATE TYPE public.servicestate AS ENUM (
    'running',
    'stopped'
)""",
    """CREATE TYPE public.syncstatus AS ENUM (
    'pending',
    'in_sync',
    'out_of_sync',
    'unknown',
    'error'
)""",
    """CREATE TYPE public.userstate AS ENUM (
    'present',
    'absent'
)""",
    """CREATE TABLE public.action_host_runs (
    id integer NOT NULL,
    action_run_id integer NOT NULL,
    host_id integer NOT NULL,
    status character varying(20) NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    exit_code integer,
    output text DEFAULT ''''''::text NOT NULL,
    error_message text,
    snapshot_name character varying(128)
)""",
    """CREATE SEQUENCE public.action_host_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE public.action_host_runs_id_seq OWNED BY public.action_host_runs.id""",
    """CREATE TABLE public.action_packs (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    last_synced_at timestamp with time zone,
    last_sync_status character varying(20),
    last_sync_error text,
    current_sha character varying(40),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    source_type public.packsourcetype DEFAULT 'git'::public.packsourcetype NOT NULL,
    git_repository_id integer,
    path character varying(500) DEFAULT ''::character varying NOT NULL,
    local_path character varying(500),
    "position" integer DEFAULT 0 NOT NULL,
    CONSTRAINT ck_action_packs_ck_action_packs_source_shape CHECK ((((source_type = 'git'::public.packsourcetype) AND (git_repository_id IS NOT NULL) AND (local_path IS NULL)) OR ((source_type = 'local'::public.packsourcetype) AND (git_repository_id IS NULL) AND (local_path IS NOT NULL))))
)""",
    """CREATE SEQUENCE public.action_packs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE public.action_packs_id_seq OWNED BY public.action_packs.id""",
    """CREATE TABLE public.action_registry_snapshot (
    action_key character varying(64) NOT NULL,
    pack_id integer,
    computed_at timestamp with time zone DEFAULT now() NOT NULL
)""",
    """CREATE TABLE public.action_resolution (
    action_key character varying(64) NOT NULL,
    pack_id integer,
    decided_at timestamp with time zone DEFAULT now() NOT NULL,
    decided_by_user_id integer
)""",
    """CREATE TABLE public.action_runs (
    id integer NOT NULL,
    action_key character varying(64) NOT NULL,
    action_version character varying(32) NOT NULL,
    host_id integer,
    group_id integer,
    parameters jsonb DEFAULT '{}'::jsonb NOT NULL,
    parallelism integer NOT NULL,
    status character varying(20) NOT NULL,
    triggered_by_user_id integer,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    error_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    scheduled_action_id integer,
    snapshot_enabled boolean DEFAULT true NOT NULL,
    verify_enabled boolean DEFAULT true NOT NULL,
    auto_rollback boolean DEFAULT true NOT NULL,
    CONSTRAINT ck_action_runs_ck_action_runs_scope CHECK ((((host_id IS NOT NULL) AND (group_id IS NULL)) OR ((host_id IS NULL) AND (group_id IS NOT NULL)) OR ((host_id IS NULL) AND (group_id IS NULL) AND (scheduled_action_id IS NOT NULL))))
)""",
    """CREATE SEQUENCE public.action_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE public.action_runs_id_seq OWNED BY public.action_runs.id""",
    """CREATE TABLE public.app_settings (
    id integer NOT NULL,
    key character varying(100) NOT NULL,
    value text NOT NULL,
    value_type character varying(20) DEFAULT 'string'::character varying NOT NULL,
    description text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by integer
)""",
    """CREATE SEQUENCE public.app_settings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE public.app_settings_id_seq OWNED BY public.app_settings.id""",
    """CREATE TABLE public.audit_log (
    id integer NOT NULL,
    user_id integer,
    action character varying(100) NOT NULL,
    entity_type character varying(50) NOT NULL,
    entity_id integer,
    before_state jsonb,
    after_state jsonb,
    ip_address character varying(50),
    created_at timestamp with time zone NOT NULL
)""",
    """CREATE SEQUENCE public.audit_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE public.audit_log_id_seq OWNED BY public.audit_log.id""",
    """CREATE TABLE public.ca_cert_rules (
    id integer NOT NULL,
    group_id integer,
    host_id integer,
    name character varying(200) NOT NULL,
    pem_content text NOT NULL,
    fingerprint_sha256 character varying(95) NOT NULL,
    subject character varying(500),
    issuer character varying(500),
    not_before timestamp with time zone,
    not_after timestamp with time zone,
    state public.certstate NOT NULL,
    comment text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_ca_cert_rules_ck_ca_cert_rules_scope CHECK ((((group_id IS NOT NULL) AND (host_id IS NULL)) OR ((group_id IS NULL) AND (host_id IS NOT NULL))))
)""",
    """CREATE SEQUENCE public.ca_cert_rules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE public.ca_cert_rules_id_seq OWNED BY public.ca_cert_rules.id""",
    """CREATE TABLE public.cron_jobs (
    id integer NOT NULL,
    group_id integer,
    host_id integer,
    name character varying(100) NOT NULL,
    "user" character varying(32) DEFAULT 'root'::character varying NOT NULL,
    schedule character varying(100) NOT NULL,
    command text NOT NULL,
    environment jsonb DEFAULT '{}'::jsonb NOT NULL,
    state public.cronstate DEFAULT 'present'::public.cronstate NOT NULL,
    priority integer DEFAULT 0 NOT NULL,
    comment text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_cron_jobs_ck_cron_jobs_scope CHECK ((((group_id IS NOT NULL) AND (host_id IS NULL)) OR ((group_id IS NULL) AND (host_id IS NOT NULL))))
)""",
    """CREATE SEQUENCE public.cron_jobs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE public.cron_jobs_id_seq OWNED BY public.cron_jobs.id""",
    """CREATE TABLE public.firewall_rules (
    id integer NOT NULL,
    group_id integer,
    action public.ruleaction NOT NULL,
    protocol public.ruleprotocol NOT NULL,
    direction public.ruledirection NOT NULL,
    source_cidr character varying(50),
    destination_cidr character varying(50),
    port_start integer,
    port_end integer,
    priority integer DEFAULT 0 NOT NULL,
    comment text,
    is_system boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    host_id integer,
    source_host_id integer,
    destination_host_id integer,
    CONSTRAINT ck_firewall_rules_ck_firewall_rules_destination_ref CHECK ((NOT ((destination_cidr IS NOT NULL) AND (destination_host_id IS NOT NULL)))),
    CONSTRAINT ck_firewall_rules_ck_firewall_rules_scope CHECK ((((group_id IS NOT NULL) AND (host_id IS NULL)) OR ((group_id IS NULL) AND (host_id IS NOT NULL)))),
    CONSTRAINT ck_firewall_rules_ck_firewall_rules_source_ref CHECK ((NOT ((source_cidr IS NOT NULL) AND (source_host_id IS NOT NULL))))
)""",
    """CREATE SEQUENCE public.firewall_rules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE public.firewall_rules_id_seq OWNED BY public.firewall_rules.id""",
    """CREATE TABLE public.git_repositories (
    id integer NOT NULL,
    name character varying(200) NOT NULL,
    url character varying(500) NOT NULL,
    branch character varying(100) DEFAULT 'main'::character varying NOT NULL,
    auth_type public.gitauthtype NOT NULL,
    ssh_key_id integer,
    encrypted_https_token bytea,
    webhook_secret character varying(200),
    last_commit_sha character varying(64),
    last_sync_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
)""",
    """CREATE SEQUENCE public.git_repositories_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE public.git_repositories_id_seq OWNED BY public.git_repositories.id""",
    """CREATE TABLE public.host_group_memberships (
    host_id integer NOT NULL,
    group_id integer NOT NULL,
    role character varying(32),
    CONSTRAINT ck_host_group_memberships_ck_host_group_memberships_role_valid CHECK (((role IS NULL) OR ((role)::text = ANY ((ARRAY['control_plane'::character varying, 'worker'::character varying])::text[]))))
)""",
    """CREATE TABLE public.host_groups (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    description text,
    priority integer NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    git_repository_id integer,
    gitops_enabled boolean DEFAULT false NOT NULL,
    gitops_file_path character varying(500),
    gitops_status public.gitopsstatus DEFAULT 'disconnected'::public.gitopsstatus NOT NULL,
    gitops_error_message text,
    gitops_last_import_at timestamp with time zone,
    category character varying(100),
    input_policy character varying(6),
    output_policy character varying(6),
    CONSTRAINT ck_host_groups_ck_host_groups_priority_range CHECK (((priority >= 1) AND (priority <= 1000)))
)""",
    """CREATE SEQUENCE public.host_groups_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE public.host_groups_id_seq OWNED BY public.host_groups.id""",
    """CREATE TABLE public.host_module_status (
    id integer NOT NULL,
    host_id integer NOT NULL,
    module_type character varying(50) NOT NULL,
    sync_status character varying(20) DEFAULT 'unknown'::character varying NOT NULL,
    drift_check_enabled boolean DEFAULT false NOT NULL,
    last_sync_at timestamp with time zone,
    last_drift_check_at timestamp with time zone,
    collected_state json,
    collected_at timestamp with time zone,
    error_message text
)""",
    """CREATE SEQUENCE public.host_module_status_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE public.host_module_status_id_seq OWNED BY public.host_module_status.id""",
    """CREATE TABLE public.hosts (
    id integer NOT NULL,
    hostname character varying(255) NOT NULL,
    ip_address character varying(50) NOT NULL,
    ssh_port integer DEFAULT 22 NOT NULL,
    firewall_backend public.firewallbackend DEFAULT 'unknown'::public.firewallbackend NOT NULL,
    ssh_key_id integer,
    sync_status public.syncstatus DEFAULT 'unknown'::public.syncstatus NOT NULL,
    drift_check_enabled boolean DEFAULT false NOT NULL,
    last_sync_at timestamp with time zone,
    last_drift_check_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    ssh_user character varying(32) DEFAULT 'root'::character varying NOT NULL,
    labdog_source_ip character varying(50),
    os_codename character varying(64),
    os_pretty_name character varying(255),
    os_facts_collected_at timestamp with time zone,
    os_family character varying(32),
    default_nic character varying(32),
    kernel_version character varying(64),
    kernel_release character varying(32)
)""",
    """CREATE TABLE public.hosts_entries (
    id integer NOT NULL,
    group_id integer,
    host_id integer,
    ip_address character varying(45),
    hostname character varying(253),
    aliases jsonb DEFAULT '[]'::jsonb NOT NULL,
    comment text,
    priority integer DEFAULT 0 NOT NULL,
    is_system boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    host_ref_id integer,
    CONSTRAINT ck_hosts_entries_ck_hosts_entries_ref_or_literal CHECK (((host_ref_id IS NOT NULL) OR ((ip_address IS NOT NULL) AND (hostname IS NOT NULL)))),
    CONSTRAINT ck_hosts_entries_ck_hosts_entries_scope CHECK ((((group_id IS NOT NULL) AND (host_id IS NULL)) OR ((group_id IS NULL) AND (host_id IS NOT NULL))))
)""",
    """CREATE SEQUENCE public.hosts_entries_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE public.hosts_entries_id_seq OWNED BY public.hosts_entries.id""",
    """CREATE SEQUENCE public.hosts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE public.hosts_id_seq OWNED BY public.hosts.id""",
    """CREATE TABLE public.linux_groups (
    id integer NOT NULL,
    group_id integer,
    host_id integer,
    groupname character varying(32) NOT NULL,
    gid integer,
    state public.userstate DEFAULT 'present'::public.userstate NOT NULL,
    priority integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_linux_groups_ck_linux_groups_scope CHECK ((((group_id IS NOT NULL) AND (host_id IS NULL)) OR ((group_id IS NULL) AND (host_id IS NOT NULL))))
)""",
    """CREATE SEQUENCE public.linux_groups_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE public.linux_groups_id_seq OWNED BY public.linux_groups.id""",
    """CREATE TABLE public.linux_users (
    id integer NOT NULL,
    group_id integer,
    host_id integer,
    username character varying(32) NOT NULL,
    uid integer,
    shell character varying(100) DEFAULT '/bin/bash'::character varying NOT NULL,
    home_dir character varying(200),
    state public.userstate DEFAULT 'present'::public.userstate NOT NULL,
    comment text,
    sudo_rule text,
    authorized_keys jsonb DEFAULT '[]'::jsonb NOT NULL,
    supplementary_groups jsonb DEFAULT '[]'::jsonb NOT NULL,
    priority integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_linux_users_ck_linux_users_scope CHECK ((((group_id IS NOT NULL) AND (host_id IS NULL)) OR ((group_id IS NULL) AND (host_id IS NOT NULL))))
)""",
    """CREATE SEQUENCE public.linux_users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE public.linux_users_id_seq OWNED BY public.linux_users.id""",
    """CREATE TABLE public.package_repositories (
    id integer NOT NULL,
    group_id integer NOT NULL,
    name character varying(100) NOT NULL,
    url character varying(500) NOT NULL,
    key_url character varying(500),
    repo_type public.repotype NOT NULL,
    distribution character varying(100),
    components character varying(200),
    state public.packagestate NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
)""",
    """CREATE SEQUENCE public.package_repositories_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE public.package_repositories_id_seq OWNED BY public.package_repositories.id""",
    """CREATE TABLE public.package_rules (
    id integer NOT NULL,
    group_id integer,
    host_id integer,
    package_name character varying(200) NOT NULL,
    version character varying(100),
    state public.packagestate NOT NULL,
    package_manager public.packagemanager NOT NULL,
    priority integer NOT NULL,
    comment text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    hold boolean DEFAULT false NOT NULL,
    CONSTRAINT ck_package_rules_ck_package_rules_scope CHECK ((((group_id IS NOT NULL) AND (host_id IS NULL)) OR ((group_id IS NULL) AND (host_id IS NOT NULL))))
)""",
    """CREATE SEQUENCE public.package_rules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE public.package_rules_id_seq OWNED BY public.package_rules.id""",
    """CREATE TABLE public.pending_hosts (
    id integer NOT NULL,
    scan_config_id integer NOT NULL,
    ip_address character varying(45) NOT NULL,
    hostname character varying(253),
    ssh_verified boolean NOT NULL,
    ssh_error text,
    discovered_at timestamp with time zone DEFAULT now() NOT NULL
)""",
    """CREATE SEQUENCE public.pending_hosts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE public.pending_hosts_id_seq OWNED BY public.pending_hosts.id""",
    """CREATE TABLE public.proxmox_nodes (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    api_url character varying(500) NOT NULL,
    token_id character varying(200) NOT NULL,
    encrypted_token_secret bytea NOT NULL,
    verify_ssl boolean NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
)""",
    """CREATE SEQUENCE public.proxmox_nodes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE public.proxmox_nodes_id_seq OWNED BY public.proxmox_nodes.id""",
    """CREATE TABLE public.resolver_configs (
    id integer NOT NULL,
    group_id integer,
    host_id integer,
    nameservers jsonb NOT NULL,
    search_domains jsonb DEFAULT '[]'::jsonb NOT NULL,
    options jsonb DEFAULT '{}'::jsonb NOT NULL,
    resolver_type public.resolvertype NOT NULL,
    dns_over_tls boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_resolver_configs_ck_resolver_configs_scope CHECK ((((group_id IS NOT NULL) AND (host_id IS NULL)) OR ((group_id IS NULL) AND (host_id IS NOT NULL))))
)""",
    """CREATE SEQUENCE public.resolver_configs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE public.resolver_configs_id_seq OWNED BY public.resolver_configs.id""",
    """CREATE TABLE public.scan_configs (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    cidrs jsonb DEFAULT '[]'::jsonb NOT NULL,
    ssh_key_id integer NOT NULL,
    ssh_port integer NOT NULL,
    default_group_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    interval_minutes integer,
    cron_expression character varying(100),
    enabled boolean NOT NULL,
    auto_add boolean NOT NULL,
    last_run_at timestamp with time zone,
    last_run_status character varying(20),
    last_run_hosts_added integer NOT NULL,
    last_run_hosts_pending integer NOT NULL,
    last_run_error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_scan_configs_ck_scan_configs_schedule_one_of CHECK (((interval_minutes IS NOT NULL) <> (cron_expression IS NOT NULL)))
)""",
    """CREATE SEQUENCE public.scan_configs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE public.scan_configs_id_seq OWNED BY public.scan_configs.id""",
    """CREATE TABLE public.scheduled_actions (
    id integer NOT NULL,
    target_kind character varying(8) NOT NULL,
    target_id integer,
    action_key character varying(64) NOT NULL,
    parameters jsonb DEFAULT '{}'::jsonb NOT NULL,
    schedule_cron character varying(100),
    enabled boolean DEFAULT false NOT NULL,
    snapshot_enabled boolean DEFAULT true NOT NULL,
    verify_enabled boolean DEFAULT true NOT NULL,
    auto_rollback boolean DEFAULT true NOT NULL,
    batch_size integer DEFAULT 1 NOT NULL,
    last_dispatched_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_scheduled_actions_ck_scheduled_actions_target CHECK (((((target_kind)::text = 'fleet'::text) AND (target_id IS NULL)) OR (((target_kind)::text = ANY ((ARRAY['host'::character varying, 'group'::character varying])::text[])) AND (target_id IS NOT NULL))))
)""",
    """CREATE SEQUENCE public.scheduled_actions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE public.scheduled_actions_id_seq OWNED BY public.scheduled_actions.id""",
    """CREATE TABLE public.service_rules (
    id integer NOT NULL,
    group_id integer,
    host_id integer,
    service_name character varying(100) NOT NULL,
    state public.servicestate DEFAULT 'running'::public.servicestate NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    priority integer DEFAULT 0 NOT NULL,
    comment text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    unit_content text,
    deploy_mode public.deploymode DEFAULT 'override'::public.deploymode NOT NULL,
    CONSTRAINT ck_service_rules_ck_service_rules_scope CHECK ((((group_id IS NOT NULL) AND (host_id IS NULL)) OR ((group_id IS NULL) AND (host_id IS NOT NULL))))
)""",
    """CREATE SEQUENCE public.service_rules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE public.service_rules_id_seq OWNED BY public.service_rules.id""",
    """CREATE TABLE public.ssh_keys (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    encrypted_private_key bytea NOT NULL,
    public_key text,
    is_default boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone NOT NULL,
    ssh_user character varying(32) DEFAULT 'root'::character varying NOT NULL
)""",
    """CREATE SEQUENCE public.ssh_keys_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE public.ssh_keys_id_seq OWNED BY public.ssh_keys.id""",
    """CREATE TABLE public.sync_jobs (
    id integer NOT NULL,
    host_id integer NOT NULL,
    group_id integer,
    status public.jobstatus DEFAULT 'pending'::public.jobstatus NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    ansible_output text,
    error_message text,
    triggered_by_user_id integer,
    created_at timestamp with time zone NOT NULL,
    module_type character varying(50) DEFAULT 'firewall'::character varying NOT NULL
)""",
    """CREATE SEQUENCE public.sync_jobs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE public.sync_jobs_id_seq OWNED BY public.sync_jobs.id""",
    """CREATE TABLE public.users (
    id integer NOT NULL,
    email character varying(255) NOT NULL,
    hashed_password character varying(255) NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    is_superuser boolean DEFAULT false NOT NULL,
    is_verified boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
)""",
    """CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id""",
    """CREATE TABLE public.vm_mappings (
    id integer NOT NULL,
    host_id integer NOT NULL,
    proxmox_node_id integer NOT NULL,
    pve_node_name character varying(100) NOT NULL,
    vmid integer NOT NULL,
    vm_name character varying(200) NOT NULL,
    discovered_at timestamp with time zone NOT NULL
)""",
    """CREATE SEQUENCE public.vm_mappings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE public.vm_mappings_id_seq OWNED BY public.vm_mappings.id""",
    """ALTER TABLE ONLY public.action_host_runs ALTER COLUMN id SET DEFAULT nextval('public.action_host_runs_id_seq'::regclass)""",
    """ALTER TABLE ONLY public.action_packs ALTER COLUMN id SET DEFAULT nextval('public.action_packs_id_seq'::regclass)""",
    """ALTER TABLE ONLY public.action_runs ALTER COLUMN id SET DEFAULT nextval('public.action_runs_id_seq'::regclass)""",
    """ALTER TABLE ONLY public.app_settings ALTER COLUMN id SET DEFAULT nextval('public.app_settings_id_seq'::regclass)""",
    """ALTER TABLE ONLY public.audit_log ALTER COLUMN id SET DEFAULT nextval('public.audit_log_id_seq'::regclass)""",
    """ALTER TABLE ONLY public.ca_cert_rules ALTER COLUMN id SET DEFAULT nextval('public.ca_cert_rules_id_seq'::regclass)""",
    """ALTER TABLE ONLY public.cron_jobs ALTER COLUMN id SET DEFAULT nextval('public.cron_jobs_id_seq'::regclass)""",
    """ALTER TABLE ONLY public.firewall_rules ALTER COLUMN id SET DEFAULT nextval('public.firewall_rules_id_seq'::regclass)""",
    """ALTER TABLE ONLY public.git_repositories ALTER COLUMN id SET DEFAULT nextval('public.git_repositories_id_seq'::regclass)""",
    """ALTER TABLE ONLY public.host_groups ALTER COLUMN id SET DEFAULT nextval('public.host_groups_id_seq'::regclass)""",
    """ALTER TABLE ONLY public.host_module_status ALTER COLUMN id SET DEFAULT nextval('public.host_module_status_id_seq'::regclass)""",
    """ALTER TABLE ONLY public.hosts ALTER COLUMN id SET DEFAULT nextval('public.hosts_id_seq'::regclass)""",
    """ALTER TABLE ONLY public.hosts_entries ALTER COLUMN id SET DEFAULT nextval('public.hosts_entries_id_seq'::regclass)""",
    """ALTER TABLE ONLY public.linux_groups ALTER COLUMN id SET DEFAULT nextval('public.linux_groups_id_seq'::regclass)""",
    """ALTER TABLE ONLY public.linux_users ALTER COLUMN id SET DEFAULT nextval('public.linux_users_id_seq'::regclass)""",
    """ALTER TABLE ONLY public.package_repositories ALTER COLUMN id SET DEFAULT nextval('public.package_repositories_id_seq'::regclass)""",
    """ALTER TABLE ONLY public.package_rules ALTER COLUMN id SET DEFAULT nextval('public.package_rules_id_seq'::regclass)""",
    """ALTER TABLE ONLY public.pending_hosts ALTER COLUMN id SET DEFAULT nextval('public.pending_hosts_id_seq'::regclass)""",
    """ALTER TABLE ONLY public.proxmox_nodes ALTER COLUMN id SET DEFAULT nextval('public.proxmox_nodes_id_seq'::regclass)""",
    """ALTER TABLE ONLY public.resolver_configs ALTER COLUMN id SET DEFAULT nextval('public.resolver_configs_id_seq'::regclass)""",
    """ALTER TABLE ONLY public.scan_configs ALTER COLUMN id SET DEFAULT nextval('public.scan_configs_id_seq'::regclass)""",
    """ALTER TABLE ONLY public.scheduled_actions ALTER COLUMN id SET DEFAULT nextval('public.scheduled_actions_id_seq'::regclass)""",
    """ALTER TABLE ONLY public.service_rules ALTER COLUMN id SET DEFAULT nextval('public.service_rules_id_seq'::regclass)""",
    """ALTER TABLE ONLY public.ssh_keys ALTER COLUMN id SET DEFAULT nextval('public.ssh_keys_id_seq'::regclass)""",
    """ALTER TABLE ONLY public.sync_jobs ALTER COLUMN id SET DEFAULT nextval('public.sync_jobs_id_seq'::regclass)""",
    """ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass)""",
    """ALTER TABLE ONLY public.vm_mappings ALTER COLUMN id SET DEFAULT nextval('public.vm_mappings_id_seq'::regclass)""",
    """ALTER TABLE ONLY public.action_host_runs
    ADD CONSTRAINT pk_action_host_runs PRIMARY KEY (id)""",
    """ALTER TABLE ONLY public.action_packs
    ADD CONSTRAINT pk_action_packs PRIMARY KEY (id)""",
    """ALTER TABLE ONLY public.action_registry_snapshot
    ADD CONSTRAINT pk_action_registry_snapshot PRIMARY KEY (action_key)""",
    """ALTER TABLE ONLY public.action_resolution
    ADD CONSTRAINT pk_action_resolution PRIMARY KEY (action_key)""",
    """ALTER TABLE ONLY public.action_runs
    ADD CONSTRAINT pk_action_runs PRIMARY KEY (id)""",
    """ALTER TABLE ONLY public.app_settings
    ADD CONSTRAINT pk_app_settings PRIMARY KEY (id)""",
    """ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT pk_audit_log PRIMARY KEY (id)""",
    """ALTER TABLE ONLY public.ca_cert_rules
    ADD CONSTRAINT pk_ca_cert_rules PRIMARY KEY (id)""",
    """ALTER TABLE ONLY public.cron_jobs
    ADD CONSTRAINT pk_cron_jobs PRIMARY KEY (id)""",
    """ALTER TABLE ONLY public.firewall_rules
    ADD CONSTRAINT pk_firewall_rules PRIMARY KEY (id)""",
    """ALTER TABLE ONLY public.git_repositories
    ADD CONSTRAINT pk_git_repositories PRIMARY KEY (id)""",
    """ALTER TABLE ONLY public.host_group_memberships
    ADD CONSTRAINT pk_host_group_memberships PRIMARY KEY (host_id, group_id)""",
    """ALTER TABLE ONLY public.host_groups
    ADD CONSTRAINT pk_host_groups PRIMARY KEY (id)""",
    """ALTER TABLE ONLY public.host_module_status
    ADD CONSTRAINT pk_host_module_status PRIMARY KEY (id)""",
    """ALTER TABLE ONLY public.hosts
    ADD CONSTRAINT pk_hosts PRIMARY KEY (id)""",
    """ALTER TABLE ONLY public.hosts_entries
    ADD CONSTRAINT pk_hosts_entries PRIMARY KEY (id)""",
    """ALTER TABLE ONLY public.linux_groups
    ADD CONSTRAINT pk_linux_groups PRIMARY KEY (id)""",
    """ALTER TABLE ONLY public.linux_users
    ADD CONSTRAINT pk_linux_users PRIMARY KEY (id)""",
    """ALTER TABLE ONLY public.package_repositories
    ADD CONSTRAINT pk_package_repositories PRIMARY KEY (id)""",
    """ALTER TABLE ONLY public.package_rules
    ADD CONSTRAINT pk_package_rules PRIMARY KEY (id)""",
    """ALTER TABLE ONLY public.pending_hosts
    ADD CONSTRAINT pk_pending_hosts PRIMARY KEY (id)""",
    """ALTER TABLE ONLY public.proxmox_nodes
    ADD CONSTRAINT pk_proxmox_nodes PRIMARY KEY (id)""",
    """ALTER TABLE ONLY public.resolver_configs
    ADD CONSTRAINT pk_resolver_configs PRIMARY KEY (id)""",
    """ALTER TABLE ONLY public.scan_configs
    ADD CONSTRAINT pk_scan_configs PRIMARY KEY (id)""",
    """ALTER TABLE ONLY public.scheduled_actions
    ADD CONSTRAINT pk_scheduled_actions PRIMARY KEY (id)""",
    """ALTER TABLE ONLY public.service_rules
    ADD CONSTRAINT pk_service_rules PRIMARY KEY (id)""",
    """ALTER TABLE ONLY public.ssh_keys
    ADD CONSTRAINT pk_ssh_keys PRIMARY KEY (id)""",
    """ALTER TABLE ONLY public.sync_jobs
    ADD CONSTRAINT pk_sync_jobs PRIMARY KEY (id)""",
    """ALTER TABLE ONLY public.users
    ADD CONSTRAINT pk_users PRIMARY KEY (id)""",
    """ALTER TABLE ONLY public.vm_mappings
    ADD CONSTRAINT pk_vm_mappings PRIMARY KEY (id)""",
    """ALTER TABLE ONLY public.action_host_runs
    ADD CONSTRAINT uq_action_host_run UNIQUE (action_run_id, host_id)""",
    """ALTER TABLE ONLY public.action_packs
    ADD CONSTRAINT uq_action_packs_name UNIQUE (name)""",
    """ALTER TABLE ONLY public.app_settings
    ADD CONSTRAINT uq_app_settings_key UNIQUE (key)""",
    """ALTER TABLE ONLY public.ca_cert_rules
    ADD CONSTRAINT uq_ca_cert_rules_group_fp UNIQUE (group_id, fingerprint_sha256)""",
    """ALTER TABLE ONLY public.ca_cert_rules
    ADD CONSTRAINT uq_ca_cert_rules_host_fp UNIQUE (host_id, fingerprint_sha256)""",
    """ALTER TABLE ONLY public.git_repositories
    ADD CONSTRAINT uq_git_repositories_name UNIQUE (name)""",
    """ALTER TABLE ONLY public.host_module_status
    ADD CONSTRAINT uq_host_module_status_host_id_module_type UNIQUE (host_id, module_type)""",
    """ALTER TABLE ONLY public.package_repositories
    ADD CONSTRAINT uq_package_repos_group_name UNIQUE (group_id, name)""",
    """ALTER TABLE ONLY public.package_rules
    ADD CONSTRAINT uq_package_rules_group_pkg UNIQUE (group_id, package_name)""",
    """ALTER TABLE ONLY public.package_rules
    ADD CONSTRAINT uq_package_rules_host_pkg UNIQUE (host_id, package_name)""",
    """ALTER TABLE ONLY public.pending_hosts
    ADD CONSTRAINT uq_pending_scan_ip UNIQUE (scan_config_id, ip_address)""",
    """ALTER TABLE ONLY public.scan_configs
    ADD CONSTRAINT uq_scan_configs_name UNIQUE (name)""",
    """ALTER TABLE ONLY public.scheduled_actions
    ADD CONSTRAINT uq_scheduled_actions_target_action UNIQUE (target_kind, target_id, action_key)""",
    """ALTER TABLE ONLY public.ssh_keys
    ADD CONSTRAINT uq_ssh_keys_name UNIQUE (name)""",
    """CREATE UNIQUE INDEX ix_action_packs_name ON public.action_packs USING btree (name)""",
    """CREATE INDEX ix_action_packs_position ON public.action_packs USING btree ("position")""",
    """CREATE INDEX ix_action_runs_scheduled_action_id ON public.action_runs USING btree (scheduled_action_id)""",
    """CREATE INDEX ix_app_settings_key ON public.app_settings USING btree (key)""",
    """CREATE INDEX ix_audit_log_created_at ON public.audit_log USING btree (created_at)""",
    """CREATE INDEX ix_audit_log_entity ON public.audit_log USING btree (entity_type, entity_id)""",
    """CREATE INDEX ix_cron_jobs_group_id ON public.cron_jobs USING btree (group_id)""",
    """CREATE INDEX ix_cron_jobs_host_id ON public.cron_jobs USING btree (host_id)""",
    """CREATE INDEX ix_firewall_rules_destination_host_id ON public.firewall_rules USING btree (destination_host_id)""",
    """CREATE INDEX ix_firewall_rules_group_id ON public.firewall_rules USING btree (group_id)""",
    """CREATE INDEX ix_firewall_rules_host_id ON public.firewall_rules USING btree (host_id)""",
    """CREATE INDEX ix_firewall_rules_source_host_id ON public.firewall_rules USING btree (source_host_id)""",
    """CREATE UNIQUE INDEX ix_host_groups_name ON public.host_groups USING btree (name)""",
    """CREATE INDEX ix_host_module_status_host_id ON public.host_module_status USING btree (host_id)""",
    """CREATE INDEX ix_hosts_entries_group_id ON public.hosts_entries USING btree (group_id)""",
    """CREATE INDEX ix_hosts_entries_host_id ON public.hosts_entries USING btree (host_id)""",
    """CREATE INDEX ix_hosts_entries_host_ref_id ON public.hosts_entries USING btree (host_ref_id)""",
    """CREATE UNIQUE INDEX ix_hosts_hostname ON public.hosts USING btree (hostname)""",
    """CREATE INDEX ix_linux_groups_group_id ON public.linux_groups USING btree (group_id)""",
    """CREATE INDEX ix_linux_groups_host_id ON public.linux_groups USING btree (host_id)""",
    """CREATE INDEX ix_linux_users_group_id ON public.linux_users USING btree (group_id)""",
    """CREATE INDEX ix_linux_users_host_id ON public.linux_users USING btree (host_id)""",
    """CREATE INDEX ix_package_rules_group_id ON public.package_rules USING btree (group_id)""",
    """CREATE INDEX ix_package_rules_host_id ON public.package_rules USING btree (host_id)""",
    """CREATE UNIQUE INDEX ix_proxmox_nodes_name ON public.proxmox_nodes USING btree (name)""",
    """CREATE UNIQUE INDEX ix_resolver_config_group_unique ON public.resolver_configs USING btree (group_id) WHERE (group_id IS NOT NULL)""",
    """CREATE UNIQUE INDEX ix_resolver_config_host_unique ON public.resolver_configs USING btree (host_id) WHERE (host_id IS NOT NULL)""",
    """CREATE INDEX ix_scheduled_actions_due ON public.scheduled_actions USING btree (action_key, enabled)""",
    """CREATE INDEX ix_scheduled_actions_target ON public.scheduled_actions USING btree (target_kind, target_id)""",
    """CREATE INDEX ix_service_rules_group_id ON public.service_rules USING btree (group_id)""",
    """CREATE INDEX ix_service_rules_host_id ON public.service_rules USING btree (host_id)""",
    """CREATE INDEX ix_sync_jobs_host_module_status ON public.sync_jobs USING btree (host_id, module_type, status)""",
    """CREATE UNIQUE INDEX ix_users_email ON public.users USING btree (email)""",
    """CREATE UNIQUE INDEX ix_vm_mappings_host_id ON public.vm_mappings USING btree (host_id)""",
    """CREATE INDEX ix_vm_mappings_proxmox_node_id ON public.vm_mappings USING btree (proxmox_node_id)""",
    """CREATE UNIQUE INDEX uq_sync_job_active ON public.sync_jobs USING btree (host_id, module_type) WHERE (status = ANY (ARRAY['pending'::public.jobstatus, 'running'::public.jobstatus]))""",
    """ALTER TABLE ONLY public.action_host_runs
    ADD CONSTRAINT fk_action_host_runs_action_run_id_action_runs FOREIGN KEY (action_run_id) REFERENCES public.action_runs(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY public.action_host_runs
    ADD CONSTRAINT fk_action_host_runs_host_id_hosts FOREIGN KEY (host_id) REFERENCES public.hosts(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY public.action_packs
    ADD CONSTRAINT fk_action_packs_git_repository_id_git_repositories FOREIGN KEY (git_repository_id) REFERENCES public.git_repositories(id) ON DELETE RESTRICT""",
    """ALTER TABLE ONLY public.action_registry_snapshot
    ADD CONSTRAINT fk_action_registry_snapshot_pack_id_action_packs FOREIGN KEY (pack_id) REFERENCES public.action_packs(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY public.action_resolution
    ADD CONSTRAINT fk_action_resolution_decided_by_user_id_users FOREIGN KEY (decided_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL""",
    """ALTER TABLE ONLY public.action_resolution
    ADD CONSTRAINT fk_action_resolution_pack_id_action_packs FOREIGN KEY (pack_id) REFERENCES public.action_packs(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY public.action_runs
    ADD CONSTRAINT fk_action_runs_group_id_host_groups FOREIGN KEY (group_id) REFERENCES public.host_groups(id) ON DELETE SET NULL""",
    """ALTER TABLE ONLY public.action_runs
    ADD CONSTRAINT fk_action_runs_host_id_hosts FOREIGN KEY (host_id) REFERENCES public.hosts(id) ON DELETE SET NULL""",
    """ALTER TABLE ONLY public.action_runs
    ADD CONSTRAINT fk_action_runs_scheduled_action_id_scheduled_actions FOREIGN KEY (scheduled_action_id) REFERENCES public.scheduled_actions(id) ON DELETE SET NULL""",
    """ALTER TABLE ONLY public.action_runs
    ADD CONSTRAINT fk_action_runs_triggered_by_user_id_users FOREIGN KEY (triggered_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL""",
    """ALTER TABLE ONLY public.app_settings
    ADD CONSTRAINT fk_app_settings_updated_by_users FOREIGN KEY (updated_by) REFERENCES public.users(id) ON DELETE SET NULL""",
    """ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT fk_audit_log_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL""",
    """ALTER TABLE ONLY public.ca_cert_rules
    ADD CONSTRAINT fk_ca_cert_rules_group_id_host_groups FOREIGN KEY (group_id) REFERENCES public.host_groups(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY public.ca_cert_rules
    ADD CONSTRAINT fk_ca_cert_rules_host_id_hosts FOREIGN KEY (host_id) REFERENCES public.hosts(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY public.cron_jobs
    ADD CONSTRAINT fk_cron_jobs_group_id_host_groups FOREIGN KEY (group_id) REFERENCES public.host_groups(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY public.cron_jobs
    ADD CONSTRAINT fk_cron_jobs_host_id_hosts FOREIGN KEY (host_id) REFERENCES public.hosts(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY public.firewall_rules
    ADD CONSTRAINT fk_firewall_rules_destination_host_id FOREIGN KEY (destination_host_id) REFERENCES public.hosts(id) ON DELETE RESTRICT""",
    """ALTER TABLE ONLY public.firewall_rules
    ADD CONSTRAINT fk_firewall_rules_group_id_host_groups FOREIGN KEY (group_id) REFERENCES public.host_groups(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY public.firewall_rules
    ADD CONSTRAINT fk_firewall_rules_host_id FOREIGN KEY (host_id) REFERENCES public.hosts(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY public.firewall_rules
    ADD CONSTRAINT fk_firewall_rules_source_host_id FOREIGN KEY (source_host_id) REFERENCES public.hosts(id) ON DELETE RESTRICT""",
    """ALTER TABLE ONLY public.host_group_memberships
    ADD CONSTRAINT fk_host_group_memberships_group_id_host_groups FOREIGN KEY (group_id) REFERENCES public.host_groups(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY public.host_group_memberships
    ADD CONSTRAINT fk_host_group_memberships_host_id_hosts FOREIGN KEY (host_id) REFERENCES public.hosts(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY public.host_groups
    ADD CONSTRAINT fk_host_groups_git_repository_id_git_repositories FOREIGN KEY (git_repository_id) REFERENCES public.git_repositories(id)""",
    """ALTER TABLE ONLY public.host_module_status
    ADD CONSTRAINT fk_host_module_status_host_id_hosts FOREIGN KEY (host_id) REFERENCES public.hosts(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY public.hosts_entries
    ADD CONSTRAINT fk_hosts_entries_group_id_host_groups FOREIGN KEY (group_id) REFERENCES public.host_groups(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY public.hosts_entries
    ADD CONSTRAINT fk_hosts_entries_host_id_hosts FOREIGN KEY (host_id) REFERENCES public.hosts(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY public.hosts_entries
    ADD CONSTRAINT fk_hosts_entries_host_ref_id FOREIGN KEY (host_ref_id) REFERENCES public.hosts(id) ON DELETE RESTRICT""",
    """ALTER TABLE ONLY public.hosts
    ADD CONSTRAINT fk_hosts_ssh_key_id_ssh_keys FOREIGN KEY (ssh_key_id) REFERENCES public.ssh_keys(id) ON DELETE SET NULL""",
    """ALTER TABLE ONLY public.linux_groups
    ADD CONSTRAINT fk_linux_groups_group_id_host_groups FOREIGN KEY (group_id) REFERENCES public.host_groups(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY public.linux_groups
    ADD CONSTRAINT fk_linux_groups_host_id_hosts FOREIGN KEY (host_id) REFERENCES public.hosts(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY public.linux_users
    ADD CONSTRAINT fk_linux_users_group_id_host_groups FOREIGN KEY (group_id) REFERENCES public.host_groups(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY public.linux_users
    ADD CONSTRAINT fk_linux_users_host_id_hosts FOREIGN KEY (host_id) REFERENCES public.hosts(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY public.package_repositories
    ADD CONSTRAINT fk_package_repositories_group_id_host_groups FOREIGN KEY (group_id) REFERENCES public.host_groups(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY public.package_rules
    ADD CONSTRAINT fk_package_rules_group_id_host_groups FOREIGN KEY (group_id) REFERENCES public.host_groups(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY public.package_rules
    ADD CONSTRAINT fk_package_rules_host_id_hosts FOREIGN KEY (host_id) REFERENCES public.hosts(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY public.pending_hosts
    ADD CONSTRAINT fk_pending_hosts_scan_config_id_scan_configs FOREIGN KEY (scan_config_id) REFERENCES public.scan_configs(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY public.resolver_configs
    ADD CONSTRAINT fk_resolver_configs_group_id_host_groups FOREIGN KEY (group_id) REFERENCES public.host_groups(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY public.resolver_configs
    ADD CONSTRAINT fk_resolver_configs_host_id_hosts FOREIGN KEY (host_id) REFERENCES public.hosts(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY public.scan_configs
    ADD CONSTRAINT fk_scan_configs_ssh_key_id_ssh_keys FOREIGN KEY (ssh_key_id) REFERENCES public.ssh_keys(id) ON DELETE RESTRICT""",
    """ALTER TABLE ONLY public.service_rules
    ADD CONSTRAINT fk_service_rules_group_id_host_groups FOREIGN KEY (group_id) REFERENCES public.host_groups(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY public.service_rules
    ADD CONSTRAINT fk_service_rules_host_id_hosts FOREIGN KEY (host_id) REFERENCES public.hosts(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY public.sync_jobs
    ADD CONSTRAINT fk_sync_jobs_group_id_host_groups FOREIGN KEY (group_id) REFERENCES public.host_groups(id) ON DELETE SET NULL""",
    """ALTER TABLE ONLY public.sync_jobs
    ADD CONSTRAINT fk_sync_jobs_host_id_hosts FOREIGN KEY (host_id) REFERENCES public.hosts(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY public.sync_jobs
    ADD CONSTRAINT fk_sync_jobs_triggered_by_user_id_users FOREIGN KEY (triggered_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL""",
    """ALTER TABLE ONLY public.vm_mappings
    ADD CONSTRAINT fk_vm_mappings_host_id_hosts FOREIGN KEY (host_id) REFERENCES public.hosts(id) ON DELETE CASCADE""",
    """ALTER TABLE ONLY public.vm_mappings
    ADD CONSTRAINT fk_vm_mappings_proxmox_node_id_proxmox_nodes FOREIGN KEY (proxmox_node_id) REFERENCES public.proxmox_nodes(id) ON DELETE CASCADE;""",
)


SEED_STATEMENTS = (
    """INSERT INTO public.git_repositories (id, name, url, branch, auth_type, ssh_key_id, encrypted_https_token, webhook_secret, last_commit_sha, last_sync_at, created_at, updated_at) VALUES (1, 'labdog-playbooks', 'https://github.com/open-labdog/labdog-playbooks', 'main', 'none', NULL, NULL, NULL, NULL, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
    """INSERT INTO public.action_packs (id, name, enabled, last_synced_at, last_sync_status, last_sync_error, current_sha, created_at, updated_at, source_type, git_repository_id, path, local_path, "position") VALUES (1, 'labdog-playbooks', true, NULL, NULL, NULL, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'git', 1, '', NULL, 0)""",
    """INSERT INTO public.app_settings (id, key, value, value_type, description, updated_at, updated_by) VALUES (1, 'drift.check_interval_minutes', '30', 'int', 'Minutes between automatic drift checks', CURRENT_TIMESTAMP, NULL)""",
    """INSERT INTO public.app_settings (id, key, value, value_type, description, updated_at, updated_by) VALUES (2, 'ssh.connect_timeout', '10', 'int', 'SSH connection timeout in seconds', CURRENT_TIMESTAMP, NULL)""",
    """INSERT INTO public.app_settings (id, key, value, value_type, description, updated_at, updated_by) VALUES (3, 'ansible.playbook_timeout', '300', 'int', 'Ansible playbook execution timeout in seconds', CURRENT_TIMESTAMP, NULL)""",
    """INSERT INTO public.app_settings (id, key, value, value_type, description, updated_at, updated_by) VALUES (4, 'discovery.scan_timeout', '1.0', 'float', 'Per-host TCP scan timeout during discovery (seconds)', CURRENT_TIMESTAMP, NULL)""",
    """INSERT INTO public.app_settings (id, key, value, value_type, description, updated_at, updated_by) VALUES (5, 'discovery.max_concurrent', '100', 'int', 'Maximum concurrent connections during network scan', CURRENT_TIMESTAMP, NULL)""",
    """INSERT INTO public.app_settings (id, key, value, value_type, description, updated_at, updated_by) VALUES (6, 'ssh.idle_timeout_seconds', '1800', 'int', 'SSH terminal idle timeout before auto-disconnect (seconds)', CURRENT_TIMESTAMP, NULL)""",
    """INSERT INTO public.app_settings (id, key, value, value_type, description, updated_at, updated_by) VALUES (7, 'logging.audit_retention_days', '90', 'int', 'Days to retain audit log entries', CURRENT_TIMESTAMP, NULL)""",
    """INSERT INTO public.app_settings (id, key, value, value_type, description, updated_at, updated_by) VALUES (8, 'logging.level', 'info', 'string', 'Application log level', CURRENT_TIMESTAMP, NULL)""",
    """INSERT INTO public.app_settings (id, key, value, value_type, description, updated_at, updated_by) VALUES (9, 'celery.concurrency', '4', 'int', 'Number of Celery worker processes (requires restart)', CURRENT_TIMESTAMP, NULL)""",
)


def upgrade() -> None:
    for stmt in SCHEMA_STATEMENTS:
        op.execute(stmt)
    for stmt in SEED_STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    # The squashed baseline drops the entire public schema on
    # downgrade. There is no incremental revert path to a
    # pre-existing intermediate revision.
    op.execute("DROP SCHEMA public CASCADE")
    op.execute("CREATE SCHEMA public")
