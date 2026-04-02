from typing import Dict, Any
import asyncio
import logging

from backend.agents.base.base_agent import BaseAgent

logger = logging.getLogger(__name__)

class CommanderAgentV3(BaseAgent):
    """
    Commander agent with emotional intelligence and simplified state management for personal use.
    """
    
    EMOTION_WEIGHTS = {
        "joy": 2,
        "resolve": 1,
        "curiosity": 3,
        "neutral": 1,
        "sadness": 8,
        "anger": 9,
        "fear": 7,
        "duty": 4,
        "grief": 9
    }
    
    def __init__(
        self,
        agent_id: str,
        agent_name: str,
        tenant_id: str,
        mission_payload: Dict[str, Any],
        configuration: Dict[str, Any] = None
    ):
        super().__init__(agent_id, agent_name, tenant_id, mission_payload, configuration)
        
        # State for personal build
        self.emotion = "neutral"
        self.intensity = 0.0
        self.risk_score = 0.0
        self.reflex_patterns = self.get_config("reflex_patterns", {})
        self.decision_count = 0
    
    def validate_mission(self) -> bool:
        """Validate mission payload."""
        required_fields = ["signal", "emotion", "intensity"]
        
        for field in required_fields:
            if field not in self.mission_payload:
                self.log_error(f"Missing required field: {field}")
                return False
        
        emotion = self.mission_payload.get("emotion")
        if emotion not in self.EMOTION_WEIGHTS:
            self.log_warning(f"Unknown emotion: {emotion}, using default weight")
        
        return True
    
    def execute_mission(self) -> Dict[str, Any]:
        """
        Execute the commander\"s decision-making process.
        
        Returns:
            Decision results with simplified state
        """
        signal = self.mission_payload["signal"]
        emotion = self.mission_payload["emotion"]
        intensity = self.mission_payload["intensity"]
        
        self.log_info(f"Evaluating signal \'{signal}\' with emotion \'{emotion}\' at intensity {intensity}")
        
        # Apply reflex substitution
        reflex_result = self._apply_reflex(signal, emotion)
        final_emotion = reflex_result["final_emotion"]
        
        # Calculate risk score
        risk_score = self._calculate_risk(final_emotion, intensity)
        
        # Determine pulse
        pulse = "EXECUTED" if risk_score < 7 else "BLOCKED"
        
        result = {
            "signal": signal,
            "original_emotion": emotion,
            "final_emotion": final_emotion,
            "intensity": intensity,
            "risk_score": risk_score,
            "pulse": pulse,
            "reflex_status": reflex_result["status"],
            "rationale": reflex_result.get("rationale"),
            "decision_count": self.decision_count + 1,
            "aggregate_version": 1 # Simplified for personal build
        }
        self.decision_count += 1
        
        self.log_info(f"Decision: {pulse} (risk score: {risk_score})")
        
        return result
    
    def _apply_reflex(self, signal: str, emotion: str) -> Dict[str, Any]:
        """Apply reflex substitution based on learned patterns."""
        if signal in self.reflex_patterns:
            pattern = self.reflex_patterns[signal]
            
            if emotion == pattern.get("substitute_from"):
                return {
                    "final_emotion": pattern.get("substitute_to"),
                    "status": "RECOMMENDED",
                    "rationale": pattern.get("rationale")
                }
        
        return {
            "final_emotion": emotion,
            "status": "UNCHANGED"
        }
    
    def _calculate_risk(self, emotion: str, intensity: float) -> float:
        """Calculate risk score based on emotion and intensity."""
        base_weight = self.EMOTION_WEIGHTS.get(emotion, 5)
        risk_score = min(10, base_weight + (intensity * 0.5))
        
        return round(risk_score, 2)
