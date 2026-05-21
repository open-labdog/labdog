from datetime import datetime

from pydantic import BaseModel, field_validator

from app.schemas._shared import validate_linux_username


class SSHKeyCreate(BaseModel):
    name: str
    private_key: str  # plaintext — encrypted before storage
    ssh_user: str = "root"
    is_default: bool = False

    @field_validator("ssh_user")
    @classmethod
    def validate_ssh_user(cls, v: str) -> str:
        return validate_linux_username(v)


class SSHKeyUpdate(BaseModel):
    name: str | None = None
    ssh_user: str | None = None
    is_default: bool | None = None

    @field_validator("ssh_user")
    @classmethod
    def validate_ssh_user(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return validate_linux_username(v)


class SSHKeyResponse(BaseModel):
    id: int
    name: str
    public_key: str | None
    ssh_user: str
    is_default: bool
    created_at: datetime
    # NEVER include private_key or encrypted_private_key

    model_config = {"from_attributes": True}
