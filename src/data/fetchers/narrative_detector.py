"""
Multi-Source Narrative Detector

Session 336: David says "c'est juste la narrative qui faut attrapé" (just catch the narrative).

This module aggregates crypto news from multiple sources to detect trending narratives:
- CoinDesk (primary - accessible)
- The Block (secondary)
- Decrypt (tertiary)

CryptoSlate blocked (403), so using accessible alternatives.

Use Cases:
- Detect which narratives are trending (AI, Gaming, DeFi, L2, etc.)
- Identify tokens getting mentioned alongside narratives
- Gauge market sentiment (bullish/bearish)
"""

import logging
import re
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# Narrative keywords to detect in article content
NARRATIVE_KEYWORDS = {
    "AI": [
        "artificial intelligence", "ai agent", "ai token", "machine learning",
        "neural", "gpt", "llm", "chatgpt", "deep learning", "ai crypto"
    ],
    "GAMING": [
        "gaming", "gamefi", "play to earn", "p2e", "metaverse", "nft game",
        "web3 game", "blockchain gaming", "game token"
    ],
    "DEFI": [
        "defi", "decentralized finance", "lending", "borrowing", "yield",
        "liquidity", "amm", "dex", "swap", "staking", "farming"
    ],
    "L1": [
        "layer 1", "layer-1", "l1", "blockchain", "smart contract platform",
        "ethereum killer", "alt l1", "monolithic"
    ],
    "L2": [
        "layer 2", "layer-2", "l2", "rollup", "zk", "optimistic", "scaling",
        "base", "arbitrum", "optimism", "zksync", "starknet"
    ],
    "MEME": [
        "meme", "memecoin", "doge", "shiba", "pepe", "community token",
        "viral", "dog coin", "frog", "meme season"
    ],
    "RWA": [
        "real world assets", "rwa", "tokenization", "tokenized", "real estate",
        "commodities", "treasury", "bonds", "securities"
    ],
    "DEPIN": [
        "depin", "decentralized infrastructure", "physical infrastructure",
        "iot", "helium", "wireless", "compute", "storage"
    ],
    "RESTAKING": [
        "restaking", "eigenlayer", "liquid staking", "lst", "lrt",
        "liquid restaking", "avs"
    ],
    "MODULAR": [
        "modular", "data availability", "celestia", "da layer",
        "rollup as service", "raas", "modular blockchain"
    ],
    "INSTITUTIONAL": [
        "institutional", "etf", "blackrock", "fidelity", "wall street",
        "hedge fund", "asset manager", "custody"
    ],
    "REGULATION": [
        "regulation", "sec", "cftc", "compliance", "legal", "legislation",
        "bill", "law", "policy", "government"
    ],
    "AIRDROP": [
        "airdrop", "token launch", "tge", "points", "farming points",
        "retroactive", "claim", "distribution"
    ],
}

# Sentiment keywords
BULLISH_WORDS = [
    "surge", "rally", "pump", "soar", "gain", "bullish", "breakthrough",
    "milestone", "adoption", "partnership", "inflow", "buy", "accumulate",
    "breakout", "all-time high", "ath", "moon", "growth"
]

BEARISH_WORDS = [
    "crash", "dump", "plunge", "drop", "bearish", "collapse", "hack",
    "exploit", "scam", "warning", "fear", "outflow", "sell", "decline",
    "correction", "bear market", "liquidation", "risk"
]


@dataclass
class Article:
    """A news article from any source."""
    headline: str
    url: str
    summary: str
    source: str
    published_at: Optional[str] = None
    narratives: List[str] = field(default_factory=list)
    tokens_mentioned: List[str] = field(default_factory=list)
    sentiment: str = "NEUTRAL"


@dataclass
class NarrativeSignal:
    """A detected narrative trend."""
    narrative: str
    strength: float  # 0-1
    article_count: int
    tokens_mentioned: List[str]
    sample_headlines: List[str]
    sentiment: str  # BULLISH, BEARISH, NEUTRAL
    detected_at: str


