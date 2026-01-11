"""
Data Consolidation Module

DEPRECATED: Use src.data module instead.
Session 256: Marked for migration to src/data/consolidator.py

Automatically merges data from multiple sources with intelligent conflict resolution.
Generates validation notes and creates final consolidated JSON.

Session 86A (SEEK Learnings):
- Added live status integration: If token is live on CoinGecko, prioritize live data
- Added TGE date cross-validation against live market status
- SEEK case study: ICODrops had May 30, token actually launched Dec 5
- CoinGecko live data is GOLD STANDARD for post-TGE tokens

Data Source Priority (Session 79H, updated 86A):
0. CoinGecko LIVE CHECK (Session 86A) - If live, use this data
1. CryptoRank API - Primary TGE data (cryptorank.json)
2. Dropstab - Vesting, funding, investors (dropstab.json)
3. CoinGecko API - Free FDV, market_cap, contract_address (coingecko.json)
4. CoinMarketCap API - contract_address only (coinmarketcap.json)
5. Perplexity/OpenAI - AI research fallback (perplexity.json)

Based on: DATA_QUALITY_IMPROVEMENT_PROCESS.md (Session 51.5)
Case Study: IRYS consolidation (33% → 71% automated, 100% after consolidation)
Case Study: SEEK TGE date mismatch (Session 86A)

Created: 2025-11-24 (Session 51.5 - Automation Priority #3)
Updated: 2025-11-30 (Session 79H - Data Source Priority System)
Updated: 2025-12-05 (Session 86A - SEEK TGE Date Learning)

Usage:
    from src.data.data_consolidator import DataConsolidator

    consolidator = DataConsolidator()
    result = consolidator.consolidate(
        automated_data=cryptorank_data,
        manual_data=perplexity_data,
        token="IRYS"
    )

    # Save consolidated data
    consolidator.save_consolidated(result, "data/tokens/IRYS/final/IRYS_consolidated.json")
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Session 290: Redis caching for consolidate() operations
try:
    from src.utils.redis_cache import get_redis_cache
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logging.warning("Redis cache not available - caching disabled")

# Import Perplexity validator for pre-consolidation validation
try:
    from src.data.validation.perplexity_validator import validate_perplexity_data
    VALIDATOR_AVAILABLE = True
except ImportError:
    VALIDATOR_AVAILABLE = False
    logging.warning("Perplexity validator not available - skipping validation")

# Import TGE date validator for listing vs vesting date validation
try:
    from src.data.exchange_listing_verifier import validate_tge_date
    TGE_VALIDATOR_AVAILABLE = True
except ImportError:
    TGE_VALIDATOR_AVAILABLE = False
    logging.warning("TGE date validator not available - skipping TGE date validation")

# Import VC tier classifier for auto-classification (Gemini P1 Fix)
try:
    from src.conviction.vc_tier_classifier import VCTierClassifier
    VC_CLASSIFIER_AVAILABLE = True
except ImportError:
    VC_CLASSIFIER_AVAILABLE = False
    logging.warning("VC tier classifier not available - skipping VC tier auto-classification")

# Session 291: Import liquidity validator (P0 Critical Safety)
try:
    from src.data.validation.liquidity_validator import validate_token_liquidity
    LIQUIDITY_VALIDATOR_AVAILABLE = True
except ImportError:
    LIQUIDITY_VALIDATOR_AVAILABLE = False
    logging.warning("Liquidity validator not available - skipping liquidity validation")

# Session 291: Import vesting schedule LLM parser (P0 Critical Feature)
try:
    from src.data.validation.vesting_parser import parse_vesting_schedule
    VESTING_PARSER_AVAILABLE = True
except ImportError:
    VESTING_PARSER_AVAILABLE = False
    logging.warning("Vesting parser not available - using basic regex fallback")

# Session 316: Import FDV Estimator for pre-TGE projects without official FDV
try:
    from src.data.fdv_estimator import estimate_fdv, EstimationMethod, ConfidenceLevel
    FDV_ESTIMATOR_AVAILABLE = True
except ImportError:
    FDV_ESTIMATOR_AVAILABLE = False
    logging.warning("FDV Estimator not available - skipping FDV estimation fallback")

# Session 86A: Import live status check for TGE date validation
try:
    from src.data.primary_source_fetcher import (
        check_token_live_status,
        validate_tge_date_against_live_status
    )
    LIVE_STATUS_CHECK_AVAILABLE = True
except ImportError:
    LIVE_STATUS_CHECK_AVAILABLE = False
    logging.warning("Live status check not available - skipping TGE date live validation")

# Session 297: Import DEX data fetcher for DEX-only tokens (ALLOCA on Monad use case)
try:
    from src.data.fetchers.dex_data_fetcher import DEXDataFetcher
    DEX_FETCHER_AVAILABLE = True
except ImportError:
    DEX_FETCHER_AVAILABLE = False
    logging.warning("DEX data fetcher not available - DEX-only token enhancement disabled")

logger = logging.getLogger(__name__)


# GEMINI P1 FIX: CRITICAL fields requiring citations
# Session 89: Updated to use _low variants for FDV and listing_price
CRITICAL_FIELDS_REQUIRING_CITATION = [
    "fdv_at_tge_low",  # Session 89: Updated from "fdv"
    "fdv_low",         # Fallback for legacy data
    "tge_date",
    "float_percent",
    "total_supply",
    "circulating_supply_at_tge",
    "listing_price_low",  # Session 89: Updated from "listing_price"
    "funding_raised_usd"
]


def normalize_allocation(raw_data: Any) -> Dict[str, float]:
    """
    Session 79I: Normalize token_allocation to standard format: {"Category": float_percent}

    Handles different source formats:
    - CryptoRank: [{"name": "Team", "percent": 20}]
    - Dropstab: {"Team": 0.2} or {"team": 20.0}
    - General: [{"category": "Team", "percentage": 20}]

    Output format: {"Team": 20.0, "Investors": 15.0, ...}
    All values as percentages (0-100), category names Title Cased.
    """
    if not raw_data:
        return {}

    normalized = {}

    if isinstance(raw_data, list):
        # Format: [{"name": "Team", "percent": 20}]
        for item in raw_data:
            if isinstance(item, dict):
                # Try various key names for category
                name = (item.get("name") or item.get("category") or
                        item.get("type") or item.get("allocation_type"))
                # Try various key names for percentage
                pct = (item.get("percent") or item.get("percentage") or
                       item.get("value") or item.get("share"))

                if name and pct is not None:
                    try:
                        pct = float(pct)
                        # Normalize percent (handle 0.2 vs 20 formats)
                        if pct > 0 and pct < 1:  # Likely decimal format (0.2)
                            pct = pct * 100
                        normalized[name.title()] = round(pct, 2)
                    except (ValueError, TypeError):
                        continue

    elif isinstance(raw_data, dict):
        # Format: {"Team": 0.2} or {"team": 20}
        for key, value in raw_data.items():
            if key.startswith("_"):  # Skip metadata fields
                continue
            try:
                pct = float(value)
                # Normalize percent
                if pct > 0 and pct < 1:  # Likely decimal format
                    pct = pct * 100
                normalized[key.title()] = round(pct, 2)
            except (ValueError, TypeError):
                continue

    # Validate: percentages should sum to approximately 100
    total = sum(normalized.values())
    if normalized and (total < 90 or total > 110):
        logging.getLogger(__name__).warning(
            f"Token allocation sums to {total:.1f}% (expected ~100%)"
        )

    return normalized


def _reorder_consolidated_fields(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Session 79K: Reorder consolidated.json fields for human scanning.

    Structure:
    1. IDENTITY - token_symbol, token_name, status
    2. CRITICAL TRADING DATA - tge_date, float, FDV, prices, ratios
    3. INVESTORS & FUNDING - investors, funding_raised, VC tier
    4. TOKEN ECONOMICS - allocation, vesting, supply
    5. MARKET DATA - exchanges, OTC, sentiment
    6. PROJECT INFO - description, links, categories
    7. SOURCES & METADATA - all _source fields and _metadata at bottom

    This makes it easy for David to scan the key data without scrolling
    through sources and metadata first.
    """
    # Define field order by category
    FIELD_ORDER = [
        # === SECTION 1: IDENTITY ===
        "token_symbol",
        "symbol",
        "token_name",
        "name",
        "status",

        # === SECTION 2: CRITICAL TRADING DATA ===
        "tge_date",
        "days_until_tge",
        "float_percent",
        "float_red_flag",
        "fdv_low",
        "fdv_high",
        "fdv",
        "fdv_mc_ratio_low",
        "fdv_mc_ratio_high",
        "listing_price_low",
        "listing_price_high",
        "initial_market_cap_low",
        "initial_market_cap_high",
        "total_supply",
        "circulating_supply_at_tge",

        # === SECTION 3: INVESTORS & FUNDING ===
        "investors",
        "vc_investors",
        "investor_tier",
        "tier_1_vc_count",
        "tier_1_vcs_list",
        "vc_tier_assessment",
        "funding_raised_usd",
        "total_funding",
        "funding_rounds",

        # === SECTION 4: TOKEN ECONOMICS ===
        "token_allocation",
        "community_allocation_pct",
        "vesting_schedule",
        "unlock_schedule",

        # === SECTION 5: MARKET DATA ===
        "listing_exchanges",
        "exchanges",
        "tier_1_exchange_count",
        "exchange_warning",
        "exchange_announcements",
        "contract_address",
        "blockchain",
        "has_points_campaign",
        "reward_type",
        "otc_platforms",
        "otc_data_available",
        "otc_conviction_impact",
        "oi_data",
        "orderbook_data",
        "historical_pattern",

        # === SECTION 6: PROJECT INFO ===
        "project_description",
        "category",
        "categories",
        "category_risk_tier",
        "category_dump_predictability",
        "category_historical_pattern",
        "website_url",
        "website",
        "twitter_url",
        "twitter_handle",
        "whitepaper_url",
        "team",
        "comparable_tges",

        # === SECTION 7: MARKET CONDITIONS ===
        "btc_market_structure",
        "eth_market_structure",
        "macro_market_conditions",
        "listing_ta_confluence",
        "social_validation_tier",
        "social_conviction",
        "alpha_callers_list",
        "perplexity_data",

        # === SECTION 8: VALIDATION FLAGS ===
        "tge_date_changed",
        "date_change_count",
        "token_identity_verified",
        "conflicting_tokens",
        "data_confidence",
        "missing_fields",
        "conflicting_data",
        "analysis_date",
        "releaseDate",
        "research_time_available",
        "time_pressure_warning",
        "data_completeness_vs_time",
        "vs_major_tge_benchmark",
    ]

    # Build ordered dict
    ordered = {}

    # First: Add fields in defined order
    for field in FIELD_ORDER:
        if field in data:
            ordered[field] = data[field]

    # Second: Add any remaining non-metadata fields (not starting with _)
    for key, value in data.items():
        if key not in ordered and not key.startswith("_") and not key.endswith("_source"):
            ordered[key] = value

    # Third: Add all _source fields together (for easy reference)
    source_fields = {}
    for key, value in data.items():
        if key.endswith("_source") and key not in ordered:
            source_fields[key] = value
    if source_fields:
        ordered.update(source_fields)

    # Fourth: Add all _note fields
    note_fields = {}
    for key, value in data.items():
        if key.endswith("_note") and key not in ordered:
            note_fields[key] = value
    if note_fields:
        ordered.update(note_fields)

    # Fifth: Add all other _ prefixed metadata fields at the very end
    metadata_fields = {}
    for key, value in data.items():
        if key.startswith("_") and key not in ordered:
            metadata_fields[key] = value
    if metadata_fields:
        ordered.update(metadata_fields)

    return ordered


class ConflictResolution:
    """Represents a conflict resolution decision."""

    # Resolution types (from DATA_QUALITY_IMPROVEMENT_PROCESS.md Phase 4)
    TYPE_1_AUTOMATED_WINS = "automated_wins"  # Structured data from CryptoRank
    TYPE_2_MANUAL_WINS = "manual_wins"  # Recent announcements, exchange listings
    TYPE_3_BOTH_PARTIAL = "both_partial"  # Different timeframes/definitions

    def __init__(
        self,
        field: str,
        resolution_type: str,
        chosen_value: Any,
        automated_value: Any,
        manual_value: Any,
        reasoning: str
    ):
        self.field = field
        self.resolution_type = resolution_type
        self.chosen_value = chosen_value
        self.automated_value = automated_value
        self.manual_value = manual_value
        self.reasoning = reasoning

    def generate_note(self) -> str:
        """Generate validation note for the field."""
        if self.resolution_type == self.TYPE_1_AUTOMATED_WINS:
            return f"VALIDATED: {self.reasoning} CryptoRank value ({self.automated_value}) used over Perplexity ({self.manual_value})."

        elif self.resolution_type == self.TYPE_2_MANUAL_WINS:
            return f"VALIDATED: {self.reasoning} Perplexity value ({self.manual_value}) used over CryptoRank ({self.automated_value})."

        elif self.resolution_type == self.TYPE_3_BOTH_PARTIAL:
            return f"VALIDATED: {self.reasoning} Using {self.chosen_value} (both sources partially correct)."

        return f"VALIDATED: {self.reasoning}"


