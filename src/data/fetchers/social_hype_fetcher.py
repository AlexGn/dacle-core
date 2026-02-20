#!/usr/bin/env python3
"""
Social Hype Intelligence Fetcher (FREE alternatives to Kaito.ai)

DEPRECATED: Use src.data.fetchers.token_data module instead.
Session 256: Marked for migration to src/data/fetchers/token_data.py

Fetches social hype data from FREE sources:
1. CryptoRank Social Score API (cryptorank.io)
2. Twitter/X Search (via Perplexity or manual count)
3. CoinGecko Community Data API (coingecko.com)

Used by conviction scoring v3.0 to calculate Social Hype component (3% weight).

Author: DACLE System
Version: 1.0 (Session 39)
"""

import warnings
warnings.warn(
    "scripts.helpers.social_hype_fetcher is deprecated. "
    "Use src.data.fetchers.token_data module instead.",
    DeprecationWarning,
    stacklevel=2
)

import logging
import os
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROVIDER_HEALTH_METRICS_PATH = PROJECT_ROOT / "data" / "metrics" / "provider_health_metrics.json"
PROVIDER_ERROR_WINDOW_SECONDS = 24 * 60 * 60
PROVIDER_ERROR_ALERT_THRESHOLD = 0.20
PROVIDER_ERROR_ALERT_MIN_SAMPLES = 10
PROVIDER_ERROR_ALERT_COOLDOWN_SECONDS = 6 * 60 * 60
PROVIDER_HEALTH_HISTORY_LIMIT = 5000
PROVIDER_HEALTH_DAILY_RETENTION_DAYS = 14
SOCIAL_HYPE_LAST_GOOD_CACHE_PATH = PROJECT_ROOT / "data" / "cache" / "social_hype_last_good_cache.json"
SOCIAL_HYPE_LAST_GOOD_TTL_SECONDS = 72 * 60 * 60

# Symbol/name variations where provider canonical IDs differ or search relevance is weak.
# Focused aliases include SUPRA path that was intermittently failing on primary symbol-only lookups.
TOKEN_PROVIDER_ALIASES: Dict[str, Dict[str, list[str]]] = {
    "SUPRA": {
        "cryptorank_symbols": ["SUPRA"],
        "cryptorank_names": ["supra", "supra oracles", "supra labs"],
        "coingecko_queries": ["SUPRA", "Supra", "Supra Labs", "Supra Oracles"],
        "coingecko_names": ["supra", "supra labs", "supra oracles"],
        "coingecko_symbols": ["supra"],
    }
}


def _utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now_dt().isoformat().replace("+00:00", "Z")


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _default_provider_metrics() -> Dict[str, Any]:
    return {
        "updated_at": _utc_now_iso(),
        "history": [],
        "daily": {},
        "last_alert_at": {},
        "providers_24h": {},
    }


def _default_social_hype_cache() -> Dict[str, Any]:
    return {
        "updated_at": _utc_now_iso(),
        "tokens": {},
    }


def _load_social_hype_cache(path: Optional[Path] = None) -> Dict[str, Any]:
    cache_path = path or SOCIAL_HYPE_LAST_GOOD_CACHE_PATH
    if not cache_path.exists():
        return _default_social_hype_cache()
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            data = _default_social_hype_cache()
            data.update(payload)
            if not isinstance(data.get("tokens"), dict):
                data["tokens"] = {}
            return data
    except Exception:
        pass
    return _default_social_hype_cache()


def _save_social_hype_cache(cache: Dict[str, Any], path: Optional[Path] = None) -> None:
    cache_path = path or SOCIAL_HYPE_LAST_GOOD_CACHE_PATH
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    tmp.replace(cache_path)


def _cache_set_twitter_mentions(token_symbol: str, mentions: int, as_of: Optional[str] = None, path: Optional[Path] = None) -> None:
    cache = _load_social_hype_cache(path)
    tokens = cache.get("tokens", {})
    if not isinstance(tokens, dict):
        tokens = {}
    tokens[token_symbol.upper()] = {
        "twitter_mentions_7d": int(max(0, mentions)),
        "as_of": as_of or _utc_now_iso(),
    }
    cache["tokens"] = tokens
    cache["updated_at"] = _utc_now_iso()
    _save_social_hype_cache(cache, path)