class NarrativeDetector:
    """
    Detects trending crypto narratives from multiple news sources.

    Uses WebFetch-compatible approach for reliable scraping.
    """

    def __init__(self):
        self._cache: Dict[str, Tuple[datetime, any]] = {}
        self._cache_ttl = 1800  # 30 minutes

    def _get_cached(self, key: str) -> Optional[any]:
        """Get cached result if still valid."""
        if key in self._cache:
            cached_time, data = self._cache[key]
            age = (datetime.utcnow() - cached_time).total_seconds()
            if age < self._cache_ttl:
                logger.debug(f"Cache hit for {key}")
                return data
        return None

    def _set_cached(self, key: str, data: any):
        """Cache a result."""
        self._cache[key] = (datetime.utcnow(), data)

    def _extract_token_symbols(self, text: str) -> List[str]:
        """Extract likely token symbols from text."""
        symbols = set()

        # $ prefixed symbols (high confidence)
        dollar_pattern = r'\$([A-Z]{2,6})\b'
        for match in re.finditer(dollar_pattern, text.upper()):
            symbols.add(match.group(1))

        # Known major tokens
        known_tokens = {
            "BITCOIN": "BTC", "ETHEREUM": "ETH", "SOLANA": "SOL",
            "CARDANO": "ADA", "AVALANCHE": "AVAX", "POLYGON": "MATIC",
            "CHAINLINK": "LINK", "UNISWAP": "UNI", "AAVE": "AAVE",
            "ARBITRUM": "ARB", "OPTIMISM": "OP", "BASE": "BASE",
            "CELESTIA": "TIA", "EIGENLAYER": "EIGEN", "STARKNET": "STRK"
        }

        text_upper = text.upper()
        for name, symbol in known_tokens.items():
            if name in text_upper:
                symbols.add(symbol)

        return list(symbols)

    def _detect_narratives(self, text: str) -> List[str]:
        """Detect which narratives are present in text."""
        text_lower = text.lower()
        detected = []

        for narrative, keywords in NARRATIVE_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text_lower:
                    detected.append(narrative)
                    break  # Only count each narrative once

        return detected

    def _analyze_sentiment(self, text: str) -> str:
        """Analyze text sentiment."""
        text_lower = text.lower()

        bullish_count = sum(1 for w in BULLISH_WORDS if w in text_lower)
        bearish_count = sum(1 for w in BEARISH_WORDS if w in text_lower)

        if bullish_count > bearish_count + 1:
            return "BULLISH"
        elif bearish_count > bullish_count + 1:
            return "BEARISH"
        return "NEUTRAL"

    def process_articles(self, articles: List[Dict]) -> List[Article]:
        """Process raw article dicts into enriched Article objects."""
        processed = []

        for art in articles:
            text = f"{art.get('headline', '')} {art.get('summary', '')}"

            article = Article(
                headline=art.get("headline", ""),
                url=art.get("url", ""),
                summary=art.get("summary", ""),
                source=art.get("source", "unknown"),
                published_at=art.get("published_at"),
                narratives=self._detect_narratives(text),
                tokens_mentioned=self._extract_token_symbols(text),
                sentiment=self._analyze_sentiment(text)
            )
            processed.append(article)

        return processed

    def detect_trending_narratives(self, articles: List[Article]) -> List[NarrativeSignal]:
        """
        Analyze articles to find trending narratives.

        Returns narratives sorted by strength (article frequency + sentiment).
        """
        if not articles:
            return []

        # Aggregate by narrative
        narrative_data: Dict[str, Dict] = {}

        for article in articles:
            for narrative in article.narratives:
                if narrative not in narrative_data:
                    narrative_data[narrative] = {
                        "count": 0,
                        "headlines": [],
                        "tokens": set(),
                        "bullish": 0,
                        "bearish": 0,
                        "neutral": 0
                    }

                data = narrative_data[narrative]
                data["count"] += 1
                data["headlines"].append(article.headline)
                data["tokens"].update(article.tokens_mentioned)

                if article.sentiment == "BULLISH":
                    data["bullish"] += 1
                elif article.sentiment == "BEARISH":
                    data["bearish"] += 1
                else:
                    data["neutral"] += 1

        # Build signals
        signals = []
        total_articles = len(articles)

        for narrative, data in narrative_data.items():
            if data["count"] >= 2:  # At least 2 mentions
                # Strength based on coverage
                coverage = data["count"] / total_articles
                strength = min(coverage * 5, 1.0)  # Scale to 0-1

                # Aggregate sentiment
                if data["bullish"] > data["bearish"] + data["neutral"]:
                    sentiment = "BULLISH"
                elif data["bearish"] > data["bullish"] + data["neutral"]:
                    sentiment = "BEARISH"
                else:
                    sentiment = "NEUTRAL"

                signal = NarrativeSignal(
                    narrative=narrative,
                    strength=strength,
                    article_count=data["count"],
                    tokens_mentioned=list(data["tokens"])[:10],
                    sample_headlines=data["headlines"][:3],
                    sentiment=sentiment,
                    detected_at=datetime.utcnow().isoformat()
                )
                signals.append(signal)

        # Sort by strength
        signals.sort(key=lambda x: x.strength, reverse=True)

        return signals

    def get_narrative_report(self, signals: List[NarrativeSignal]) -> str:
        """Generate a human-readable narrative report."""
        if not signals:
            return "No significant narratives detected in recent coverage."

        sentiment_emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪"}

        lines = [
            "=" * 60,
            "📰 CRYPTO NARRATIVE ANALYSIS",
            "=" * 60,
            ""
        ]

        for i, signal in enumerate(signals[:5], 1):
            strength_bar = "█" * int(signal.strength * 10) + "░" * (10 - int(signal.strength * 10))
            emoji = sentiment_emoji.get(signal.sentiment, "⚪")

            lines.append(f"{i}. {signal.narrative} {emoji}")
            lines.append(f"   Strength: [{strength_bar}] ({signal.article_count} articles)")
            lines.append(f"   Sentiment: {signal.sentiment}")

            if signal.tokens_mentioned:
                lines.append(f"   Tokens: {', '.join(signal.tokens_mentioned[:5])}")

            if signal.sample_headlines:
                lines.append("   Headlines:")
                for headline in signal.sample_headlines[:2]:
                    # Truncate long headlines
                    h = headline[:57] + "..." if len(headline) > 60 else headline
                    lines.append(f"     • {h}")

            lines.append("")

        lines.append("=" * 60)
        lines.append(f"Detected at: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

        return "\n".join(lines)

    def find_narrative_for_token(
        self,
        symbol: str,
        articles: List[Article]
    ) -> Optional[str]:
        """
        Find what narrative a token is associated with.

        Useful for understanding why a token might be pumping.
        """
        symbol_upper = symbol.upper()

        for article in articles:
            if symbol_upper in article.tokens_mentioned:
                if article.narratives:
                    return article.narratives[0]

        return None

    def get_tokens_by_narrative(
        self,
        narrative: str,
        articles: List[Article]
    ) -> List[str]:
        """
        Get all tokens mentioned alongside a specific narrative.

        Useful for finding tokens to watch when a narrative is trending.
        """
        tokens = set()

        for article in articles:
            if narrative in article.narratives:
                tokens.update(article.tokens_mentioned)

        return list(tokens)


def parse_coindesk_articles(raw_text: str) -> List[Dict]:
    """
    Parse CoinDesk articles from WebFetch output.

    This is designed to work with the structured output from WebFetch.
    """
    articles = []

    # Split by article markers (## for headlines)
    sections = raw_text.split("## ")

    for section in sections[1:]:  # Skip first empty section
        lines = section.strip().split("\n")
        if not lines:
            continue

        # First line is headline (may have ** markers)
        headline = lines[0].replace("**", "").strip()

        # Extract URL, summary, etc.
        url = ""
        summary = ""

        for line in lines[1:]:
            if line.startswith("- **URL:**"):
                url = line.replace("- **URL:**", "").strip()
            elif line.startswith("- **Summary:**"):
                summary = line.replace("- **Summary:**", "").strip()

        if headline:
            articles.append({
                "headline": headline,
                "url": url,
                "summary": summary,
                "source": "coindesk"
            })

    return articles


def main():
    """Test the narrative detector with sample data."""
    # Sample articles (simulating WebFetch output)
    sample_articles = [
        {
            "headline": "Bitcoin ETF inflows surge past $1.2 billion as institutions buy",
            "url": "https://coindesk.com/markets/btc-etf",
            "summary": "Institutional adoption accelerates with massive ETF inflows",
            "source": "coindesk"
        },
        {
            "headline": "Solana gaming ecosystem explodes with new P2E launches",
            "url": "https://coindesk.com/gaming",
            "summary": "Gaming tokens rally as metaverse interest returns",
            "source": "coindesk"
        },
        {
            "headline": "AI crypto tokens surge amid ChatGPT integration rumors",
            "url": "https://coindesk.com/ai",
            "summary": "Artificial intelligence narrative drives altcoin rally",
            "source": "coindesk"
        },
        {
            "headline": "Layer 2 TVL hits new record as Base and Arbitrum compete",
            "url": "https://coindesk.com/l2",
            "summary": "L2 rollups see massive growth in DeFi activity",
            "source": "coindesk"
        },
        {
            "headline": "Eigenlayer restaking TVL doubles in January",
            "url": "https://coindesk.com/restaking",
            "summary": "Liquid restaking tokens gain momentum",
            "source": "coindesk"
        },
    ]

    detector = NarrativeDetector()

    # Process articles
    processed = detector.process_articles(sample_articles)

    print("Processed Articles:")
    for art in processed:
        print(f"  - {art.headline[:50]}...")
        print(f"    Narratives: {art.narratives}")
        print(f"    Tokens: {art.tokens_mentioned}")
        print(f"    Sentiment: {art.sentiment}")
        print()

    # Detect narratives
    signals = detector.detect_trending_narratives(processed)

    # Print report
    report = detector.get_narrative_report(signals)
    print(report)


if __name__ == "__main__":
    main()
