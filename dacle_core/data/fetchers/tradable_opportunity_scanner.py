"""
Tradable Opportunity Scanner - David's Criteria Implementation

Session 336: Based on David's feedback:
1. "Les vieux il sont bien aussi" - Old coins can pump too (like wine)
2. "Plus le OI il est eleve plus ça le trade" - High OI = more tradable
3. "Faut s'assuré qu'il sois listé dans les exchanges, MEXC surtout" - MUST be on MEXC
4. "c'est juste la narrative qui faut attrapé" - Catch the narrative

This scanner combines:
- Funding rate data (from Binance)
- Open Interest (tradability indicator)
- MEXC listing verification (David's #1 requirement)
- Gainers/Trending detection
- Narrative context from multiple sources
"""

import logging
import requests
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
from dataclasses import dataclass
from enum import Enum

from dacle_core.data.fetchers.narrative_detector import (
    NarrativeDetector, Article, NarrativeSignal
)

logger = logging.getLogger(__name__)


class OpportunityType(Enum):
    """Types of trading opportunities."""
    SHORT = "SHORT"      # Overextended, likely to dump
    LONG = "LONG"        # Oversold, likely to bounce
    MONITOR = "MONITOR"  # Interesting but wait for setup
    AVOID = "AVOID"      # Skip (low OI, not on MEXC, etc.)


@dataclass
class TradableOpportunity:
    """A token that meets David's tradability criteria."""
    symbol: str
    name: str
    opportunity_type: OpportunityType

    # Tradability metrics
    is_on_mexc: bool
    open_interest_usd: Optional[float]
    funding_rate: Optional[float]
    volume_24h: Optional[float]

    # Price action
    price_usd: Optional[float]
    percent_change_24h: Optional[float]
    percent_change_7d: Optional[float]
    market_cap: Optional[float]

    # Context
    narrative: Optional[str]  # Why it's moving
    discovery_source: str
    discovery_reason: str

    # Scoring
    tradability_score: float  # 0-100 based on OI, volume, MEXC
    conviction_estimate: Optional[float]

    # Timestamps
    discovered_at: str
    token_age_days: Optional[int]


