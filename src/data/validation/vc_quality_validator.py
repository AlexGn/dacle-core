"""
VC Quality Validator - P0 Critical Feature (Session 291)

Multi-source VC validation using FREE APIs to improve conviction scoring accuracy.

PROBLEM: Current system uses binary "VC present: yes/no" which is insufficient.
Dump-prone VCs (short lockups, quick flips) score same as patient capital (Paradigm, a16z).

SOLUTION: Multi-tier VC quality scoring using:
1. CryptoRank API (FREE Sandbox tier) - Primary source for funding rounds
2. GPT-4o-mini semantic validation (existing, $0.16/month) - VC reputation analysis
3. Manual override support for David's VC expertise

**Gemini's Rationale** (Session 291):
"VC quality matters more than VC presence. Patient capital (Paradigm, a16z, Sequoia) =
bullish signal. Dump-prone VCs (KuCoin Labs, unlisted funds) = bearish signal."

Cost: $0/month (100% FREE tier usage)
Fallback: Manual VC quality scoring when APIs unavailable

Created: Session 291 (2026-01-06)
"""

import logging
import json
from typing import Dict, Optional, List, Any
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class VCTier(Enum):
    """VC reputation tiers based on crypto track record"""
    TIER_1 = "TIER_1"  # Top-tier: Paradigm, a16z, Sequoia, Polychain, Pantera
    TIER_2 = "TIER_2"  # Established: Binance Labs, Coinbase Ventures, Animoca
    TIER_3 = "TIER_3"  # Mid-tier: Regional VCs, smaller funds
    TIER_4 = "TIER_4"  # Unknown/dump-prone: Unverified, quick-flip funds
    UNKNOWN = "UNKNOWN"  # Not enough data


class VCQualityLevel(Enum):
    """VC quality classification for conviction scoring"""
    EXCELLENT = "EXCELLENT"  # Tier 1 VCs, long vesting, proven track record
    GOOD = "GOOD"           # Tier 2 VCs, standard vesting
    NEUTRAL = "NEUTRAL"     # Tier 3 VCs, mixed track record
    POOR = "POOR"           # Tier 4 VCs, short vesting, dump history
    INSUFFICIENT = "INSUFFICIENT"  # No VC data available


@dataclass
class VCInvestor:
    """VC investor information from CryptoRank"""
    name: str
    tier: VCTier
    is_lead: bool
    funding_stage: str  # SEED, SERIES_A, SERIES_B, STRATEGIC, etc.
    investment_date: Optional[str]
    logo_url: Optional[str]


@dataclass
class VCQualityResult:
    """VC quality validation result"""
    quality_level: VCQualityLevel
    conviction_modifier: float  # -1.0 to +1.0 adjustment to conviction score
    investors: List[VCInvestor]
    tier_distribution: Dict[str, int]  # Count by tier
    warning_message: Optional[str]
    data_source: str  # cryptorank, manual, gpt
    confidence: str  # HIGH, MEDIUM, LOW


# Tier 1: Top-tier crypto VCs (patient capital, proven winners)
TIER_1_VCS = {
    "paradigm", "a16z", "andreessen horowitz", "sequoia", "sequoia capital",
    "polychain", "polychain capital", "pantera", "pantera capital",
    "dragonfly", "dragonfly capital", "electric capital", "framework ventures",
    "multicoin", "multicoin capital", "1confirmation", "placeholder",
    "variant", "variant fund", "union square ventures", "usv"
}

# Tier 2: Established crypto VCs (credible, standard vesting)
TIER_2_VCS = {
    "binance labs", "coinbase ventures", "animoca", "animoca brands",
    "alameda research", "jump crypto", "three arrows capital", "3ac",
    "delphi digital", "galaxy digital", "dcg", "digital currency group",
    "blockchain capital", "okx ventures", "kucoin labs", "bybit ventures"
}

# Tier 4: Dump-prone/unverified VCs (short vesting, quick flips, poor track record)
TIER_4_VCS = {
    "anonymous", "undisclosed", "private investors", "strategic investors",
    "advisors", "community sale", "ico", "public sale"
}


