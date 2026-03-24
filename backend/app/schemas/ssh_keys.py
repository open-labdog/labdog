from datetime import datetime

from pydantic import BaseModel


class SSHKeyCreate(BaseModel):
    name: str
    private_key: str  # plaintext — encrypted before storage
    ssh_user: str = "root"
    is_default: bool = False


class SSHKeyUpdate(BaseModel):
    name: str | None = None
    ssh_user: str | None = None
    is_default: bool | None = None


class SSHKeyResponse(BaseModel):
    id: int
    name: str
    public_key: str | None
    ssh_user: str
    is_default: bool
    created_at: datetime
    # NEVER include private_key or encrypted_private_key

    model_config = {"from_attributes": True}