class TradableOpportunityScanner:
    """
    Scans multiple sources and filters for actually tradable opportunities.

    David's Requirements:
    1. Must be on MEXC (primary exchange filter)
    2. Higher OI = more tradable
    3. Both old and new coins are valid (narrative matters)
    4. Funding rate extremes = opportunity signals
    """

    MEXC_API_URL = "https://api.mexc.com"
    BINANCE_FUTURES_URL = "https://fapi.binance.com"
    TIMEOUT = 30

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "DACLE-Scanner/1.0"
        })
        self._mexc_symbols_cache: Optional[set] = None
        self._cache_time: Optional[datetime] = None
        self._narrative_detector = NarrativeDetector()
        self._cached_articles: Optional[List[Article]] = None
        self._articles_cache_time: Optional[datetime] = None

    def _get_mexc_listed_symbols(self, refresh: bool = False) -> set:
        """
        Get all symbols listed on MEXC Futures (David's #1 requirement).

        Caches for 1 hour to avoid rate limits.
        """
        # Check cache (1 hour TTL)
        if not refresh and self._mexc_symbols_cache and self._cache_time:
            cache_age = (datetime.utcnow() - self._cache_time).seconds
            if cache_age < 3600:  # 1 hour
                return self._mexc_symbols_cache

        try:
            # MEXC Futures contracts
            url = f"{self.MEXC_API_URL}/api/v3/exchangeInfo"
            response = self.session.get(url, timeout=self.TIMEOUT)
            response.raise_for_status()
            data = response.json()

            symbols = set()
            for s in data.get("symbols", []):
                base = s.get("baseAsset", "").upper()
                # MEXC uses status="1" for trading, not "TRADING"
                status = s.get("status")
                is_trading = status == "1" or status == "TRADING"
                is_spot = s.get("isSpotTradingAllowed", False)
                if base and is_trading and is_spot:
                    symbols.add(base)

            self._mexc_symbols_cache = symbols
            self._cache_time = datetime.utcnow()

            logger.info(f"MEXC: Cached {len(symbols)} tradable symbols")
            return symbols

        except Exception as e:
            logger.error(f"Failed to fetch MEXC symbols: {e}")
            return self._mexc_symbols_cache or set()

    def is_on_mexc(self, symbol: str) -> bool:
        """Check if token is listed on MEXC."""
        symbols = self._get_mexc_listed_symbols()
        return symbol.upper() in symbols

    def get_open_interest(self, symbol: str) -> Optional[Dict]:
        """
        Get Open Interest for a symbol (David: "Plus le OI il est eleve plus ça le trade").

        Uses Binance Futures API (most liquid, good OI data).
        """
        try:
            url = f"{self.BINANCE_FUTURES_URL}/fapi/v1/openInterest"
            params = {"symbol": f"{symbol.upper()}USDT"}
            response = self.session.get(url, params=params, timeout=self.TIMEOUT)

            if response.status_code == 400:
                # Symbol not on Binance Futures
                return None

            response.raise_for_status()
            data = response.json()

            return {
                "symbol": symbol.upper(),
                "open_interest": float(data.get("openInterest", 0)),
                "open_interest_usd": None,  # Need price to calculate
                "timestamp": data.get("time")
            }

        except Exception as e:
            logger.debug(f"No OI data for {symbol}: {e}")
            return None

    def get_funding_rate(self, symbol: str) -> Optional[float]:
        """Get current funding rate from Binance."""
        try:
            url = f"{self.BINANCE_FUTURES_URL}/fapi/v1/premiumIndex"
            params = {"symbol": f"{symbol.upper()}USDT"}
            response = self.session.get(url, params=params, timeout=self.TIMEOUT)

            if response.status_code != 200:
                return None

            data = response.json()
            return float(data.get("lastFundingRate", 0)) * 100  # Convert to %

        except Exception:
            return None

    def load_narrative_context(self, articles: List[Dict]) -> Tuple[List[NarrativeSignal], List[Article]]:
        """
        Load narrative context from news articles.

        David: "c'est juste la narrative qui faut attrapé" (just catch the narrative)

        Args:
            articles: List of article dicts with headline, url, summary, source

        Returns:
            Tuple of (narrative_signals, processed_articles)
        """
        # Check cache (30 min TTL)
        if self._cached_articles and self._articles_cache_time:
            cache_age = (datetime.utcnow() - self._articles_cache_time).seconds
            if cache_age < 1800:  # 30 minutes
                signals = self._narrative_detector.detect_trending_narratives(self._cached_articles)
                return signals, self._cached_articles

        # Process new articles
        processed = self._narrative_detector.process_articles(articles)
        signals = self._narrative_detector.detect_trending_narratives(processed)

        # Cache results
        self._cached_articles = processed
        self._articles_cache_time = datetime.utcnow()

        logger.info(f"Narrative: Processed {len(processed)} articles, detected {len(signals)} narratives")
        return signals, processed

    def get_token_narrative(self, symbol: str, articles: Optional[List[Article]] = None) -> Optional[str]:
        """
        Find what narrative a token is associated with.

        David: Narrative explains WHY a token is moving.
        """
        if articles is None:
            articles = self._cached_articles or []

        return self._narrative_detector.find_narrative_for_token(symbol, articles)

    def get_trending_narratives(self) -> List[NarrativeSignal]:
        """Get current trending narratives from cached articles."""
        if not self._cached_articles:
            return []
        return self._narrative_detector.detect_trending_narratives(self._cached_articles)

    def get_tokens_for_narrative(self, narrative: str) -> List[str]:
        """Find tokens associated with a trending narrative."""
        if not self._cached_articles:
            return []
        return self._narrative_detector.get_tokens_by_narrative(narrative, self._cached_articles)

    def calculate_tradability_score(
        self,
        is_on_mexc: bool,
        open_interest_usd: Optional[float],
        volume_24h: Optional[float],
        funding_rate: Optional[float]
    ) -> Tuple[float, str]:
        """
        Calculate tradability score (0-100) based on David's criteria.

        Returns: (score, reasoning)
        """
        score = 0.0
        reasons = []

        # MEXC listing is CRITICAL (40 points)
        if is_on_mexc:
            score += 40
            reasons.append("Listed on MEXC ✓")
        else:
            reasons.append("NOT on MEXC ✗")
            # Can still trade on other exchanges, but less ideal

        # Open Interest (30 points) - David: "Plus le OI il est eleve"
        if open_interest_usd:
            if open_interest_usd >= 10_000_000:  # $10M+
                score += 30
                reasons.append(f"High OI: ${open_interest_usd/1e6:.1f}M")
            elif open_interest_usd >= 1_000_000:  # $1M+
                score += 20
                reasons.append(f"Good OI: ${open_interest_usd/1e6:.1f}M")
            elif open_interest_usd >= 100_000:  # $100K+
                score += 10
                reasons.append(f"Low OI: ${open_interest_usd/1e3:.0f}K")
            else:
                reasons.append(f"Very low OI: ${open_interest_usd/1e3:.0f}K")
        else:
            reasons.append("No OI data")

        # Volume (20 points)
        if volume_24h:
            if volume_24h >= 50_000_000:  # $50M+
                score += 20
                reasons.append(f"High volume: ${volume_24h/1e6:.1f}M")
            elif volume_24h >= 10_000_000:  # $10M+
                score += 15
                reasons.append(f"Good volume: ${volume_24h/1e6:.1f}M")
            elif volume_24h >= 1_000_000:  # $1M+
                score += 10
                reasons.append(f"Low volume: ${volume_24h/1e6:.1f}M")

        # Funding rate extremes add opportunity points (10 points)
        if funding_rate is not None:
            if abs(funding_rate) >= 0.1:  # Extreme (±0.1%+)
                score += 10
                direction = "positive (crowded longs)" if funding_rate > 0 else "negative (crowded shorts)"
                reasons.append(f"Extreme funding: {funding_rate:.4f}% {direction}")
            elif abs(funding_rate) >= 0.05:
                score += 5
                reasons.append(f"Elevated funding: {funding_rate:.4f}%")

        reasoning = " | ".join(reasons)
        return min(score, 100), reasoning

    def classify_opportunity(
        self,
        percent_change_24h: Optional[float],
        percent_change_7d: Optional[float],
        funding_rate: Optional[float],
        is_on_mexc: bool,
        tradability_score: float
    ) -> OpportunityType:
        """
        Classify the opportunity type based on price action and metrics.
        """
        # Must be minimally tradable
        if tradability_score < 30:
            return OpportunityType.AVOID

        # Not on MEXC = lower priority
        if not is_on_mexc and tradability_score < 50:
            return OpportunityType.AVOID

        pct_24h = percent_change_24h or 0
        pct_7d = percent_change_7d or 0
        funding = funding_rate or 0

        # SHORT opportunities
        if pct_24h > 30 or pct_7d > 50:
            # Overextended pump
            return OpportunityType.SHORT
        if funding > 0.1:
            # Crowded longs
            return OpportunityType.SHORT

        # LONG opportunities
        if pct_24h < -20 or pct_7d < -40:
            # Oversold
            return OpportunityType.LONG
        if funding < -0.1:
            # Crowded shorts = squeeze potential
            return OpportunityType.LONG

        # Otherwise monitor
        return OpportunityType.MONITOR

    def scan_tradable_opportunities(
        self,
        candidates: List[Dict],
        min_tradability: float = 40.0,
        articles: Optional[List[Dict]] = None
    ) -> List[TradableOpportunity]:
        """
        Filter candidates through David's tradability criteria.

        Args:
            candidates: List of tokens from various sources (CMC, CoinPaprika, etc.)
            min_tradability: Minimum tradability score to include
            articles: Optional news articles for narrative context

        Returns:
            List of TradableOpportunity objects sorted by tradability
        """
        opportunities = []
        mexc_symbols = self._get_mexc_listed_symbols()

        # Load narrative context if articles provided
        if articles:
            self.load_narrative_context(articles)

        for candidate in candidates:
            symbol = candidate.get("token_symbol") or candidate.get("symbol", "")
            if not symbol:
                continue

            symbol = symbol.upper()

            # Check MEXC listing (David's #1 requirement)
            is_on_mexc = symbol in mexc_symbols

            # Get OI and funding data
            oi_data = self.get_open_interest(symbol)
            open_interest_usd = None
            if oi_data:
                oi = oi_data.get("open_interest", 0)
                price = candidate.get("price_usd") or 0
                open_interest_usd = oi * price

            funding_rate = self.get_funding_rate(symbol)

            # Calculate tradability
            volume_24h = candidate.get("volume_24h") or candidate.get("volume_24h_usd")
            tradability_score, reasoning = self.calculate_tradability_score(
                is_on_mexc=is_on_mexc,
                open_interest_usd=open_interest_usd,
                volume_24h=volume_24h,
                funding_rate=funding_rate
            )

            # Filter by minimum tradability
            if tradability_score < min_tradability:
                continue

            # Classify opportunity type
            pct_24h = candidate.get("percent_change_24h")
            pct_7d = candidate.get("percent_change_7d")
            opportunity_type = self.classify_opportunity(
                percent_change_24h=pct_24h,
                percent_change_7d=pct_7d,
                funding_rate=funding_rate,
                is_on_mexc=is_on_mexc,
                tradability_score=tradability_score
            )

            # Skip AVOID opportunities
            if opportunity_type == OpportunityType.AVOID:
                continue

            # Calculate token age if we have date_added
            token_age_days = None
            date_added = candidate.get("date_added")
            if date_added:
                try:
                    added_date = datetime.fromisoformat(date_added.replace("Z", "+00:00"))
                    token_age_days = (datetime.utcnow() - added_date.replace(tzinfo=None)).days
                except Exception:
                    pass

            # Get narrative context (David: "c'est juste la narrative qui faut attrapé")
            narrative = candidate.get("discovery_reason") or candidate.get("narrative")
            if not narrative:
                # Try to detect narrative from news articles
                detected_narrative = self.get_token_narrative(symbol)
                if detected_narrative:
                    narrative = f"Narrative: {detected_narrative}"

            # Build opportunity object
            opportunity = TradableOpportunity(
                symbol=symbol,
                name=candidate.get("token_name") or candidate.get("name", ""),
                opportunity_type=opportunity_type,
                is_on_mexc=is_on_mexc,
                open_interest_usd=open_interest_usd,
                funding_rate=funding_rate,
                volume_24h=volume_24h,
                price_usd=candidate.get("price_usd"),
                percent_change_24h=pct_24h,
                percent_change_7d=pct_7d,
                market_cap=candidate.get("market_cap") or candidate.get("market_cap_usd"),
                narrative=narrative,
                discovery_source=candidate.get("data_source") or candidate.get("source", "unknown"),
                discovery_reason=reasoning,
                tradability_score=tradability_score,
                conviction_estimate=candidate.get("conviction_estimate"),
                discovered_at=datetime.utcnow().isoformat(),
                token_age_days=token_age_days
            )

            # Session 338: Calculate conviction_estimate now so it's available for filtering
            if opportunity.conviction_estimate is None:
                opportunity.conviction_estimate = self._estimate_conviction(opportunity)

            opportunities.append(opportunity)

        # Sort by tradability score (highest first)
        opportunities.sort(key=lambda x: x.tradability_score, reverse=True)

        return opportunities

    def generate_report(self, opportunities: List[TradableOpportunity]) -> str:
        """Generate a readable report of tradable opportunities."""
        if not opportunities:
            return "No tradable opportunities found matching David's criteria."

        lines = [
            "=" * 70,
            "🎯 TRADABLE OPPORTUNITIES (David's Criteria)",
            f"   Found: {len(opportunities)} | MEXC-listed: {sum(1 for o in opportunities if o.is_on_mexc)}",
            "=" * 70,
            ""
        ]

        # Add trending narratives section if available
        narratives = self.get_trending_narratives()
        if narratives:
            lines.append("📰 TRENDING NARRATIVES:")
            lines.append("-" * 50)
            for sig in narratives[:5]:
                sentiment_emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪"}.get(sig.sentiment, "⚪")
                strength_bar = "█" * int(sig.strength * 5) + "░" * (5 - int(sig.strength * 5))
                tokens_str = ", ".join(sig.tokens_mentioned[:3]) if sig.tokens_mentioned else "No tokens"
                lines.append(f"  {sig.narrative:12s} {sentiment_emoji} [{strength_bar}] ({sig.article_count} articles) | {tokens_str}")
            lines.append("")

        # Group by opportunity type
        by_type = {}
        for opp in opportunities:
            t = opp.opportunity_type.value
            if t not in by_type:
                by_type[t] = []
            by_type[t].append(opp)

        type_emojis = {
            "SHORT": "🔴",
            "LONG": "🟢",
            "MONITOR": "🟡"
        }

        for opp_type in ["SHORT", "LONG", "MONITOR"]:
            if opp_type not in by_type:
                continue

            emoji = type_emojis.get(opp_type, "⚪")
            lines.append(f"\n{emoji} {opp_type} Opportunities:")
            lines.append("-" * 50)

            for opp in by_type[opp_type][:10]:  # Top 10 per type
                mexc = "✅ MEXC" if opp.is_on_mexc else "❌ No MEXC"
                pct = f"+{opp.percent_change_24h:.1f}%" if (opp.percent_change_24h or 0) >= 0 else f"{opp.percent_change_24h:.1f}%"

                oi_str = ""
                if opp.open_interest_usd:
                    oi_str = f" | OI: ${opp.open_interest_usd/1e6:.1f}M"

                funding_str = ""
                if opp.funding_rate:
                    funding_str = f" | FR: {opp.funding_rate:.4f}%"

                age_str = ""
                if opp.token_age_days is not None:
                    if opp.token_age_days < 30:
                        age_str = f" | 🆕 {opp.token_age_days}d old"
                    elif opp.token_age_days > 365:
                        age_str = f" | 🍷 {opp.token_age_days//365}y old"

                lines.append(
                    f"  {opp.symbol:8s} {pct:>8s} | Score: {opp.tradability_score:.0f}/100 | {mexc}{oi_str}{funding_str}{age_str}"
                )
                if opp.narrative:
                    lines.append(f"           📝 {opp.narrative[:60]}")

        lines.append("")
        lines.append("=" * 70)

        return "\n".join(lines)

    def integrate_to_pending_qc(
        self,
        opportunities: List[TradableOpportunity],
        dry_run: bool = True
    ) -> Dict[str, Any]:
        """
        Integrate scanner results to Pending QC workflow.

        Session 336: Connect tradable opportunities to the dashboard's
        "Pending QC" section by creating token directories with is_new_discovery=true.

        Args:
            opportunities: List of TradableOpportunity objects from scan
            dry_run: If True, only report what would be created (no file writes)

        Returns:
            Dict with created/skipped counts and details
        """
        from pathlib import Path
        import json

        data_dir = Path(__file__).parent.parent.parent.parent / "data" / "tokens"
        watchlist_path = Path(__file__).parent.parent.parent.parent / "data" / "watchlist.json"
        now = datetime.utcnow().isoformat()

        results = {
            "created": [],
            "skipped": [],
            "already_exists": [],
            "dry_run": dry_run
        }

        # Session 337: Load watchlist to check if token already exists in normal list
        existing_watchlist_tokens = set()
        try:
            if watchlist_path.exists():
                with open(watchlist_path, "r") as f:
                    watchlist_data = json.load(f)
                    existing_watchlist_tokens = set(watchlist_data.get("tokens", {}).keys())
                    logger.info(f"Loaded {len(existing_watchlist_tokens)} tokens from watchlist for duplicate check")
        except Exception as e:
            logger.warning(f"Could not load watchlist for duplicate check: {e}")

        for opp in opportunities:
            symbol = opp.symbol.upper()
            token_dir = data_dir / symbol
            consolidated_path = token_dir / "consolidated.json"

            # Session 337: Skip if already in watchlist (normal list)
            if symbol in existing_watchlist_tokens:
                results["already_exists"].append({
                    "symbol": symbol,
                    "reason": "Already in watchlist (normal list)"
                })
                continue

            # Skip if consolidated.json already exists
            if consolidated_path.exists():
                results["already_exists"].append({
                    "symbol": symbol,
                    "reason": "Token directory already exists"
                })
                continue

            # Skip AVOID opportunities
            if opp.opportunity_type == OpportunityType.AVOID:
                results["skipped"].append({
                    "symbol": symbol,
                    "reason": "OpportunityType.AVOID"
                })
                continue

            # Create minimal consolidated.json for Pending QC
            consolidated_data = {
                "token_symbol": symbol,
                "name": opp.name,
                "created_at": now,
                "source": "tradable_scanner",

                # Discovery metadata - triggers is_new_discovery=true in API
                "discovered_at": opp.discovered_at,

                # Tradability data from scanner
                "tradability_score": opp.tradability_score,
                "is_on_mexc": opp.is_on_mexc,
                "open_interest_usd": opp.open_interest_usd,
                "funding_rate": opp.funding_rate,

                # Price data
                "price_usd": opp.price_usd,
                "market_cap": opp.market_cap,
                "volume_24h": opp.volume_24h,
                "percent_change_24h": opp.percent_change_24h,
                "percent_change_7d": opp.percent_change_7d,

                # Opportunity classification
                "scanner_opportunity_type": opp.opportunity_type.value,
                "narrative": opp.narrative,
                "discovery_source": opp.discovery_source,
                "discovery_reason": opp.discovery_reason,

                # Pre-TA conviction estimate (for QC traffic light)
                "conviction_estimate": opp.conviction_estimate or self._estimate_conviction(opp),

                # Token lifecycle
                "token_age_days": opp.token_age_days,

                # Session 336: QC status for dashboard routing
                "qc_status": "pending",

                # Placeholder for analysis
                "last_analyzed": None,
                "analysis_version": None
            }

            if dry_run:
                results["created"].append({
                    "symbol": symbol,
                    "opportunity_type": opp.opportunity_type.value,
                    "tradability_score": opp.tradability_score,
                    "conviction_estimate": consolidated_data["conviction_estimate"],
                    "narrative": opp.narrative,
                    "dry_run": True
                })
            else:
                # Create directory structure
                token_dir.mkdir(parents=True, exist_ok=True)
                (token_dir / "sources").mkdir(exist_ok=True)
                (token_dir / "sources" / "raw").mkdir(exist_ok=True)

                # Write consolidated.json
                with open(consolidated_path, "w") as f:
                    json.dump(consolidated_data, f, indent=2, default=str)

                # Session 337: Also add to watchlist with discovery_metadata for Pending QC section
                try:
                    if watchlist_path.exists():
                        with open(watchlist_path, "r") as f:
                            watchlist_data = json.load(f)
                    else:
                        watchlist_data = {"tokens": {}, "last_updated": now}

                    watchlist_data["tokens"][symbol] = {
                        "state": "DISCOVERED",
                        "added_at": now,
                        "state_history": [{
                            "from": "NEW",
                            "to": "DISCOVERED",
                            "at": now,
                            "reason": f"Tradable scanner: {opp.discovery_reason}"
                        }],
                        "updated_at": now,
                        "discovery_metadata": {
                            "qc_status": "pending",
                            "discovered_at": opp.discovered_at,
                            "sources": [opp.discovery_source],
                            "scanner_opportunity_type": opp.opportunity_type.value,
                            "tradability_score": opp.tradability_score,
                            "conviction_estimate": consolidated_data["conviction_estimate"],
                            "is_on_mexc": opp.is_on_mexc
                        }
                    }
                    watchlist_data["last_updated"] = now

                    with open(watchlist_path, "w") as f:
                        json.dump(watchlist_data, f, indent=2, default=str)

                except Exception as e:
                    logger.warning(f"Could not add {symbol} to watchlist: {e}")

                results["created"].append({
                    "symbol": symbol,
                    "opportunity_type": opp.opportunity_type.value,
                    "tradability_score": opp.tradability_score,
                    "conviction_estimate": consolidated_data["conviction_estimate"],
                    "path": str(consolidated_path)
                })
                logger.info(f"Created pending QC token: {symbol} ({opp.opportunity_type.value})")

        return results

    def _estimate_conviction(self, opp: TradableOpportunity) -> float:
        """
        Estimate pre-TA conviction score for Pending QC traffic light.

        Based on tradability metrics only (no chart analysis yet).
        This is the "conviction_estimate" that appears in QC section.

        Returns:
            Score 0-10
        """
        score = 5.0  # Base score

        # MEXC listing is critical (+1.5)
        if opp.is_on_mexc:
            score += 1.5

        # High OI is tradable (+1.0)
        if opp.open_interest_usd:
            if opp.open_interest_usd > 10_000_000:
                score += 1.0
            elif opp.open_interest_usd > 1_000_000:
                score += 0.5

        # Extreme funding rate = opportunity (+0.5)
        if opp.funding_rate:
            if abs(opp.funding_rate) > 0.1:
                score += 0.5

        # Strong directional move (+0.5)
        if opp.percent_change_24h:
            if abs(opp.percent_change_24h) > 20:
                score += 0.5

        # Narrative context (+0.5)
        if opp.narrative:
            score += 0.5

        # Tradability score contribution
        if opp.tradability_score >= 70:
            score += 0.5
        elif opp.tradability_score >= 50:
            score += 0.25

        return min(10.0, max(0.0, score))


