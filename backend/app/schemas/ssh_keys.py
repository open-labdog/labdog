from datetime import datetime

from pydantic import BaseModel


class SSHKeyCreate(BaseModel):
    name: str
    private_key: str  # plaintext — encrypted before storage
    is_default: bool = False


class SSHKeyResponse(BaseModel):
    id: int
    name: str
    public_key: str | None
    is_default: bool
    created_at: datetime
    # NEVER include private_key or encrypted_private_key

    model_config = {"from_attributes": True}
