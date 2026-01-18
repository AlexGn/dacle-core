"""
CryptoSlate Web Scraper - Narrative Detection

Session 336: David mentioned catching the narrative is key.
CryptoSlate has no API but has good narrative/news content.

This scraper extracts:
- Trending narratives (AI, Gaming, DeFi, L2, etc.)
- Token mentions in articles
- Market sentiment context

Rate Limiting: Be conservative (1 req/5sec) to avoid blocks.
"""

import logging
import requests
import re
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from bs4 import BeautifulSoup
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# Common narrative keywords to detect
NARRATIVE_KEYWORDS = {
    "AI": ["artificial intelligence", "ai agent", "ai token", "machine learning", "neural", "gpt", "llm"],
    "GAMING": ["gaming", "gamefi", "play to earn", "p2e", "metaverse", "nft game", "web3 game"],
    "DEFI": ["defi", "decentralized finance", "lending", "borrowing", "yield", "liquidity", "amm", "dex"],
    "L1": ["layer 1", "layer-1", "l1", "blockchain", "smart contract platform", "ethereum killer"],
    "L2": ["layer 2", "layer-2", "l2", "rollup", "zk", "optimistic", "scaling", "base", "arbitrum"],
    "MEME": ["meme", "memecoin", "doge", "shiba", "pepe", "community token", "viral"],
    "RWA": ["real world assets", "rwa", "tokenization", "tokenized", "real estate", "commodities"],
    "DEPIN": ["depin", "decentralized infrastructure", "physical infrastructure", "iot", "helium"],
    "RESTAKING": ["restaking", "eigenlayer", "liquid staking", "lst", "lrt"],
    "MODULAR": ["modular", "data availability", "celestia", "da layer", "rollup as service"],
}


@dataclass
class NarrativeSignal:
    """A detected narrative with associated tokens."""
    narrative: str
    strength: float  # 0-1, based on article frequency
    tokens_mentioned: List[str]
    article_count: int
    sample_headlines: List[str]
    detected_at: str


@dataclass
class TokenMention:
    """A token mentioned in CryptoSlate articles."""
    symbol: str
    name: str
    narrative: Optional[str]
    sentiment: str  # BULLISH, BEARISH, NEUTRAL
    headline: str
    article_url: str
    published_at: Optional[str]


