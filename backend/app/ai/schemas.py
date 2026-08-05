"""Request and response shapes for the AI API.

Follows the Grafana/Proxmox convention: the API key is write-only —
responses report ``has_api_key`` and never the value — and updates are
tri-state, where ``None`` keeps the stored secret, ``""`` clears it, and a
non-blank value replaces it.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.ai.models import AIProvider, AISession
from app.proxmox.schemas import _ca_cert_fingerprint, _validate_ca_cert_pem

ProviderType = Literal["openai_compat", "anthropic", "claude_cli"]
AutonomyLevel = Literal["read_only", "approval", "full_auto"]
SessionMode = Literal["chat", "scheduled", "verify", "alert_investigation"]


def _validate_base_url(v: str | None) -> str | None:
    if v is None or not v.strip():
        return None
    parsed = urlparse(v)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Base URL must start with http:// or https://")
    if not parsed.hostname:
        raise ValueError("Base URL must include a host")
    return v.rstrip("/")


# --- providers -----------------------------------------------------------


class AIProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    provider_type: ProviderType
    base_url: str | None = None
    model: str = Field(min_length=1, max_length=200)
    api_key: str | None = None
    verify_ssl: bool = True
    ca_cert_pem: str | None = None
    max_tokens: int = Field(default=8192, ge=256, le=200_000)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    is_default: bool = False
    allow_cloud_egress: bool = False
    input_cost_per_mtok: float = Field(default=0.0, ge=0.0)
    output_cost_per_mtok: float = Field(default=0.0, ge=0.0)
    monthly_budget_usd: float = Field(default=0.0, ge=0.0)
    enabled: bool = True

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, v: str | None) -> str | None:
        return _validate_base_url(v)

    @field_validator("ca_cert_pem")
    @classmethod
    def _ca(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        return _validate_ca_cert_pem(v)


class AIProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    provider_type: ProviderType | None = None
    base_url: str | None = None
    model: str | None = Field(default=None, min_length=1, max_length=200)
    #: None keeps the stored key, "" clears it, any other value replaces it.
    api_key: str | None = None
    verify_ssl: bool | None = None
    ca_cert_pem: str | None = None
    max_tokens: int | None = Field(default=None, ge=256, le=200_000)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    is_default: bool | None = None
    allow_cloud_egress: bool | None = None
    input_cost_per_mtok: float | None = Field(default=None, ge=0.0)
    output_cost_per_mtok: float | None = Field(default=None, ge=0.0)
    monthly_budget_usd: float | None = Field(default=None, ge=0.0)
    enabled: bool | None = None

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, v: str | None) -> str | None:
        # "" clears the URL; anything else must parse.
        return None if v == "" else _validate_base_url(v)

    @field_validator("ca_cert_pem")
    @classmethod
    def _ca(cls, v: str | None) -> str | None:
        # Tri-state: None leaves it unchanged, blank clears it.
        if v is None or not v.strip():
            return v
        return _validate_ca_cert_pem(v)


class AIProviderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: int
    name: str
    provider_type: str
    base_url: str | None
    model: str
    has_api_key: bool
    verify_ssl: bool
    ca_cert_fingerprint: str | None
    max_tokens: int
    temperature: float
    is_default: bool
    allow_cloud_egress: bool
    sends_data_offsite: bool
    input_cost_per_mtok: float
    output_cost_per_mtok: float
    monthly_budget_usd: float
    enabled: bool
    created_at: datetime
    updated_at: datetime


def provider_to_response(provider: AIProvider) -> AIProviderResponse:
    # Imported here: factory pulls in the provider backends, which the
    # schema module itself has no need for.
    from app.ai.providers.factory import sends_data_offsite

    return AIProviderResponse(
        id=provider.id,
        name=provider.name,
        provider_type=provider.provider_type,
        base_url=provider.base_url,
        model=provider.model,
        has_api_key=provider.encrypted_api_key is not None,
        verify_ssl=provider.verify_ssl,
        ca_cert_fingerprint=(
            _ca_cert_fingerprint(provider.ca_cert_pem) if provider.ca_cert_pem else None
        ),
        max_tokens=provider.max_tokens,
        temperature=provider.temperature,
        is_default=provider.is_default,
        allow_cloud_egress=provider.allow_cloud_egress,
        sends_data_offsite=sends_data_offsite(provider),
        input_cost_per_mtok=provider.input_cost_per_mtok,
        output_cost_per_mtok=provider.output_cost_per_mtok,
        monthly_budget_usd=provider.monthly_budget_usd,
        enabled=provider.enabled,
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


class AIProviderTestResponse(BaseModel):
    ok: bool
    message: str


# --- sessions ------------------------------------------------------------


class AISessionCreate(BaseModel):
    mission: str = Field(min_length=1, max_length=20_000)
    title: str | None = Field(default=None, max_length=200)
    provider_id: int | None = None
    autonomy_level: AutonomyLevel = "read_only"
    target_host_ids: list[int] = Field(default_factory=list)


class AISessionMessageRequest(BaseModel):
    """A follow-up turn in an existing chat session."""

    message: str = Field(min_length=1, max_length=20_000)


class AIToolCallResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tool_name: str
    arguments: dict | None
    classification: str
    status: str
    target_host_id: int | None
    result_summary: str | None
    started_at: datetime
    finished_at: datetime | None


class AIMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    seq: int
    role: str
    content: str
    tool_calls: list | None
    tool_call_id: str | None
    created_at: datetime


class AISessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_id: int | None
    mode: str
    title: str | None
    mission: str
    autonomy_level: str
    status: str
    target_host_ids: list | None
    action_run_id: int | None
    iterations: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    cost_unknown: bool
    command_count: int
    report_markdown: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class AISessionDetail(AISessionResponse):
    messages: list[AIMessageResponse] = Field(default_factory=list)
    tool_calls: list[AIToolCallResponse] = Field(default_factory=list)


def session_to_response(session: AISession) -> AISessionResponse:
    return AISessionResponse.model_validate(session)


# --- usage ---------------------------------------------------------------


class AIUsageDayResponse(BaseModel):
    usage_date: date
    provider_id: int | None
    provider_name: str | None
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    turn_count: int


class AIUsageSummary(BaseModel):
    """Spend against every active limit, plus the per-day breakdown."""

    day_spend: float
    month_spend: float
    day_limit: float
    month_limit: float
    warn_pct: int
    exceeded: bool
    reason: str
    days: list[AIUsageDayResponse] = Field(default_factory=list)