class VCQualityValidator:
    """
    Multi-source VC quality validator with FREE API fallback chain

    **Data Sources** (in priority order):
    1. CryptoRank API (FREE Sandbox tier) - Funding rounds endpoint
    2. GPT-4o-mini (existing) - Semantic VC reputation analysis
    3. Manual override - David's VC expertise (consolidated.json)

    **VC Quality Scoring**:
    - EXCELLENT: ≥50% Tier 1 VCs → +0.5 conviction
    - GOOD: ≥30% Tier 1+2 VCs → +0.25 conviction
    - NEUTRAL: Mixed tiers → 0.0 conviction
    - POOR: ≥30% Tier 4 VCs → -0.5 conviction
    - INSUFFICIENT: No VC data → -0.25 conviction (conservative)
    """

    def __init__(self):
        self.cryptorank_available = False
        self._check_cryptorank_availability()

    def _check_cryptorank_availability(self):
        """Check if CryptoRank API client is available"""
        try:
            from src.data.tge_data_loaders import fetch_tge_data_from_cryptorank
            self.cryptorank_available = True
            logger.debug("CryptoRank API available for VC validation")
        except ImportError:
            logger.warning("CryptoRank client unavailable - will use GPT fallback")
            self.cryptorank_available = False

    def validate_vc_quality(
        self,
        symbol: str,
        existing_vc_data: Optional[Dict[str, Any]] = None,
        use_cryptorank: bool = True
    ) -> VCQualityResult:
        """
        Validate VC quality for a token

        Args:
            symbol: Token symbol (e.g., "POWER", "MONAD")
            existing_vc_data: Manually entered VC data from consolidated.json
            use_cryptorank: Use CryptoRank API if available (default: True)

        Returns:
            VCQualityResult with quality level and conviction modifier
        """
        # Priority 1: Try CryptoRank API (if available)
        if use_cryptorank and self.cryptorank_available:
            try:
                result = self._fetch_from_cryptorank(symbol)
                if result:
                    return result
            except Exception as e:
                logger.warning(f"CryptoRank VC fetch failed for {symbol}: {e}")

        # Priority 2: Use manual VC data (if provided)
        if existing_vc_data:
            return self._validate_manual_vc_data(existing_vc_data)

        # Priority 3: GPT-4o-mini semantic analysis (fallback)
        return self._validate_with_gpt(symbol)

    def _fetch_from_cryptorank(self, symbol: str) -> Optional[VCQualityResult]:
        """
        Fetch VC data from CryptoRank funding rounds endpoint

        Uses existing CryptoRank integration from tge_data_loaders.py
        Endpoint: /v2/funding-rounds (FREE tier)
        """
        try:
            from src.data.tge_data_loaders import fetch_tge_data_from_cryptorank

            # Fetch funding data (uses existing Redis cache)
            tge_data = fetch_tge_data_from_cryptorank(symbol)

            if not tge_data or "funding_rounds" not in tge_data:
                logger.debug(f"No funding rounds found for {symbol} on CryptoRank")
                return None

            funding_rounds = tge_data.get("funding_rounds", [])
            if not funding_rounds:
                return None

            # Extract VC investors from all funding rounds
            all_investors = []
            for round_data in funding_rounds:
                stage = round_data.get("stage", "UNKNOWN")
                date = round_data.get("date")
                funds = round_data.get("funds", [])

                for fund in funds:
                    investor = VCInvestor(
                        name=fund.get("name", "Unknown"),
                        tier=self._classify_vc_tier(fund.get("name", "")),
                        is_lead=fund.get("isLead", False),
                        funding_stage=stage,
                        investment_date=date,
                        logo_url=fund.get("logo")
                    )
                    all_investors.append(investor)

            if not all_investors:
                logger.debug(f"No VC investors found for {symbol}")
                return None

            # Calculate tier distribution
            tier_counts = {
                "TIER_1": 0,
                "TIER_2": 0,
                "TIER_3": 0,
                "TIER_4": 0,
                "UNKNOWN": 0
            }
            for investor in all_investors:
                tier_counts[investor.tier.value] += 1

            # Determine quality level based on tier distribution
            total_vcs = len(all_investors)
            tier1_pct = (tier_counts["TIER_1"] / total_vcs) * 100 if total_vcs > 0 else 0
            tier2_pct = (tier_counts["TIER_2"] / total_vcs) * 100 if total_vcs > 0 else 0
            tier4_pct = (tier_counts["TIER_4"] / total_vcs) * 100 if total_vcs > 0 else 0

            # Quality classification rules
            if tier1_pct >= 50:
                quality_level = VCQualityLevel.EXCELLENT
                modifier = +0.5
                warning = None
            elif (tier1_pct + tier2_pct) >= 30:
                quality_level = VCQualityLevel.GOOD
                modifier = +0.25
                warning = None
            elif tier4_pct >= 30:
                quality_level = VCQualityLevel.POOR
                modifier = -0.5
                warning = f"⚠️ HIGH DUMP RISK: {tier4_pct:.0f}% Tier 4 VCs (unverified/dump-prone)"
            else:
                quality_level = VCQualityLevel.NEUTRAL
                modifier = 0.0
                warning = None

            return VCQualityResult(
                quality_level=quality_level,
                conviction_modifier=modifier,
                investors=all_investors,
                tier_distribution=tier_counts,
                warning_message=warning,
                data_source="cryptorank",
                confidence="HIGH"
            )

        except Exception as e:
            logger.error(f"CryptoRank VC fetch error for {symbol}: {e}")
            return None

    def _classify_vc_tier(self, vc_name: str) -> VCTier:
        """
        Classify VC firm into tier based on reputation

        Args:
            vc_name: VC firm name (case-insensitive)

        Returns:
            VCTier classification
        """
        name_lower = vc_name.lower().strip()

        # Check Tier 1 (top-tier VCs)
        if any(tier1_vc in name_lower for tier1_vc in TIER_1_VCS):
            return VCTier.TIER_1

        # Check Tier 2 (established VCs)
        if any(tier2_vc in name_lower for tier2_vc in TIER_2_VCS):
            return VCTier.TIER_2

        # Check Tier 4 (dump-prone/unverified)
        if any(tier4_vc in name_lower for tier4_vc in TIER_4_VCS):
            return VCTier.TIER_4

        # Check for empty/invalid names
        if not name_lower or len(name_lower) < 3:
            return VCTier.UNKNOWN

        # Default: Tier 3 (regional/smaller VCs)
        return VCTier.TIER_3

    def _validate_manual_vc_data(
        self,
        vc_data: Dict[str, Any]
    ) -> VCQualityResult:
        """
        Validate manually entered VC data from consolidated.json

        Expected format:
        {
            "vc_investors": ["Paradigm", "a16z", "Sequoia"],
            "vc_quality_override": "EXCELLENT"  # Optional manual override
        }
        """
        vc_investors = vc_data.get("vc_investors", [])
        manual_override = vc_data.get("vc_quality_override")

        # If David manually set quality, use it
        if manual_override and manual_override in [level.value for level in VCQualityLevel]:
            quality_level = VCQualityLevel(manual_override)
            modifier = self._get_modifier_for_quality(quality_level)
            return VCQualityResult(
                quality_level=quality_level,
                conviction_modifier=modifier,
                investors=[],
                tier_distribution={},
                warning_message=None,
                data_source="manual",
                confidence="HIGH"
            )

        # Otherwise, classify VCs from list
        if not vc_investors:
            return VCQualityResult(
                quality_level=VCQualityLevel.INSUFFICIENT,
                conviction_modifier=-0.25,
                investors=[],
                tier_distribution={},
                warning_message="⚠️ NO VC DATA: Conservative -0.25 penalty applied",
                data_source="manual",
                confidence="LOW"
            )

        # Create investor objects
        investors = []
        tier_counts = {"TIER_1": 0, "TIER_2": 0, "TIER_3": 0, "TIER_4": 0, "UNKNOWN": 0}

        for vc_item in vc_investors:
            # Handle both string and dict formats
            # String format: "Framework Ventures"
            # Dict format: {"name": "Framework Ventures", "tier": "Tier 1"}
            if isinstance(vc_item, dict):
                vc_name = vc_item.get("name", "") or vc_item.get("vc_name", "") or str(vc_item)
            elif isinstance(vc_item, str):
                vc_name = vc_item
            else:
                vc_name = str(vc_item)

            tier = self._classify_vc_tier(vc_name)
            investors.append(VCInvestor(
                name=vc_name,
                tier=tier,
                is_lead=False,  # Unknown from manual data
                funding_stage="UNKNOWN",
                investment_date=None,
                logo_url=None
            ))
            tier_counts[tier.value] += 1

        # Calculate quality level (same logic as CryptoRank)
        total_vcs = len(investors)
        tier1_pct = (tier_counts["TIER_1"] / total_vcs) * 100 if total_vcs > 0 else 0
        tier2_pct = (tier_counts["TIER_2"] / total_vcs) * 100 if total_vcs > 0 else 0
        tier4_pct = (tier_counts["TIER_4"] / total_vcs) * 100 if total_vcs > 0 else 0

        if tier1_pct >= 50:
            quality_level = VCQualityLevel.EXCELLENT
            modifier = +0.5
            warning = None
        elif (tier1_pct + tier2_pct) >= 30:
            quality_level = VCQualityLevel.GOOD
            modifier = +0.25
            warning = None
        elif tier4_pct >= 30:
            quality_level = VCQualityLevel.POOR
            modifier = -0.5
            warning = f"⚠️ HIGH DUMP RISK: {tier4_pct:.0f}% Tier 4 VCs"
        else:
            quality_level = VCQualityLevel.NEUTRAL
            modifier = 0.0
            warning = None

        return VCQualityResult(
            quality_level=quality_level,
            conviction_modifier=modifier,
            investors=investors,
            tier_distribution=tier_counts,
            warning_message=warning,
            data_source="manual",
            confidence="MEDIUM"
        )

    def _validate_with_gpt(self, symbol: str) -> VCQualityResult:
        """
        Fallback: Use GPT-4o-mini for VC reputation analysis

        Uses existing OpenAI integration (Session 263 optimization: $0.16/month)
        Prompt: "Research VC backers for {symbol} token and classify their reputation"
        """
        try:
            from src.integrations.openai.openai_api_client import OpenAIAPIClient

            client = OpenAIAPIClient(model="gpt-4o-mini")

            prompt = f"""
            Research the VC backers/investors for the {symbol} cryptocurrency token.

            Classify them into tiers:
            - TIER_1: Top-tier (Paradigm, a16z, Sequoia, Polychain, Pantera)
            - TIER_2: Established (Binance Labs, Coinbase Ventures, Animoca)
            - TIER_3: Mid-tier regional VCs
            - TIER_4: Unknown/dump-prone funds

            Return JSON:
            {{
                "investors": ["VC Name 1", "VC Name 2"],
                "tier_distribution": {{"TIER_1": 2, "TIER_2": 1, "TIER_3": 0, "TIER_4": 0}},
                "quality": "EXCELLENT/GOOD/NEUTRAL/POOR/INSUFFICIENT"
            }}

            If no VC data found, return {{"quality": "INSUFFICIENT"}}.
            """

            response = client.client.post(
                "https://api.openai.com/v1/chat/completions",
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                    "max_tokens": 300
                }
            )
            response.raise_for_status()

            content = response.json()["choices"][0]["message"]["content"]
            parsed = json.loads(content)

            quality = parsed.get("quality", "INSUFFICIENT")
            quality_level = VCQualityLevel(quality) if quality in [level.value for level in VCQualityLevel] else VCQualityLevel.INSUFFICIENT
            modifier = self._get_modifier_for_quality(quality_level)

            return VCQualityResult(
                quality_level=quality_level,
                conviction_modifier=modifier,
                investors=[],
                tier_distribution=parsed.get("tier_distribution", {}),
                warning_message=None,
                data_source="gpt",
                confidence="LOW"  # GPT hallucination risk
            )

        except Exception as e:
            logger.error(f"GPT VC validation failed for {symbol}: {e}")

            # Ultimate fallback: INSUFFICIENT with conservative penalty
            return VCQualityResult(
                quality_level=VCQualityLevel.INSUFFICIENT,
                conviction_modifier=-0.25,
                investors=[],
                tier_distribution={},
                warning_message="⚠️ VC VALIDATION FAILED: Conservative -0.25 penalty applied",
                data_source="none",
                confidence="VERY_LOW"
            )

    def _get_modifier_for_quality(self, quality: VCQualityLevel) -> float:
        """Get conviction modifier for quality level"""
        modifiers = {
            VCQualityLevel.EXCELLENT: +0.5,
            VCQualityLevel.GOOD: +0.25,
            VCQualityLevel.NEUTRAL: 0.0,
            VCQualityLevel.POOR: -0.5,
            VCQualityLevel.INSUFFICIENT: -0.25
        }
        return modifiers.get(quality, 0.0)


