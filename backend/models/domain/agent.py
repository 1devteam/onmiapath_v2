from enum import Enum
from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import datetime


class AgentStatus(str, Enum):
    IDLE = "idle"
    BUSY = "busy"
    OFFLINE = "offline"
    ERROR = "error"


class Agent(BaseModel):
    id: str
    name: str
    type: str
    status: AgentStatus = AgentStatus.IDLE
    metadata: Dict[str, Any] = {}
    created_at: datetime = datetime.now()
