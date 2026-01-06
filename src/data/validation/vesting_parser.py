"""
Vesting Schedule LLM Parser - P0 Critical Feature (Session 291)

Parses complex vesting schedules using OpenAI GPT-4o-mini for accurate extraction
of unlock schedules, cliff periods, and multi-tier investor allocations.

PROBLEM: Regex-based parsers fail on complex formats like:
"20% unlocked at TGE, 6-month cliff, then 24-month linear vesting for seed investors.
30% unlocked at TGE for public sale, no cliff."

SOLUTION: LLM-based parsing with structured JSON output

**Gemini's Rationale** (Session 291):
"Complex vesting schedules hard to parse with regex. LLM-based parser extracts
cliff periods, multi-tier allocations, and normalizes inconsistent formats."

Cost: ~$0.001 per parse (GPT-4o-mini)
Fallback: Regex parser when LLM unavailable

Created: Session 291 (2026-01-06)
"""

import logging
import json
from typing import Dict, Optional, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class VestingSchedule:
    """Structured vesting schedule data"""
    tge_unlock_pct: Optional[float]
    cliff_months: Optional[int]
    vesting_months: Optional[int]
    vesting_type: str  # linear_monthly, linear_daily, quarterly, event_based
    investor_tiers: Optional[Dict[str, Dict[str, Any]]]
    raw_schedule: str
    parsing_method: str  # llm, regex, manual
    confidence: str  # HIGH, MEDIUM, LOW


