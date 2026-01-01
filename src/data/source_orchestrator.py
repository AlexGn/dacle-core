#!/usr/bin/env python3
"""
Source Orchestrator - Session 86A (Intelligent Multi-Source Data Extraction)

Orchestrates TGE data extraction with coverage tracking and intelligent fallbacks.
Session 267: Migrated from scripts/helpers/source_orchestrator.py to src/data/source_orchestrator.py

Architecture:
    Phase 0: check_live_status() - CoinGecko live check (already implemented)
    Phase 1: parallel_fetch_primary() - CryptoRank, CoinGecko, ICODrops simultaneously
    Phase 2: analyze_coverage() - Which critical/important fields are missing?
    Phase 3: targeted_perplexity() - Only query for specific missing fields
    Phase 4: derive_calculated_fields() - FDV from price*supply, float from unlocks
    Phase 5: final_validation() - 90%+ critical or flag for manual review

Key Improvements:
- Cost: Perplexity only called if critical coverage < 90%
- Speed: ThreadPoolExecutor runs primary sources in parallel
- Data Quality: Calculated fields ensure we derive what we can
- Robustness: Try/except prevents single source failures from crashing pipeline

Usage:
    from src.data.source_orchestrator import SourceOrchestrator

    orchestrator = SourceOrchestrator()
    data = orchestrator.orchestrate("SEEK", "Talisman")
    print(f"Critical Coverage: {data['_coverage']['critical_pct']}%")

Created: 2025-12-05 (Session 86A - SEEK Learnings)
"""

import logging
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

# Setup logging
logger = logging.getLogger(__name__)

# Project root
PROJECT_ROOT = Path(__file__).parent.parent