def _cache_get_twitter_mentions(
    token_symbol: str,
    max_age_seconds: int = SOCIAL_HYPE_LAST_GOOD_TTL_SECONDS,
    path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    cache = _load_social_hype_cache(path)
    tokens = cache.get("tokens", {})
    if not isinstance(tokens, dict):
        return None
    record = tokens.get(token_symbol.upper())
    if not isinstance(record, dict):
        return None
    mentions = record.get("twitter_mentions_7d")
    as_of = record.get("as_of")
    if not isinstance(mentions, (int, float)):
        return None
    as_of_dt = _parse_iso(as_of) if isinstance(as_of, str) else None
    if as_of_dt is None:
        return None
    age_seconds = (_utc_now_dt() - as_of_dt).total_seconds()
    if age_seconds > max(0, int(max_age_seconds)):
        return None
    return {
        "twitter_mentions_7d": int(max(0, mentions)),
        "as_of": as_of_dt.isoformat().replace("+00:00", "Z"),
    }


def _load_provider_metrics(path: Path = PROVIDER_HEALTH_METRICS_PATH) -> Dict[str, Any]:
    if not path.exists():
        return _default_provider_metrics()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            data = _default_provider_metrics()
            data.update(payload)
            if not isinstance(data.get("history"), list):
                data["history"] = []
            if not isinstance(data.get("daily"), dict):
                data["daily"] = {}
            if not isinstance(data.get("last_alert_at"), dict):
                data["last_alert_at"] = {}
            if not isinstance(data.get("providers_24h"), dict):
                data["providers_24h"] = {}
            return data
    except Exception:
        pass
    return _default_provider_metrics()


def _save_provider_metrics(metrics: Dict[str, Any], path: Path = PROVIDER_HEALTH_METRICS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    tmp.replace(path)


def _prune_daily_metrics(metrics: Dict[str, Any], now_dt: datetime) -> None:
    daily = metrics.get("daily")
    if not isinstance(daily, dict):
        metrics["daily"] = {}
        return

    min_day = (now_dt - timedelta(days=PROVIDER_HEALTH_DAILY_RETENTION_DAYS)).date()
    kept: Dict[str, Any] = {}
    for day_key, day_data in daily.items():
        try:
            day = datetime.strptime(day_key, "%Y-%m-%d").date()
            if day >= min_day:
                kept[day_key] = day_data
        except Exception:
            continue
    metrics["daily"] = kept


def _compute_provider_24h_stats(history: list[Dict[str, Any]], provider: str, now_dt: datetime) -> Dict[str, Any]:
    cutoff = now_dt.timestamp() - PROVIDER_ERROR_WINDOW_SECONDS
    events = []
    for event in history:
        if not isinstance(event, dict):
            continue
        if event.get("provider") != provider:
            continue
        dt = _parse_iso(event.get("timestamp"))
        if dt and dt.timestamp() >= cutoff:
            events.append(event)

    total = len(events)
    failed = sum(1 for event in events if not bool(event.get("success")))
    error_rate = (failed / total) if total > 0 else 0.0
    return {
        "total": total,
        "failed": failed,
        "error_rate": round(error_rate, 4),
    }


def record_provider_health_event(
    provider: str,
    token_symbol: str,
    source: str,
    success: bool,
    detail: Optional[str],
    path: Optional[Path] = None,
) -> Optional[str]:
    """
    Track provider success/failure and emit alert message only for persistent failure.

    Alert condition:
    - >= PROVIDER_ERROR_ALERT_MIN_SAMPLES requests in last 24h
    - failure rate > PROVIDER_ERROR_ALERT_THRESHOLD
    - alert cooldown elapsed for this provider
    """
    metrics_path = path or PROVIDER_HEALTH_METRICS_PATH
    now_dt = _utc_now_dt()
    metrics = _load_provider_metrics(metrics_path)
    history = metrics.get("history", [])
    if not isinstance(history, list):
        history = []

    history.append(
        {
            "timestamp": _utc_now_iso(),
            "provider": provider,
            "token_symbol": token_symbol.upper(),
            "source": source,
            "success": bool(success),
            "detail": detail,
        }
    )
    if len(history) > PROVIDER_HEALTH_HISTORY_LIMIT:
        history = history[-PROVIDER_HEALTH_HISTORY_LIMIT:]
    metrics["history"] = history
    metrics["updated_at"] = _utc_now_iso()

    today_key = now_dt.strftime("%Y-%m-%d")
    daily = metrics.get("daily", {})
    if not isinstance(daily, dict):
        daily = {}
    day_bucket = daily.get(today_key, {})
    if not isinstance(day_bucket, dict):
        day_bucket = {}
    provider_bucket = day_bucket.get(provider, {"total": 0, "failed": 0})
    if not isinstance(provider_bucket, dict):
        provider_bucket = {"total": 0, "failed": 0}
    provider_bucket["total"] = int(provider_bucket.get("total", 0)) + 1
    if not success:
        provider_bucket["failed"] = int(provider_bucket.get("failed", 0)) + 1
    day_bucket[provider] = provider_bucket
    daily[today_key] = day_bucket
    metrics["daily"] = daily
    _prune_daily_metrics(metrics, now_dt)

    stats = _compute_provider_24h_stats(history, provider, now_dt)
    providers_24h = metrics.get("providers_24h", {})
    if not isinstance(providers_24h, dict):
        providers_24h = {}
    providers_24h[provider] = stats
    metrics["providers_24h"] = providers_24h

    alert_message = None
    if (
        stats["total"] >= PROVIDER_ERROR_ALERT_MIN_SAMPLES
        and stats["error_rate"] > PROVIDER_ERROR_ALERT_THRESHOLD
    ):
        last_alert_map = metrics.get("last_alert_at", {})
        if not isinstance(last_alert_map, dict):
            last_alert_map = {}

        can_alert = True
        last_alert_dt = _parse_iso(last_alert_map.get(provider))
        if last_alert_dt is not None:
            elapsed = (now_dt - last_alert_dt).total_seconds()
            can_alert = elapsed >= PROVIDER_ERROR_ALERT_COOLDOWN_SECONDS

        if can_alert:
            last_alert_map[provider] = _utc_now_iso()
            metrics["last_alert_at"] = last_alert_map
            alert_message = (
                f"Provider health alert ({provider}): 24h failure rate "
                f"{stats['error_rate'] * 100:.1f}% ({stats['failed']}/{stats['total']})"
            )

    _save_provider_metrics(metrics, metrics_path)
    return alert_message


class SocialHypeFetcher:
    """Fetch social hype intelligence from FREE sources."""

    def __init__(self):
        """Initialize social hype fetcher with API clients."""
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })

        # CryptoRank API (free tier)
        self.cryptorank_api_key = os.getenv("CRYPTORANK_API_KEY", "")  # Optional

        # CoinGecko API (free tier)
        self.coingecko_api_key = os.getenv("COINGECKO_API_KEY", "")  # Optional

    def _provider_aliases(self, token_symbol: str) -> Dict[str, list[str]]:
        token_upper = token_symbol.upper()
        custom = TOKEN_PROVIDER_ALIASES.get(token_upper, {})
        return {
            "cryptorank_symbols": [token_upper] + [s.upper() for s in custom.get("cryptorank_symbols", [])],
            "cryptorank_names": [token_symbol.lower()] + list(custom.get("cryptorank_names", [])),
            "coingecko_queries": [token_symbol] + list(custom.get("coingecko_queries", [])),
            "coingecko_names": [token_symbol.lower()] + list(custom.get("coingecko_names", [])),
            "coingecko_symbols": [token_upper] + [s.upper() for s in custom.get("coingecko_symbols", [])],
        }

    @staticmethod
    def _dedupe_preserve(items: list[str]) -> list[str]:
        seen = set()
        ordered: list[str] = []
        for item in items:
            key = (item or "").strip()
            if not key:
                continue
            lowered = key.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            ordered.append(key)
        return ordered

    @staticmethod
    def _build_data_unavailable_note(provider: str, detail: str, resolved_by_fallback: bool) -> Dict[str, Any]:
        return {
            "type": "data_unavailable",
            "provider": provider,
            "resolved_by_fallback": bool(resolved_by_fallback),
            "detail": detail,
        }

    def _record_provider_health(
        self,
        provider: str,
        token_symbol: str,
        source: str,
        success: bool,
        detail: Optional[str],
    ) -> None:
        alert_message = record_provider_health_event(provider, token_symbol, source, success, detail)
        if alert_message:
            logger.warning(alert_message)

    def fetch_cryptorank_social(self, token_symbol: str) -> Dict:
        """
        Fetch CryptoRank social score (0-100).

        Args:
            token_symbol: Token symbol (e.g., "MONAD")

        Returns:
            Dict with social_score, twitter_followers, etc.
        """
        aliases = self._provider_aliases(token_symbol)
        symbol_candidates = self._dedupe_preserve(aliases["cryptorank_symbols"])
        failures: list[str] = []

        params = {}
        if self.cryptorank_api_key:
            params["api_key"] = self.cryptorank_api_key

        for idx, symbol in enumerate(symbol_candidates):
            try:
                list_url = "https://api.cryptorank.io/v1/currencies"
                list_params = dict(params)
                list_params["symbols"] = symbol
                list_resp = self.session.get(list_url, params=list_params, timeout=10)
                if list_resp.status_code != 200:
                    failures.append(f"{symbol}:lookup:{list_resp.status_code}")
                    continue

                rows = list_resp.json().get("data", [])
                if not isinstance(rows, list) or not rows:
                    failures.append(f"{symbol}:lookup:no_results")
                    continue

                currency_id = None
                expected_names = {name.lower() for name in aliases.get("cryptorank_names", [])}
                expected_symbols = {s.upper() for s in aliases.get("cryptorank_symbols", [])}
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    row_symbol = str(row.get("symbol", "")).upper()
                    row_name = str(row.get("name", "")).lower()
                    if row_symbol in expected_symbols and (
                        row_name in expected_names or token_symbol.lower() in row_name
                    ):
                        row_id = row.get("id")
                        if str(row_id).isdigit():
                            currency_id = int(str(row_id))
                            break
                if currency_id is None:
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        row_symbol = str(row.get("symbol", "")).upper()
                        row_id = row.get("id")
                        if row_symbol in expected_symbols and str(row_id).isdigit():
                            currency_id = int(str(row_id))
                            break
                if currency_id is None:
                    failures.append(f"{symbol}:lookup:no_symbol_match")
                    continue

                detail_url = f"https://api.cryptorank.io/v1/currencies/{currency_id}"
                detail_resp = self.session.get(detail_url, params=params, timeout=10)
                if detail_resp.status_code != 200:
                    failures.append(f"{symbol}:detail:{detail_resp.status_code}")
                    continue

                data = detail_resp.json().get("data", {})
                social_raw = {
                    "socialScore": data.get("socialScore"),
                    "twitterFollowers": data.get("twitterFollowers"),
                    "telegramMembers": data.get("telegramMembers"),
                    "redditSubscribers": data.get("redditSubscribers"),
                }
                no_social_metrics = all(value is None for value in social_raw.values())
                social_data = {
                    "social_score": int(social_raw.get("socialScore") or 0),
                    "twitter_followers": int(social_raw.get("twitterFollowers") or 0),
                    "telegram_members": int(social_raw.get("telegramMembers") or 0),
                    "reddit_subscribers": int(social_raw.get("redditSubscribers") or 0),
                }
                source = "unavailable" if no_social_metrics else ("cached" if idx == 0 else "fallback")
                fetched_at = _utc_now_iso()
                notes = []
                if idx > 0:
                    notes.append(
                        self._build_data_unavailable_note(
                            "cryptorank",
                            f"primary symbol '{symbol_candidates[0]}' unavailable; used fallback symbol '{symbol}'",
                            resolved_by_fallback=True,
                        )
                    )
                if no_social_metrics:
                    notes.append(
                        self._build_data_unavailable_note(
                            "cryptorank",
                            "provider returned no social metrics for matched currency",
                            resolved_by_fallback=False,
                        )
                    )

                social_data.update(
                    {
                        "_source": source,
                        "_fetched_at": fetched_at,
                        "_notes": notes,
                        "_matched_symbol": symbol,
                        "_matched_id": currency_id,
                    }
                )
                self._record_provider_health("cryptorank", token_symbol, source, True, None)
                logger.info(f"CryptoRank social score for {token_symbol}: {social_data['social_score']}/100")
                return social_data
            except Exception as exc:
                failures.append(f"{symbol}:exception:{exc}")

        detail = "all aliases failed"
        if failures:
            detail = f"all aliases failed ({'; '.join(failures[:3])})"
        self._record_provider_health("cryptorank", token_symbol, "unavailable", False, detail)
        return {
            "social_score": 0,
            "twitter_followers": 0,
            "telegram_members": 0,
            "reddit_subscribers": 0,
            "_source": "unavailable",
            "_fetched_at": _utc_now_iso(),
            "_notes": [self._build_data_unavailable_note("cryptorank", detail, resolved_by_fallback=False)],
        }

    def _select_coingecko_coin_id(
        self,
        coins: list[Dict[str, Any]],
        token_symbol: str,
        aliases: Dict[str, list[str]],
    ) -> Optional[str]:
        expected_symbols = {s.upper() for s in aliases["coingecko_symbols"]}
        expected_names = {n.lower() for n in aliases["coingecko_names"]}

        for coin in coins:
            symbol = str(coin.get("symbol", "")).upper()
            name = str(coin.get("name", "")).lower()
            if symbol in expected_symbols and (name in expected_names or token_symbol.lower() in name):
                return coin.get("id")

        for coin in coins:
            if str(coin.get("symbol", "")).upper() in expected_symbols:
                return coin.get("id")

        return None

    def fetch_coingecko_community(self, token_symbol: str) -> Dict:
        """
        Fetch CoinGecko community data (watchlist count, upvotes).

        Args:
            token_symbol: Token symbol (e.g., "MONAD")

        Returns:
            Dict with watchlist_count, upvotes, etc.
        """
        aliases = self._provider_aliases(token_symbol)
        query_candidates = self._dedupe_preserve(aliases["coingecko_queries"])
        failures: list[str] = []

        for idx, query in enumerate(query_candidates):
            try:
                search_url = "https://api.coingecko.com/api/v3/search"
                params = {"query": query}
                if self.coingecko_api_key:
                    params["x_cg_pro_api_key"] = self.coingecko_api_key

                search_response = self.session.get(search_url, params=params, timeout=10)
                if search_response.status_code != 200:
                    failures.append(f"{query}:search:{search_response.status_code}")
                    continue

                coins = search_response.json().get("coins", [])
                if not coins:
                    failures.append(f"{query}:search:no_results")
                    continue

                coin_id = self._select_coingecko_coin_id(coins, token_symbol, aliases)
                if not coin_id:
                    failures.append(f"{query}:search:no_symbol_match")
                    continue

                coin_url = f"https://api.coingecko.com/api/v3/coins/{coin_id}"
                coin_params = {}
                if self.coingecko_api_key:
                    coin_params["x_cg_pro_api_key"] = self.coingecko_api_key
                coin_response = self.session.get(coin_url, params=coin_params, timeout=10)

                if coin_response.status_code != 200:
                    failures.append(f"{query}:coin:{coin_response.status_code}")
                    continue

                data = coin_response.json()
                community_data = {
                    "watchlist_count": data.get("watchlist_portfolio_users", 0),
                    "upvotes": data.get("sentiment_votes_up_percentage", 0),
                    "downvotes": data.get("sentiment_votes_down_percentage", 0),
                    "reddit_subscribers": data.get("community_data", {}).get("reddit_subscribers", 0),
                    "twitter_followers": data.get("community_data", {}).get("twitter_followers", 0),
                }
                source = "cached" if idx == 0 else "fallback"
                notes = []
                if idx > 0:
                    notes.append(
                        self._build_data_unavailable_note(
                            "coingecko",
                            f"primary query '{query_candidates[0]}' unavailable; used fallback query '{query}'",
                            resolved_by_fallback=True,
                        )
                    )
                community_data.update(
                    {
                        "_source": source,
                        "_fetched_at": _utc_now_iso(),
                        "_notes": notes,
                        "_matched_query": query,
                    }
                )
                self._record_provider_health("coingecko", token_symbol, source, True, None)
                logger.info(f"CoinGecko watchlist for {token_symbol}: {community_data['watchlist_count']} users")
                return community_data
            except Exception as exc:
                failures.append(f"{query}:exception:{exc}")

        detail = "search and alias exhausted"
        if failures:
            detail = f"search and alias exhausted ({'; '.join(failures[:3])})"
        self._record_provider_health("coingecko", token_symbol, "unavailable", False, detail)
        return {
            "watchlist_count": 0,
            "upvotes": 0,
            "downvotes": 0,
            "reddit_subscribers": 0,
            "twitter_followers": 0,
            "_source": "unavailable",
            "_fetched_at": _utc_now_iso(),
            "_notes": [self._build_data_unavailable_note("coingecko", detail, resolved_by_fallback=False)],
        }

    def fetch_twitter_mentions(
        self,
        token_symbol: str,
        perplexity_data: Optional[Dict] = None,
        coingecko_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Fetch Twitter mentions from cached Perplexity payload when available.

        Returns a structured payload with source metadata and data notes.
        """
        return self.fetch_twitter_mentions_with_fallback(
            token_symbol=token_symbol,
            perplexity_data=perplexity_data,
            coingecko_data=coingecko_data,
        )

    @staticmethod
    def _estimate_mentions_from_coingecko(community: Optional[Dict[str, Any]]) -> Optional[int]:
        if not isinstance(community, dict):
            return None
        followers = int(community.get("twitter_followers") or 0)
        watchlist = int(community.get("watchlist_count") or 0)
        if followers <= 0 and watchlist <= 0:
            return None
        # Conservative heuristic: approximate weekly mentions from community scale.
        # Keeps estimates low to avoid overstating social hype when direct data is missing.
        estimate = max(int(followers * 0.01), int(watchlist * 0.02))
        return max(estimate, 1)

    def fetch_twitter_mentions_with_fallback(
        self,
        token_symbol: str,
        perplexity_data: Optional[Dict] = None,
        coingecko_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if (
            isinstance(perplexity_data, dict)
            and isinstance(perplexity_data.get("social_hype"), dict)
            and "twitter_mentions_7d" in perplexity_data.get("social_hype", {})
        ):
            mentions = int(perplexity_data["social_hype"].get("twitter_mentions_7d", 0) or 0)
            _cache_set_twitter_mentions(token_symbol, mentions)
            self._record_provider_health("twitter_mentions", token_symbol, "cached", True, None)
            return {
                "twitter_mentions_7d": mentions,
                "_source": "cached",
                "_fetched_at": _utc_now_iso(),
                "_notes": [],
            }

        estimated_mentions = self._estimate_mentions_from_coingecko(coingecko_data)
        if estimated_mentions is not None:
            _cache_set_twitter_mentions(token_symbol, estimated_mentions)
            detail = "estimated from coingecko community metrics"
            self._record_provider_health("twitter_mentions", token_symbol, "fallback", True, detail)
            return {
                "twitter_mentions_7d": estimated_mentions,
                "_source": "fallback",
                "_fetched_at": _utc_now_iso(),
                "_notes": [self._build_data_unavailable_note("twitter_mentions", detail, resolved_by_fallback=True)],
            }

        cached = _cache_get_twitter_mentions(token_symbol)
        if cached is not None:
            detail = f"using last-good cache from {cached['as_of']}"
            self._record_provider_health("twitter_mentions", token_symbol, "fallback", True, detail)
            return {
                "twitter_mentions_7d": int(cached["twitter_mentions_7d"]),
                "_source": "fallback",
                "_fetched_at": _utc_now_iso(),
                "_notes": [self._build_data_unavailable_note("twitter_mentions", detail, resolved_by_fallback=True)],
            }

        detail = "missing from perplexity payload"
        self._record_provider_health("twitter_mentions", token_symbol, "unavailable", False, detail)
        return {
            "twitter_mentions_7d": 0,
            "_source": "unavailable",
            "_fetched_at": _utc_now_iso(),
            "_notes": [self._build_data_unavailable_note("twitter_mentions", detail, resolved_by_fallback=False)],
        }

    def estimate_twitter_mentions(self, token_symbol: str, perplexity_data: Optional[Dict] = None) -> int:
        """
        Estimate Twitter mentions from Perplexity data or return 0.

        For now, this is a placeholder. In practice:
        1. Use Perplexity daily scan JSON (social_hype.twitter_mentions_7d)
        2. OR manually count mentions via Twitter Search
        3. OR use Twitter API (requires paid access)

        Args:
            token_symbol: Token symbol
            perplexity_data: Optional Perplexity discovery JSON

        Returns:
            int: Estimated Twitter mentions (past 7 days)
        """
        return int(
            self.fetch_twitter_mentions_with_fallback(
                token_symbol=token_symbol,
                perplexity_data=perplexity_data,
                coingecko_data=None,
            ).get("twitter_mentions_7d", 0) or 0
        )

    def calculate_social_hype_score(
        self,
        cryptorank_score: int,
        twitter_mentions: int,
        watchlist_count: int
    ) -> Tuple[float, str]:
        """
        Calculate composite social hype score from free sources.

        Scoring (Session 39 v3.0):
        - 5 pts: EXTREME HYPE (>10K mentions OR >80 CryptoRank OR >50K watchlist)
        - 4 pts: HIGH HYPE (5K-10K mentions OR 60-80 CR OR 20K-50K watchlist)
        - 2 pts: MODERATE HYPE (1K-5K mentions OR 40-60 CR OR 5K-20K watchlist)
        - 0 pts: LOW HYPE (<1K mentions OR <40 CR OR <5K watchlist)

        Args:
            cryptorank_score: CryptoRank social score (0-100)
            twitter_mentions: Twitter mentions count (past 7 days)
            watchlist_count: CoinGecko watchlist count

        Returns:
            Tuple[float, str]: (score 0-5, description)
        """
        # Check for EXTREME HYPE (any metric triggers)
        if twitter_mentions > 10000 or cryptorank_score > 80 or watchlist_count > 50000:
            return (
                5.0,
                f"EXTREME HYPE: {twitter_mentions} Twitter mentions, {cryptorank_score}/100 CryptoRank, "
                f"{watchlist_count} watchlist → contrarian short opportunity"
            )

        # Check for HIGH HYPE
        if (5000 <= twitter_mentions <= 10000 or
            60 <= cryptorank_score <= 80 or
            20000 <= watchlist_count <= 50000):
            return (
                4.0,
                f"HIGH HYPE: {twitter_mentions} Twitter mentions, {cryptorank_score}/100 CryptoRank, "
                f"{watchlist_count} watchlist → strong dump setup"
            )

        # Check for MODERATE HYPE
        if (1000 <= twitter_mentions <= 5000 or
            40 <= cryptorank_score <= 60 or
            5000 <= watchlist_count <= 20000):
            return (
                2.0,
                f"MODERATE HYPE: {twitter_mentions} Twitter mentions, {cryptorank_score}/100 CryptoRank, "
                f"{watchlist_count} watchlist → standard setup"
            )

        # LOW HYPE (skip - no retail)
        return (
            0.0,
            f"LOW HYPE: {twitter_mentions} Twitter mentions, {cryptorank_score}/100 CryptoRank, "
            f"{watchlist_count} watchlist → skip (no retail to dump on)"
        )

    def get_social_hype_intelligence(
        self,
        token_symbol: str,
        perplexity_data: Optional[Dict] = None
    ) -> Dict:
        """
        Get complete social hype intelligence for a token.

        Args:
            token_symbol: Token symbol (e.g., "MONAD")
            perplexity_data: Optional Perplexity discovery JSON with social_hype field

        Returns:
            Dict with score, description, and raw metrics
        """
        logger.info(f"Fetching social hype intelligence for {token_symbol}...")

        # Fetch from free sources
        cryptorank_data = self.fetch_cryptorank_social(token_symbol)
        coingecko_data = self.fetch_coingecko_community(token_symbol)
        twitter_data = self.fetch_twitter_mentions_with_fallback(
            token_symbol=token_symbol,
            perplexity_data=perplexity_data,
            coingecko_data=coingecko_data,
        )
        twitter_mentions = int(twitter_data.get("twitter_mentions_7d", 0) or 0)

        # Calculate composite score
        score, description = self.calculate_social_hype_score(
            cryptorank_score=cryptorank_data.get("social_score", 0),
            twitter_mentions=twitter_mentions,
            watchlist_count=coingecko_data.get("watchlist_count", 0)
        )

        data_notes = []
        for source_data in (cryptorank_data, coingecko_data, twitter_data):
            notes = source_data.get("_notes", [])
            if isinstance(notes, list):
                data_notes.extend([n for n in notes if isinstance(n, dict)])

        return {
            "score": score,
            "description": description,
            "cryptorank_social_score": cryptorank_data.get("social_score", 0),
            "twitter_mentions_7d": twitter_mentions,
            "coingecko_watchlist": coingecko_data.get("watchlist_count", 0),
            "source_badges": {
                "cryptorank": {
                    "source": cryptorank_data.get("_source", "unavailable"),
                    "as_of": cryptorank_data.get("_fetched_at"),
                },
                "coingecko": {
                    "source": coingecko_data.get("_source", "unavailable"),
                    "as_of": coingecko_data.get("_fetched_at"),
                },
                "twitter_mentions": {
                    "source": twitter_data.get("_source", "unavailable"),
                    "as_of": twitter_data.get("_fetched_at"),
                },
            },
            "data_notes": data_notes,
            "raw_data": {
                "cryptorank": cryptorank_data,
                "coingecko": coingecko_data,
                "twitter_mentions": twitter_data,
            }
        }


def fetch_social_hype(symbol: str, token_name: Optional[str] = None) -> Dict[str, Any]:
    """Compatibility wrapper used by exchange fetcher facade."""
    _ = token_name  # Reserved for future provider disambiguation.
    return SocialHypeFetcher().get_social_hype_intelligence(symbol)


def main():
    """Test the social hype fetcher."""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python social_hype_fetcher.py TOKEN_SYMBOL")
        sys.exit(1)

    token_symbol = sys.argv[1].upper()

    fetcher = SocialHypeFetcher()
    result = fetcher.get_social_hype_intelligence(token_symbol)

    print(f"\n🎯 Social Hype Intelligence: {token_symbol}")
    print("=" * 60)
    print(f"Score: {result['score']}/5")
    print(f"Description: {result['description']}")
    print(f"\nMetrics:")
    print(f"  CryptoRank Social: {result['cryptorank_social_score']}/100")
    print(f"  Twitter Mentions (7d): {result['twitter_mentions_7d']}")
    print(f"  CoinGecko Watchlist: {result['coingecko_watchlist']} users")
    print("=" * 60)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