def validate_token_vc_quality(
    symbol: str,
    existing_vc_data: Optional[Dict[str, Any]] = None,
    use_cryptorank: bool = True
) -> Dict[str, Any]:
    """
    Convenience function for VC quality validation

    Args:
        symbol: Token symbol
        existing_vc_data: Manual VC data from consolidated.json
        use_cryptorank: Use CryptoRank API if available

    Returns:
        Dict with VC quality result for storage in consolidated.json
    """
    validator = VCQualityValidator()
    result = validator.validate_vc_quality(symbol, existing_vc_data, use_cryptorank)

    return {
        "quality_level": result.quality_level.value,
        "conviction_modifier": result.conviction_modifier,
        "investors": [
            {
                "name": inv.name,
                "tier": inv.tier.value,
                "is_lead": inv.is_lead,
                "funding_stage": inv.funding_stage,
                "investment_date": inv.investment_date
            }
            for inv in result.investors
        ],
        "tier_distribution": result.tier_distribution,
        "warning_message": result.warning_message,
        "data_source": result.data_source,
        "confidence": result.confidence
    }


if __name__ == "__main__":
    # Example usage
    import sys

    if len(sys.argv) < 2:
        print("Usage: python vc_quality_validator.py <SYMBOL>")
        sys.exit(1)

    symbol = sys.argv[1]

    result = validate_token_vc_quality(symbol)

    print(f"\n{'='*60}")
    print(f"VC QUALITY VALIDATION: {symbol}")
    print(f"{'='*60}")
    print(f"Quality Level: {result['quality_level']}")
    print(f"Conviction Modifier: {result['conviction_modifier']:+.2f}")
    print(f"Data Source: {result['data_source']}")
    print(f"Confidence: {result['confidence']}")
    print(f"\nTier Distribution:")
    for tier, count in result['tier_distribution'].items():
        print(f"  {tier}: {count}")
    if result['warning_message']:
        print(f"\n⚠️  {result['warning_message']}")
    print(f"{'='*60}\n")
