"""
Lightweight Binance Futures aggression poller for delta-lead signals.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class AggressionSignal:
    signal_id: int
    side: str
    buy_qty: float
    sell_qty: float
    sweep_qty: float
    observed_at: float
    source_symbol: str


class BinanceAggressionStream:
    def __init__(self, symbol: str, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.window_sec = float(cfg.get("window_sec", 2.0))
        self.signal_ttl_sec = float(cfg.get("signal_ttl_sec", 3.0))
        self._base_poll_interval = float(cfg.get("poll_interval_sec", 2.0))
        self.request_timeout_sec = float(cfg.get("request_timeout_sec", 10.0))
        self.min_sweep_qty = float(cfg.get("min_sweep_qty", 100.0))
        self.dominance_ratio = float(cfg.get("dominance_ratio", 1.4))
        self.limit = int(cfg.get("limit", 200))
        self.api_url = str(cfg.get("api_url", "https://fapi.binance.com/fapi/v1/aggTrades"))
        self.error_backoff_base_sec = float(cfg.get("error_backoff_base_sec", 120.0))
        self.error_backoff_cap_sec = float(cfg.get("error_backoff_cap_sec", 1800.0))

        quote_asset = str(cfg.get("quote_asset", "USDT")).upper()
        configured_symbol = str(cfg.get("symbol", "")).strip().upper()
        self.exchange_symbol = configured_symbol or self._default_exchange_symbol(symbol, quote_asset)

        self._last_signal: Optional[AggressionSignal] = None
        self._signal_seq = 0
        self._last_error_log_ts = 0.0
        self._suspend_until_monotonic = 0.0
        self._suspend_backoff_sec = 0.0
        self._suspend_reason = ""
        self._last_http_status: Optional[int] = None
        
        # Session 491: Heat Shield State Machine
        self.throttle_state = "NORMAL" # NORMAL | STEALTH | RECOVERY
        self.stealth_interval = float(cfg.get("throttle", {}).get("stealth_interval_sec", 60.0))
        self.cooldown_period = float(cfg.get("throttle", {}).get("cooldown_sec", 1800.0))
        self.recovery_step = float(cfg.get("throttle", {}).get("recovery_step_sec", 5.0))
        self.recovery_interval = float(cfg.get("throttle", {}).get("recovery_step_interval_sec", 300.0))
        
        self._last_state_change_ts = 0.0
        self._active_poll_interval = self.poll_interval_sec

    @property
    def poll_interval_sec(self) -> float:
        return getattr(self, "_active_poll_interval", self._base_poll_interval)

    @staticmethod
    def _default_exchange_symbol(symbol: str, quote_asset: str) -> str:
        raw = str(symbol or "").upper()
        if "-" in raw:
            base = raw.split("-", 1)[0]
        elif "/" in raw:
            base = raw.split("/", 1)[0]
        else:
            base = raw
        return f"{base}{quote_asset}"

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    async def poll_once(self) -> Optional[AggressionSignal]:
        if not self.enabled:
            return None
            
        now = time.monotonic()
        
        # Handle Recovery Logic
        if self.throttle_state == "STEALTH":
            if (now - self._last_state_change_ts) > self.cooldown_period:
                self._transition_to("RECOVERY")
        
        if self.throttle_state == "RECOVERY":
            # Gradually step down the interval
            if (now - self._last_state_change_ts) > self.recovery_interval:
                if self._active_poll_interval > self.poll_interval_sec:
                    new_interval = max(self.poll_interval_sec, self._active_poll_interval - self.recovery_step)
                    logger.info(f"BINANCE_THROTTLE: Recovery Step {self._active_poll_interval:.1f}s -> {new_interval:.1f}s")
                    self._active_poll_interval = new_interval
                    self._last_state_change_ts = now
                else:
                    self._transition_to("NORMAL")

        if self.is_temporarily_suspended():
            return None
            
        trades = await asyncio.to_thread(self._fetch_recent_trades_sync)
        if not trades:
            return None
        signal = self._classify_signal(trades)
        if signal:
            self._last_signal = signal
        return signal

    def _transition_to(self, new_state: str):
        if self.throttle_state == new_state:
            return
        logger.info(f"BINANCE_THROTTLE: {self.throttle_state} -> {new_state}")
        self.throttle_state = new_state
        self._last_state_change_ts = time.monotonic()
        
        if new_state == "STEALTH":
            self._active_poll_interval = self.stealth_interval
        elif new_state == "NORMAL":
            self._active_poll_interval = self.poll_interval_sec

    def is_temporarily_suspended(self, now_monotonic: Optional[float] = None) -> bool:
        now_mono = now_monotonic if now_monotonic is not None else time.monotonic()
        return now_mono < self._suspend_until_monotonic

    def suspend_remaining_sec(self, now_monotonic: Optional[float] = None) -> float:
        now_mono = now_monotonic if now_monotonic is not None else time.monotonic()
        return max(0.0, self._suspend_until_monotonic - now_mono)

    def suspend_reason(self) -> str:
        return self._suspend_reason

    def last_http_status(self) -> Optional[int]:
        return self._last_http_status

    def get_active_signal(self, now: Optional[float] = None) -> Optional[AggressionSignal]:
        if not self.enabled or self._last_signal is None:
            return None
        now_ts = now or time.monotonic()
        if (now_ts - self._last_signal.observed_at) > self.signal_ttl_sec:
            return None
        return self._last_signal

    def _fetch_recent_trades_sync(self) -> List[Dict[str, Any]]:
        params = urllib.parse.urlencode({
            "symbol": self.exchange_symbol,
            "limit": max(10, min(self.limit, 1000)),
        })
        url = f"{self.api_url}?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "dacle-scalper/1.0"}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.request_timeout_sec) as resp:
                if int(getattr(resp, "status", 200) or 200) != 200:
                    return []
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
            self._last_http_status = 200
            self._suspend_backoff_sec = 0.0
            self._suspend_until_monotonic = 0.0
            self._suspend_reason = ""
        except urllib.error.HTTPError as exc:
            status = int(getattr(exc, "code", 0) or 0)
            self._last_http_status = status
            if status in (418, 429):
                self._apply_error_backoff(status=status)
            now = time.monotonic()
            if (now - self._last_error_log_ts) > 30.0:
                remaining = self.suspend_remaining_sec(now)
                if remaining > 0:
                    logger.warning(
                        "Binance aggression source throttled (%s): status=%s suspended_for=%.0fs",
                        self.exchange_symbol,
                        status,
                        remaining,
                    )
                else:
                    logger.warning("Binance aggression poll failed (%s): HTTP %s", self.exchange_symbol, status)
                self._last_error_log_ts = now
            return []
        except Exception as exc:
            now = time.monotonic()
            if (now - self._last_error_log_ts) > 30.0:
                logger.warning("Binance aggression poll failed (%s): %s", self.exchange_symbol, exc)
                self._last_error_log_ts = now
            return []

        if not isinstance(payload, list):
            return []
        return [x for x in payload if isinstance(x, dict)]

    def _apply_error_backoff(self, status: int):
        base = max(1.0, self.error_backoff_base_sec)
        cap = max(base, self.error_backoff_cap_sec)
        if self._suspend_backoff_sec <= 0.0:
            self._suspend_backoff_sec = base
        else:
            self._suspend_backoff_sec = min(cap, max(base, self._suspend_backoff_sec * 2.0))
        self._suspend_until_monotonic = time.monotonic() + self._suspend_backoff_sec
        self._suspend_reason = f"http_{status}"
        self._transition_to("STEALTH")
    def _classify_signal(self, trades: List[Dict[str, Any]]) -> Optional[AggressionSignal]:
        now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - int(self.window_sec * 1000)
        buy_qty = 0.0
        sell_qty = 0.0

        for trade in trades:
            ts_ms = int(self._to_float(trade.get("T", trade.get("time", 0))))
            if ts_ms < cutoff_ms:
                continue
            qty = abs(self._to_float(trade.get("q", trade.get("qty", 0.0))))
            if qty <= 0:
                continue
            # Binance aggTrades: m=true means buyer is maker => sell taker aggression.
            is_sell_aggressor = bool(trade.get("m", False))
            if is_sell_aggressor:
                sell_qty += qty
            else:
                buy_qty += qty

        if buy_qty <= 0 and sell_qty <= 0:
            return None

        side: Optional[str] = None
        sweep_qty = 0.0
        if buy_qty >= self.min_sweep_qty and buy_qty >= (sell_qty * self.dominance_ratio):
            side = "BUY"
            sweep_qty = buy_qty
        elif sell_qty >= self.min_sweep_qty and sell_qty >= (buy_qty * self.dominance_ratio):
            side = "SELL"
            sweep_qty = sell_qty

        if side is None:
            return None

        self._signal_seq += 1
        return AggressionSignal(
            signal_id=self._signal_seq,
            side=side,
            buy_qty=round(buy_qty, 3),
            sell_qty=round(sell_qty, 3),
            sweep_qty=round(sweep_qty, 3),
            observed_at=time.monotonic(),
            source_symbol=self.exchange_symbol,
        )
