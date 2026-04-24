import logging
import json
from typing import Dict, List, Any, Optional
from datetime import datetime
import redis.asyncio as redis

logger = logging.getLogger(__name__)


class ResourceMarketplace:
    """
    Production-grade Resource Marketplace.
    Uses Redis for atomic, persistent tracking of agent balances and transactions.
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

    def _get_balance_key(self, tenant_id: str, agent_id: str) -> str:
        return f"economy:balance:{tenant_id}:{agent_id}"

    def _get_transactions_key(self, tenant_id: str, agent_id: Optional[str] = None) -> str:
        if agent_id:
            return f"economy:transactions:{tenant_id}:{agent_id}"
        return f"economy:transactions:{tenant_id}"

    def _get_tenant_agents_key(self, tenant_id: str) -> str:
        return f"economy:tenant_agents:{tenant_id}"

    async def get_balance(self, tenant_id: str, agent_id: str) -> Dict[str, Any]:
        """Retrieve agent balance from Redis."""
        key = self._get_balance_key(tenant_id, agent_id)
        data = await self.redis_client.hgetall(key)

        if not data:
            # Initialize with default values if not exists
            # Default balance is 100.0 for new agents in this version
            return {
                "agent_id": agent_id,
                "type": "unknown",
                "balance": 100.0,
                "total_earned": 0.0,
                "total_spent": 0.0,
                "last_updated": datetime.utcnow().isoformat(),
            }

        return {
            "agent_id": agent_id,
            "type": data.get("type", "unknown"),
            "balance": float(data.get("balance", 0.0)),
            "total_earned": float(data.get("total_earned", 0.0)),
            "total_spent": float(data.get("total_spent", 0.0)),
            "last_updated": data.get("last_updated", datetime.utcnow().isoformat()),
        }

    async def charge(
        self,
        tenant_id: str,
        agent_id: str,
        amount: float,
        resource_type: str,
        mission_id: str = None,
        agent_type: str = "unknown",
    ) -> Dict:
        """Charge an agent for resource usage."""
        key = self._get_balance_key(tenant_id, agent_id)

        # Atomic update using pipeline
        async with self.redis_client.pipeline(transaction=True) as pipe:
            await pipe.hincrbyfloat(key, "balance", -amount)
            await pipe.hincrbyfloat(key, "total_spent", amount)
            await pipe.hset(key, "last_updated", datetime.utcnow().isoformat())
            await pipe.hset(key, "type", agent_type)
            await pipe.sadd(self._get_tenant_agents_key(tenant_id), agent_id)
            await pipe.execute()

        transaction_id = await self.record_transaction(
            tenant_id,
            agent_id,
            -amount,
            "charge",
            f"Charge for {resource_type} (Mission: {mission_id})",
        )

        logger.info(f"Charged {amount} to agent {agent_id} for {resource_type}")

        return {
            "id": transaction_id,
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
        """Reward an agent for mission completion."""
        key = self._get_balance_key(tenant_id, agent_id)

        async with self.redis_client.pipeline(transaction=True) as pipe:
            await pipe.hincrbyfloat(key, "balance", amount)
            await pipe.hincrbyfloat(key, "total_earned", amount)
            await pipe.hset(key, "last_updated", datetime.utcnow().isoformat())
            await pipe.hset(key, "type", agent_type)
            await pipe.sadd(self._get_tenant_agents_key(tenant_id), agent_id)
            await pipe.execute()

        transaction_id = await self.record_transaction(
            tenant_id,
            agent_id,
            amount,
            "reward",
            f"Reward for {resource_type} (Mission: {mission_id})",
        )

        logger.info(f"Rewarded {amount} to agent {agent_id} for {resource_type}")

        return {
            "id": transaction_id,
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
        """Top up agent balance."""
        key = self._get_balance_key(tenant_id, agent_id)

        async with self.redis_client.pipeline(transaction=True) as pipe:
            await pipe.hincrbyfloat(key, "balance", amount)
            await pipe.hset(key, "last_updated", datetime.utcnow().isoformat())
            await pipe.sadd(self._get_tenant_agents_key(tenant_id), agent_id)
            await pipe.execute()

        await self.record_transaction(
            tenant_id, agent_id, amount, "top_up", f"Balance top-up {transaction_id or ''}"
        )

        logger.info(f"Topped up {amount} for agent {agent_id}")
        return True

    async def get_tenant_balances(self, tenant_id: str) -> Dict[str, Dict[str, Any]]:
        """Retrieve balances for all agents in a tenant."""
        agent_ids = await self.redis_client.smembers(self._get_tenant_agents_key(tenant_id))
        balances = {}
        for agent_id in agent_ids:
            balances[agent_id] = await self.get_balance(tenant_id, agent_id)
        return balances

    async def record_transaction(
        self,
        tenant_id: str,
        agent_id: str,
        amount: float,
        type: str,
        description: str = None,
    ) -> str:
        """Record a transaction in Redis."""
        transaction_id = f"tx_{datetime.utcnow().timestamp()}_{agent_id}"
        transaction = {
            "id": transaction_id,
            "tenant_id": tenant_id,
            "agent_id": agent_id,
            "amount": amount,
            "type": type,
            "description": description,
            "timestamp": datetime.utcnow().isoformat(),
        }

        # Store in tenant and agent-specific lists
        tx_json = json.dumps(transaction)
        async with self.redis_client.pipeline(transaction=True) as pipe:
            await pipe.lpush(self._get_transactions_key(tenant_id), tx_json)
            await pipe.ltrim(self._get_transactions_key(tenant_id), 0, 999)  # Keep last 1000
            await pipe.lpush(self._get_transactions_key(tenant_id, agent_id), tx_json)
            await pipe.ltrim(
                self._get_transactions_key(tenant_id, agent_id), 0, 499
            )  # Keep last 500
            await pipe.execute()

        return transaction_id

    async def get_transactions(
        self, tenant_id: str, limit: int = 100, offset: int = 0, agent_id: str = None
    ) -> List[Dict]:
        """Retrieve transaction history."""
        key = self._get_transactions_key(tenant_id, agent_id)
        start = offset
        end = offset + limit - 1

        tx_list = await self.redis_client.lrange(key, start, end)
        return [json.loads(tx) for tx in tx_list]

    async def get_tenant_stats(self, tenant_id: str) -> Dict:
        """Calculate tenant statistics from agent balances."""
        balances = await self.get_tenant_balances(tenant_id)

        if not balances:
            return {
                "total_agents": 0,
                "total_balance": 0.0,
                "total_spent_today": 0.0,
                "total_earned_today": 0.0,
            }

        total_balance = sum(b["balance"] for b in balances.values())
        total_spent = sum(b["total_spent"] for b in balances.values())
        total_earned = sum(b["total_earned"] for b in balances.values())

        return {
            "total_agents": len(balances),
            "total_balance": total_balance,
            "total_spent_all_time": total_spent,
            "total_earned_all_time": total_earned,
        }

    async def add_tenant_credits(self, tenant_id: str, amount: float):
        """Add credits to a tenant (not specific to an agent)."""
        # For simplicity, we can track this in a tenant-level balance key
        key = f"economy:tenant_balance:{tenant_id}"
        await self.redis_client.incrbyfloat(key, amount)
        logger.info(f"Added {amount} credits to tenant {tenant_id}")

    async def get_tenant_total_balance(self, tenant_id: str) -> float:
        """Get the total balance available for a tenant."""
        key = f"economy:tenant_balance:{tenant_id}"
        balance = await self.redis_client.get(key)
        return float(balance) if balance else 0.0
