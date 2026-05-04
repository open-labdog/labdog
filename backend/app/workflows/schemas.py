from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UpdateWorkflowCreate(BaseModel):
    batch_size: int = Field(default=1, ge=1)
    schedule_cron: str | None = None
    pre_update_snapshot: bool = True
    auto_rollback: bool = True
    verification_prompt: str | None = None
    auto_reboot: bool = True
    enabled: bool = False


class UpdateWorkflowUpdate(BaseModel):
    batch_size: int | None = Field(default=None, ge=1)
    schedule_cron: str | None = Field(default=None)
    pre_update_snapshot: bool | None = Field(default=None)
    auto_rollback: bool | None = Field(default=None)
    verification_prompt: str | None = Field(default=None)
    auto_reboot: bool | None = Field(default=None)
    enabled: bool | None = Field(default=None)
    action_key: str | None = None
    action_parameters: dict | None = None


class UpdateWorkflowResponse(BaseModel):
    id: int
    group_id: int
    batch_size: int
    schedule_cron: str | None
    pre_update_snapshot: bool
    auto_rollback: bool
    verification_prompt: str | None
    auto_reboot: bool
    action_key: str
    action_parameters: dict
    enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WorkflowHostRunResponse(BaseModel):
    id: int
    host_id: int
    hostname: str
    step: str
    status: str
    snapshot_name: str | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class WorkflowRunResponse(BaseModel):
    id: int
    workflow_id: int
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    triggered_by: int | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WorkflowRunDetailResponse(WorkflowRunResponse):
    host_runs: list[WorkflowHostRunResponse]


class WorkflowTriggerResponse(BaseModel):
    run_id: int
    message: str
