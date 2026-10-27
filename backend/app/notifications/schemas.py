"""Request and response shapes for the notifications API."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

TLSMode = Literal["none", "starttls", "tls"]


def _address(value: str) -> str:
    """A bare address, checked for shape only.

    Not pydantic's ``EmailStr``: it refuses special-use domains, and
    ``home.arpa`` — the domain RFC 8375 sets aside for home networks — is
    one of them, as is ``.local``. A homelab mail relay accepting
    ``labdog@home.arpa`` is the ordinary case here. Whether the server
    takes an address is the server's call, and the test button asks it.
    """
    value = value.strip()
    local, at, domain = value.rpartition("@")
    if not at or not local or not domain or any(c.isspace() for c in value) or "<" in value:
        raise ValueError("must be a plain address such as labdog@example.com")
    return value


Address = Annotated[str, Field(max_length=255), AfterValidator(_address)]


class EmailSettingsBase(BaseModel):
    enabled: bool = False
    host: str = Field(default="", max_length=255)
    port: int = Field(default=587, ge=1, le=65535)
    tls_mode: TLSMode = "starttls"
    username: str | None = Field(default=None, max_length=255)
    from_address: Address | Literal[""] = ""

    @model_validator(mode="after")
    def _complete_when_enabled(self) -> EmailSettingsBase:
        # Saving "on" with nowhere to send from would look configured
        # while every message failed. Refused here, where it can be fixed.
        if self.enabled and not (self.host.strip() and self.from_address):
            raise ValueError("A server and a From address are needed before email can be on.")
        return self


class EmailSettingsUpdate(EmailSettingsBase):
    #: ``None`` keeps the stored password; a value replaces it. Write-only:
    #: no response ever carries it back.
    password: str | None = Field(default=None, max_length=1000)
    clear_password: bool = False


class EmailTestRequest(EmailSettingsUpdate):
    #: Where the test goes. Defaults to the signed-in user's address.
    to: Address | None = None

    @model_validator(mode="after")
    def _needs_a_server(self) -> EmailTestRequest:
        if not (self.host.strip() and self.from_address):
            raise ValueError("Fill in the server and the From address to send a test.")
        return self


class EmailSettingsResponse(BaseModel):
    enabled: bool
    host: str
    port: int
    tls_mode: TLSMode
    username: str | None
    password_set: bool
    from_address: str
    updated_at: datetime | None
    #: ``notifications.public_url`` — echoed here because nothing with a
    #: link is sent without it, and the Email page is where that shows.
    public_url: str
    #: On, complete, and with a public URL: notifications will go out.
    ready: bool


class EmailTestResponse(BaseModel):
    success: bool
    message: str


class EventTypeResponse(BaseModel):
    key: str
    label: str
    description: str


class SubscriptionsBody(BaseModel):
    event_types: list[str]


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: str
    channel: str
    user_id: int | None
    recipient: str
    subject: str
    status: str
    attempts: int
    next_attempt_at: datetime
    last_attempt_at: datetime | None
    last_error: str | None
    created_at: datetime
    sent_at: datetime | None
