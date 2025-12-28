#!/usr/bin/env python3
"""
FDV Semantic Validator - LLM-based Outlier Detection.

Session 251 Gemini Review: "Add an LLM Sanity Check step. Ask the LLM:
'Is an FDV of $100B for a new L2 realistic compared to Optimism/Arbitrum?'
If the LLM says 'Outlier,' flag the data for manual review.
This prevents 'Fat Finger' data entry errors from your sources from skewing scores."

Purpose:
- Detect unrealistic FDV values before they pollute conviction scoring
- Compare against established benchmarks (OP, ARB, SOL, etc.)
- Flag anomalies for manual review rather than auto-skip

Cost: ~$0.001 per validation (GPT-4o-mini recommended)

Usage:
    from scripts.helpers.fdv_semantic_validator import validate_fdv_realism
    result = validate_fdv_realism("MONAD", 10_000_000_000, "L2")
    if result["is_outlier"]:
        print(f"WARNING: {result['reason']}")
"""

import json
import os
import logging
from typing import Dict, Optional, Any
from dataclasses import dataclass, asdict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Benchmark FDVs by category (as of Dec 2025)
# Used for quick heuristic checks before LLM validation
CATEGORY_BENCHMARKS = {
    "L1": {
        "established": ["ETH", "SOL", "AVAX", "NEAR", "APT", "SUI"],
        "typical_fdv_range": (1_000_000_000, 100_000_000_000),  # $1B - $100B
        "max_reasonable_new_launch": 50_000_000_000,  # $50B max for new L1
        "examples": {
            "SOL": 90_000_000_000,
            "AVAX": 15_000_000_000,
            "NEAR": 8_000_000_000,
            "APT": 12_000_000_000,
            "SUI": 25_000_000_000
        }
    },
    "L2": {
        "established": ["OP", "ARB", "MATIC", "BASE", "MANTA", "BLAST"],
        "typical_fdv_range": (100_000_000, 20_000_000_000),  # $100M - $20B
        "max_reasonable_new_launch": 15_000_000_000,  # $15B max for new L2
        "examples": {
            "OP": 12_000_000_000,
            "ARB": 10_000_000_000,
            "MATIC": 5_000_000_000,
            "MANTA": 2_000_000_000,
            "BLAST": 3_000_000_000
        }
    },
    "DeFi": {
        "established": ["UNI", "AAVE", "MKR", "CRV", "LDO"],
        "typical_fdv_range": (10_000_000, 10_000_000_000),  # $10M - $10B
        "max_reasonable_new_launch": 5_000_000_000,  # $5B max for new DeFi
        "examples": {
            "UNI": 6_000_000_000,
            "AAVE": 2_500_000_000,
            "LDO": 2_000_000_000,
            "CRV": 500_000_000
        }
    },
    "Gaming": {
        "established": ["AXS", "SAND", "MANA", "IMX", "GALA"],
        "typical_fdv_range": (5_000_000, 3_000_000_000),  # $5M - $3B
        "max_reasonable_new_launch": 2_000_000_000,  # $2B max for new Gaming
        "examples": {
            "AXS": 1_500_000_000,
            "IMX": 2_000_000_000,
            "GALA": 500_000_000
        }
    },
    "AI": {
        "established": ["FET", "AGIX", "OCEAN", "RNDR", "TAO"],
        "typical_fdv_range": (10_000_000, 15_000_000_000),  # $10M - $15B
        "max_reasonable_new_launch": 10_000_000_000,  # $10B max for new AI
        "examples": {
            "FET": 4_000_000_000,
            "TAO": 8_000_000_000,
            "RNDR": 5_000_000_000
        }
    },
    "Meme": {
        "established": ["DOGE", "SHIB", "PEPE", "BONK", "WIF"],
        "typical_fdv_range": (1_000_000, 30_000_000_000),  # $1M - $30B (wide range)
        "max_reasonable_new_launch": 5_000_000_000,  # $5B max for new meme at TGE
        "examples": {
            "DOGE": 25_000_000_000,
            "SHIB": 8_000_000_000,
            "PEPE": 3_000_000_000
        }
    },
    "Infrastructure": {
        "established": ["LINK", "GRT", "FIL", "AR", "PYTH"],
        "typical_fdv_range": (50_000_000, 15_000_000_000),  # $50M - $15B
        "max_reasonable_new_launch": 8_000_000_000,  # $8B max for new infra
        "examples": {
            "LINK": 12_000_000_000,
            "GRT": 3_000_000_000,
            "PYTH": 4_000_000_000
        }
    },
    "Unknown": {
        "typical_fdv_range": (1_000_000, 5_000_000_000),  # Conservative default
        "max_reasonable_new_launch": 3_000_000_000,  # $3B max for unknown category
        "examples": {}
    }
}


