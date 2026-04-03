import logging
from datetime import datetime
import json
import redis.asyncio as redis
from typing import List, Optional

from backend.config.settings import settings
from backend.models.domain.mission import MissionStatus
from backend.integrations.llm.llm_factory import LLMFactory

logger = logging.getLogger(__name__)


class MissionExecutor:
    """Orchestrates the lifecycle of a mission with durable Redis persistence."""

    def __init__(self, marketplace, llm_factory: LLMFactory):
        self.marketplace = marketplace
        # self.event_bus = event_bus # Removed for simplification
        self.llm_factory = llm_factory
        self._redis = redis.from_url(settings.REDIS_URL, decode_responses=True)

    def _get_mission_key(self, mission_id: str) -> str:
        return f"mission:{mission_id}"

    def _get_tenant_missions_key(self, tenant_id: str) -> str:
        return f"tenant_missions:{tenant_id}"

    async def _save_mission_state(self, mission_id: str, state: dict):
        """Save mission state to Redis and add to tenant mission list."""
        mission_key = self._get_mission_key(mission_id)
        tenant_id = state.get("tenant_id")

        # Add to tenant mission list if not already there
        if tenant_id:
            await self._redis.sadd(self._get_tenant_missions_key(tenant_id), mission_id)

        # Serialize complex types
        state_to_save = state.copy()
        for k, v in state_to_save.items():
            if isinstance(v, (list, dict)):
                state_to_save[k] = json.dumps(v)
            elif isinstance(v, datetime):
                state_to_save[k] = v.isoformat()

        await self._redis.hset(mission_key, mapping=state_to_save)
        # Set expiration for mission state (e.g., 30 days)
        await self._redis.expire(mission_key, 60 * 60 * 24 * 30)

    async def get_mission_state(self, mission_id: str) -> Optional[dict]:
        """Retrieve mission state from Redis."""
        mission_key = self._get_mission_key(mission_id)
        state = await self._redis.hgetall(mission_key)

        if not state:
            return None

        # Deserialize complex types
        for k, v in state.items():
            try:
                state[k] = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                pass
        return state

    async def list_tenant_missions(
        self, tenant_id: str, limit: int = 50, offset: int = 0
    ) -> List[dict]:
        """List all missions for a tenant from Redis."""
        mission_ids = await self._redis.smembers(self._get_tenant_missions_key(tenant_id))

        missions = []
        for mid in list(mission_ids)[offset : offset + limit]:
            m_state = await self.get_mission_state(mid)
            if m_state:
                missions.append(m_state)

        # Sort by created_at descending
        missions.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return missions

    async def execute_mission(
        self,
        mission_id: str,
        goal: str,
        tenant_id: str,
        user_id: str,
        budget: float = None,
        name: str = "unnamed",
    ) -> dict:
        """Execute a mission from planning to archival."""
        start_time = datetime.utcnow()
        mission_state = {
            "mission_id": mission_id,
            "name": name,
            "goal": goal,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "status": MissionStatus.PLANNING.value,
            "cost": 0.0,
            "agents_used": [],
            "created_at": start_time.isoformat(),
        }

        try:
            logger.info(f"Starting mission {mission_id}: {goal}")
            await self._save_mission_state(mission_id, mission_state)

            # Phase 1: Planning
            llm = self.llm_factory.create_llm(
                provider=settings.COMMANDER_PROVIDER, model=settings.COMMANDER_MODEL
            )
            plan_response = await llm.ainvoke(
                f"Create a 3-step plan for: {goal}. Respond with only the steps, one per line."
            )
            steps = [s.strip() for s in plan_response.content.split("\n") if s.strip()]
            mission_state.update({"steps": steps, "status": MissionStatus.PLANNING.value})
            await self._save_mission_state(mission_id, mission_state)

            # Phase 2: Execution
            outputs = []
            total_cost = 0.0
            for i, step in enumerate(steps):
                logger.info(f"Executing step {i+1}/{len(steps)} for mission {mission_id}: {step}")

                # Charge for the step
                logger.info(f"Charging for step {i+1}...")
                await self.marketplace.charge(
                    tenant_id, "executor", 1.0, "llm_call", mission_id=mission_id
                )
                total_cost += 1.0

                logger.info(f"Calling LLM for step {i+1}...")
                exec_llm = self.llm_factory.create_llm(
                    provider=settings.COMMANDER_PROVIDER, model=settings.COMMANDER_MODEL
                )
                resp = await exec_llm.ainvoke(step)
                logger.info(f"LLM responded for step {i+1}")
                outputs.append(f"Step {i+1}: {step}\nResult: {resp.content}")

                # Update cost in state
                mission_state["cost"] = total_cost
                mission_state["status"] = f"executing_step_{i+1}"
                await self._save_mission_state(mission_id, mission_state)

            # Phase 3: Finalization
            end_time = datetime.utcnow()
            duration = (end_time - start_time).total_seconds()

            mission_state.update(
                {
                    "status": MissionStatus.COMPLETED.value,
                    "output": "\n\n".join(outputs),
                    "duration_seconds": duration,
                    "completed_at": end_time.isoformat(),
                }
            )
            await self._save_mission_state(mission_id, mission_state)
            logger.info(f"Mission {mission_id} completed successfully")

            return mission_state

        except Exception as e:
            logger.error(f"Mission {mission_id} failed: {str(e)}", exc_info=True)
            mission_state.update({"status": MissionStatus.FAILED.value, "error": str(e)})
            await self._save_mission_state(mission_id, mission_state)
            return mission_state
