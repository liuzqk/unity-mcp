from typing import Any
from pydantic import BaseModel, Field
from models.models import ToolDefinitionModel

# Outgoing (Server -> Plugin)


class WelcomeMessage(BaseModel):
    type: str = "welcome"
    serverTimeout: int
    keepAliveInterval: int


class RegisteredMessage(BaseModel):
    type: str = "registered"
    session_id: str


class ExecuteCommandMessage(BaseModel):
    type: str = "execute"
    id: str
    name: str
    params: dict[str, Any]
    timeout: float
    envelope_sha256: str | None = None


class PingMessage(BaseModel):
    """Server-initiated ping to detect dead connections."""
    type: str = "ping"

# Incoming (Plugin -> Server)


class RegisterMessage(BaseModel):
    type: str = "register"
    project_name: str = "Unknown Project"
    project_hash: str
    unity_version: str = "Unknown"
    project_path: str | None = None  # Full path to project root (for focus nudging)
    receipt_protocol: int = 0


class RegisterToolsMessage(BaseModel):
    type: str = "register_tools"
    tools: list[ToolDefinitionModel]


class PongMessage(BaseModel):
    type: str = "pong"
    session_id: str | None = None


class CommandResultMessage(BaseModel):
    type: str = "command_result"
    id: str
    result: dict[str, Any] = Field(default_factory=dict)


class CommandReceiptMessage(BaseModel):
    type: str = "command_receipt"
    id: str
    project_hash: str
    name: str
    envelope_sha256: str
    state: str
    result: dict[str, Any] | None = None

# Session Info (API response)


class SessionDetails(BaseModel):
    project: str
    hash: str
    unity_version: str
    connected_at: str
    receipt_protocol: int = 0


class SessionList(BaseModel):
    sessions: dict[str, SessionDetails]