@dataclass
class FDVValidationResult:
    """Result of FDV semantic validation."""
    token: str
    fdv: float
    category: str
    is_outlier: bool
    severity: str  # "LOW", "MEDIUM", "HIGH", "CRITICAL"
    reason: str
    benchmark_comparison: Dict[str, Any]
    recommended_action: str  # "PROCEED", "VERIFY", "SKIP", "MANUAL_REVIEW"
    confidence: float  # 0-1 confidence in the validation


def _heuristic_check(fdv: float, category: str) -> tuple:
    """
    Quick heuristic check before expensive LLM call.

    Returns (is_outlier, severity, reason) or (None, None, None) if uncertain.
    """
    benchmarks = CATEGORY_BENCHMARKS.get(category, CATEGORY_BENCHMARKS["Unknown"])
    min_fdv, max_fdv = benchmarks["typical_fdv_range"]
    max_new = benchmarks.get("max_reasonable_new_launch", max_fdv)

    # Obvious outliers - don't need LLM
    if fdv > max_new * 3:  # 3x above max reasonable
        return True, "CRITICAL", f"FDV ${fdv/1e9:.1f}B is 3x+ above max reasonable for {category} (${max_new/1e9:.1f}B)"

    if fdv < 100_000:  # Below $100K is suspicious for any TGE
        return True, "HIGH", f"FDV ${fdv/1e6:.2f}M is suspiciously low for any TGE"

    if fdv > max_new:  # Above max but not extreme
        return True, "MEDIUM", f"FDV ${fdv/1e9:.1f}B exceeds typical max for new {category} (${max_new/1e9:.1f}B)"

    if fdv < min_fdv * 0.1:  # 10x below minimum typical
        return True, "LOW", f"FDV ${fdv/1e6:.1f}M is below typical minimum for {category}"

    # Within normal range
    return False, "LOW", "FDV within normal range"


def _llm_validate(token: str, fdv: float, category: str, additional_context: str = "") -> Dict[str, Any]:
    """
    Use LLM to validate FDV realism with market context.

    Uses OpenAI GPT-4o-mini for cost efficiency (~$0.001 per call).
    Includes response caching to reduce redundant API calls.
    """
    import requests

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.warning("No OPENAI_API_KEY - skipping LLM validation")
        return {"used_llm": False, "error": "No API key"}

    # Check cache first
    try:
        from src.utils.llm_cache import get_llm_cache
        cache = get_llm_cache()
        cache_key_params = f"{token}_{fdv}_{category}"
        cached = cache.get("openai_fdv", cache_key_params, model="gpt-4o-mini")
        if cached:
            logger.info(f"✅ FDV validation cache HIT for {token} (saved $0.001)")
            return cached
    except ImportError:
        pass  # Cache not available

    # Get benchmark context
    benchmarks = CATEGORY_BENCHMARKS.get(category, CATEGORY_BENCHMARKS["Unknown"])
    examples = benchmarks.get("examples", {})
    examples_str = ", ".join([f"{k}: ${v/1e9:.1f}B" for k, v in examples.items()])

    prompt = f"""You are a crypto market analyst. Evaluate if this FDV is realistic for a NEW token launch.

Token: {token}
Category: {category}
TGE FDV: ${fdv/1e9:.2f}B (${fdv:,.0f})

Established {category} benchmarks: {examples_str}

Additional context: {additional_context if additional_context else "None provided"}

Questions to answer:
1. Is this FDV realistic for a NEW {category} project at TGE?
2. How does it compare to established projects in this category?
3. Could this be a data entry error (fat finger)?

Respond in JSON format:
{{
    "is_realistic": true/false,
    "confidence": 0.0-1.0,
    "comparison": "Brief comparison to benchmarks",
    "concerns": ["List any concerns"],
    "recommendation": "PROCEED/VERIFY/SKIP"
}}"""

    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 300
            },
            timeout=10
        )

        if response.status_code != 200:
            logger.error(f"LLM API error: {response.status_code}")
            return {"used_llm": False, "error": f"API error {response.status_code}"}

        data = response.json()
        content = data["choices"][0]["message"]["content"]

        # Parse JSON from response
        try:
            # Handle markdown code blocks
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            result = json.loads(content.strip())
            result["used_llm"] = True

            # Store in cache (7 days TTL for benchmark validations)
            try:
                from src.utils.llm_cache import get_llm_cache
                cache = get_llm_cache()
                cache_key_params = f"{token}_{fdv}_{category}"
                cache.set("openai_fdv", cache_key_params, result, ttl_hours=168, model="gpt-4o-mini")
            except ImportError:
                pass  # Cache not available

            return result

        except json.JSONDecodeError:
            logger.warning(f"Failed to parse LLM response: {content[:200]}")
            return {"used_llm": True, "raw_response": content, "parse_error": True}

    except Exception as e:
        logger.error(f"LLM validation error: {e}")
        return {"used_llm": False, "error": str(e)}