class DataConsolidator:
    """Consolidates data from multiple sources with intelligent conflict resolution."""

    # Tolerance thresholds for conflict detection
    TOLERANCE_FLOAT_PCT = 3.0  # ±3%
    TOLERANCE_SUPPLY = 0.05  # ±5%
    TOLERANCE_FUNDING = 0.10  # ±10%

    def __init__(self):
        self.resolutions: List[ConflictResolution] = []
        self.agreements: List[str] = []
        self.missing_fields: List[str] = []

    def consolidate(
        self,
        automated_data: Dict[str, Any],
        manual_data: Dict[str, Any],
        token: str,
        dry_run: bool = False
    ) -> Dict[str, Any]:
        """
        Consolidate automated and manual data sources.

        Args:
            automated_data: Data from CryptoRank extraction
            manual_data: Data from Perplexity research
            token: Token symbol
            dry_run: If True, show what would be done without applying changes

        Returns:
            Consolidated data dictionary with validation notes
        """
        # Session 290: Redis caching (24h TTL - consolidated data rarely changes)
        if REDIS_AVAILABLE and not dry_run:
            redis_cache = get_redis_cache()
            cache_key = f"consolidate:{token}"

            # Try to get from cache
            cached = redis_cache.get(cache_key, namespace="data")
            if cached is not None:
                logger.debug(f"✅ Cache HIT: consolidate({token})")
                return cached

            logger.debug(f"❌ Cache MISS: consolidate({token}) - executing consolidation")

        self.resolutions = []
        self.agreements = []
        self.missing_fields = []

        # PHASE 1.5: Validate Perplexity data (Priority #2 from DATA_QUALITY_IMPROVEMENT_PROCESS.md)
        if VALIDATOR_AVAILABLE:
            logger.info(f"🔍 Validating Perplexity data for {token}...")
            validation_result = validate_perplexity_data(manual_data, verbose=False)

            if validation_result["errors"]:
                logger.error(f"🚨 CRITICAL: {len(validation_result['errors'])} validation errors found!")
                for error in validation_result["errors"]:
                    logger.error(f"   [{error['field']}] {error['message']}")
                logger.error("⚠️  Data requires manual review before consolidation")

            if validation_result["warnings"]:
                logger.warning(f"⚠️  {len(validation_result['warnings'])} validation warnings:")
                for warning in validation_result["warnings"]:
                    logger.warning(f"   [{warning['field']}] {warning['message']}")

        # PHASE 1.6: Validate TGE date (Priority #3 from DATA_QUALITY_IMPROVEMENT_PROCESS.md)
        if TGE_VALIDATOR_AVAILABLE:
            tge_validation = validate_tge_date(
                token_symbol=token,
                automated_date=automated_data.get("tge_date"),
                automated_date_type=automated_data.get("tge_date_type"),
                manual_date=manual_data.get("tge_date")
            )

            if tge_validation["requires_verification"]:
                logger.warning(f"⚠️  TGE DATE VERIFICATION REQUIRED")
                logger.warning(f"   {tge_validation['reason']}")
                if tge_validation["verification_prompt"]:
                    logger.info(f"   Use Perplexity to verify listing date")
            elif tge_validation["conflict_detected"]:
                logger.info(f"✅ TGE date conflict resolved: using {tge_validation['tge_date']} ({tge_validation['date_source']})")
                logger.info(f"   Date difference: {tge_validation['date_diff_days']} days")
            else:
                logger.info(f"✅ TGE date validated: {tge_validation['tge_date']} ({tge_validation['date_source']})")

        # Start with manual data as base (usually more complete)
        consolidated = dict(manual_data)

        # Session 79I: In primary_sources_only mode, start with automated_data as base
        # Check for substantive manual_data (not just metadata fields)
        substantive_manual_fields = [k for k in manual_data.keys()
                                     if not k.startswith("_") and k != "contract_address"]
        if not substantive_manual_fields:
            # No real Perplexity data - use automated_data as base
            for key, value in automated_data.items():
                if not key.startswith("_") and value is not None:
                    consolidated[key] = value
            logger.info(f"⚡ Primary sources base: {len(consolidated)} fields from automated_data")

        # Critical fields to consolidate
        self._consolidate_tge_date(automated_data, manual_data, consolidated)
        self._consolidate_funding(automated_data, manual_data, consolidated)
        self._consolidate_supply(automated_data, manual_data, consolidated)
        self._consolidate_float(automated_data, manual_data, consolidated)
        self._consolidate_fdv(automated_data, manual_data, consolidated)
        self._normalize_exchanges(consolidated)

        # GEMINI P1 FIXES: Additional consolidation steps
        self._consolidate_unlock_schedule(automated_data, manual_data, consolidated)
        self._auto_classify_vc_tier(consolidated, token)
        self._validate_citations(consolidated)

        # Session 79H: Derive calculated fields from existing data
        self._derive_calculated_fields(consolidated)

        # Session 316: FDV Estimation Fallback for pre-TGE projects without official FDV
        # This enables conviction scoring for projects where FDV is unknown
        if FDV_ESTIMATOR_AVAILABLE:
            fdv_low = consolidated.get("fdv_at_tge_low") or consolidated.get("fdv_low")
            fdv_high = consolidated.get("fdv_at_tge_high") or consolidated.get("fdv_high")

            if not fdv_low and not fdv_high:
                logger.info(f"🔮 No FDV data found for {token} - attempting estimation...")
                try:
                    # Get category and hype level for estimation
                    category = consolidated.get("category") or consolidated.get("token_type") or "Unknown"
                    # Determine hype level from investors, exchange tier, etc.
                    hype_level = "moderate"  # Default
                    investors = consolidated.get("investors") or []
                    if isinstance(investors, list) and len(investors) >= 3:
                        hype_level = "high"
                    if consolidated.get("tier_1_exchange") or consolidated.get("exchange_tier") == 1:
                        hype_level = "high"

                    fdv_result = estimate_fdv(
                        project_name=token,
                        category=category,
                        hype_level=hype_level
                    )

                    if fdv_result and fdv_result.estimated_fdv:
                        # Store estimated FDV with metadata
                        consolidated["fdv_at_tge_low"] = fdv_result.range_low
                        consolidated["fdv_at_tge_high"] = fdv_result.range_high
                        consolidated["fdv_estimated"] = fdv_result.estimated_fdv
                        consolidated["_fdv_estimation"] = {
                            "method": fdv_result.method.value,
                            "confidence": fdv_result.confidence.value,
                            "reasoning": fdv_result.reasoning,
                            "data_sources": fdv_result.data_sources,
                            "is_estimated": True
                        }
                        logger.info(f"✅ FDV estimated for {token}: ${fdv_result.estimated_fdv/1e9:.2f}B "
                                  f"(method: {fdv_result.method.value}, confidence: {fdv_result.confidence.value})")
                except Exception as e:
                    logger.warning(f"⚠️ FDV estimation failed for {token}: {e}")

        # Session 84: Merge ALL remaining fields from automated_data (not just hardcoded list)
        # This fixes the MONAD data loss issue where investors, vesting_schedule, blockchain
        # and other critical fields were being discarded
        merged_remaining = self._merge_remaining_fields(automated_data, manual_data, consolidated)
        if merged_remaining:
            # Show first 10 fields merged for brevity
            preview = merged_remaining[:10]
            more = f" (and {len(merged_remaining) - 10} more)" if len(merged_remaining) > 10 else ""
            logger.info(f"✅ Merged {len(merged_remaining)} additional fields from primary sources: {preview}{more}")

        # Session 84 - Data Pipeline Analysis: P0 Sanity Checks
        sanity_check_results = self._run_sanity_checks(consolidated, token)
        if sanity_check_results["errors"]:
            for error in sanity_check_results["errors"]:
                logger.error(f"❌ SANITY CHECK FAILED: {error}")
        if sanity_check_results["warnings"]:
            for warning in sanity_check_results["warnings"]:
                logger.warning(f"⚠️  SANITY CHECK WARNING: {warning}")

        # Session 291: P0 Liquidity Validation (Critical Safety)
        # Prevents honeypot tokens with $5K liquidity from getting 8/10 conviction scores
        if LIQUIDITY_VALIDATOR_AVAILABLE:
            logger.info(f"🔍 Validating liquidity for {token}...")
            liquidity_result = validate_token_liquidity(
                coingecko_id=consolidated.get("coingecko_id"),
                symbol=token,
                fdv=consolidated.get("fdv_at_tge_low") or consolidated.get("fdv_low"),
                current_price=consolidated.get("listing_price_low") or consolidated.get("listing_price")
            )

            # Store in _validation metadata
            if "_validation" not in consolidated:
                consolidated["_validation"] = {}

            consolidated["_validation"]["liquidity"] = liquidity_result

            # Log warnings for low liquidity
            if liquidity_result["warning_message"]:
                logger.warning(f"⚠️  LIQUIDITY WARNING: {liquidity_result['warning_message']}")
                logger.warning(f"   Conviction penalty: {liquidity_result['conviction_penalty']}")

        # Session 297: DEX Data Enhancement for missing fields
        # Fills market_cap, float_pct, RSI, ATH, drawdown, EMA support, volume ratio for DEX-only tokens
        if DEX_FETCHER_AVAILABLE:
            dex_enhanced_fields = self._enhance_with_dex_data(consolidated, token)
            if dex_enhanced_fields:
                logger.info(f"🔗 DEX enhancement: Added {len(dex_enhanced_fields)} fields: {dex_enhanced_fields}")

        # Add metadata
        consolidated["_consolidation_metadata"] = {
            "consolidated_at": datetime.utcnow().isoformat() + "Z",
            "token": token,
            "agreements": len(self.agreements),
            "conflicts_resolved": len(self.resolutions),
            "missing_from_automated": len(self.missing_fields),
            "data_confidence": self._calculate_confidence(),
            "sanity_checks_passed": len(sanity_check_results["errors"]) == 0,
            "sanity_check_warnings": len(sanity_check_results["warnings"]),
            "dry_run": dry_run,
            "liquidity_validated": LIQUIDITY_VALIDATOR_AVAILABLE
        }

        if not dry_run:
            logger.info(f"✅ Consolidated {token}: {len(self.agreements)} agreements, {len(self.resolutions)} conflicts resolved")

            # Session 290: Cache result for 24h (consolidated data rarely changes)
            if REDIS_AVAILABLE:
                redis_cache = get_redis_cache()
                cache_key = f"consolidate:{token}"
                redis_cache.set(cache_key, consolidated, ttl_seconds=86400, namespace="data")
                logger.debug(f"💾 Cached consolidate({token}) for 24h")

        return consolidated

    def _consolidate_tge_date(
        self,
        automated: Dict,
        manual: Dict,
        consolidated: Dict
    ) -> None:
        """
        Consolidate TGE date with special handling for vesting vs listing dates.

        Pattern from IRYS case study:
        - CryptoRank vesting date (Jan 1) vs Exchange listing date (Nov 25)
        - TYPE_2_MANUAL_WINS: Always prefer exchange listing for perpetual futures execution
        """
        a_date = automated.get("tge_date")
        m_date = manual.get("tge_date")
        a_date_type = automated.get("tge_date_type")

        if not a_date or not m_date:
            if m_date:
                self.missing_fields.append("tge_date")
            return

        # Check if automated date is vesting estimate
        if a_date_type == "vesting_estimate":
            # TYPE_2: Manual wins (exchange listing > vesting schedule)
            resolution = ConflictResolution(
                field="tge_date",
                resolution_type=ConflictResolution.TYPE_2_MANUAL_WINS,
                chosen_value=m_date,
                automated_value=a_date,
                manual_value=m_date,
                reasoning=f"Exchange listing {self._format_date(m_date)} (confirmed). "
                          f"CryptoRank vesting data shows {self._format_date(a_date)} (internal vesting schedule, not exchange listing). "
                          f"Always use exchange listing date for perpetual futures execution timing."
            )
            self.resolutions.append(resolution)
            consolidated["tge_date"] = m_date
            consolidated["_tge_date_note"] = resolution.generate_note()

        elif a_date == m_date:
            # Perfect agreement
            self.agreements.append("tge_date")
        else:
            # Dates differ but both are listings - use manual (usually more precise with time)
            resolution = ConflictResolution(
                field="tge_date",
                resolution_type=ConflictResolution.TYPE_2_MANUAL_WINS,
                chosen_value=m_date,
                automated_value=a_date,
                manual_value=m_date,
                reasoning=f"Using Perplexity date {self._format_date(m_date)} (more precise timing information). "
                          f"CryptoRank date: {self._format_date(a_date)}."
            )
            self.resolutions.append(resolution)
            consolidated["tge_date"] = m_date
            consolidated["_tge_date_note"] = resolution.generate_note()

    def _consolidate_funding(
        self,
        automated: Dict,
        manual: Dict,
        consolidated: Dict
    ) -> None:
        """
        Consolidate funding with special handling for parsing errors.

        Pattern from IRYS case study:
        - Perplexity: $13.5T (parsing error - trillion vs total)
        - CryptoRank: $10.4M (correct from structured data)
        - TYPE_1_AUTOMATED_WINS: Structured data from official source
        """
        a_funding = self._extract_funding(automated)
        m_funding = self._extract_funding(manual)

        if not a_funding or not m_funding:
            if m_funding:
                self.missing_fields.append("funding")
            return

        # Check for absurd differences (likely parsing error)
        if m_funding > a_funding * 100:  # Manual is 100x larger
            # TYPE_1: Automated wins (likely Perplexity parsing error)
            resolution = ConflictResolution(
                field="total_funding",
                resolution_type=ConflictResolution.TYPE_1_AUTOMATED_WINS,
                chosen_value=a_funding,
                automated_value=a_funding,
                manual_value=m_funding,
                reasoning=f"Perplexity parsed as ${m_funding:,.0f} (parsing error). "
                          f"Validated from CryptoRank: ${a_funding:,.0f}. "
                          f"Original parser likely interpreted 'T' as trillion instead of 'total'."
            )
            self.resolutions.append(resolution)
            consolidated["total_funding"] = str(int(a_funding))
            consolidated["_funding_note"] = f"CORRECTED: {resolution.generate_note()}"

        elif abs(a_funding - m_funding) / a_funding <= self.TOLERANCE_FUNDING:
            # Within tolerance - agreement
            self.agreements.append("funding")
        else:
            # Significant difference but not absurd - prefer CryptoRank (structured data)
            resolution = ConflictResolution(
                field="total_funding",
                resolution_type=ConflictResolution.TYPE_1_AUTOMATED_WINS,
                chosen_value=a_funding,
                automated_value=a_funding,
                manual_value=m_funding,
                reasoning=f"Using CryptoRank ${a_funding:,.0f} (structured data from official source). "
                          f"Perplexity: ${m_funding:,.0f}. Difference: {abs(a_funding - m_funding) / a_funding * 100:.1f}%."
            )
            self.resolutions.append(resolution)
            consolidated["total_funding"] = str(int(a_funding))
            consolidated["_funding_note"] = resolution.generate_note()

    def _consolidate_supply(
        self,
        automated: Dict,
        manual: Dict,
        consolidated: Dict
    ) -> None:
        """
        Consolidate total supply.

        Enhancement 2 (Session 87): Hallucination Detection
        If Perplexity supply differs >200% from automated sources,
        flag as potential hallucination (invented supply).
        """
        a_supply = automated.get("total_supply")
        m_supply = manual.get("total_supply")

        if not a_supply or not m_supply:
            if m_supply:
                self.missing_fields.append("total_supply")
            return

        diff_pct = abs(a_supply - m_supply) / a_supply

        # Enhancement 2: Hallucination Pattern Detection (Session 87)
        # If Perplexity supply is wildly different (>200%), likely hallucinated
        if diff_pct > 2.0:  # >200% variance
            logger.error(f"🚨 HALLUCINATION DETECTED: Supply mismatch >200%")
            logger.error(f"   CryptoRank supply: {a_supply:,.0f}")
            logger.error(f"   Perplexity supply: {m_supply:,.0f}")
            logger.error(f"   Variance: {diff_pct * 100:.0f}%")
            logger.error(f"   This supply value matches NO official source")
            logger.error(f"   Likely AI hallucination - using automated source")

            consolidated["_hallucination_detected"] = True
            consolidated["_hallucination_flags"] = {
                "field": "total_supply",
                "perplexity_value": m_supply,
                "automated_value": a_supply,
                "variance_pct": diff_pct * 100,
                "confidence": 0.0,
                "reasoning": "Supply differs >200% from automated source - likely hallucinated"
            }

        if diff_pct <= self.TOLERANCE_SUPPLY:
            # Agreement
            self.agreements.append("total_supply")
        else:
            # Conflict - prefer CryptoRank (official source)
            resolution = ConflictResolution(
                field="total_supply",
                resolution_type=ConflictResolution.TYPE_1_AUTOMATED_WINS,
                chosen_value=a_supply,
                automated_value=a_supply,
                manual_value=m_supply,
                reasoning=f"Using CryptoRank {a_supply:,.0f} (official source). "
                          f"Perplexity: {m_supply:,.0f}. Difference: {diff_pct * 100:.1f}%."
            )
            self.resolutions.append(resolution)
            consolidated["total_supply"] = a_supply
            consolidated["_supply_note"] = resolution.generate_note()

    def _consolidate_float(
        self,
        automated: Dict,
        manual: Dict,
        consolidated: Dict
    ) -> None:
        """
        Consolidate float percentage with time-based resolution.

        Pattern: Pre-TGE vs Post-TGE can have different floats (unlocks)
        """
        a_float = automated.get("float_percent")
        m_float = manual.get("float_percent")

        if not a_float or not m_float:
            if m_float:
                self.missing_fields.append("float_percent")
            return

        diff_pct = abs(a_float - m_float)

        if diff_pct <= self.TOLERANCE_FLOAT_PCT:
            # Agreement
            self.agreements.append("float_percent")
        else:
            # TYPE_3: Both partial - different definitions (TGE unlock vs current circulating)
            resolution = ConflictResolution(
                field="float_percent",
                resolution_type=ConflictResolution.TYPE_3_BOTH_PARTIAL,
                chosen_value=m_float,  # Use Perplexity for TGE analysis
                automated_value=a_float,
                manual_value=m_float,
                reasoning=f"Using Perplexity {m_float:.1f}% (TGE unlock). "
                          f"CryptoRank {a_float:.1f}% may include future unlocks. "
                          f"For perpetual futures shorts, use initial TGE unlock."
            )
            self.resolutions.append(resolution)
            consolidated["float_percent"] = m_float
            consolidated["_float_note"] = resolution.generate_note()

    def _consolidate_fdv(
        self,
        automated: Dict,
        manual: Dict,
        consolidated: Dict
    ) -> None:
        """
        Consolidate FDV with GEMINI CRITICAL FIX: Cross-Source Variance Check.

        If variance >50% (e.g., CryptoRank $10M vs Perplexity $100M = 10x variance),
        downgrade confidence to 0% and force manual review (Tier 3).

        This prevents algorithm from picking "trusted" but potentially hallucinated source.
        """
        a_fdv = automated.get("fdv")
        m_fdv = manual.get("fdv")

        if not a_fdv or not m_fdv:
            if m_fdv:
                self.missing_fields.append("fdv")
            return

        # GEMINI CRITICAL FIX: Cross-Source Variance Check
        # Calculate variance ratio
        variance_ratio = abs(a_fdv - m_fdv) / max(a_fdv, m_fdv)

        # If variance > 50%, this is a red flag for hallucination
        if variance_ratio > 0.5:
            # CRITICAL: Force manual review
            logger.error(f"🚨 CRITICAL: FDV variance >50% detected!")
            logger.error(f"   CryptoRank FDV: ${a_fdv:,.0f}")
            logger.error(f"   Perplexity FDV: ${m_fdv:,.0f}")
            logger.error(f"   Variance: {variance_ratio * 100:.1f}%")
            logger.error(f"   This indicates potential hallucination or data error")
            logger.error(f"   Downgrading confidence to 0% - MANUAL REVIEW REQUIRED")

            # Set confidence to 0% to trigger quality gate block
            consolidated["fdv"] = None  # Mark as missing
            consolidated["_fdv_variance_error"] = {
                "automated_fdv": a_fdv,
                "manual_fdv": m_fdv,
                "variance_pct": variance_ratio * 100,
                "confidence": 0.0,
                "requires_manual_review": True,
                "reasoning": f"FDV variance >50% (CryptoRank ${a_fdv:,.0f} vs Perplexity ${m_fdv:,.0f}). "
                             f"Algorithm cannot reliably choose - human verification required."
            }

            # Do NOT proceed with consolidation - return early
            return

        # Variance ≤50%: Proceed with normal consolidation
        if variance_ratio > 0.2:  # >20% difference (but <50%)
            # Large difference - likely different price assumptions
            resolution = ConflictResolution(
                field="fdv",
                resolution_type=ConflictResolution.TYPE_2_MANUAL_WINS,
                chosen_value=m_fdv,
                automated_value=a_fdv,
                manual_value=m_fdv,
                reasoning=f"Using Perplexity ${m_fdv:,.0f} (includes listing price estimate). "
                          f"CryptoRank ${a_fdv:,.0f} may use different price assumption."
            )
            self.resolutions.append(resolution)
            consolidated["fdv"] = m_fdv
            consolidated["_fdv_note"] = resolution.generate_note()
        else:
            # Close enough - agreement
            self.agreements.append("fdv")

    def _consolidate_unlock_schedule(
        self,
        automated: Dict,
        manual: Dict,
        consolidated: Dict
    ) -> None:
        """
        GEMINI P1 FIX #1: Structured Unlock Schedule Consolidation

        Validates and consolidates unlock_schedule with month_1 through month_12 format.
        Calculates cumulative unlock percentages and validates against float_percent.

        Session 79H: Also preserves raw vesting_schedule from Dropstab (date-based format)
        for detailed unlock timing analysis.

        Expected unlock_schedule format:
        {
            "month_1": 10.0,   # TGE unlock %
            "month_2": 12.5,   # Cumulative % after month 2
            ...
            "month_12": 100.0  # Fully unlocked
        }

        Dropstab vesting_schedule format (preserved as-is):
        [
            {"date": "2025-12-25", "amount": 74390000, "percent": 0.74},
            {"date": "2026-01-25", "amount": 74390000, "percent": 0.74},
            ...
        ]
        """
        # Session 79H: Preserve raw vesting_schedule from Dropstab (date-based format)
        # This is crucial for trading - exact unlock dates matter!
        raw_vesting = automated.get("vesting_schedule") or manual.get("vesting_schedule")
        if raw_vesting and isinstance(raw_vesting, list):
            consolidated["vesting_schedule"] = raw_vesting
            logger.info(f"✅ Preserved vesting_schedule: {len(raw_vesting)} unlock events")

        a_unlock = automated.get("unlock_schedule", {})
        m_unlock = manual.get("unlock_schedule", {})

        # Prefer Perplexity (more detailed from research) over CryptoRank
        unlock_schedule = m_unlock if m_unlock else a_unlock

        # Session 85 FIX: Check if unlock_schedule is effectively empty (all null/None values)
        def is_unlock_schedule_empty(schedule):
            """Check if unlock_schedule exists but is effectively empty (all nulls)"""
            if not schedule or not isinstance(schedule, dict):
                return True
            # Check if all month values are None/null
            month_values = [schedule.get(f"month_{i}") for i in range(1, 13)]
            return all(v is None for v in month_values)

        # Session 85: Normalize vesting schedule for conviction scoring
        # Apply normalization if we have vesting_schedule but no/empty unlock_schedule
        if raw_vesting and is_unlock_schedule_empty(unlock_schedule):
            normalized = self._normalize_vesting_schedule(raw_vesting)
            if normalized:
                consolidated["unlock_schedule"] = normalized
                logger.info(f"✅ Normalized vesting_schedule → unlock_schedule: {normalized.get('tge_unlock_pct')}% TGE, {normalized.get('vesting_months')} months")
                # Also update vesting_schedule string for human readability
                if isinstance(raw_vesting, list):
                    consolidated["vesting_schedule"] = self._format_vesting_human_readable(normalized)
                return  # Exit early - normalization complete

        # Also normalize string format vesting_schedule
        if isinstance(raw_vesting, str) and is_unlock_schedule_empty(unlock_schedule):
            normalized = self._normalize_vesting_schedule(raw_vesting)
            if normalized:
                consolidated["unlock_schedule"] = normalized
                logger.info(f"✅ Normalized vesting_schedule string → unlock_schedule")
                return  # Exit early - normalization complete

        if not unlock_schedule or not isinstance(unlock_schedule, dict):
            # No unlock schedule available
            consolidated["unlock_schedule"] = None
            consolidated["_unlock_schedule_note"] = "No structured unlock schedule available"
            return

        # Validate month_1 through month_12 structure
        valid_months = []
        validated_schedule = {}

        for i in range(1, 13):
            month_key = f"month_{i}"
            value = unlock_schedule.get(month_key)

            if value is not None:
                try:
                    pct = float(value)
                    if 0 <= pct <= 100:
                        validated_schedule[month_key] = pct
                        valid_months.append(i)
                    else:
                        logger.warning(f"   Invalid unlock % for {month_key}: {pct} (must be 0-100)")
                except (ValueError, TypeError):
                    logger.warning(f"   Cannot parse {month_key} value: {value}")

        if not validated_schedule:
            consolidated["unlock_schedule"] = None
            consolidated["_unlock_schedule_note"] = "Unlock schedule invalid or unparseable"
            return

        # Add derived fields
        month_1_unlock = validated_schedule.get("month_1", 0)
        month_12_unlock = validated_schedule.get("month_12")

        # Cross-validate with float_percent (month_1 should ≈ float_percent)
        float_pct = consolidated.get("float_percent")
        if float_pct and month_1_unlock:
            diff = abs(float_pct - month_1_unlock)
            if diff > 5:  # >5% difference
                logger.warning(f"   Unlock schedule month_1 ({month_1_unlock}%) differs from float_percent ({float_pct}%)")
                validated_schedule["_float_mismatch"] = {
                    "month_1": month_1_unlock,
                    "float_percent": float_pct,
                    "difference": diff,
                    "note": "month_1 should equal TGE float percentage"
                }

        # Calculate unlock velocity (useful for short timing)
        if len(valid_months) >= 2:
            first_month = min(valid_months)
            last_month = max(valid_months)
            first_val = validated_schedule.get(f"month_{first_month}", 0)
            last_val = validated_schedule.get(f"month_{last_month}", 0)

            if last_month > first_month:
                velocity = (last_val - first_val) / (last_month - first_month)
                validated_schedule["_unlock_velocity_pct_per_month"] = round(velocity, 2)

        consolidated["unlock_schedule"] = validated_schedule
        consolidated["_unlock_schedule_note"] = f"Validated {len(valid_months)} months of unlock data"
        self.agreements.append("unlock_schedule")

        logger.info(f"✅ Unlock schedule consolidated: {len(valid_months)} months, TGE unlock: {month_1_unlock}%")

    def _auto_classify_vc_tier(
        self,
        consolidated: Dict,
        token: str
    ) -> None:
        """
        GEMINI P1 FIX #2: VC Tier Auto-Classification

        Automatically classifies investor tier from investor list using VCTierClassifier.
        Sets investor_tier field and adds conviction modifier.
        """
        if not VC_CLASSIFIER_AVAILABLE:
            logger.debug("   VC tier classifier not available - skipping")
            return

        # Get investor list from consolidated data
        investors = consolidated.get("investors") or consolidated.get("vc_investors") or []

        if not investors:
            consolidated["investor_tier"] = "Unknown"
            consolidated["_vc_tier_note"] = "No investor list available for classification"
            return

        try:
            classifier = VCTierClassifier()

            # Classify from investor list
            result = classifier.classify_from_investor_list(investors)

            # Map VC tier score to investor_tier field
            score = result.get("vc_tier_score", 0)
            if score >= 8:
                tier = "Tier 1"
            elif score >= 5:
                tier = "Tier 2"
            elif score >= 1:
                tier = "Tier 3"
            else:
                tier = "Unknown"

            consolidated["investor_tier"] = tier
            consolidated["_vc_tier_classification"] = {
                "vc_tier_score": score,
                "tier_1_count": result.get("tier_1_count", 0),
                "tier_1_vcs": result.get("tier_1_vcs", []),
                "tier_classification": result.get("tier_classification"),
                "paradigm_dump_risk": result.get("paradigm_dump_risk", False),
                "conviction_modifier": result.get("conviction_modifier"),
                "scoring_rationale": result.get("scoring_rationale")
            }

            # Session 79K: Sync tier_1_vcs_list with classifier output
            # This ensures consistency between user-facing field and internal classification
            tier_1_vcs = result.get("tier_1_vcs", [])
            consolidated["tier_1_vcs_list"] = tier_1_vcs
            consolidated["tier_1_vc_count"] = len(tier_1_vcs)

            # Add Paradigm dump risk warning if detected
            if result.get("paradigm_dump_risk"):
                logger.warning(f"🚨 PARADIGM DUMP RISK DETECTED for {token}")
                logger.warning(f"   Historical pattern: 100% of Paradigm-backed tokens with >$10B FDV dumped -40% to -86%")
                consolidated["_paradigm_warning"] = True

            logger.info(f"✅ VC tier auto-classified: {tier} (score: {score}/10)")
            if result.get("tier_1_vcs"):
                logger.info(f"   Tier 1 VCs: {', '.join(result['tier_1_vcs'])}")

        except Exception as e:
            logger.error(f"❌ VC tier classification failed: {e}")
            consolidated["investor_tier"] = "Unknown"
            consolidated["_vc_tier_note"] = f"Classification error: {str(e)}"

    def _validate_citations(self, consolidated: Dict) -> None:
        """
        GEMINI P1 FIX #3: Citation Validation Enforcement

        Validates that CRITICAL fields have associated source URLs.
        Flags fields without citations for manual review.
        """
        missing_citations = []
        valid_citations = []

        for field in CRITICAL_FIELDS_REQUIRING_CITATION:
            value = consolidated.get(field)

            if value is None:
                continue  # Field not present, skip citation check

            # Check for associated source field
            source_field = f"{field}_source"
            source_url = consolidated.get(source_field)

            # Also check common alternative source field names
            alt_source_fields = [
                f"{field}_url",
                f"{field.replace('_', '')}source",
                "data_sources_used"
            ]

            has_citation = bool(source_url)
            if not has_citation:
                for alt_field in alt_source_fields:
                    alt_source = consolidated.get(alt_field)
                    if alt_source:
                        has_citation = True
                        break

            if has_citation:
                valid_citations.append(field)
            else:
                missing_citations.append(field)

        if missing_citations:
            logger.warning(f"⚠️ CRITICAL fields missing citations: {', '.join(missing_citations)}")
            consolidated["_citation_validation"] = {
                "status": "INCOMPLETE",
                "missing_citations": missing_citations,
                "valid_citations": valid_citations,
                "warning": f"{len(missing_citations)} CRITICAL fields lack source URLs. "
                           f"Manual verification recommended before trading."
            }
        else:
            consolidated["_citation_validation"] = {
                "status": "COMPLETE",
                "missing_citations": [],
                "valid_citations": valid_citations
            }
            logger.info(f"✅ All {len(valid_citations)} CRITICAL fields have citations")

    def _derive_calculated_fields(self, consolidated: Dict) -> None:
        """
        Session 79H: Derive calculated fields from existing data.

        Calculates fields that can be computed from other fields:
        - fdv_mc_ratio_low = fdv_low / initial_market_cap_low
        - fdv_mc_ratio_high = fdv_high / initial_market_cap_high

        This avoids requiring external API calls for derivable values.
        """
        derived_fields = []

        # Session 84 Phase 2: Calculate initial_market_cap from circulating_supply × listing_price
        # This is a prerequisite for FDV/MC ratio calculation
        circ_supply = consolidated.get("circulating_supply_at_tge")

        # Session 282: On-chain supply - both fallback AND cross-validation
        contract_address = consolidated.get("contract_address")
        blockchain = consolidated.get("blockchain", "").lower()
        total_supply = consolidated.get("total_supply")

        # Map DACLE blockchain names to fetcher chain names
        chain_map = {
            "ethereum": "ethereum",
            "eth": "ethereum",
            "bsc": "bsc",
            "binance smart chain": "bsc",
            "polygon": "polygon",
            "matic": "polygon",
            "arbitrum": "arbitrum",
            "arbitrum one": "arbitrum",
            "base": "base",
            "avalanche": "avalanche",
            "avax": "avalanche",
            "optimism": "optimism",
            "op": "optimism",
        }

        chain = chain_map.get(blockchain) if blockchain else None
        onchain_data = None

        # Try to fetch on-chain data if we have contract and supported chain
        if contract_address and chain:
            try:
                from src.data.fetchers.onchain_supply_fetcher import OnChainSupplyFetcher

                token_symbol = consolidated.get("token_symbol", "UNKNOWN")
                logger.info(f"   🔗 Fetching on-chain supply for {token_symbol} ({contract_address}) on {chain}...")
                fetcher = OnChainSupplyFetcher()
                onchain_data = fetcher.fetch_for_token(token_symbol, contract_address, chain)

                if onchain_data:
                    # Store on-chain data for reference
                    consolidated["_onchain_supply_data"] = {
                        "total_supply": onchain_data.get("total_supply"),
                        "circulating_supply": onchain_data.get("circulating_supply"),
                        "float_percent": onchain_data.get("float_percent"),
                        "chain": chain,
                        "contract": contract_address,
                    }

            except ImportError:
                logger.debug("   ⚠️ On-chain supply fetcher not available")
            except Exception as e:
                logger.debug(f"   ⚠️ On-chain supply fetch failed: {e}")

        # Mode 1: FALLBACK - Use on-chain data when sources fail
        if not circ_supply or circ_supply == 0:
            if onchain_data and onchain_data.get("circulating_supply"):
                onchain_circ = onchain_data["circulating_supply"]
                consolidated["circulating_supply_at_tge"] = onchain_circ
                consolidated["circulating_supply"] = onchain_circ
                consolidated["_onchain_supply_source"] = f"{chain}:{contract_address}"
                circ_supply = onchain_circ
                derived_fields.append(f"circulating_supply={onchain_circ} (on-chain fallback)")
                logger.info(f"   ✅ On-chain circulating supply (fallback): {onchain_circ:,.0f}")

                # Also calculate float_percent directly from on-chain data
                if total_supply and total_supply > 0:
                    float_pct = round((onchain_circ / total_supply) * 100, 2)
                    consolidated["float_percent"] = float_pct
                    derived_fields.append(f"float_percent={float_pct} (on-chain)")
                    logger.info(f"   ✅ On-chain float_percent: {float_pct}%")

        # Mode 2: CROSS-VALIDATION - Compare on-chain vs source data
        elif onchain_data and onchain_data.get("circulating_supply") and circ_supply:
            onchain_circ = onchain_data["circulating_supply"]
            source_circ = circ_supply

            # Calculate deviation percentage
            if source_circ > 0:
                deviation_pct = abs(onchain_circ - source_circ) / source_circ * 100

                if deviation_pct > 20:
                    # Significant deviation - TRUST ON-CHAIN (Option A)
                    # Blockchain is ground truth, source data may be stale
                    consolidated["_supply_validation"] = {
                        "status": "OVERRIDE",
                        "action": "TRUST_ONCHAIN",
                        "source_circulating": source_circ,
                        "onchain_circulating": onchain_circ,
                        "deviation_pct": round(deviation_pct, 1),
                        "reason": "On-chain differs >20% - using blockchain as ground truth"
                    }
                    # Override with on-chain data
                    consolidated["circulating_supply_at_tge_original"] = source_circ  # Preserve original
                    consolidated["circulating_supply_at_tge"] = onchain_circ
                    consolidated["circulating_supply"] = onchain_circ
                    consolidated["_onchain_supply_source"] = f"{chain}:{contract_address}"
                    circ_supply = onchain_circ  # Update for downstream calculations
                    derived_fields.append(f"circulating_supply={onchain_circ} (on-chain override)")
                    logger.warning(f"   🔄 Supply OVERRIDE: source={source_circ:,.0f} → on-chain={onchain_circ:,.0f} ({deviation_pct:.1f}% deviation)")

                    # Recalculate float_percent with on-chain data
                    if total_supply and total_supply > 0:
                        float_pct = round((onchain_circ / total_supply) * 100, 2)
                        consolidated["float_percent"] = float_pct
                        derived_fields.append(f"float_percent={float_pct} (on-chain)")
                        logger.info(f"   ✅ Recalculated float_percent: {float_pct}%")

                elif deviation_pct > 5:
                    # Minor deviation - note it but trust source
                    consolidated["_supply_validation"] = {
                        "status": "MINOR_DEVIATION",
                        "source_circulating": source_circ,
                        "onchain_circulating": onchain_circ,
                        "deviation_pct": round(deviation_pct, 1),
                    }
                    logger.info(f"   ℹ️ Supply minor deviation: source={source_circ:,.0f} vs on-chain={onchain_circ:,.0f} ({deviation_pct:.1f}%)")
                else:
                    # Match - data is validated
                    consolidated["_supply_validation"] = {
                        "status": "VALIDATED",
                        "source_circulating": source_circ,
                        "onchain_circulating": onchain_circ,
                        "deviation_pct": round(deviation_pct, 1),
                    }
                    logger.info(f"   ✅ Supply validated: source matches on-chain ({deviation_pct:.1f}% deviation)")

        price_low = consolidated.get("listing_price_low")
        price_high = consolidated.get("listing_price_high")

        if circ_supply and price_low and not consolidated.get("initial_market_cap_low"):
            mc_low = circ_supply * price_low
            consolidated["initial_market_cap_low"] = mc_low
            derived_fields.append(f"initial_market_cap_low={mc_low}")
            logger.info(f"   📐 Derived initial_market_cap_low: {mc_low:,.0f} (from circulating_supply × listing_price_low)")

        if circ_supply and price_high and not consolidated.get("initial_market_cap_high"):
            mc_high = circ_supply * price_high
            consolidated["initial_market_cap_high"] = mc_high
            derived_fields.append(f"initial_market_cap_high={mc_high}")
            logger.info(f"   📐 Derived initial_market_cap_high: {mc_high:,.0f} (from circulating_supply × listing_price_high)")

        # Session 84 Phase 2: Calculate float_percent from circulating_supply / total_supply
        total_supply = consolidated.get("total_supply")
        if circ_supply and total_supply and total_supply > 0 and not consolidated.get("float_percent"):
            float_pct = round((circ_supply / total_supply) * 100, 2)
            consolidated["float_percent"] = float_pct
            derived_fields.append(f"float_percent={float_pct}")
            logger.info(f"   📐 Derived float_percent: {float_pct}% (from circulating_supply / total_supply)")

        # Session 179: Calculate locked_percent from float_percent (dashboard display field)
        float_pct = consolidated.get("float_percent")
        if float_pct is not None and not consolidated.get("locked_percent"):
            locked_pct = round(100 - float_pct, 2)
            consolidated["locked_percent"] = locked_pct
            derived_fields.append(f"locked_percent={locked_pct}")
            logger.info(f"   📐 Derived locked_percent: {locked_pct}% (from 100 - float_percent)")

        # Session 179: Map circulating_supply from circulating_supply_at_tge (dashboard display field)
        if circ_supply and not consolidated.get("circulating_supply"):
            consolidated["circulating_supply"] = circ_supply
            derived_fields.append(f"circulating_supply={circ_supply}")
            logger.info(f"   📐 Mapped circulating_supply: {circ_supply:,.0f} (from circulating_supply_at_tge)")

        # Calculate FDV/MC ratio (PRIMARY SHORT SIGNAL)
        # Formula: fdv_mc_ratio = fdv / market_cap
        fdv_low = consolidated.get("fdv_low")
        fdv_high = consolidated.get("fdv_high")
        mc_low = consolidated.get("initial_market_cap_low")
        mc_high = consolidated.get("initial_market_cap_high")

        # Derive fdv_mc_ratio_low
        if fdv_low and mc_low and mc_low > 0:
            if not consolidated.get("fdv_mc_ratio_low"):
                ratio = round(fdv_low / mc_low, 2)
                consolidated["fdv_mc_ratio_low"] = ratio
                derived_fields.append(f"fdv_mc_ratio_low={ratio}")
                logger.info(f"   📐 Derived fdv_mc_ratio_low: {ratio} (from fdv_low/mc_low)")

        # Derive fdv_mc_ratio_high
        if fdv_high and mc_high and mc_high > 0:
            if not consolidated.get("fdv_mc_ratio_high"):
                ratio = round(fdv_high / mc_high, 2)
                consolidated["fdv_mc_ratio_high"] = ratio
                derived_fields.append(f"fdv_mc_ratio_high={ratio}")
                logger.info(f"   📐 Derived fdv_mc_ratio_high: {ratio} (from fdv_high/mc_high)")

        # Alternative: Calculate from float_percent if MC not available
        # fdv_mc_ratio = 1 / float_percent (approximately)
        float_pct = consolidated.get("float_percent")
        if float_pct and float_pct > 0:
            if not consolidated.get("fdv_mc_ratio_low") and not consolidated.get("fdv_mc_ratio_high"):
                ratio = round(100 / float_pct, 2)
                consolidated["fdv_mc_ratio_low"] = ratio
                consolidated["fdv_mc_ratio_high"] = ratio
                derived_fields.append(f"fdv_mc_ratio={ratio} (from float_percent)")
                logger.info(f"   📐 Derived fdv_mc_ratio: {ratio} (from 100/float_percent)")

        # Session 79H: Field aliasing - map alternative field names to expected names
        # These fields exist but under different names
        # Session 79I: Added whitepaper_url, project_description aliases
        field_aliases = {
            "listing_exchanges": ["exchanges", "exchange_list", "listed_exchanges"],
            "funding_raised_usd": ["total_funding", "funding_total", "total_raised"],
            "contract_address": ["token_contract", "token_address", "contract"],
            # IMPORTANT fields that may have alternative names
            "token_allocation": ["tokenomics", "token_distribution", "allocation_breakdown"],
            "float_percent": ["circulating_supply_percent", "initial_float", "tge_unlock_pct"],
            "community_allocation_pct": ["community_allocation", "community_pct"],
            # Session 79I: New field aliases
            "whitepaper_url": ["whitepaper", "whitepaperUrl", "whitepaper_link"],
            "project_description": ["description", "about", "overview"],
        }

        for target_field, source_fields in field_aliases.items():
            if not consolidated.get(target_field):
                for source_field in source_fields:
                    value = consolidated.get(source_field)
                    if value:
                        # Convert to expected type if needed
                        if target_field == "funding_raised_usd":
                            # Ensure it's a number
                            if isinstance(value, str):
                                value = float(value.replace(",", ""))
                            value = float(value)
                        consolidated[target_field] = value
                        derived_fields.append(f"{target_field}={value} (from {source_field})")
                        logger.info(f"   🔗 Aliased {target_field} from {source_field}")
                        break

        # Session 79H: Extract contract_address from URLs if not found
        if not consolidated.get("contract_address"):
            import re
            # Pattern for Ethereum/EVM addresses (0x followed by 40 hex chars)
            eth_pattern = re.compile(r'0x[a-fA-F0-9]{40}')

            # Search through all string values and lists for contract addresses
            def find_contract_in_data(data, depth=0):
                if depth > 5:  # Prevent infinite recursion
                    return None
                if isinstance(data, str):
                    match = eth_pattern.search(data)
                    if match:
                        return match.group(0)
                elif isinstance(data, list):
                    for item in data:
                        result = find_contract_in_data(item, depth + 1)
                        if result:
                            return result
                elif isinstance(data, dict):
                    # Check common contract-related keys first
                    for key in ["contract_address", "contract", "token_address", "address"]:
                        if key in data and data[key]:
                            val = data[key]
                            if isinstance(val, str) and eth_pattern.match(val):
                                return val
                    # Then search all values
                    for v in data.values():
                        result = find_contract_in_data(v, depth + 1)
                        if result:
                            return result
                return None

            contract = find_contract_in_data(consolidated)
            if contract:
                consolidated["contract_address"] = contract
                derived_fields.append(f"contract_address={contract} (extracted from data)")
                logger.info(f"   🔍 Extracted contract_address: {contract}")

        if derived_fields:
            # Track derivation in metadata
            consolidated["_derived_fields"] = derived_fields
            logger.info(f"✅ Derived {len(derived_fields)} calculated field(s)")

    def _normalize_vesting_schedule(self, vesting_data: Any) -> Optional[Dict[str, Any]]:
        """
        Normalize vesting schedule from various formats to standard structure (Session 85)

        Handles formats from:
        - CryptoRank: String or dict
        - Dropstab: List of events [{"date": "2026-11-24", "percent": 16.62}]
        - Perplexity: Month-by-month {"month_1": 2.0, ...}
        - Manual: String "25% TGE unlock, 6 months linear monthly vesting"

        Returns:
            {
                "tge_unlock_pct": 25,
                "cliff_months": 0,
                "vesting_months": 6,
                "vesting_type": "linear_monthly",
                "raw_schedule": <original data>
            }
        """
        if not vesting_data:
            return None

        try:
            if isinstance(vesting_data, str):
                # Parse string like "25% TGE unlock, 6 months linear monthly vesting"
                return self._parse_vesting_string(vesting_data)
            elif isinstance(vesting_data, list):
                # Dropstab format: [{"date": "2026-11-24", "percent": 16.62}]
                return self._parse_vesting_events(vesting_data)
            elif isinstance(vesting_data, dict):
                # Check if it's already normalized
                if "tge_unlock_pct" in vesting_data:
                    return vesting_data
                # Perplexity or CryptoRank format
                return self._parse_vesting_object(vesting_data)
            else:
                logger.debug(f"Unsupported vesting format: {type(vesting_data)}")
                return None
        except Exception as e:
            logger.debug(f"Failed to normalize vesting schedule: {e}")
            return None

    def _parse_vesting_string(self, vesting_str: str) -> Optional[Dict[str, Any]]:
        """
        Parse vesting schedule from string format

        Session 291: Enhanced with LLM parser for complex schedules
        - Primary: OpenAI GPT-4o-mini (~$0.001/parse)
        - Fallback: Regex-based parser
        """
        # Session 291: Try LLM parser first (if available)
        if VESTING_PARSER_AVAILABLE:
            try:
                logger.debug(f"🤖 Parsing vesting with LLM: {vesting_str[:100]}...")
                parsed = parse_vesting_schedule(vesting_str, use_llm=True)

                # Check if LLM parsing was successful (HIGH or MEDIUM confidence)
                if parsed.get("confidence") in ("HIGH", "MEDIUM"):
                    logger.info(f"✅ LLM vesting parse ({parsed['confidence']}): {parsed['tge_unlock_pct']}% TGE, {parsed['cliff_months']}mo cliff, {parsed['vesting_months']}mo vest")
                    return parsed
                elif parsed.get("parsing_method") == "llm":
                    # LLM parsed but LOW confidence - log warning and fall back to regex
                    logger.warning(f"⚠️  LLM parse LOW confidence, falling back to regex")
            except Exception as e:
                logger.warning(f"LLM vesting parse failed: {e}, falling back to regex")

        # Fallback: Regex-based parser (original implementation)
        import re

        result = {
            "tge_unlock_pct": None,
            "cliff_months": 0,
            "vesting_months": None,
            "vesting_type": "unknown",
            "raw_schedule": vesting_str,
            "parsing_method": "regex",
            "confidence": "LOW"
        }

        # Extract TGE unlock percentage
        tge_match = re.search(r'(\d+(?:\.\d+)?)%?\s*(?:TGE|unlock|at\s+TGE)', vesting_str, re.IGNORECASE)
        if tge_match:
            result["tge_unlock_pct"] = float(tge_match.group(1))

        # Extract cliff first (to avoid capturing it as vesting duration)
        cliff_match = re.search(r'(\d+)\s*months?\s+cliff', vesting_str, re.IGNORECASE)
        if cliff_match:
            result["cliff_months"] = int(cliff_match.group(1))

        # Extract vesting duration (look for last occurrence after TGE/cliff)
        duration_matches = list(re.finditer(r'(\d+)\s*months?\s+(?:linear|vesting)', vesting_str, re.IGNORECASE))
        if duration_matches:
            # Use the last match (usually the vesting period, not cliff)
            result["vesting_months"] = int(duration_matches[-1].group(1))

        # Detect vesting type
        if 'linear' in vesting_str.lower():
            result["vesting_type"] = "linear_monthly"
        elif 'daily' in vesting_str.lower():
            result["vesting_type"] = "linear_daily"
        elif 'quarterly' in vesting_str.lower():
            result["vesting_type"] = "quarterly"

        return result if result["tge_unlock_pct"] is not None or result["vesting_months"] is not None else None

    def _parse_vesting_events(self, events: List[Dict]) -> Optional[Dict[str, Any]]:
        """Parse vesting schedule from Dropstab event list"""
        if not events:
            return None

        result = {
            "tge_unlock_pct": None,
            "cliff_months": 0,
            "vesting_months": None,
            "vesting_type": "event_based",
            "raw_schedule": events
        }

        # Find TGE unlock (first event)
        if len(events) > 0:
            first_event = events[0]
            result["tge_unlock_pct"] = first_event.get("percent")

        # Calculate vesting duration from events
        if len(events) > 1:
            # Approximate duration in months
            result["vesting_months"] = len(events) - 1  # Rough estimate

        return result

    def _parse_vesting_object(self, vesting_obj: Dict) -> Optional[Dict[str, Any]]:
        """Parse vesting schedule from Perplexity/CryptoRank object"""
        result = {
            "tge_unlock_pct": None,
            "cliff_months": None,
            "vesting_months": None,
            "vesting_type": "unknown",
            "raw_schedule": vesting_obj
        }

        # Check for month-by-month format (Perplexity)
        if "month_1" in vesting_obj:
            # Sum up all months
            total_unlock = sum(v for k, v in vesting_obj.items() if k.startswith("month_") and v is not None)
            result["vesting_months"] = 12  # Perplexity uses 12-month format

        # Check for standard fields
        result["tge_unlock_pct"] = vesting_obj.get("tge_unlock_pct")
        result["cliff_months"] = vesting_obj.get("cliff_months", 0)
        result["vesting_months"] = vesting_obj.get("vesting_months") or result["vesting_months"]
        result["vesting_type"] = vesting_obj.get("vesting_type", "unknown")

        return result if any(v is not None for k, v in result.items() if k != "raw_schedule") else None

    def _format_vesting_human_readable(self, vesting_obj: Dict) -> str:
        """Convert normalized vesting object to human-readable string"""
        parts = []

        tge_pct = vesting_obj.get("tge_unlock_pct")
        if tge_pct:
            parts.append(f"{tge_pct}% TGE unlock")

        cliff = vesting_obj.get("cliff_months", 0)
        if cliff and cliff > 0:
            parts.append(f"{cliff} months cliff")

        vesting_months = vesting_obj.get("vesting_months")
        vesting_type = vesting_obj.get("vesting_type", "")
        if vesting_months:
            type_str = vesting_type.replace("_", " ") if vesting_type != "unknown" else ""
            parts.append(f"{vesting_months} months {type_str} vesting".strip())

        return ", ".join(parts) if parts else "Unknown vesting schedule"

    def _normalize_exchanges(self, consolidated: Dict) -> None:
        """
        Normalize exchanges field to list of strings.

        Perplexity manual research may use detailed format:
        [{"name": "Binance", "type": "Alpha", "listing_type": "Tier 2"}]

        Pipeline expects simple format:
        ["Binance", "MEXC"]

        This method normalizes to the expected format.
        """
        exchanges = consolidated.get("exchanges")

        if not exchanges:
            return

        # If already a list of strings, nothing to do
        if isinstance(exchanges, list) and all(isinstance(ex, str) for ex in exchanges):
            return

        # If list of dicts, extract "name" field
        if isinstance(exchanges, list) and all(isinstance(ex, dict) for ex in exchanges):
            normalized = [ex.get("name") for ex in exchanges if ex.get("name")]
            if normalized:
                consolidated["exchanges"] = normalized
                logger.debug(f"   Normalized {len(normalized)} exchanges from dict format to string list")
                return

        # If other format, log warning but don't modify
        logger.warning(f"   Unexpected exchanges format: {type(exchanges)} - may cause pipeline errors")

    def _merge_remaining_fields(
        self,
        automated: Dict,
        manual: Dict,
        consolidated: Dict
    ) -> List[str]:
        """
        Merge all remaining fields from automated_data that aren't already in consolidated.
        This ensures we don't lose valuable data from primary sources (Dropstab, CryptoRank, etc.)

        Session 84: Fix for MONAD data loss issue where investors, vesting_schedule, blockchain
        and other fields from primary sources were not being merged into consolidated.json.

        Args:
            automated: Data from primary sources (CryptoRank, Dropstab, CoinGecko, CMC)
            manual: Data from AI sources (Perplexity, OpenAI)
            consolidated: Current consolidated data (starts with manual_data as base)

        Returns:
            List of field names that were merged from automated_data
        """
        merged_fields = []

        # Get all fields from automated that we haven't explicitly processed
        logger.debug(f"_merge_remaining_fields: Processing {len(automated)} automated fields")
        for key, value in automated.items():
            # Skip metadata fields and empty values
            if key.startswith("_") or value is None or value == "":
                continue

            # Check if field already exists in consolidated
            existing = consolidated.get(key)

            # Debug logging for investors field
            if key == 'investors':
                logger.debug(f"  {key}: existing={type(existing)} len={len(existing) if isinstance(existing, list) else 'N/A'}, value={type(value)} len={len(value) if isinstance(value, list) else 'N/A'}")

            # Case 1: Field doesn't exist or is empty - add it
            if existing is None or existing == "" or existing == []:
                consolidated[key] = value
                merged_fields.append(key)
                if key == 'investors':
                    logger.debug(f"  {key}: Added (was empty)")
                continue

            # Case 2: Field exists - merge intelligently based on type
            if isinstance(existing, list) and isinstance(value, list):
                # For arrays: merge and deduplicate
                # Special handling for investor lists that may have mixed formats
                combined = list(existing)  # Start with existing

                for item in value:
                    # Check if item already in list
                    # For investor lists: extract name from dict if needed
                    if isinstance(item, dict) and 'name' in item:
                        item_name = item['name']
                        # Check if this name already exists in combined
                        found_index = -1
                        for idx, existing_item in enumerate(combined):
                            if isinstance(existing_item, dict) and existing_item.get('name') == item_name:
                                # Dict with same name exists - keep the dict, skip
                                found_index = idx
                                break
                            elif isinstance(existing_item, str) and existing_item == item_name:
                                # String with same name exists - replace with dict (more info)
                                found_index = idx
                                break

                        if found_index == -1:
                            # Not found - add it
                            combined.append(item)
                        elif isinstance(combined[found_index], str):
                            # Replace string with dict (dict has more info like tier)
                            combined[found_index] = item
                    elif isinstance(item, str):
                        # String investor - check if exists as string or in dict format
                        already_exists = False
                        for existing_item in combined:
                            if isinstance(existing_item, dict) and existing_item.get('name') == item:
                                already_exists = True
                                break
                            elif isinstance(existing_item, str) and existing_item == item:
                                already_exists = True
                                break
                        if not already_exists:
                            combined.append(item)
                    else:
                        # Other types - simple equality check
                        if item not in combined:
                            combined.append(item)

                # Only update if we added new items
                if len(combined) > len(existing):
                    consolidated[key] = combined
                    merged_fields.append(key)
                    logger.debug(f"   Merged {key}: {len(existing)} + {len(value)} → {len(combined)} items")
                    if key == 'investors':
                        logger.info(f"  ✅ Merged investors: {len(existing)} → {len(combined)} items")
                elif key == 'investors':
                    logger.debug(f"  {key}: No new items to merge ({len(existing)} == {len(combined)})")

            elif isinstance(existing, dict) and isinstance(value, dict):
                # For dicts: deep merge non-null values
                for sub_key, sub_value in value.items():
                    if sub_key not in existing or existing[sub_key] is None:
                        existing[sub_key] = sub_value
                        if key not in merged_fields:
                            merged_fields.append(key)

            # For scalars: existing value takes precedence (manual_data already won)

        return merged_fields

    def _extract_funding(self, data: Dict) -> Optional[float]:
        """Extract funding value from various field formats."""
        # Try direct fields
        for field in ["total_funding", "funding", "raise_amount"]:
            value = data.get(field)
            if value is not None:
                try:
                    if isinstance(value, str):
                        return float(value.replace(",", ""))
                    elif isinstance(value, dict):
                        return float(value.get("total_raised", 0))
                    return float(value)
                except (ValueError, TypeError):
                    continue
        return None

    def _format_date(self, date_str: str) -> str:
        """Format date string for display."""
        try:
            dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            return dt.strftime("%b %d, %Y at %H:%M UTC")
        except (ValueError, AttributeError):
            return date_str

    def _enhance_with_dex_data(self, consolidated: Dict[str, Any], token: str) -> List[str]:
        """
        Session 297: Enhance consolidated data with DEX sources for missing fields.

        Fills missing data from DexScreener + GeckoTerminal + On-chain RPC:
        - market_cap, float_pct, fdv_mc_ratio (from DEX pair + on-chain supply)
        - rsi_4h, rsi_14, ath_price, drawdown_from_ath (from OHLCV TA calculations)
        - days_since_ath, at_ema_200_support, dump_volume_ratio, bottom_signals_count
        - exchange_tier (DEX_ONLY), binance_listing (False)

        Only fetches if critical fields are missing - avoids redundant API calls.

        Args:
            consolidated: Current consolidated data
            token: Token symbol

        Returns:
            List of field names that were enhanced
        """
        # Define fields that trigger DEX enhancement when missing
        TRIGGER_FIELDS = [
            "market_cap", "float_pct", "float_percent",
            "rsi_4h", "rsi_14", "ath_price", "drawdown_from_ath"
        ]

        # Numeric trigger fields where 0.0 counts as "missing"
        NUMERIC_TRIGGER_FIELDS = {"market_cap", "float_pct", "float_percent", "ath_price"}

        # Check if we need enhancement (0.0 counts as missing for numeric fields)
        def is_trigger_missing(field: str) -> bool:
            val = consolidated.get(field)
            if val is None or val == "MISSING":
                return True
            if field in NUMERIC_TRIGGER_FIELDS and val in (0, 0.0):
                return True
            return False

        missing_triggers = [f for f in TRIGGER_FIELDS if is_trigger_missing(f)]

        if not missing_triggers:
            logger.debug(f"DEX enhancement skipped: No missing trigger fields for {token}")
            return []

        # Try to get contract address and chain
        contract_address = consolidated.get("contract_address")
        chain = consolidated.get("chain") or consolidated.get("blockchain")

        # If no contract address, try to find via DexScreener search
        try:
            fetcher = DEXDataFetcher()
            dex_data = fetcher.fetch_complete_data(
                symbol=token,
                contract_address=contract_address,
                chain=chain
            )

            if not dex_data or dex_data.errors:
                logger.debug(f"DEX enhancement: No data found for {token}")
                return []

            # Get consolidated format from DEX data
            dex_consolidated = fetcher.to_consolidated_format(dex_data)

            # Track which fields we enhance
            enhanced_fields = []

            # Fields to potentially fill (only if missing in consolidated)
            DEX_FIELDS_TO_FILL = [
                # Market data
                ("current_price", "current_price"),
                ("market_cap", "market_cap"),
                ("fdv", "fdv"),
                ("fdv_mc_ratio", "fdv_mc_ratio"),
                ("circulating_supply", "circulating_supply"),
                ("total_supply", "total_supply"),
                ("float_pct", "float_pct"),
                ("float_percent", "float_pct"),  # Alias
                ("liquidity_usd", "liquidity_usd"),
                ("volume_24h", "volume_24h"),
                ("chain", "chain"),
                # TA indicators
                ("rsi_4h", "rsi_4h"),
                ("rsi_14", "rsi_14"),
                ("ath_price", "ath_price"),
                ("drawdown_from_ath", "drawdown_from_ath"),
                ("days_since_ath", "days_since_ath"),
                ("at_ema_200_support", "at_ema_200_support"),
                ("dump_volume_ratio", "dump_volume_ratio"),
                ("bottom_signals_count", "bottom_signals_count"),
                # Exchange tier for DEX-only tokens
                ("exchange_tier", "exchange_tier"),
                ("binance_listing", "binance_listing"),
            ]

            # Numeric fields where 0 or 0.0 should be treated as "missing"
            NUMERIC_FIELDS = {
                "market_cap", "fdv", "circulating_supply", "total_supply",
                "float_pct", "float_percent", "liquidity_usd", "volume_24h",
                "current_price", "ath_price"
            }

            for consolidated_key, dex_key in DEX_FIELDS_TO_FILL:
                # Only fill if missing in consolidated AND available from DEX
                current_val = consolidated.get(consolidated_key)
                dex_val = dex_consolidated.get(dex_key)

                # Check if value is "missing" - None, "MISSING", "N/A", or 0 for numeric fields
                is_missing = (
                    current_val is None or
                    current_val == "MISSING" or
                    current_val == "N/A" or
                    (consolidated_key in NUMERIC_FIELDS and current_val in (0, 0.0))
                )

                if is_missing and dex_val is not None:
                    consolidated[consolidated_key] = dex_val
                    enhanced_fields.append(consolidated_key)

            # Also store contract address if we discovered it
            if not contract_address and dex_data.contract_address:
                consolidated["contract_address"] = dex_data.contract_address
                enhanced_fields.append("contract_address")

            # Store DEX info for reference
            if dex_data.dex_pair:
                consolidated["_dex_source"] = {
                    "dex": dex_data.dex_pair.dex,
                    "pair_address": dex_data.dex_pair.pair_address,
                    "chain": dex_data.chain,
                    "enhanced_at": dex_data.fetch_timestamp,
                }

            return enhanced_fields

        except Exception as e:
            logger.warning(f"DEX enhancement failed for {token}: {e}")
            return []

    def _run_sanity_checks(self, data: Dict[str, Any], token: str) -> Dict[str, List[str]]:
        """
        Session 84 - Data Pipeline Analysis: P0 Sanity Checks

        Run cross-field validation to catch data quality issues:
        1. FDV/MC ratio validation (should be realistic)
        2. Float % range validation (should be 0-100%)
        3. Circulating supply ≤ Total supply
        4. Listing price derivation check
        5. Exchange tier validation

        Args:
            data: Consolidated token data
            token: Token symbol

        Returns:
            dict with "errors" and "warnings" lists
        """
        errors = []
        warnings = []

        # CHECK 1: Float % range validation
        float_pct = data.get("float_percent")
        if float_pct is not None:
            try:
                float_val = float(float_pct)
                if float_val < 0:
                    errors.append(f"Float % is negative: {float_val}% (must be 0-100%)")
                elif float_val > 100:
                    errors.append(f"Float % exceeds 100%: {float_val}% (impossible)")
                elif float_val > 50:
                    warnings.append(f"Float % is unusually high: {float_val}% (expected <25% for most TGEs)")
            except (ValueError, TypeError):
                errors.append(f"Float % is not numeric: {float_pct}")

        # CHECK 2: Circulating supply ≤ Total supply
        circ_supply = data.get("circulating_supply_at_tge")
        total_supply = data.get("total_supply")
        if circ_supply and total_supply:
            try:
                circ_val = float(circ_supply)
                total_val = float(total_supply)
                if circ_val > total_val:
                    errors.append(f"Circulating supply ({circ_val:,}) > Total supply ({total_val:,}) - impossible")
                    # Session 85 FIX: Auto-correct using float_percent if available
                    float_pct = data.get("float_percent")
                    if float_pct:
                        try:
                            corrected_circ = total_val * (float(float_pct) / 100.0)
                            data["circulating_supply_at_tge"] = corrected_circ
                            data["_circulating_supply_corrected"] = True
                            data["_circulating_supply_original"] = circ_val
                            logger.warning(f"⚠️  AUTO-CORRECTED circulating_supply_at_tge: {circ_val:,} → {corrected_circ:,.0f} (using float_percent={float_pct}%)")
                            # Clear the error since we fixed it
                            errors.pop()  # Remove the error we just added

                            # Session 85: Recalculate market cap with corrected circulating supply
                            price_low = data.get("listing_price_low")
                            price_high = data.get("listing_price_high")
                            if price_low:
                                mc_low = corrected_circ * float(price_low)
                                data["initial_market_cap_low"] = mc_low
                                logger.info(f"   📐 Recalculated initial_market_cap_low: {mc_low:,.0f}")
                            if price_high:
                                mc_high = corrected_circ * float(price_high)
                                data["initial_market_cap_high"] = mc_high
                                logger.info(f"   📐 Recalculated initial_market_cap_high: {mc_high:,.0f}")
                        except (ValueError, TypeError):
                            pass  # Keep error if correction fails
                # Session 85: Also validate consistency even when circ < total
                elif float_pct and abs(circ_val - (total_val * float(float_pct) / 100.0)) / circ_val > 0.10:
                    # More than 10% difference between stated and calculated circulating supply
                    calculated = total_val * (float(float_pct) / 100.0)
                    warnings.append(f"Circulating supply mismatch: stated={circ_val:,.0f} but calculated from float_percent={calculated:,.0f} (diff={(circ_val-calculated)/circ_val*100:.1f}%)")
            except (ValueError, TypeError):
                pass  # Non-numeric values already flagged by field validator

        # CHECK 3: FDV/MC ratio validation (must be ≥ 1.0)
        fdv_low = data.get("fdv_low")
        fdv_high = data.get("fdv_high")
        fdv_mc_low = data.get("fdv_mc_ratio_low")
        fdv_mc_high = data.get("fdv_mc_ratio_high")

        if fdv_mc_low is not None:
            try:
                ratio_low = float(fdv_mc_low)
                if ratio_low < 1.0:
                    errors.append(f"FDV/MC ratio_low ({ratio_low:.2f}x) is < 1.0 (impossible - FDV must be ≥ MC)")
                elif ratio_low > 50.0:
                    warnings.append(f"FDV/MC ratio_low ({ratio_low:.2f}x) is extremely high (potential data error)")
            except (ValueError, TypeError):
                pass

        if fdv_mc_high is not None:
            try:
                ratio_high = float(fdv_mc_high)
                if ratio_high < 1.0:
                    errors.append(f"FDV/MC ratio_high ({ratio_high:.2f}x) is < 1.0 (impossible - FDV must be ≥ MC)")
                elif ratio_high > 50.0:
                    warnings.append(f"FDV/MC ratio_high ({ratio_high:.2f}x) is extremely high (potential data error)")
            except (ValueError, TypeError):
                pass

        # CHECK 4: FDV range validation (low ≤ high)
        if fdv_low and fdv_high:
            try:
                fdv_low_val = float(fdv_low)
                fdv_high_val = float(fdv_high)
                if fdv_low_val > fdv_high_val:
                    errors.append(f"FDV low (${fdv_low_val:,.0f}) > FDV high (${fdv_high_val:,.0f}) - range is inverted")

                # Session 85 Option A: Detect placeholder FDV values (repeating digits)
                # Conservative approach - mark as unreliable instead of auto-correcting
                def is_placeholder_number(num: float) -> bool:
                    """Detect placeholder numbers like 11111111111 or 22222222222"""
                    num_str = str(int(num))
                    if len(num_str) < 5:
                        return False
                    # Check if all digits are the same (repeating)
                    unique_digits = set(num_str)
                    return len(unique_digits) == 1

                # Check if FDV values are placeholders
                if is_placeholder_number(fdv_low_val) or is_placeholder_number(fdv_high_val):
                    warnings.append(f"FDV values appear to be placeholders (repeating digits): ${fdv_low_val:,.0f} / ${fdv_high_val:,.0f}")

                    # Session 85 Option A: Mark as unreliable instead of auto-correcting
                    # Why: listing_price is often ALSO a placeholder from the same Perplexity response
                    # Auto-correcting placeholder with placeholder = garbage from garbage
                    # Lesson: Never conflate pre-TGE (IDO) price with TGE listing price
                    data["fdv_low"] = None
                    data["fdv_high"] = None
                    data["fdv"] = None
                    data["_fdv_data_quality"] = "PLACEHOLDER"
                    data["_skip_fdv_mc_scoring"] = True
                    data["_fdv_placeholder_original"] = {
                        "fdv_low": fdv_low_val,
                        "fdv_high": fdv_high_val
                    }
                    logger.warning("⚠️  FDV data is PLACEHOLDER - marked as unreliable, skipping FDV/MC conviction scoring")
                    logger.info("   💡 Conservative approach: Skip scoring component rather than use garbage data")

            except (ValueError, TypeError):
                pass

        # CHECK 5: Listing price derivation consistency
        listing_price_low = data.get("listing_price_low")
        listing_price_high = data.get("listing_price_high")

        if fdv_low and total_supply and not listing_price_low:
            # Listing price can be derived but is missing - this is OK (validator handles)
            pass

        if listing_price_low and listing_price_high:
            try:
                price_low = float(listing_price_low)
                price_high = float(listing_price_high)
                if price_low > price_high:
                    errors.append(f"Listing price low (${price_low:.4f}) > high (${price_high:.4f}) - range is inverted")

                # Session 85 Option A: Check for suspiciously round listing prices
                # Often indicates placeholder data from Perplexity (e.g., $0.01, $0.02)
                if price_low in [0.01, 0.1, 1.0, 10.0] and price_high in [0.02, 0.2, 2.0, 20.0]:
                    warnings.append(f"Listing prices appear suspiciously round: ${price_low} / ${price_high} (may be Perplexity placeholders)")
                    data["_listing_price_confidence"] = "LOW"
                    logger.info("   ⚠️  Listing prices are suspiciously round - likely placeholders")
            except (ValueError, TypeError):
                pass

        # CHECK 6: Exchange tier validation
        exchanges = data.get("listing_exchanges")
        if exchanges:
            if isinstance(exchanges, list):
                if len(exchanges) == 0:
                    warnings.append("Listing exchanges list is empty (expected at least 1 exchange)")
                # Could add tier-1 exchange detection here (Binance, Coinbase, etc.)
            elif isinstance(exchanges, str):
                # Single exchange string - should be converted to list
                warnings.append(f"Listing exchanges is string, not list: '{exchanges}' (should be ['exchange1', ...])")

        # CHECK 7: TGE date format validation
        tge_date = data.get("tge_date")
        if tge_date:
            try:
                # Try parsing as ISO format
                if isinstance(tge_date, str):
                    datetime.fromisoformat(tge_date.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                warnings.append(f"TGE date format may be invalid: '{tge_date}' (expected ISO format)")

        # CHECK 8: OTC price validation (Session 86 - SEEK learning)
        # Detect stale/invalid OTC prices that cause false conviction boosts
        # SEEK case: otc_price=0.0084 (presale price) vs listing_price=0.408 = 4126% "premium"
        otc_price = data.get("otc_price")
        otc_analysis_possible = data.get("otc_analysis_possible")
        listing_price = data.get("listing_price_low") or data.get("listing_price_high") or data.get("actual_listing_price")

        if otc_price is not None:
            try:
                otc_val = float(otc_price)

                # Check 8a: If otc_analysis_possible is "NO", clear the OTC price
                if otc_analysis_possible in ["NO", "no", False]:
                    warnings.append(f"OTC price {otc_val} exists but otc_analysis_possible=NO - clearing stale data")
                    data["otc_price"] = None
                    data["listing_vs_otc_premium_pct"] = None
                    data["_otc_cleared_reason"] = "otc_analysis_possible was NO"
                    logger.warning(f"⚠️  Cleared stale OTC price: {otc_val} (otc_analysis_possible=NO)")

                # Check 8b: If OTC price is suspiciously different from listing price (>10x difference)
                elif listing_price:
                    try:
                        listing_val = float(listing_price)
                        if otc_val > 0 and listing_val > 0:
                            premium_pct = ((listing_val - otc_val) / otc_val) * 100

                            # >1000% premium is almost certainly stale/wrong OTC data
                            if premium_pct > 1000:
                                errors.append(f"OTC premium {premium_pct:.0f}% is impossibly high - OTC price {otc_val} is likely stale presale price, not current OTC")
                                data["otc_price"] = None
                                data["listing_vs_otc_premium_pct"] = None
                                data["_otc_cleared_reason"] = f"Impossible premium: {premium_pct:.0f}% (likely presale price, not OTC)"
                                logger.error(f"❌ Cleared invalid OTC price: {otc_val} (impossible {premium_pct:.0f}% premium vs listing {listing_val})")

                            # >200% premium is suspicious but possible
                            elif premium_pct > 200:
                                warnings.append(f"OTC premium {premium_pct:.0f}% is very high - verify OTC data source")
                    except (ValueError, TypeError):
                        pass
            except (ValueError, TypeError):
                warnings.append(f"OTC price is not numeric: {otc_price}")

        return {
            "errors": errors,
            "warnings": warnings
        }

    def _calculate_confidence(self) -> int:
        """Calculate overall data confidence."""
        total_fields = len(self.agreements) + len(self.resolutions) + len(self.missing_fields)
        if total_fields == 0:
            return 0

        # Agreements: 100%, Resolved conflicts: 90%, Missing: 50%
        score = (
            len(self.agreements) * 100 +
            len(self.resolutions) * 90 +
            len(self.missing_fields) * 50
        ) / total_fields

        return int(score)

    def save_consolidated(
        self,
        consolidated_data: Dict[str, Any],
        output_path: str,
        create_backup: bool = True
    ) -> None:
        """
        Save consolidated data to JSON file.

        Args:
            consolidated_data: Consolidated data dictionary
            output_path: Path to save consolidated JSON
            create_backup: If True, backup existing file before overwriting
        """
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # Create backup if file exists
        if create_backup and output_file.exists():
            backup_path = output_file.with_suffix('.json.bak')
            output_file.rename(backup_path)
            logger.info(f"📦 Created backup: {backup_path}")

        # Session 306: Set last_updated timestamp for data freshness tracking
        # This is THE canonical timestamp for when source data was fetched/consolidated
        from datetime import datetime, timezone
        consolidated_data["last_updated"] = datetime.now(timezone.utc).isoformat()

        # Session 79K: Reorder fields for human scanning
        # Critical trading data first, sources/metadata at bottom
        ordered_data = _reorder_consolidated_fields(consolidated_data)

        # Save consolidated data
        with open(output_file, 'w') as f:
            json.dump(ordered_data, f, indent=2)

        logger.info(f"✅ Saved consolidated data: {output_file}")
        logger.info(f"   Confidence: {consolidated_data['_consolidation_metadata']['data_confidence']}%")
        logger.info(f"   Agreements: {consolidated_data['_consolidation_metadata']['agreements']}")
        logger.info(f"   Conflicts resolved: {consolidated_data['_consolidation_metadata']['conflicts_resolved']}")


def consolidate_token(
    token: str,
    dry_run: bool = False,
    primary_sources_only: bool = False
) -> Optional[Dict]:
    """
    Session 79G: Consolidate a token from sources/raw/ and write consolidated.json

    This is the ONLY function that should write to consolidated.json.
    It reads from sources/raw/ (cryptorank.json, perplexity.json, dropstab.json)
    and writes a single consolidated.json to the token root directory.

    Session 79I: Added primary_sources_only mode to exhaust free APIs before Perplexity.

    Args:
        token: Token symbol (e.g., "IRYS", "MONAD")
        dry_run: If True, don't write file, just return result
        primary_sources_only: If True, SKIP Perplexity/OpenAI data entirely.
                              Use only: CryptoRank, Dropstab, CoinGecko, CMC

    Returns:
        Consolidated data dictionary, or None if no data available
    """
    # Find project root
    project_root = Path(__file__).parent.parent.parent
    token_dir = project_root / "data" / "tokens" / token.upper()

    if not token_dir.exists():
        logger.error(f"Token directory not found: {token_dir}")
        return None

    # Session 79G: New structure - sources/raw/
    raw_dir = token_dir / "sources" / "raw"

    # Fallback to legacy structure
    if not raw_dir.exists():
        raw_dir = token_dir / "sources"

    if not raw_dir.exists():
        logger.error(f"Sources directory not found for {token}")
        return None

    # Load source files
    automated_data = {}
    manual_data = {}

    # Session 84: Track source URLs for citation preservation
    field_sources = {}  # Maps field_name -> source_url

    def track_field_sources(data: Dict, field_sources: Dict) -> None:
        """
        Session 84: Extract source URL from data and track which fields came from which source.

        Args:
            data: Source data dictionary with _source_url metadata
            field_sources: Dictionary to populate with field -> source_url mappings
        """
        source_url = data.get('_source_url')
        if not source_url:
            return

        # Track all non-empty, non-metadata fields
        for field, value in data.items():
            if field.startswith('_'):
                continue  # Skip metadata fields
            if value is None or value == '' or value == [] or value == {}:
                continue  # Skip empty values

            # Only track CRITICAL fields that need citations
            if field in CRITICAL_FIELDS_REQUIRING_CITATION:
                # Don't override if already tracked (first source wins)
                if field not in field_sources:
                    field_sources[field] = source_url
                    logger.debug(f"    📌 Tracking {field} source: {source_url}")

    # CryptoRank → automated_data
    cryptorank_file = raw_dir / "cryptorank.json"
    if not cryptorank_file.exists():
        # Legacy fallback: search for *cryptorank*.json
        for f in raw_dir.glob("*cryptorank*.json"):
            cryptorank_file = f
            break

    if cryptorank_file.exists():
        with open(cryptorank_file) as f:
            automated_data = json.load(f)
        track_field_sources(automated_data, field_sources)
        logger.info(f"📥 Loaded CryptoRank data: {cryptorank_file.name}")

    # Dropstab → extend automated_data
    dropstab_file = raw_dir / "dropstab.json"
    if not dropstab_file.exists():
        for f in raw_dir.glob("*dropstab*.json"):
            dropstab_file = f
            break

    if dropstab_file.exists():
        with open(dropstab_file) as f:
            dropstab_data = json.load(f)
        track_field_sources(dropstab_data, field_sources)
        # Merge dropstab into automated (cryptorank takes precedence for non-empty values)
        for key, value in dropstab_data.items():
            existing = automated_data.get(key)
            # Session 84: Also check for empty lists/dicts, not just None
            if existing is None or existing == [] or existing == {}:
                automated_data[key] = value
        logger.info(f"📥 Loaded Dropstab data: {dropstab_file.name}")

    # Session 85: ICODrops → extend automated_data (high-quality vesting/whitepaper/farming)
    icodrops_file = raw_dir / "icodrops.json"
    if not icodrops_file.exists():
        for f in raw_dir.glob("*icodrops*.json"):
            icodrops_file = f
            break

    if icodrops_file.exists():
        with open(icodrops_file) as f:
            icodrops_data = json.load(f)
        track_field_sources(icodrops_data, field_sources)
        # Merge ICODrops (previous sources take precedence for non-empty values)
        # Session 85: Focus on vesting, whitepaper, and farming fields
        icodrops_fields = [
            "vesting_schedule", "whitepaper_url", "farming_sources",
            "total_supply", "token_allocation"
        ]
        for key in icodrops_fields:
            if icodrops_data.get(key) and not automated_data.get(key):
                automated_data[key] = icodrops_data[key]
        logger.info(f"📥 Loaded ICODrops data: {icodrops_file.name}")

    # Session 79H: CoinGecko → extend automated_data (free API for FDV, contract_address)
    coingecko_file = raw_dir / "coingecko.json"
    if not coingecko_file.exists():
        for f in raw_dir.glob("*coingecko*.json"):
            coingecko_file = f
            break

    if coingecko_file.exists():
        with open(coingecko_file) as f:
            coingecko_data = json.load(f)
        track_field_sources(coingecko_data, field_sources)
        # Merge coingecko (previous sources take precedence)
        # Session 79I: Added new IMPORTANT fields from CoinGecko
        coingecko_fields = [
            "contract_address", "fdv", "market_cap", "current_price",
            "circulating_supply", "total_supply",
            # Session 79I: New IMPORTANT fields (use correct field names)
            "whitepaper_url", "website", "website_url", "project_description",
            "categories", "twitter_handle", "twitter_url", "telegram_channel"
        ]
        for key in coingecko_fields:
            if coingecko_data.get(key) and not automated_data.get(key):
                automated_data[key] = coingecko_data[key]
        logger.info(f"📥 Loaded CoinGecko data: {coingecko_file.name}")

    # Session 79H: CoinMarketCap → contract_address only (333/day limit)
    cmc_file = raw_dir / "coinmarketcap.json"
    if not cmc_file.exists():
        for f in raw_dir.glob("*coinmarketcap*.json"):
            cmc_file = f
            break

    if cmc_file.exists():
        with open(cmc_file) as f:
            cmc_data = json.load(f)
        track_field_sources(cmc_data, field_sources)
        # CMC is primarily used for contract_address (most reliable for that field)
        if cmc_data.get("contract_address") and not automated_data.get("contract_address"):
            automated_data["contract_address"] = cmc_data["contract_address"]
            logger.info(f"📥 Loaded CMC contract_address: {cmc_data['contract_address']}")

    # Perplexity → manual_data (most comprehensive research)
    # Session 79I: Skip Perplexity if primary_sources_only mode is enabled
    sources_dir = token_dir / "sources"
    all_sources_used = []  # Collect all data_sources_used entries

    if primary_sources_only:
        logger.info("⚡ PRIMARY SOURCES ONLY mode - skipping Perplexity/OpenAI data")
    else:
        # Session 79H: Load ALL perplexity files and merge (newer values override, but keep data from older)
        perplexity_files = []

        # Collect all perplexity files from both locations
        # raw/ folder (legacy)
        legacy_file = raw_dir / "perplexity.json"
        if legacy_file.exists():
            perplexity_files.append(legacy_file)
        for f in raw_dir.glob("*perplexity*.json"):
            if f not in perplexity_files:
                perplexity_files.append(f)

        # sources/ folder (API-generated, numbered)
        for f in sorted(sources_dir.glob("*perplexity*.json")):
            if f not in perplexity_files:
                perplexity_files.append(f)

        # Load and merge all perplexity files (older first, newer overrides)
        for pf in perplexity_files:
            with open(pf) as f:
                pf_data = json.load(f)
            track_field_sources(pf_data, field_sources)
            # Collect all data_sources_used for contract extraction later
            if pf_data.get("data_sources_used"):
                all_sources_used.extend(pf_data.get("data_sources_used", []))
            # Merge: newer data takes priority, BUT never replace non-empty with empty
            for key, value in pf_data.items():
                existing = manual_data.get(key)
                # For lists: only override if new list has more items
                if isinstance(value, list):
                    if not existing or (len(value) > len(existing) if isinstance(existing, list) else len(value) > 0):
                        manual_data[key] = value
                # For other values: newer file wins (unless value is None/empty)
                elif value is not None and value != "":
                    manual_data[key] = value
            logger.info(f"📥 Loaded Perplexity data: {pf.name}")

    # Session 86: Web Research files (manual research with nested 'data:' structure)
    # These files have rich data but in a nested format that needs flattening
    for f in raw_dir.glob("*web_research*.json"):
        try:
            with open(f) as wf:
                web_data = json.load(wf)
            # Handle nested 'data:' structure common in web research files
            if "data" in web_data and isinstance(web_data["data"], dict):
                flat_data = web_data["data"]
                # Also grab top-level metadata
                for key in ["token_symbol", "token_name", "source", "fetched_at"]:
                    if key in web_data and key not in flat_data:
                        flat_data[key] = web_data[key]
            else:
                flat_data = web_data

            track_field_sources(flat_data, field_sources)
            # Merge web_research data (fills gaps only, doesn't override)
            for key, value in flat_data.items():
                existing = manual_data.get(key)
                if existing is None or existing == [] or existing == {}:
                    manual_data[key] = value
            logger.info(f"📥 Loaded Web Research data: {f.name}")
        except Exception as e:
            logger.warning(f"⚠️ Failed to load {f.name}: {e}")

    # Store all sources for contract extraction (may contain full URLs)
    manual_data["_all_data_sources"] = all_sources_used

    # Session 79H: Extract contract_address from source data before consolidation
    # Search all source data for EVM contract addresses
    import re
    eth_pattern = re.compile(r'0x[a-fA-F0-9]{40}')

    def extract_contract_from_sources(*sources):
        """Search source dicts for contract addresses embedded in URLs or fields."""
        for source in sources:
            if not source:
                continue
            source_str = json.dumps(source)
            matches = eth_pattern.findall(source_str)
            if matches:
                # Return the first valid contract (prefer ones not from common addresses)
                for addr in matches:
                    # Skip common non-contract addresses (like DEX routers, etc)
                    if addr.lower() not in [
                        "0x0000000000000000000000000000000000000000",
                    ]:
                        return addr
        return None

    # Check if contract_address already exists in sources
    contract_addr = (
        manual_data.get("contract_address") or
        automated_data.get("contract_address") or
        extract_contract_from_sources(manual_data, automated_data)
    )

    # Also search the collected data_sources URLs for contract addresses
    if not contract_addr and all_sources_used:
        sources_str = json.dumps(all_sources_used)
        matches = eth_pattern.findall(sources_str)
        for addr in matches:
            if addr.lower() not in ["0x0000000000000000000000000000000000000000"]:
                contract_addr = addr
                break

    if contract_addr and not manual_data.get("contract_address"):
        manual_data["contract_address"] = contract_addr
        logger.info(f"📥 Extracted contract_address: {contract_addr}")

    # Enhancement 3 (Session 87): Contract Address Priority for Generic Names
    # Generic token names (STABLE, TOKEN, COIN, etc.) are high-risk for confusion
    # Always use contract address as primary identifier
    generic_token_names = ["STABLE", "TOKEN", "COIN", "CHAIN", "SWAP", "BRIDGE", "NETWORK"]
    if token.upper() in generic_token_names:
        if contract_addr:
            logger.warning(f"⚠️  Generic token name '{token}' detected")
            logger.warning(f"   Using contract address as primary identifier: {contract_addr}")
            logger.warning(f"   Multiple projects may share this symbol - verify contract on blockchain explorer")
            manual_data["_generic_name_warning"] = {
                "token_symbol": token,
                "contract_address": contract_addr,
                "warning": f"Multiple projects may use symbol '{token}' - always verify contract address"
            }
        else:
            logger.error(f"🚨 CRITICAL: Generic token name '{token}' without contract address")
            logger.error(f"   Cannot reliably identify token - high risk of data confusion")
            logger.error(f"   Recommendation: Find contract address before proceeding")

    if not automated_data and not manual_data:
        logger.error(f"No source data found for {token}")
        return None

    # Consolidate
    consolidator = DataConsolidator()

    # Use manual_data as base if automated is empty
    if not automated_data:
        automated_data = {}
    if not manual_data:
        manual_data = automated_data
        automated_data = {}

    # Session 80: Phase 2 - Load existing consolidated data to preserve manual overrides
    existing_consolidated = None
    output_file = token_dir / "consolidated.json"

    if output_file.exists():
        try:
            with open(output_file) as f:
                existing_consolidated = json.load(f)
                logger.info(f"📥 Found existing consolidated.json")
        except Exception as e:
            logger.warning(f"⚠️  Could not load existing consolidated: {e}")

    result = consolidator.consolidate(automated_data, manual_data, token, dry_run=dry_run)

    # Session 84: Inject source citations for CRITICAL fields
    for field, source_url in field_sources.items():
        if field in result and result[field] is not None:
            citation_field = f"{field}_source"
            result[citation_field] = source_url
            logger.debug(f"    ✅ Added citation: {citation_field} = {source_url}")

    # Session 80: Phase 2 - Restore manual overrides (preserve human edits)
    if existing_consolidated and '_manual_overrides' in existing_consolidated:
        manual_overrides = existing_consolidated['_manual_overrides']

        logger.info(f"  🔒 Restoring {len(manual_overrides)} manual overrides")

        for field, override_data in manual_overrides.items():
            if field in result:
                old_value = result[field]
                result[field] = override_data['value']
                logger.info(f"    {field}: {old_value} → {override_data['value']} (manual override)")

        # Preserve override metadata
        result['_manual_overrides'] = manual_overrides

    # Session 80: Phase 1 - Auto-calculate float % from vesting schedules
    try:
        # Import from same directory
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from float_calculator import FloatCalculator

        if not result.get('float_percent'):
            allocation = result.get('token_allocation', {})
            vesting = result.get('vesting_schedule', {})

            if allocation and vesting:
                calculated_float = FloatCalculator.calculate_tge_float(allocation, vesting)

                if calculated_float:
                    logger.info(f"✅ Auto-calculated float_percent: {calculated_float}%")
                    result['float_percent'] = calculated_float
                    result['_float_calculated_from'] = 'vesting_schedule'

                    # Also calculate circulating supply if we have total
                    if result.get('total_supply'):
                        result['circulating_supply_at_tge'] = FloatCalculator.derive_circulating_supply(
                            calculated_float,
                            result['total_supply']
                        )
                        logger.info(f"✅ Derived circulating_supply_at_tge: {result['circulating_supply_at_tge']:,}")
    except Exception as e:
        logger.warning(f"⚠️  Float calculation failed: {e}")

    # Session 80: Phase 1 - Validate consolidated data
    try:
        from data_validators import DataValidator

        validation = DataValidator.validate_all(result)

        if not validation['valid']:
            logger.error(f"❌ Validation failed for {token}:")
            for error in validation['errors']:
                logger.error(f"  - {error['field']}: {error['message']}")

            # Add validation status to result
            result['_validation_errors'] = validation['errors']
            result['_needs_manual_review'] = True

            # Suggest fixes
            suggestions = DataValidator.suggest_fixes(result, validation)
            if suggestions:
                logger.info(f"💡 Suggested fixes:")
                for fix in suggestions:
                    logger.info(f"  - {fix['field']}: {fix['current_value']} → {fix['suggested_value']}")
                    logger.info(f"    Reason: {fix['reason']} (confidence: {fix['confidence']}%)")

                result['_validation_suggestions'] = suggestions

        if validation.get('warnings'):
            logger.warning(f"⚠️  Validation warnings for {token}:")
            for warning in validation['warnings']:
                logger.warning(f"  - {warning['field']}: {warning['message']}")
            result['_validation_warnings'] = validation['warnings']

    except Exception as e:
        logger.warning(f"⚠️  Validation failed: {e}")

    if not dry_run:
        # Session 79G: Write to token root as consolidated.json
        output_file = token_dir / "consolidated.json"
        consolidator.save_consolidated(result, str(output_file), create_backup=False)
        logger.info(f"✅ Consolidated {token} → {output_file}")

    return result


# CLI for testing
if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(
        description="Data Consolidator - Merge source files into consolidated.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Session 79G: Consolidate a token from sources/raw/
  python data_consolidator.py --token IRYS

  # Dry run (don't write file)
  python data_consolidator.py --token IRYS --dry-run

  # Legacy: Consolidate from specific files
  python data_consolidator.py automated.json manual.json
        """
    )

    parser.add_argument(
        "--token", "-t",
        type=str,
        help="Token symbol to consolidate (uses new sources/raw/ structure)"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without writing files"
    )

    parser.add_argument(
        "automated_json",
        nargs="?",
        help="(Legacy) Path to automated data JSON"
    )

    parser.add_argument(
        "manual_json",
        nargs="?",
        help="(Legacy) Path to manual data JSON"
    )

    args = parser.parse_args()

    # Session 79G: New token-based mode
    if args.token:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        result = consolidate_token(args.token, dry_run=args.dry_run)
        if result:
            print(f"\n✅ Consolidation complete for {args.token}")
            print(f"   Confidence: {result['_consolidation_metadata']['data_confidence']}%")
        else:
            print(f"❌ Failed to consolidate {args.token}")
            sys.exit(1)

    # Legacy file-based mode
    elif args.automated_json and args.manual_json:
        automated_path = Path(args.automated_json)
        manual_path = Path(args.manual_json)
        dry_run = args.dry_run

        if not automated_path.exists():
            print(f"❌ Automated data not found: {automated_path}")
            sys.exit(1)

        if not manual_path.exists():
            print(f"❌ Manual data not found: {manual_path}")
            sys.exit(1)

        # Load data
        with open(automated_path) as f:
            automated_data = json.load(f)

        with open(manual_path) as f:
            manual_data = json.load(f)

        # Extract token name
        token = manual_data.get("symbol") or manual_data.get("token_symbol") or "UNKNOWN"

        # Consolidate
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        consolidator = DataConsolidator()
        result = consolidator.consolidate(automated_data, manual_data, token, dry_run=dry_run)

        # Print summary
        print("\n" + "="*70)
        print(f"CONSOLIDATION SUMMARY: {token}")
        print("="*70)
        print(f"Data Confidence: {result['_consolidation_metadata']['data_confidence']}%")
        print(f"Agreements: {result['_consolidation_metadata']['agreements']}")
        print(f"Conflicts Resolved: {result['_consolidation_metadata']['conflicts_resolved']}")
        print(f"Missing from Automated: {result['_consolidation_metadata']['missing_from_automated']}")

        if consolidator.resolutions:
            print("\n CONFLICTS RESOLVED:")
            for resolution in consolidator.resolutions:
                print(f"  * {resolution.field}: {resolution.resolution_type}")
                print(f"    Chosen: {resolution.chosen_value}")
                print(f"    Reasoning: {resolution.reasoning}")

        if not dry_run:
            # Session 79G: Save to consolidated.json in token root
            token_dir = manual_path.parent.parent
            output_file = token_dir / "consolidated.json"
            consolidator.save_consolidated(result, str(output_file), create_backup=False)
            print(f"\n✅ Saved: {output_file}")
        else:
            print("\n DRY RUN - No files saved")
    else:
        parser.print_help()
