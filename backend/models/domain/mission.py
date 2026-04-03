from enum import Enum
from pydantic import BaseModel
from typing import Dict, Any
from datetime import datetime


class MissionStatus(str, Enum):
    PENDING = "pending"
    PLANNING = "planning"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Mission(BaseModel):
    id: str
    tenant_id: str
    name: str
    description: str
    status: MissionStatus = MissionStatus.PENDING
    created_at: datetime = datetime.now()
    updated_at: datetime = datetime.now()
    metadata: Dict[str, Any] = {}