def validate_fdv_realism(
    token: str,
    fdv: float,
    category: str = "Unknown",
    use_llm: bool = True,
    additional_context: str = ""
) -> FDVValidationResult:
    """
    Validate if an FDV is realistic for a new token launch.

    Session 251 Gemini Review: Prevents "fat finger" data entry errors
    from polluting conviction scores.

    Args:
        token: Token symbol
        fdv: Fully Diluted Valuation in USD
        category: Token category (L1, L2, DeFi, Gaming, AI, Meme, Infrastructure)
        use_llm: Whether to use LLM for validation (costs ~$0.001)
        additional_context: Any additional context about the token

    Returns:
        FDVValidationResult with outlier status, severity, and recommendations
    """
    # Normalize category
    category = category.upper() if category else "Unknown"
    if category not in CATEGORY_BENCHMARKS:
        # Try to match partial
        for cat in CATEGORY_BENCHMARKS:
            if cat.upper() in category or category in cat.upper():
                category = cat
                break
        else:
            category = "Unknown"

    benchmarks = CATEGORY_BENCHMARKS.get(category, CATEGORY_BENCHMARKS["Unknown"])

    # Step 1: Quick heuristic check
    is_outlier, severity, reason = _heuristic_check(fdv, category)

    # Build benchmark comparison
    benchmark_comparison = {
        "category": category,
        "typical_range": benchmarks["typical_fdv_range"],
        "max_reasonable_new_launch": benchmarks.get("max_reasonable_new_launch"),
        "examples": benchmarks.get("examples", {})
    }

    # If heuristic says CRITICAL or HIGH outlier, don't need LLM
    if is_outlier and severity in ["CRITICAL", "HIGH"]:
        return FDVValidationResult(
            token=token,
            fdv=fdv,
            category=category,
            is_outlier=True,
            severity=severity,
            reason=reason,
            benchmark_comparison=benchmark_comparison,
            recommended_action="MANUAL_REVIEW" if severity == "CRITICAL" else "VERIFY",
            confidence=0.9
        )

    # Step 2: LLM validation for uncertain cases
    llm_result = {}
    if use_llm and (is_outlier or fdv > benchmarks.get("max_reasonable_new_launch", 1e10) * 0.5):
        llm_result = _llm_validate(token, fdv, category, additional_context)

        if llm_result.get("used_llm") and not llm_result.get("parse_error"):
            # LLM provided a valid response
            is_realistic = llm_result.get("is_realistic", True)
            llm_confidence = llm_result.get("confidence", 0.5)

            if not is_realistic:
                is_outlier = True
                severity = "HIGH" if llm_confidence > 0.7 else "MEDIUM"
                reason = llm_result.get("comparison", "LLM flagged as unrealistic")
                concerns = llm_result.get("concerns", [])
                if concerns:
                    reason += f" Concerns: {', '.join(concerns)}"

            benchmark_comparison["llm_analysis"] = {
                "is_realistic": is_realistic,
                "confidence": llm_confidence,
                "comparison": llm_result.get("comparison"),
                "concerns": llm_result.get("concerns", []),
                "recommendation": llm_result.get("recommendation")
            }

    # Determine recommended action
    if is_outlier:
        if severity == "CRITICAL":
            action = "SKIP"
        elif severity == "HIGH":
            action = "MANUAL_REVIEW"
        elif severity == "MEDIUM":
            action = "VERIFY"
        else:
            action = "PROCEED"
    else:
        action = "PROCEED"

    return FDVValidationResult(
        token=token,
        fdv=fdv,
        category=category,
        is_outlier=is_outlier,
        severity=severity,
        reason=reason,
        benchmark_comparison=benchmark_comparison,
        recommended_action=action,
        confidence=llm_result.get("confidence", 0.7) if llm_result.get("used_llm") else 0.8
    )


def main():
    """CLI for testing FDV validation."""
    import argparse

    parser = argparse.ArgumentParser(description="Validate FDV realism")
    parser.add_argument("token", help="Token symbol")
    parser.add_argument("fdv", type=float, help="FDV in USD")
    parser.add_argument("--category", default="Unknown", help="Token category")
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM validation")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    result = validate_fdv_realism(
        token=args.token,
        fdv=args.fdv,
        category=args.category,
        use_llm=not args.no_llm
    )

    if args.json:
        print(json.dumps(asdict(result), indent=2, default=str))
    else:
        print(f"\n{'='*60}")
        print(f"FDV VALIDATION: {result.token}")
        print(f"{'='*60}")
        print(f"FDV: ${result.fdv/1e9:.2f}B")
        print(f"Category: {result.category}")
        print(f"Is Outlier: {result.is_outlier}")
        print(f"Severity: {result.severity}")
        print(f"Reason: {result.reason}")
        print(f"Recommended Action: {result.recommended_action}")
        print(f"Confidence: {result.confidence:.0%}")
        print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