def main():
    """Test the tradable opportunity scanner."""
    from dacle_core.data.fetchers.coinmarketcap_fetcher import CoinMarketCapFetcher
    from dacle_core.data.fetchers.coinpaprika_fetcher import CoinPaprikaFetcher

    scanner = TradableOpportunityScanner()

    print("Fetching candidates from multiple sources...")

    # Gather candidates
    candidates = []

    # CMC trending
    try:
        cmc = CoinMarketCapFetcher()
        trending = cmc.get_trending(limit=20)
        for t in trending:
            t["data_source"] = "cmc_trending"
            t["token_symbol"] = t.get("symbol")
            t["token_name"] = t.get("name")
        candidates.extend(trending)
        print(f"  CMC Trending: {len(trending)} tokens")
    except Exception as e:
        print(f"  CMC failed: {e}")

    # CMC top gainers
    try:
        gainers = cmc.get_top_gainers(limit=20, min_market_cap=5_000_000)
        for g in gainers:
            g["data_source"] = "cmc_gainers"
            g["token_symbol"] = g.get("symbol")
            g["token_name"] = g.get("name")
            g["discovery_reason"] = f"Pumped +{g.get('percent_change_24h', 0):.1f}% in 24h"
        candidates.extend(gainers)
        print(f"  CMC Gainers: {len(gainers)} tokens")
    except Exception as e:
        print(f"  CMC gainers failed: {e}")

    # CoinPaprika gainers
    try:
        paprika = CoinPaprikaFetcher()
        paprika_gainers = paprika.get_top_gainers(limit=20, min_market_cap=5_000_000)
        for p in paprika_gainers:
            p["data_source"] = "coinpaprika_gainers"
            p["token_symbol"] = p.get("symbol")
            p["token_name"] = p.get("name")
        candidates.extend(paprika_gainers)
        print(f"  CoinPaprika Gainers: {len(paprika_gainers)} tokens")
    except Exception as e:
        print(f"  CoinPaprika failed: {e}")

    print(f"\nTotal candidates: {len(candidates)}")
    print("\nScanning for tradable opportunities (MEXC + OI + Funding)...")

    opportunities = scanner.scan_tradable_opportunities(candidates, min_tradability=30)

    report = scanner.generate_report(opportunities)
    print(report)

    # Session 336: Show integration to Pending QC
    if opportunities:
        print("\n" + "=" * 70)
        print("📋 PENDING QC INTEGRATION (DRY RUN)")
        print("=" * 70)

        qc_results = scanner.integrate_to_pending_qc(opportunities, dry_run=True)

        print(f"\n✅ Would create: {len(qc_results['created'])} new tokens")
        for item in qc_results['created'][:5]:
            print(f"   {item['symbol']:8s} | {item['opportunity_type']:7s} | Conv: {item['conviction_estimate']:.1f}/10")

        print(f"\n⏭️  Already exists: {len(qc_results['already_exists'])} tokens")
        for item in qc_results['already_exists'][:5]:
            print(f"   {item['symbol']:8s} | {item['reason']}")

        print(f"\n❌ Skipped: {len(qc_results['skipped'])} tokens")

        print("\n💡 To create tokens, run with dry_run=False:")
        print("   scanner.integrate_to_pending_qc(opportunities, dry_run=False)")


if __name__ == "__main__":
    main()
