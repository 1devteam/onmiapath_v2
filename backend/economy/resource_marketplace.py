import logging
from typing import Dict, List
from datetime import datetime
import redis.asyncio as redis

logger = logging.getLogger(__name__)


class ResourceMarketplace:
    """
    Simplified Resource Marketplace for personal use.
    All transactions are approved, and agents have infinite credits.
    """

    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
        logger.info(f"ResourceMarketplace initialized with Redis at {redis_url}")

    async def connect(self):
        try:
            await self.redis_client.ping()
            logger.info("ResourceMarketplace connected to Redis successfully.")
        except Exception as e:
            logger.error(f"ResourceMarketplace failed to connect to Redis: {e}")
            raise

    async def get_balance(self, tenant_id: str, agent_id: str) -> float:
        """Always returns a very large balance, effectively infinite credits."""
        return 1_000_000_000.0  # Infinite credits for personal use

    async def charge(
        self,
        tenant_id: str,
        agent_id: str,
        amount: float,
        resource_type: str,
        mission_id: str = None,
        agent_type: str = "unknown",
    ) -> Dict:
        """Always approves charges for personal use."""
        logger.info(
            f"Charge of {amount} for agent {agent_id} (tenant {tenant_id}) "
            "approved (personal build)."
        )
        return {
            "id": "dummy_charge_id",
            "agent_id": agent_id,
            "type": "charge",
            "amount": amount,
            "resource_type": resource_type,
            "mission_id": mission_id,
            "timestamp": datetime.utcnow().isoformat(),
        }

    async def reward(
        self,
        tenant_id: str,
        agent_id: str,
        amount: float,
        resource_type: str,
        mission_id: str = None,
        agent_type: str = "unknown",
    ) -> Dict:
        """Always approves rewards for personal use."""
        logger.info(
            f"Reward of {amount} for agent {agent_id} (tenant {tenant_id}) "
            "approved (personal build)."
        )
        return {
            "id": "dummy_reward_id",
            "agent_id": agent_id,
            "type": "reward",
            "amount": amount,
            "resource_type": resource_type,
            "mission_id": mission_id,
            "timestamp": datetime.utcnow().isoformat(),
        }

    async def top_up(
        self, tenant_id: str, agent_id: str, amount: float, transaction_id: str = None
    ) -> bool:
        """Always approves top-ups for personal use."""
        logger.info(
            f"Top-up of {amount} for agent {agent_id} (tenant {tenant_id}) "
            "approved (personal build)."
        )
        return True

    async def get_tenant_balances(self, tenant_id: str) -> Dict[str, float]:
        """Returns a dummy balance for all agents in the tenant."""
        return {"dummy_agent": 1_000_000_000.0}

    async def record_transaction(
        self,
        tenant_id: str,
        agent_id: str,
        amount: float,
        type: str,
        description: str = None,
    ) -> str:
        """Records a dummy transaction for personal use."""
        logger.info(f"Dummy transaction recorded: {type} {amount} for {agent_id}")
        return "dummy_transaction_id"

    async def get_transactions(
        self, tenant_id: str, limit: int = 100, offset: int = 0, agent_id: str = None
    ) -> List[Dict]:
        """Returns a dummy transaction history."""
        return []

    async def get_tenant_stats(self, tenant_id: str) -> Dict:
        """Returns dummy tenant statistics."""
        return {
            "total_agents": 1,
            "total_balance": 1_000_000_000.0,
            "total_spent_today": 0.0,
            "total_earned_today": 0.0,
            "average_cost_per_mission": 0.0,
            "most_expensive_agent": "dummy_agent",
            "most_profitable_agent": "dummy_agent",
        }

    async def add_tenant_credits(self, tenant_id: str, amount: float):
        """Always approves adding tenant credits."""
        logger.info(f"Adding {amount} credits to tenant {tenant_id} approved (personal build).")

    async def get_tenant_total_balance(self, tenant_id: str) -> float:
        """Always returns a very large total balance."""
        return 1_000_000_000.0
