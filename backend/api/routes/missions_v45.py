from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

from backend.security.auth_utils import get_current_user
from backend.orchestration.mission_executor import MissionExecutor
from backend.economy.resource_marketplace import ResourceMarketplace

# from backend.core.event_bus.nats_bus import NATSEventBus # Removed for simplification
from backend.integrations.llm.llm_factory import LLMFactory
from backend.models.domain.user import User

router = APIRouter(prefix="/api/v1/missions", tags=["missions"])

# Dependency injection
_executor: Optional[MissionExecutor] = None


def get_mission_executor() -> MissionExecutor:
    """Get or create mission executor singleton"""
    global _executor
    if _executor is None:
        marketplace = ResourceMarketplace()
        # event_bus = NATSEventBus() # Removed for simplification
        llm_factory = LLMFactory()
        _executor = MissionExecutor(marketplace, llm_factory)
    return _executor


class CreateMissionRequest(BaseModel):
    """Request to create a new mission"""

    name: str = Field(..., description="Human-readable mission name")
    goal: str = Field(..., description="Mission objective in natural language")
    budget: Optional[float] = Field(None, description="Budget limit in credits")
    priority: Optional[int] = Field(1, description="Priority level (1-10)")


class MissionResponse(BaseModel):
    """Mission execution response"""

    mission_id: str
    status: str
    message: Optional[str] = None
    output: Optional[str] = None
    cost: Optional[float] = None
    duration_seconds: Optional[float] = None
    agents_used: Optional[List[str]] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


@router.post("/", response_model=MissionResponse)
async def create_mission(
    request: CreateMissionRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    executor: MissionExecutor = Depends(get_mission_executor),
):
    """
    Create and execute a new mission

    This endpoint:
    1. Validates the mission with Guardian
    2. Creates an execution plan with Commander
    3. Executes the mission with appropriate agents
    4. Archives the results with Archivist
    5. Distributes rewards through the Agent Economy
    """
    import uuid

    mission_id = str(uuid.uuid4())

    # Execute mission in background
    background_tasks.add_task(
        executor.execute_mission,
        mission_id=mission_id,
        goal=request.goal,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        budget=request.budget,
        name=request.name,
    )

    return MissionResponse(
        mission_id=mission_id,
        status="accepted",
        message=f"Mission '{request.name}' started in background",
        output=None,
        cost=0.0,
        duration_seconds=None,
        agents_used=[],
    )


@router.get("/{mission_id}", response_model=MissionResponse)
async def get_mission(
    mission_id: str,
    current_user: User = Depends(get_current_user),
    executor: MissionExecutor = Depends(get_mission_executor),
):
    """Get mission status and results from Redis."""
    result = await executor.get_mission_state(mission_id)

    if not result or result.get("tenant_id") != current_user.tenant_id:
        raise HTTPException(status_code=404, detail="Mission not found")

    return MissionResponse(
        mission_id=mission_id,
        status=result["status"],
        message=f"Mission '{result.get('name', 'unnamed')}' status retrieved",
        output=result.get("output"),
        cost=result.get("cost"),
        duration_seconds=result.get("duration_seconds"),
        agents_used=result.get("agents_used"),
        created_at=result.get("created_at"),
    )


@router.get("/", response_model=List[MissionResponse])
async def list_missions(
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    executor: MissionExecutor = Depends(get_mission_executor),
):
    """List all missions for the current tenant from Redis."""
    missions = await executor.list_tenant_missions(
        tenant_id=current_user.tenant_id, limit=limit, offset=offset
    )

    return [
        MissionResponse(
            mission_id=m["mission_id"],
            status=m["status"],
            message=f"Mission '{m.get('name', 'unnamed')}' listed",
            output=m.get("output"),
            cost=m.get("cost"),
            duration_seconds=m.get("duration_seconds"),
            agents_used=m.get("agents_used"),
            created_at=m.get("created_at"),
        )
        for m in missions
    ]
