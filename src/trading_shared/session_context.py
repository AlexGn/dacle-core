"""
DACLE Session Context Engine (Gap A)
Provides time-of-day based multipliers to adjust signal thresholds and sizing.
Focuses on UTC-based market sessions:
- Asian (00:00 - 08:00): Low vol, high noise (multiplier 0.5)
- London (08:00 - 13:30): Rising volume (multiplier 1.0)
- NY Open (13:30 - 16:30): Peak momentum (multiplier 1.3)
- NY Afternoon (16:30 - 21:00): Sustained (multiplier 1.1)
- Dead Zone (21:00 - 00:00): Low liquidity (multiplier 0.7)
"""

import time
from datetime import datetime, timezone

class SessionContextEngine:
    def __init__(self, config: dict = None):
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)

    def get_session_multiplier(self) -> float:
        """Returns a multiplier based on the current UTC hour."""
        if not self.enabled:
            return 1.0
            
        now = datetime.now(timezone.utc)
        hour = now.hour + now.minute / 60.0
        
        # Asian Session (00:00 - 08:00 UTC)
        if 0 <= hour < 8:
            return 0.5
            
        # London Session (08:00 - 13:30 UTC)
        if 8 <= hour < 13.5:
            return 1.0
            
        # NY Open / Peak Momentum (13:30 - 16:30 UTC)
        if 13.5 <= hour < 16.5:
            return 1.3
            
        # NY Afternoon (16.5 - 21:00 UTC)
        if 16.5 <= hour < 21:
            return 1.1
            
        # Dead Zone (21:00 - 00:00 UTC)
        return 0.7

    def get_session_name(self) -> str:
        """Returns the name of the current market session."""
        now = datetime.now(timezone.utc)
        hour = now.hour
        
        if 0 <= hour < 8: return "ASIAN"
        if 8 <= hour < 13: return "LONDON"
        if 13 <= hour < 17: return "NY_OPEN"
        if 17 <= hour < 21: return "NY_LATE"
        return "DEAD_ZONE"
