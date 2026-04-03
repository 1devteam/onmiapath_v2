"""
Impact Assessment for Omnipath V2.

This module assesses potential impact of asset usage across three dimensions:
1. Business Impact (40%): Revenue, customers, regulatory penalties, reputation
2. Technical Impact (35%): Dependencies, data volume, blast radius, RTO
3. Compliance Impact (25%): Requirements, audit trails, documentation, reporting

Author: Dev Team Lead
Date: 2026-02-26
Built with Pride for Obex Blackvault
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from .risk_scoring import RiskTier
from ..registry.asset_registry import get_registry


class ImpactDimension(str, Enum):
    """Impact assessment dimensions."""

    BUSINESS = "business"
    TECHNICAL = "technical"
    COMPLIANCE = "compliance"


@dataclass
class ImpactScore:
    """
    Impact assessment for an asset.

    Attributes:
        asset_id: AIAsset identifier
        business_impact: Business impact score (0-100)
        technical_impact: Technical impact score (0-100)
        compliance_impact: Compliance impact score (0-100)
        overall_impact: Weighted overall impact (0-100)
        blast_radius: Number of affected assets
        affected_systems: List of affected system names
        assessed_at: Assessment timestamp
        assessed_by: Who performed assessment
    """

    asset_id: str
    business_impact: float  # 0-100
    technical_impact: float  # 0-100
    compliance_impact: float  # 0-100
    overall_impact: float  # 0-100
    blast_radius: int
    affected_systems: List[str]
    assessed_at: datetime
    assessed_by: str = "impact_assessor"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "asset_id": self.asset_id,
            "business_impact": self.business_impact,
            "technical_impact": self.technical_impact,
            "compliance_impact": self.compliance_impact,
            "overall_impact": self.overall_impact,
            "blast_radius": self.blast_radius,
            "affected_systems": self.affected_systems,
            "assessed_at": self.assessed_at.isoformat(),
            "assessed_by": self.assessed_by,
        }


@dataclass
class MitigationStrategy:
    """
    Recommended mitigation strategy.

    Attributes:
        strategy_id: Unique identifier
        name: Strategy name
        description: Detailed description
        risk_tier: Risk tier this applies to
        required: Whether this is required or optional
        implementation_effort: Low/Medium/High
        effectiveness: Effectiveness score (0-1)
        cost_estimate: Optional cost estimate
    """

    strategy_id: str
    name: str
    description: str
    risk_tier: RiskTier
    required: bool
    implementation_effort: str  # Low/Medium/High
    effectiveness: float  # 0-1
    cost_estimate: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "strategy_id": self.strategy_id,
            "name": self.name,
            "description": self.description,
            "risk_tier": self.risk_tier.value,
            "required": self.required,
            "implementation_effort": self.implementation_effort,
            "effectiveness": self.effectiveness,
            "cost_estimate": self.cost_estimate,
        }


class ImpactAssessor:
    """
    Singleton for assessing asset impact and generating mitigation strategies.

    The assessor evaluates three dimensions:
    - Business: Revenue, customers, penalties, reputation
    - Technical: Dependencies, data volume, blast radius, RTO
    - Compliance: Requirements, audit trails, documentation

    Example:
        assessor = get_impact_assessor()
        impact = assessor.assess_impact("medical-agent-001")

        print(f"Overall impact: {impact.overall_impact}")
        print(f"Blast radius: {impact.blast_radius} assets")

        strategies = assessor.get_mitigation_strategies(
            risk_tier=RiskTier.HIGH,
            impact_score=impact
        )
    """

    _instance: Optional["ImpactAssessor"] = None

    # Weight factors
    BUSINESS_WEIGHT = 0.40
    TECHNICAL_WEIGHT = 0.35
    COMPLIANCE_WEIGHT = 0.25

    # Mitigation strategies by risk tier
    MITIGATION_STRATEGIES = {
        RiskTier.CRITICAL: [
            {
                "strategy_id": "crit-01",
                "name": "Real-time Human Oversight",
                "description": "Mandatory real-time human oversight for all operations",
                "required": True,
                "implementation_effort": "High",
                "effectiveness": 0.95,
            },
            {
                "strategy_id": "crit-02",
                "name": "Dual Approval",
                "description": "Require approval from two independent authorities",
                "required": True,
                "implementation_effort": "Medium",
                "effectiveness": 0.90,
            },
            {
                "strategy_id": "crit-03",
                "name": "Continuous Monitoring",
                "description": "24/7 monitoring with automated alerts",
                "required": True,
                "implementation_effort": "High",
                "effectiveness": 0.85,
            },
            {
                "strategy_id": "crit-04",
                "name": "Immediate Incident Response",
                "description": "Dedicated incident response team on standby",
                "required": True,
                "implementation_effort": "High",
                "effectiveness": 0.90,
            },
            {
                "strategy_id": "crit-05",
                "name": "Monthly Audits",
                "description": "Comprehensive monthly compliance audits",
                "required": True,
                "implementation_effort": "Medium",
                "effectiveness": 0.80,
            },
        ],
        RiskTier.HIGH: [
            {
                "strategy_id": "high-01",
                "name": "Human Oversight",
                "description": "Human oversight required for operations",
                "required": True,
                "implementation_effort": "Medium",
                "effectiveness": 0.85,
            },
            {
                "strategy_id": "high-02",
                "name": "Single Approval",
                "description": "Require approval from compliance officer",
                "required": True,
                "implementation_effort": "Low",
                "effectiveness": 0.80,
            },
            {
                "strategy_id": "high-03",
                "name": "Daily Monitoring",
                "description": "Daily monitoring with periodic reviews",
                "required": True,
                "implementation_effort": "Medium",
                "effectiveness": 0.75,
            },
            {
                "strategy_id": "high-04",
                "name": "Incident Response Plan",
                "description": "Documented incident response procedures",
                "required": True,
                "implementation_effort": "Low",
                "effectiveness": 0.70,
            },
            {
                "strategy_id": "high-05",
                "name": "Quarterly Audits",
                "description": "Quarterly compliance audits",
                "required": True,
                "implementation_effort": "Low",
                "effectiveness": 0.75,
            },
        ],
        RiskTier.MEDIUM: [
            {
                "strategy_id": "med-01",
                "name": "Automated Monitoring",
                "description": "Automated monitoring with alerts",
                "required": True,
                "implementation_effort": "Low",
                "effectiveness": 0.70,
            },
            {
                "strategy_id": "med-02",
                "name": "Change Approval",
                "description": "Approval required for changes",
                "required": True,
                "implementation_effort": "Low",
                "effectiveness": 0.65,
            },
            {
                "strategy_id": "med-03",
                "name": "Weekly Reviews",
                "description": "Weekly operational reviews",
                "required": False,
                "implementation_effort": "Low",
                "effectiveness": 0.60,
            },
            {
                "strategy_id": "med-04",
                "name": "Standard Incident Response",
                "description": "Standard incident response procedures",
                "required": True,
                "implementation_effort": "Low",
                "effectiveness": 0.65,
            },
        ],
        RiskTier.LOW: [
            {
                "strategy_id": "low-01",
                "name": "Basic Monitoring",
                "description": "Basic monitoring and logging",
                "required": True,
                "implementation_effort": "Low",
                "effectiveness": 0.55,
            },
            {
                "strategy_id": "low-02",
                "name": "Major Change Approval",
                "description": "Approval for major changes only",
                "required": False,
                "implementation_effort": "Low",
                "effectiveness": 0.50,
            },
            {
                "strategy_id": "low-03",
                "name": "Monthly Reviews",
                "description": "Monthly operational reviews",
                "required": False,
                "implementation_effort": "Low",
                "effectiveness": 0.45,
            },
        ],
        RiskTier.MINIMAL: [
            {
                "strategy_id": "min-01",
                "name": "Minimal Monitoring",
                "description": "Basic logging for audit purposes",
                "required": False,
                "implementation_effort": "Low",
                "effectiveness": 0.40,
            },
            {
                "strategy_id": "min-02",
                "name": "Annual Reviews",
                "description": "Annual operational reviews",
                "required": False,
                "implementation_effort": "Low",
                "effectiveness": 0.35,
            },
        ],
    }

    def __init__(self):
        """Initialize the assessor."""
        self.registry = get_registry()

    def assess_impact(self, asset_id: str) -> Optional[ImpactScore]:
        """
        Assess impact for an asset.

        Args:
            asset_id: AIAsset identifier

        Returns:
            ImpactScore if asset exists, else None
        """
        asset = self.registry.get(asset_id)
        if not asset:
            return None

        # Calculate scores for each dimension
        business = self._calculate_business_impact(asset)
        technical = self._calculate_technical_impact(asset)
        compliance = self._calculate_compliance_impact(asset)

        # Weighted overall impact
        overall = (
            (business * self.BUSINESS_WEIGHT)
            + (technical * self.TECHNICAL_WEIGHT)
            + (compliance * self.COMPLIANCE_WEIGHT)
        )

        # Blast radius
        blast_radius = self._calculate_blast_radius(asset_id)

        # Affected systems
        affected_systems = self._get_affected_systems(asset)

        return ImpactScore(
            asset_id=asset_id,
            business_impact=round(business, 2),
            technical_impact=round(technical, 2),
            compliance_impact=round(compliance, 2),
            overall_impact=round(overall, 2),
            blast_radius=blast_radius,
            affected_systems=affected_systems,
            assessed_at=datetime.utcnow(),
        )

    def _calculate_business_impact(self, asset) -> float:
        """Calculate business impact score (0-100)."""
        score = 0.0

        # Factor 1: Revenue dependence
        if "revenue-critical" in asset.tags:
            score += 40.0
        elif "revenue-impacting" in asset.tags:
            score += 20.0

        # Factor 2: Customer impact
        if "customer-facing" in asset.tags:
            score += 30.0
        elif "internal-only" in asset.tags:
            score += 10.0

        # Factor 3: Regulatory penalties
        if "high-regulatory-risk" in asset.tags:
            score += 30.0

        return min(100.0, score)

    def _calculate_technical_impact(self, asset) -> float:
        """Calculate technical impact score (0-100)."""
        score = 0.0

        # Factor 1: Blast radius (direct dependents)
        dependents = self.registry.get_dependents(asset.asset_id)
        score += min(40.0, len(dependents) * 5.0)

        # Factor 2: Data volume/sensitivity
        if "large-data-volume" in asset.tags:
            score += 30.0

        # Factor 3: Recovery time objective (RTO)
        # Higher score for lower RTO (more critical)
        if hasattr(asset, "metadata") and asset.metadata:
            rto = asset.metadata.get("rto_hours", 24)
            if rto <= 1:
                score += 30.0
            elif rto <= 4:
                score += 20.0
            elif rto <= 12:
                score += 10.0

        return min(100.0, score)

    def _calculate_compliance_impact(self, asset) -> float:
        """Calculate compliance impact score (0-100)."""
        score = 0.0

        # Factor 1: PII/Sensitive data
        if "pii" in asset.tags or "phi" in asset.tags:
            score += 50.0

        # Factor 2: Regulatory framework
        if "gdpr" in asset.tags or "hipaa" in asset.tags:
            score += 30.0

        # Factor 3: Audit requirements
        if "audit-critical" in asset.tags:
            score += 20.0

        return min(100.0, score)

    def _calculate_blast_radius(self, asset_id: str) -> int:
        """Calculate total blast radius (direct + indirect dependents)."""
        dependents = self.registry.get_dependents(asset_id)
        if not dependents:
            return 0

        # Count direct dependents
        blast_radius = len(dependents)

        # Recursively count indirect dependents.
        # get_dependents() returns List[AIAsset] — extract .asset_id for
        # set membership checks and recursive calls.
        visited = {asset_id}
        for dependent in dependents:
            dep_id = dependent.asset_id
            if dep_id not in visited:
                visited.add(dep_id)
                blast_radius += self._count_recursive_dependents(dep_id, visited)

        return blast_radius

    def _count_recursive_dependents(self, asset_id: str, visited: set) -> int:
        """Recursively count dependents."""
        dependents = self.registry.get_dependents(asset_id)

        if not dependents:
            return 0

        count = 0
        for dependent in dependents:
            dep_id = dependent.asset_id
            if dep_id not in visited:
                visited.add(dep_id)
                count += 1
                count += self._count_recursive_dependents(dep_id, visited)

        return count

    def _get_affected_systems(self, asset) -> List[str]:
        """Get list of affected system names."""
        systems = []

        # Check metadata
        if hasattr(asset, "metadata") and asset.metadata:
            systems.extend(asset.metadata.get("affected_systems", []))

        # Check dependencies.
        # get_dependents() returns List[AIAsset] — iterate directly.
        dependents = self.registry.get_dependents(asset.asset_id)
        if dependents:
            for dependent in dependents:
                systems.append(dependent.name)

        return list(set(systems))  # Remove duplicates

    def estimate_recovery_time(self, asset_id: str) -> timedelta:
        """
        Estimate recovery time objective (RTO).

        Args:
            asset_id: AIAsset identifier

        Returns:
            Estimated recovery time
        """
        asset = self.registry.get(asset_id)
        if not asset:
            return timedelta(hours=24)  # Default 24 hours

        # Check metadata
        if hasattr(asset, "metadata") and asset.metadata:
            rto_hours = asset.metadata.get("rto_hours", 24)
            return timedelta(hours=rto_hours)

        # Estimate based on risk score
        if hasattr(asset, "risk_score") and asset.risk_score:
            if asset.risk_score.tier == RiskTier.CRITICAL:
                return timedelta(hours=1)
            elif asset.risk_score.tier == RiskTier.HIGH:
                return timedelta(hours=4)
            elif asset.risk_score.tier == RiskTier.MEDIUM:
                return timedelta(hours=12)

        return timedelta(hours=24)

    def get_mitigation_strategies(
        self,
        risk_tier: RiskTier,
        impact_score: Optional[ImpactScore] = None,
    ) -> List[MitigationStrategy]:
        """
        Get recommended mitigation strategies for a risk tier.

        Args:
            risk_tier: Risk tier
            impact_score: Optional impact score for customization

        Returns:
            List of mitigation strategies
        """
        strategies_data = self.MITIGATION_STRATEGIES.get(risk_tier, [])

        strategies = []
        for data in strategies_data:
            strategy = MitigationStrategy(
                strategy_id=data["strategy_id"],
                name=data["name"],
                description=data["description"],
                risk_tier=risk_tier,
                required=data["required"],
                implementation_effort=data["implementation_effort"],
                effectiveness=data["effectiveness"],
            )
            strategies.append(strategy)

        return strategies


# Singleton accessor
_assessor_instance: Optional[ImpactAssessor] = None


def get_impact_assessor() -> ImpactAssessor:
    """Get the singleton impact assessor instance."""
    global _assessor_instance
    if _assessor_instance is None:
        _assessor_instance = ImpactAssessor()
    return _assessor_instance