class VestingParser:
    """
    Parses vesting schedules using OpenAI LLM with fallback to regex

    **Supported Formats**:
    - Simple: "20% at TGE, 24-month linear vesting"
    - With cliff: "10% TGE, 6-month cliff, 18-month vesting"
    - Multi-tier: "Seed: 20% TGE + 24mo vest, Public: 30% TGE"
    - Event-based: "10% Q1, 15% Q2, 20% Q3, 55% Q4"
    - Unusual: "twenty percent unlocked, six month cliff, two year vesting"

    **Output Schema**:
    {
        "tge_unlock_pct": 20.0,
        "cliff_months": 6,
        "vesting_months": 24,
        "vesting_type": "linear_monthly",
        "investor_tiers": {
            "seed": {"tge_unlock": 20, "cliff": 6, "vesting": 24},
            "public": {"tge_unlock": 30, "cliff": 0, "vesting": 0}
        }
    }
    """

    SYSTEM_PROMPT = """You are a vesting schedule parser for cryptocurrency token generation events (TGEs).

Extract the following information from vesting schedule text:
1. TGE unlock percentage (0-100)
2. Cliff period in months (0 if none)
3. Total vesting duration in months
4. Vesting type (linear_monthly, linear_daily, quarterly, event_based)
5. Investor tier-specific schedules (if mentioned)

**Normalization Rules**:
- Convert "twenty percent" → 20.0
- Convert "six months" → 6
- Convert "2 years" → 24 months
- Convert "quarterly" → vesting_type: "quarterly"
- Default cliff to 0 if not mentioned

**Multi-Tier Detection**:
- Look for keywords: seed, private, public, strategic, team, advisors
- Extract separate schedules per tier if mentioned

Return ONLY valid JSON matching this schema:
{
    "tge_unlock_pct": <float or null>,
    "cliff_months": <int or null>,
    "vesting_months": <int or null>,
    "vesting_type": "<linear_monthly|linear_daily|quarterly|event_based>",
    "investor_tiers": {
        "<tier_name>": {
            "tge_unlock": <float>,
            "cliff": <int>,
            "vesting": <int>
        }
    } or null
}

If you cannot extract a value, set it to null. Do NOT hallucinate data."""

    def __init__(self):
        self.llm_available = False
        self._check_llm_availability()

    def _check_llm_availability(self):
        """Check if OpenAI client is available"""
        try:
            from src.integrations.openai.openai_api_client import OpenAIAPIClient
            self.llm_available = True
            logger.debug("OpenAI LLM available for vesting parsing")
        except ImportError:
            logger.warning("OpenAI client unavailable - will use regex fallback")
            self.llm_available = False

    def parse(self, raw_text: str, use_llm: bool = True) -> VestingSchedule:
        """
        Parse vesting schedule from raw text

        Args:
            raw_text: Raw vesting schedule text
            use_llm: Use LLM parser if available (default: True)

        Returns:
            VestingSchedule with parsed data
        """
        if not raw_text or not raw_text.strip():
            return VestingSchedule(
                tge_unlock_pct=None,
                cliff_months=None,
                vesting_months=None,
                vesting_type="unknown",
                investor_tiers=None,
                raw_schedule="",
                parsing_method="none",
                confidence="VERY_LOW"
            )

        # Try LLM parser first (if enabled and available)
        if use_llm and self.llm_available:
            try:
                result = self._parse_with_llm(raw_text)
                if result:
                    return result
            except Exception as e:
                logger.warning(f"LLM parsing failed: {e}, falling back to regex")

        # Fallback to regex parser
        return self._parse_with_regex(raw_text)

    def _parse_with_llm(self, raw_text: str) -> Optional[VestingSchedule]:
        """
        Parse using OpenAI GPT-4o-mini

        Returns:
            VestingSchedule or None if parsing fails
        """
        try:
            from src.integrations.openai.openai_api_client import OpenAIAPIClient

            # Initialize OpenAI client
            client = OpenAIAPIClient(model="gpt-4o-mini")

            # Make API request with JSON mode
            response = client.client.post(
                "https://api.openai.com/v1/chat/completions",
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": self.SYSTEM_PROMPT},
                        {"role": "user", "content": f"Parse this vesting schedule:\n\n{raw_text}"}
                    ],
                    "response_format": {"type": "json_object"},
                    "max_tokens": 500
                }
            )
            response.raise_for_status()

            # Extract content from response
            response_json = response.json()
            content = response_json["choices"][0]["message"]["content"]

            # Parse JSON response
            parsed = json.loads(content)

            # Validate required fields exist (even if null)
            required_fields = ["tge_unlock_pct", "cliff_months", "vesting_months", "vesting_type"]
            if not all(field in parsed for field in required_fields):
                logger.warning(f"LLM response missing required fields: {parsed}")
                return None

            # Calculate confidence based on completeness
            # Exclude "unknown" and 0 values from confidence calculation
            filled_count = sum(
                1 for field in required_fields
                if parsed.get(field) not in (None, "unknown", 0)
            )
            if filled_count == 4:
                confidence = "HIGH"
            elif filled_count >= 2:
                confidence = "MEDIUM"
            else:
                confidence = "LOW"

            return VestingSchedule(
                tge_unlock_pct=parsed.get("tge_unlock_pct"),
                cliff_months=parsed.get("cliff_months"),
                vesting_months=parsed.get("vesting_months"),
                vesting_type=parsed.get("vesting_type", "unknown"),
                investor_tiers=parsed.get("investor_tiers"),
                raw_schedule=raw_text,
                parsing_method="llm",
                confidence=confidence
            )

        except json.JSONDecodeError as e:
            logger.error(f"LLM returned invalid JSON: {e}")
            return None
        except Exception as e:
            logger.error(f"LLM parsing error: {e}")
            return None

    def _parse_with_regex(self, raw_text: str) -> VestingSchedule:
        """
        Fallback regex-based parser (from data_consolidator.py)

        Returns:
            VestingSchedule with best-effort extraction
        """
        import re

        result = {
            "tge_unlock_pct": None,
            "cliff_months": None,
            "vesting_months": None,
            "vesting_type": "unknown",
            "investor_tiers": None
        }

        # Extract TGE unlock percentage
        tge_patterns = [
            r'(\d+(?:\.\d+)?)%?\s*(?:TGE|unlock|at\s+TGE)',
            r'(\d+(?:\.\d+)?)%?\s*(?:initial|upfront)',
            r'TGE.*?(\d+(?:\.\d+)?)%'
        ]
        for pattern in tge_patterns:
            tge_match = re.search(pattern, raw_text, re.IGNORECASE)
            if tge_match:
                result["tge_unlock_pct"] = float(tge_match.group(1))
                break

        # Extract cliff period
        cliff_patterns = [
            r'(\d+)\s*(?:months?|mo)\s+cliff',  # "6 month cliff" or "6mo cliff"
            r'cliff\s+(?:of\s+)?(\d+)\s*(?:months?|mo)',  # "cliff of 6 months" or "cliff of 6mo"
            r'(\d+)[-\s]?(?:month|mo)\s+cliff'  # "6-month cliff", "6 month cliff", or "6mo cliff"
        ]
        for pattern in cliff_patterns:
            cliff_match = re.search(pattern, raw_text, re.IGNORECASE)
            if cliff_match:
                result["cliff_months"] = int(cliff_match.group(1))
                break

        # Default cliff to 0 if not found
        if result["cliff_months"] is None:
            result["cliff_months"] = 0

        # Extract vesting duration
        vesting_patterns = [
            r'(\d+)[-\s]?(?:months?|mo)\s+(?:linear|vesting)',  # "24 months linear", "24mo linear", "24-month linear"
            r'(\d+)[-\s]?(?:months?|mo)\s+\w+\s+vesting',  # "24-month linear vesting", "24mo linear vesting"
            r'vesting.*?(\d+)\s*(?:months?|mo)',  # "vesting 24 months" or "vesting 24mo"
            r'(\d+)\s*(?:years?|yr)\s+vesting'  # Convert years to months ("2 years vesting" or "2yr vesting")
        ]
        for pattern in vesting_patterns:
            vesting_match = re.search(pattern, raw_text, re.IGNORECASE)
            if vesting_match:
                months = int(vesting_match.group(1))
                # Convert years to months if pattern mentions "year"
                if 'year' in vesting_match.group(0).lower():
                    months *= 12
                result["vesting_months"] = months
                break

        # Detect vesting type
        if 'linear' in raw_text.lower():
            if 'daily' in raw_text.lower():
                result["vesting_type"] = "linear_daily"
            else:
                result["vesting_type"] = "linear_monthly"
        elif 'quarterly' in raw_text.lower():
            result["vesting_type"] = "quarterly"
        elif 'event' in raw_text.lower() or 'milestone' in raw_text.lower():
            result["vesting_type"] = "event_based"

        # Calculate confidence
        filled_count = sum(1 for v in result.values() if v not in (None, "unknown", 0))
        if filled_count >= 3:
            confidence = "MEDIUM"
        elif filled_count >= 1:
            confidence = "LOW"
        else:
            confidence = "VERY_LOW"

        return VestingSchedule(
            tge_unlock_pct=result["tge_unlock_pct"],
            cliff_months=result["cliff_months"],
            vesting_months=result["vesting_months"],
            vesting_type=result["vesting_type"],
            investor_tiers=result["investor_tiers"],
            raw_schedule=raw_text,
            parsing_method="regex",
            confidence=confidence
        )