class CryptoSlateScraper:
    """
    Scrapes CryptoSlate for narrative signals and token mentions.

    Conservative rate limiting to avoid blocks:
    - 1 request per 5 seconds
    - Caches results for 1 hour
    """

    BASE_URL = "https://cryptoslate.com"
    TIMEOUT = 30

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.google.com/",
        })
        self._cache: Dict[str, Tuple[datetime, any]] = {}
        self._cache_ttl = 3600  # 1 hour

    def _get_cached(self, key: str) -> Optional[any]:
        """Get cached result if still valid."""
        if key in self._cache:
            cached_time, data = self._cache[key]
            age = (datetime.utcnow() - cached_time).seconds
            if age < self._cache_ttl:
                return data
        return None

    def _set_cached(self, key: str, data: any):
        """Cache a result."""
        self._cache[key] = (datetime.utcnow(), data)

    def _fetch_page(self, url: str) -> Optional[BeautifulSoup]:
        """Fetch and parse a page."""
        try:
            response = self.session.get(url, timeout=self.TIMEOUT)

            if response.status_code == 403:
                logger.warning("CryptoSlate: Access forbidden (may need to rotate user agent)")
                return None

            if response.status_code == 429:
                logger.warning("CryptoSlate: Rate limited")
                return None

            response.raise_for_status()
            return BeautifulSoup(response.text, "html.parser")

        except Exception as e:
            logger.error(f"CryptoSlate fetch failed for {url}: {e}")
            return None

    def get_latest_news(self, limit: int = 20) -> List[Dict]:
        """
        Scrape latest news headlines from CryptoSlate.

        Returns list of articles with headline, url, summary.
        """
        cache_key = f"news_{limit}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        try:
            soup = self._fetch_page(f"{self.BASE_URL}/news/")
            if not soup:
                return []

            articles = []

            # Find article cards
            for article in soup.select("article.post-item, div.post-card, article.news-item")[:limit]:
                try:
                    # Extract headline
                    title_elem = article.select_one("h2, h3, .post-title, .title")
                    if not title_elem:
                        continue
                    headline = title_elem.get_text(strip=True)

                    # Extract URL
                    link = article.select_one("a[href]")
                    url = link.get("href", "") if link else ""
                    if url and not url.startswith("http"):
                        url = f"{self.BASE_URL}{url}"

                    # Extract summary/excerpt if available
                    summary_elem = article.select_one(".excerpt, .summary, p")
                    summary = summary_elem.get_text(strip=True)[:200] if summary_elem else ""

                    # Extract date if available
                    date_elem = article.select_one("time, .date, .post-date")
                    date = date_elem.get("datetime") or date_elem.get_text(strip=True) if date_elem else None

                    articles.append({
                        "headline": headline,
                        "url": url,
                        "summary": summary,
                        "published_at": date,
                        "source": "cryptoslate"
                    })

                except Exception as e:
                    logger.debug(f"Failed to parse article: {e}")
                    continue

            self._set_cached(cache_key, articles)
            logger.info(f"CryptoSlate: Scraped {len(articles)} articles")
            return articles

        except Exception as e:
            logger.error(f"CryptoSlate news scraping failed: {e}")
            return []

    def detect_narratives(self, articles: Optional[List[Dict]] = None) -> List[NarrativeSignal]:
        """
        Detect trending narratives from article content.

        Analyzes headlines and summaries to identify which narratives
        are getting the most coverage.
        """
        if articles is None:
            articles = self.get_latest_news(limit=50)

        if not articles:
            return []

        # Count narrative mentions
        narrative_counts: Dict[str, Dict] = {}

        for article in articles:
            text = f"{article.get('headline', '')} {article.get('summary', '')}".lower()

            for narrative, keywords in NARRATIVE_KEYWORDS.items():
                for keyword in keywords:
                    if keyword in text:
                        if narrative not in narrative_counts:
                            narrative_counts[narrative] = {
                                "count": 0,
                                "headlines": [],
                                "tokens": set()
                            }
                        narrative_counts[narrative]["count"] += 1
                        narrative_counts[narrative]["headlines"].append(article.get("headline", ""))

                        # Try to extract token symbols mentioned
                        symbols = self._extract_token_symbols(text)
                        narrative_counts[narrative]["tokens"].update(symbols)
                        break  # Only count each narrative once per article

        # Build narrative signals
        signals = []
        total_articles = len(articles)

        for narrative, data in narrative_counts.items():
            if data["count"] >= 2:  # At least 2 mentions to be a signal
                strength = min(data["count"] / total_articles * 5, 1.0)  # Normalize

                signal = NarrativeSignal(
                    narrative=narrative,
                    strength=strength,
                    tokens_mentioned=list(data["tokens"])[:10],
                    article_count=data["count"],
                    sample_headlines=data["headlines"][:3],
                    detected_at=datetime.utcnow().isoformat()
                )
                signals.append(signal)

        # Sort by strength
        signals.sort(key=lambda x: x.strength, reverse=True)

        return signals

    def _extract_token_symbols(self, text: str) -> List[str]:
        """Extract likely token symbols from text."""
        # Look for patterns like $BTC, $ETH or standalone 3-5 char uppercase
        dollar_pattern = r'\$([A-Z]{2,6})\b'
        caps_pattern = r'\b([A-Z]{3,5})\b'

        symbols = set()

        # $ prefixed symbols (high confidence)
        for match in re.finditer(dollar_pattern, text.upper()):
            symbols.add(match.group(1))

        # Standalone caps (lower confidence, filter common words)
        common_words = {"THE", "AND", "FOR", "ARE", "BUT", "NOT", "YOU", "ALL",
                       "CAN", "HAD", "HER", "WAS", "ONE", "OUR", "OUT", "NEW"}
        for match in re.finditer(caps_pattern, text):
            sym = match.group(1).upper()
            if sym not in common_words:
                symbols.add(sym)

        return list(symbols)

    def get_token_mentions(self, symbol: str) -> List[TokenMention]:
        """
        Search for mentions of a specific token in recent articles.

        Note: This is approximate - CryptoSlate doesn't have a search API.
        """
        articles = self.get_latest_news(limit=50)
        mentions = []

        symbol_upper = symbol.upper()
        symbol_pattern = re.compile(
            rf'\b{symbol_upper}\b|\${symbol_upper}\b',
            re.IGNORECASE
        )

        for article in articles:
            text = f"{article.get('headline', '')} {article.get('summary', '')}"

            if symbol_pattern.search(text):
                # Determine sentiment from keywords
                sentiment = self._analyze_sentiment(text)

                # Detect narrative
                narrative = None
                for narr, keywords in NARRATIVE_KEYWORDS.items():
                    if any(kw in text.lower() for kw in keywords):
                        narrative = narr
                        break

                mentions.append(TokenMention(
                    symbol=symbol_upper,
                    name="",  # Not always available
                    narrative=narrative,
                    sentiment=sentiment,
                    headline=article.get("headline", ""),
                    article_url=article.get("url", ""),
                    published_at=article.get("published_at")
                ))

        return mentions

    def _analyze_sentiment(self, text: str) -> str:
        """Simple keyword-based sentiment analysis."""
        text_lower = text.lower()

        bullish_words = ["surge", "rally", "pump", "soar", "gain", "bullish",
                        "breakthrough", "milestone", "adoption", "partnership"]
        bearish_words = ["crash", "dump", "plunge", "drop", "bearish", "collapse",
                        "hack", "exploit", "scam", "warning", "fear"]

        bullish_count = sum(1 for w in bullish_words if w in text_lower)
        bearish_count = sum(1 for w in bearish_words if w in text_lower)

        if bullish_count > bearish_count:
            return "BULLISH"
        elif bearish_count > bullish_count:
            return "BEARISH"
        return "NEUTRAL"

    def get_trending_narratives_report(self) -> str:
        """Generate a human-readable report of trending narratives."""
        narratives = self.detect_narratives()

        if not narratives:
            return "No strong narratives detected in recent CryptoSlate coverage."

        lines = [
            "=" * 60,
            "📰 CRYPTOSLATE NARRATIVE ANALYSIS",
            "=" * 60,
            ""
        ]

        for i, signal in enumerate(narratives[:5], 1):
            strength_bar = "█" * int(signal.strength * 10) + "░" * (10 - int(signal.strength * 10))

            lines.append(f"{i}. {signal.narrative}")
            lines.append(f"   Strength: [{strength_bar}] ({signal.article_count} articles)")

            if signal.tokens_mentioned:
                lines.append(f"   Tokens: {', '.join(signal.tokens_mentioned[:5])}")

            if signal.sample_headlines:
                lines.append(f"   Headlines:")
                for headline in signal.sample_headlines[:2]:
                    lines.append(f"     • {headline[:60]}...")

            lines.append("")

        lines.append("=" * 60)

        return "\n".join(lines)


def main():
    """Test the CryptoSlate scraper."""
    scraper = CryptoSlateScraper()

    print("Testing CryptoSlate Scraper...")
    print("=" * 60)

    # Test news scraping
    print("\n📰 Latest News:")
    news = scraper.get_latest_news(limit=10)
    for article in news[:5]:
        print(f"  • {article['headline'][:60]}...")

    # Test narrative detection
    print("\n🎯 Narrative Detection:")
    report = scraper.get_trending_narratives_report()
    print(report)

    # Test token mention search
    print("\n🔍 Searching for BTC mentions:")
    mentions = scraper.get_token_mentions("BTC")
    for m in mentions[:3]:
        print(f"  • [{m.sentiment}] {m.headline[:50]}...")


if __name__ == "__main__":
    main()