class SourceOrchestrator:
    """
    Intelligent multi-source data extraction with coverage tracking.

    Orchestrates Phase 0 through Phase 5 of the TGE pipeline to achieve
    90%+ critical field coverage.
    """

    # CRITICAL fields that MUST be filled for conviction scoring
    CRITICAL_FIELDS = [
        'fdv', 'total_supply', 'tge_date', 'listing_price_low',
        'listing_price_high', 'float_percentage', 'status', 'token_symbol'
    ]

    # IMPORTANT fields that improve analysis quality
    IMPORTANT_FIELDS = [
        'token_allocation', 'vesting_schedule', 'exchanges',
        'funding_rounds', 'vc_investors', 'blockchain', 'description',
        'circulating_supply_at_tge', 'market_cap', 'contract_address'
    ]

    # Field name mappings (normalize different source field names)
    FIELD_ALIASES = {
        'fully_diluted_valuation': 'fdv',
        'float_percent': 'float_percentage',
        'tge_unlock_pct': 'float_percentage',
        'circulating_supply_tge': 'circulating_supply_at_tge',
        'listing_exchanges': 'exchanges',
        'investors': 'vc_investors',
        'project_description': 'description'
    }

    def __init__(self):
        """Initialize the orchestrator."""
        self.logger = logging.getLogger(__name__)

    def orchestrate(
        self,
        token_symbol: str,
        token_name: Optional[str] = None,
        save_sources: bool = True
    ) -> Dict[str, Any]:
        """
        Main orchestration flow returning consolidated data + coverage metrics.

        Args:
            token_symbol: Token symbol (e.g., "SEEK")
            token_name: Token name for better matching (e.g., "Talisman")
            save_sources: Whether to save source files to disk

        Returns:
            Dict with consolidated data and '_coverage' metrics
        """
        self.logger.info(f"Orchestrating data extraction for {token_name or token_symbol} ({token_symbol})")

        # Phase 0: Live status check (already implemented in primary_source_fetcher)
        self.logger.info("Phase 0: Checking live status on CoinGecko...")
        live_data = self._phase0_live_check(token_symbol, token_name)

        # Phase 1: Parallel primary source fetching
        self.logger.info("Phase 1: Parallel fetching from primary sources...")
        primary_data = self._phase1_parallel_fetch(token_symbol, token_name, save_sources)

        # Merge Phase 0 data (live data takes precedence for price/FDV)
        if live_data:
            primary_data = self._merge_data(primary_data, live_data, prefer_second=True)

        # Phase 2: Coverage analysis
        self.logger.info("Phase 2: Analyzing coverage...")
        coverage = self._phase2_analyze_coverage(primary_data)
        self.logger.info(f"Initial Critical Coverage: {coverage['critical_pct']:.1f}%")

        # Phase 3: Targeted Perplexity (only if gaps exist)
        if coverage['critical_pct'] < 90 or len(coverage['missing_important']) > 3:
            missing_fields = coverage['missing_critical'] + coverage['missing_important']
            if missing_fields:
                self.logger.info(f"Phase 3: Targeting Perplexity for {len(missing_fields)} fields...")
                perplexity_data = self._phase3_targeted_perplexity(
                    token_symbol, token_name, missing_fields, save_sources
                )
                if perplexity_data:
                    primary_data = self._merge_data(primary_data, perplexity_data)
        else:
            self.logger.info("Phase 3: Skipped (coverage sufficient)")

        # Phase 4: Derive calculated fields
        self.logger.info("Phase 4: Deriving calculated fields...")
        primary_data = self._phase4_derive_calculated_fields(primary_data)

        # Phase 5: Final validation
        self.logger.info("Phase 5: Final validation...")
        final_coverage = self._phase2_analyze_coverage(primary_data)

        # Add metadata
        primary_data['_coverage'] = final_coverage
        primary_data['_meta'] = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'orchestration_version': '1.0',
            'token_symbol': token_symbol.upper(),
            'token_name': token_name
        }

        # Log final coverage
        self.logger.info(f"Final Critical Coverage: {final_coverage['critical_pct']:.1f}%")
        self.logger.info(f"Final Important Coverage: {final_coverage['important_pct']:.1f}%")

        if final_coverage['missing_critical']:
            self.logger.warning(f"Missing critical: {final_coverage['missing_critical']}")

        return primary_data

    def _phase0_live_check(
        self,
        token_symbol: str,
        token_name: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        """Phase 0: Check if token is already live on CoinGecko."""
        try:
            from src.data.primary_source_fetcher import check_token_live_status

            result = check_token_live_status(token_symbol, token_name)

            if result.get('is_live'):
                self.logger.info(f"  Token is LIVE! Price: ${result.get('current_price', 'N/A')}")
                # Normalize field names
                return {
                    'status': 'Live',
                    'current_price': result.get('current_price'),
                    'fdv': result.get('fdv'),
                    'market_cap': result.get('market_cap'),
                    'total_supply': result.get('total_supply'),
                    'circulating_supply': result.get('circulating_supply'),
                    'contract_address': result.get('contract_address'),
                    'blockchain': result.get('blockchain'),
                    'coingecko_id': result.get('coingecko_id'),
                    '_source': 'coingecko_live_check'
                }
            else:
                self.logger.info("  Token not trading yet (Pre-TGE)")
                return None

        except ImportError:
            self.logger.warning("  Live status check not available")
            return None
        except Exception as e:
            self.logger.error(f"  Live status check error: {e}")
            return None

    def _phase1_parallel_fetch(
        self,
        token_symbol: str,
        token_name: Optional[str],
        save_sources: bool
    ) -> Dict[str, Any]:
        """Phase 1: Fetch from CryptoRank, CoinGecko, ICODrops in parallel."""
        results = {}

        try:
            from src.data.primary_source_fetcher import (
                fetch_cryptorank,
                fetch_coingecko,
                fetch_icodrops,
                save_to_sources
            )

            with ThreadPoolExecutor(max_workers=3) as executor:
                # Map futures to source names
                futures = {
                    executor.submit(fetch_cryptorank, token_symbol, token_name): 'cryptorank',
                    executor.submit(fetch_coingecko, token_symbol, token_name): 'coingecko',
                    executor.submit(fetch_icodrops, token_symbol, token_name): 'icodrops'
                }

                for future in as_completed(futures):
                    source = futures[future]
                    try:
                        data = future.result()
                        if data:
                            results[source] = data
                            self.logger.info(f"  Fetched data from {source}")

                            # Save source file
                            if save_sources:
                                today = datetime.now().strftime("%Y-%m-%d")
                                save_to_sources(
                                    token_symbol,
                                    f"{len(results)-1}_{source}_{today}.json",
                                    data
                                )
                        else:
                            self.logger.debug(f"  {source} returned empty data")
                    except Exception as e:
                        self.logger.warning(f"  Error fetching from {source}: {e}")
                        results[source] = {'_error': str(e)}

        except ImportError as e:
            self.logger.warning(f"  Primary source fetcher not available: {e}")

        return self._consolidate_results(results)

    def _consolidate_results(self, results: Dict[str, Dict]) -> Dict[str, Any]:
        """
        Merge data from different sources.

        Priority: CryptoRank > CoinGecko > ICODrops (highest priority first)
        """
        consolidated = {}

        # Priority order for merging (last merged overwrites previous)
        source_priority = ['icodrops', 'coingecko', 'cryptorank']

        for source in source_priority:
            if source in results and isinstance(results[source], dict):
                source_data = results[source]
                for k, v in source_data.items():
                    # Skip metadata and error fields
                    if k.startswith('_'):
                        continue
                    # Only update if value is not None/empty
                    if v not in [None, "", [], {}]:
                        # Normalize field names
                        normalized_key = self.FIELD_ALIASES.get(k, k)
                        consolidated[normalized_key] = v
                        # Track source for each field
                        consolidated[f"_{normalized_key}_source"] = source

        return consolidated

    def _phase2_analyze_coverage(self, data: Dict) -> Dict[str, Any]:
        """Phase 2: Calculate field coverage percentages."""
        # Check critical fields (with alias normalization)
        critical_filled = 0
        missing_critical = []
        for field in self.CRITICAL_FIELDS:
            value = data.get(field)
            # Also check aliases
            if value in [None, "", [], {}]:
                for alias, normalized in self.FIELD_ALIASES.items():
                    if normalized == field and data.get(alias) not in [None, "", [], {}]:
                        value = data.get(alias)
                        break

            if value not in [None, "", [], {}]:
                critical_filled += 1
            else:
                missing_critical.append(field)

        # Check important fields
        important_filled = 0
        missing_important = []
        for field in self.IMPORTANT_FIELDS:
            value = data.get(field)
            # Also check aliases
            if value in [None, "", [], {}]:
                for alias, normalized in self.FIELD_ALIASES.items():
                    if normalized == field and data.get(alias) not in [None, "", [], {}]:
                        value = data.get(alias)
                        break

            if value not in [None, "", [], {}]:
                important_filled += 1
            else:
                missing_important.append(field)

        return {
            'critical_pct': (critical_filled / len(self.CRITICAL_FIELDS)) * 100,
            'important_pct': (important_filled / len(self.IMPORTANT_FIELDS)) * 100,
            'critical_filled': critical_filled,
            'critical_total': len(self.CRITICAL_FIELDS),
            'important_filled': important_filled,
            'important_total': len(self.IMPORTANT_FIELDS),
            'missing_critical': missing_critical,
            'missing_important': missing_important
        }

    def _phase3_targeted_perplexity(
        self,
        token_symbol: str,
        token_name: Optional[str],
        missing_fields: List[str],
        save_sources: bool
    ) -> Optional[Dict[str, Any]]:
        """Phase 3: Query Perplexity only for specific missing fields."""
        try:
            from src.integrations.perplexity.perplexity_api_client import PerplexityAPIClient
            from src.data.primary_source_fetcher import save_to_sources

            # Load prompt template
            prompt_template_path = PROJECT_ROOT / "prompts" / "perplexity_single_token_analysis_v3.6.3.md"
            if prompt_template_path.exists():
                with open(prompt_template_path, 'r') as f:
                    prompt_template = f.read()
            else:
                # Fallback minimal template
                prompt_template = """
                Research the token and return JSON with these fields:
                - fdv: Fully Diluted Valuation in USD
                - total_supply: Total token supply
                - tge_date: TGE date in ISO format
                - listing_price_low: Expected listing price (low)
                - listing_price_high: Expected listing price (high)
                - float_percentage: Percentage of tokens unlocked at TGE
                - token_allocation: Dict of allocation categories to percentages
                - vesting_schedule: Description of vesting terms
                - exchanges: List of expected listing exchanges
                - vc_investors: List of known investors
                """

            # Build targeted prompt
            field_list = "\n".join(f"- {field}" for field in missing_fields)
            targeted_prompt = f"""
Analyze the crypto token {token_name or token_symbol} ({token_symbol}).

I specifically need the following MISSING data points:
{field_list}

IMPORTANT: Focus ONLY on finding these specific fields.
Return the data in a clean JSON format with keys matching the requested fields.
If a value is unknown, use null. Do not add conversational text.
"""

            # Call Perplexity API
            client = PerplexityAPIClient()
            result = client.research_tge(
                token_symbol=token_symbol,
                token_name=token_name or token_symbol,
                prompt_template=targeted_prompt,
                missing_fields=missing_fields
            )
            client.close()

            if result:
                # Add source tracking (copy keys to avoid modifying dict during iteration)
                for key in list(result.keys()):
                    if not key.startswith('_'):
                        result[f'_{key}_source'] = 'perplexity_targeted'

                # Save to sources
                if save_sources:
                    today = datetime.now().strftime("%Y-%m-%d")
                    save_to_sources(
                        token_symbol,
                        f"perplexity_targeted_{today}.json",
                        result
                    )

                self.logger.info(f"  Perplexity returned {len([k for k in result if not k.startswith('_')])} fields")
                return result

        except ImportError as e:
            self.logger.warning(f"  Perplexity client not available: {e}")
        except Exception as e:
            self.logger.error(f"  Perplexity targeted query failed: {e}")

        return None

    def _phase4_derive_calculated_fields(self, data: Dict) -> Dict[str, Any]:
        """Phase 4: Derive fields that can be calculated from other fields."""
        # 1. FDV = Listing Price * Total Supply
        if data.get('fdv') in [None, 0]:
            price = data.get('listing_price_high') or data.get('listing_price_low')
            supply = data.get('total_supply')
            if price and supply:
                try:
                    data['fdv'] = float(price) * float(supply)
                    data['_fdv_source'] = 'CALCULATED'
                    self.logger.info(f"  Derived FDV: ${data['fdv']:,.0f}")
                except (ValueError, TypeError):
                    pass

        # 2. Float Percentage from TGE Unlock
        if data.get('float_percentage') in [None, ""]:
            tge_unlock = data.get('tge_unlock_pct')
            if tge_unlock:
                data['float_percentage'] = tge_unlock
                data['_float_percentage_source'] = 'DERIVED_FROM_UNLOCK'
                self.logger.info(f"  Derived float_percentage: {data['float_percentage']}%")

        # 3. Float Percentage from Circulating Supply / Total Supply
        if data.get('float_percentage') in [None, ""]:
            circ = data.get('circulating_supply_at_tge') or data.get('circulating_supply')
            total = data.get('total_supply')
            if circ and total and float(total) > 0:
                try:
                    data['float_percentage'] = round((float(circ) / float(total)) * 100, 2)
                    data['_float_percentage_source'] = 'CALCULATED_FROM_SUPPLY'
                    self.logger.info(f"  Derived float_percentage: {data['float_percentage']}%")
                except (ValueError, TypeError, ZeroDivisionError):
                    pass

        # 4. Circulating Supply at TGE from Float and Total Supply
        if data.get('circulating_supply_at_tge') in [None, 0]:
            float_pct = data.get('float_percentage')
            total = data.get('total_supply')
            if float_pct and total:
                try:
                    data['circulating_supply_at_tge'] = float(total) * (float(float_pct) / 100)
                    data['_circulating_supply_at_tge_source'] = 'CALCULATED'
                    self.logger.info(f"  Derived circulating_supply_at_tge: {data['circulating_supply_at_tge']:,.0f}")
                except (ValueError, TypeError):
                    pass

        # 5. Market Cap at TGE
        if data.get('market_cap') in [None, 0]:
            price = data.get('listing_price_high') or data.get('listing_price_low') or data.get('current_price')
            circ = data.get('circulating_supply_at_tge')
            if price and circ:
                try:
                    data['market_cap'] = float(price) * float(circ)
                    data['_market_cap_source'] = 'CALCULATED'
                    self.logger.info(f"  Derived market_cap: ${data['market_cap']:,.0f}")
                except (ValueError, TypeError):
                    pass

        return data

    def _merge_data(
        self,
        primary: Dict,
        secondary: Dict,
        prefer_second: bool = False
    ) -> Dict[str, Any]:
        """
        Merge secondary data into primary.

        Args:
            primary: Primary data dict
            secondary: Secondary data to merge
            prefer_second: If True, secondary values overwrite primary

        Returns:
            Merged dict
        """
        for k, v in secondary.items():
            if v in [None, "", [], {}]:
                continue

            # Normalize key
            normalized_key = self.FIELD_ALIASES.get(k, k)

            if prefer_second:
                # Secondary overwrites primary
                primary[normalized_key] = v
            else:
                # Only fill if primary is empty
                if primary.get(normalized_key) in [None, "", [], {}]:
                    primary[normalized_key] = v

        return primary


def orchestrate_token(
    token_symbol: str,
    token_name: Optional[str] = None,
    save_sources: bool = True
) -> Dict[str, Any]:
    """
    Convenience function to orchestrate data extraction for a token.

    Args:
        token_symbol: Token symbol (e.g., "SEEK")
        token_name: Token name for better matching (e.g., "Talisman")
        save_sources: Whether to save source files to disk

    Returns:
        Dict with consolidated data and '_coverage' metrics
    """
    orchestrator = SourceOrchestrator()
    return orchestrator.orchestrate(token_symbol, token_name, save_sources)


# CLI for testing
if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Source Orchestrator - Intelligent Multi-Source Data Extraction"
    )
    parser.add_argument("token", help="Token symbol (e.g., SEEK)")
    parser.add_argument("--name", "-n", help="Token name (e.g., Talisman)")
    parser.add_argument("--no-save", action="store_true", help="Don't save source files")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    result = orchestrate_token(
        token_symbol=args.token,
        token_name=args.name,
        save_sources=not args.no_save
    )

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print("\n" + "=" * 60)
        print(f"ORCHESTRATION RESULT: {args.token}")
        print("=" * 60)

        coverage = result.get('_coverage', {})
        print(f"\nCritical Coverage: {coverage.get('critical_pct', 0):.1f}%")
        print(f"Important Coverage: {coverage.get('important_pct', 0):.1f}%")

        if coverage.get('missing_critical'):
            print(f"\nMissing Critical: {coverage['missing_critical']}")

        print("\nData Fields:")
        for key, value in sorted(result.items()):
            if not key.startswith('_'):
                if isinstance(value, (list, dict)):
                    print(f"  {key}: {type(value).__name__}({len(value)})")
                else:
                    print(f"  {key}: {value}")