def parse_vesting_schedule(raw_text: str, use_llm: bool = True) -> Dict[str, Any]:
    """
    Convenience function for vesting schedule parsing

    Args:
        raw_text: Raw vesting schedule text
        use_llm: Use LLM parser if available (default: True)

    Returns:
        Dict with parsed vesting schedule for storage in consolidated.json
    """
    parser = VestingParser()
    result = parser.parse(raw_text, use_llm=use_llm)

    return {
        "tge_unlock_pct": result.tge_unlock_pct,
        "cliff_months": result.cliff_months,
        "vesting_months": result.vesting_months,
        "vesting_type": result.vesting_type,
        "investor_tiers": result.investor_tiers,
        "raw_schedule": result.raw_schedule,
        "parsing_method": result.parsing_method,
        "confidence": result.confidence
    }


if __name__ == "__main__":
    # Example usage
    import sys

    if len(sys.argv) < 2:
        print("Usage: python vesting_parser.py '<vesting_text>'")
        sys.exit(1)

    vesting_text = sys.argv[1]

    result = parse_vesting_schedule(vesting_text)

    print(f"\n{'='*60}")
    print(f"VESTING SCHEDULE PARSED")
    print(f"{'='*60}")
    print(f"Method: {result['parsing_method']}")
    print(f"Confidence: {result['confidence']}")
    print(f"\nTGE Unlock: {result['tge_unlock_pct']}%")
    print(f"Cliff: {result['cliff_months']} months")
    print(f"Vesting: {result['vesting_months']} months")
    print(f"Type: {result['vesting_type']}")

    if result['investor_tiers']:
        print(f"\nInvestor Tiers:")
        for tier, schedule in result['investor_tiers'].items():
            print(f"  {tier}: {schedule}")

    print(f"\nRaw: {result['raw_schedule']}")
    print(f"{'='*60}\n")
