"""
Unified CEX Lead Signal Provider.
Orchestrates Binance, Blofin, and Local providers for robust lead signal failover.
"""

import asyncio
import logging
import time
import json
import urllib.request
import urllib.parse
import urllib.error
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

logger = logging.getLogger(__name__)

@dataclass
class LeadSignal:
    side: str
    strength: float
    source: str
    quality: str  # HIGH, MEDIUM, LOW
    observed_at: float
    signal_id: int = 0
    meta: Dict[str, Any] = field(default_factory=dict)

class LeadProvider(Protocol):
    async def poll_once(self) -> Optional[LeadSignal]:
        ...

    def is_suspended(self) -> bool:
        ...

    def suspend_reason(self) -> str:
        ...

    def suspend_remaining_sec(self) -> float:
        ...

    def health(self) -> dict:
        ...

    def update_symbol(self, symbol: str):
        ...

class BinanceLeadAdapter:
    def __init__(self, symbol: str, config: dict):
        from src.trading_shared.binance_aggression import BinanceAggressionStream
        self.stream = BinanceAggressionStream(symbol, config)
        self.enabled = self.stream.enabled
    
    @property
    def poll_interval_sec(self) -> float:
        return self.stream.poll_interval_sec

    async def poll_once(self) -> Optional[LeadSignal]:
        signal = await self.stream.poll_once()
        if not signal:
            return None
        return LeadSignal(
            side=signal.side,
            strength=signal.sweep_qty,
            source="binance",
            quality="HIGH",
            observed_at=signal.observed_at,
            signal_id=signal.signal_id,
            meta={"buy_qty": signal.buy_qty, "sell_qty": signal.sell_qty}
        )

    def is_suspended(self) -> bool:
        return self.stream.is_temporarily_suspended()

    def suspend_reason(self) -> str:
        return self.stream.suspend_reason()

    def suspend_remaining_sec(self) -> float:
        return self.stream.suspend_remaining_sec()

    def health(self) -> dict:
        return {
            "status": "suspended" if self.is_suspended() else "ok",
            "reason": self.suspend_reason()
        }

    def update_symbol(self, symbol: str):
        # Basic normalization for Binance Futures (BTC-USDT -> BTCUSDT)
        base = symbol.split("-")[0] if "-" in symbol else symbol.split("/")[0]
        self.stream.exchange_symbol = f"{base.upper()}USDT"
        # Reset last signal to avoid stale comparisons
        if hasattr(self.stream, "_last_signal"):
            self.stream._last_signal = None

class BlofinLeadAdapter:
    def __init__(self, symbol: str, config: dict):
        self.symbol = symbol
        base = symbol.split("-")[0] if "-" in symbol else symbol.split("/")[0]
        self.blofin_symbol = f"{base.upper()}-USDT"
        self.poll_interval = float(config.get("poll_interval_sec", 2.0))
        self.request_timeout = float(config.get("request_timeout_sec", 3.0))
        self._suspended_until = 0.0
        self._suspend_reason = ""
        self._last_price: Optional[float] = None
        self.min_delta_bps = float(config.get("min_delta_bps", 3.0))
        self.enabled = bool(config.get("enabled", True))
        self._signal_seq = 0

    @property
    def poll_interval_sec(self) -> float:
        return self.poll_interval

    def is_suspended(self) -> bool:
        return time.monotonic() < self._suspended_until

    def suspend_reason(self) -> str:
        return self._suspend_reason

    def suspend_remaining_sec(self) -> float:
        return max(0.0, self._suspended_until - time.monotonic())

    def health(self) -> dict:
        return {
            "status": "suspended" if self.is_suspended() else "ok",
            "reason": self.suspend_reason()
        }

    def update_symbol(self, symbol: str):
        self.symbol = symbol
        base = symbol.split("-")[0] if "-" in symbol else symbol.split("/")[0]
        self.blofin_symbol = f"{base.upper()}-USDT"
        self._last_price = None

    async def poll_once(self) -> Optional[LeadSignal]:
        if not self.enabled or self.is_suspended():
            return None
        
        try:
            url = f"https://openapi.blofin.com/api/v1/market/tickers?instId={self.blofin_symbol}"
            def fetch():
                req = urllib.request.Request(url, headers={"User-Agent": "dacle-scalper/1.0"})
                with urllib.request.urlopen(req, timeout=self.request_timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            
            payload = await asyncio.to_thread(fetch)
            data = payload.get("data", [])
            if not data:
                return None
            
            ticker = data[0]
            last_price = float(ticker.get("last", 0.0))
            if last_price <= 0:
                return None
            
            if self._last_price is None:
                self._last_price = last_price
                return None
            
            delta_bps = ((last_price - self._last_price) / self._last_price) * 10000.0
            self._last_price = last_price
            
            if abs(delta_bps) < self.min_delta_bps:
                return None
            
            self._signal_seq += 1
            return LeadSignal(
                side="BUY" if delta_bps > 0 else "SELL",
                strength=abs(delta_bps),
                source="blofin",
                quality="MEDIUM",
                observed_at=time.monotonic(),
                signal_id=self._signal_seq,
                meta={"delta_bps": delta_bps}
            )
        except Exception as e:
            logger.warning("Blofin poll error: %s", e)
            self._suspended_until = time.monotonic() + 30.0
            self._suspend_reason = str(e)
            return None

class LocalLeadAdapter:
    def __init__(self, config: dict):
        self.enabled = bool(config.get("enabled", True))
        self.poll_interval = float(config.get("poll_interval_sec", 1.0))

    @property
    def poll_interval_sec(self) -> float:
        return self.poll_interval

    def is_suspended(self) -> bool:
        return False

    def suspend_reason(self) -> str:
        return ""

    def suspend_remaining_sec(self) -> float:
        return 0.0

    def health(self) -> dict:
        return {"status": "ok"}

    def update_symbol(self, symbol: str):
        pass

    async def poll_once(self) -> Optional[LeadSignal]:
        # Local logic would go here if needed
        return None

class UnifiedCEXLead:
    """Orchestrator for multiple CEX lead providers with automatic failover."""
    def __init__(self, symbol: str, config: dict):
        self.symbol = symbol
        self.config = config
        self.enabled = bool(config.get("enabled", False))
        self.signal_ttl_sec = float(config.get("signal_ttl_sec", 3.0))
        self.binance_probe_interval = float(config.get("binance_probe_interval_sec", 1800.0))
        
        self.providers: List[LeadProvider] = []
        self.provider_names: List[str] = []
        self._active_idx = 0
        self._last_primary_probe = 0.0
        self._last_signal: Optional[LeadSignal] = None
        self._last_health_log_ts = 0.0

        self._setup_providers()

    def _setup_providers(self):
        p_list = self.config.get("providers", ["binance", "blofin", "local"])
        for p_name in p_list:
            if p_name == "binance":
                adapter = BinanceLeadAdapter(self.symbol, self.config.get("binance", {}))
            elif p_name == "blofin":
                adapter = BlofinLeadAdapter(self.symbol, self.config.get("blofin", {}))
            elif p_name == "local":
                adapter = LocalLeadAdapter(self.config.get("local", {}))
            else:
                continue
            
            if adapter.enabled:
                self.providers.append(adapter)
                self.provider_names.append(p_name)
        
        if not self.providers:
            self.enabled = False
            logger.warning("CEX_LEAD_STARTUP: No providers enabled. CEX lead is inactive.")
        else:
            health_info = {p: prov.health() for p, prov in zip(self.provider_names, self.providers)}
            logger.info(
                "CEX_LEAD_STARTUP: configured=%s enabled=%s active=%s provider_health=%s",
                p_list, self.provider_names, self.provider_names[self._active_idx], health_info
            )

    @property
    def poll_interval_sec(self) -> float:
        if not self.enabled:
            return 1.0
        return self.providers[self._active_idx].poll_interval_sec

    def is_temporarily_suspended(self) -> bool:
        if not self.enabled:
            return False
        return self.providers[self._active_idx].is_suspended()

    def suspend_reason(self) -> str:
        if not self.enabled:
            return ""
        return self.providers[self._active_idx].suspend_reason()

    def suspend_remaining_sec(self) -> float:
        if not self.enabled:
            return 0.0
        return self.providers[self._active_idx].suspend_remaining_sec()

    def last_http_status(self) -> Optional[int]:
        # Placeholder for compatibility
        return None

    def _try_failover(self, reason: str):
        if self._active_idx < len(self.providers) - 1:
            old = self.provider_names[self._active_idx]
            self._active_idx += 1
            new = self.provider_names[self._active_idx]
            logger.warning("CEX_LEAD_FAILOVER: %s -> %s reason=%s", old, new, reason)
        else:
            logger.debug("CEX_LEAD_FAILOVER: already at last provider, cannot failover. active=%s reason=%s", 
                         self.provider_names[self._active_idx], reason)

    def _try_recover_primary(self):
        if self._active_idx == 0:
            return
        now = time.monotonic()
        if (now - self._last_primary_probe) < self.binance_probe_interval:
            return
        
        self._last_primary_probe = now
        # We only recover if primary is no longer suspended
        if not self.providers[0].is_suspended():
            logger.info("CEX_LEAD_RECOVER: switching back to primary (binance)")
            self._active_idx = 0
        else:
            logger.debug("CEX_LEAD_RECOVER_PROBE: primary still suspended.")

    async def poll_once(self) -> Optional[LeadSignal]:
        if not self.enabled:
            return None
        
        self._try_recover_primary()
        active = self.providers[self._active_idx]
        
        sig = await active.poll_once()
        now = time.monotonic()
        if sig:
            self._last_signal = sig
            return sig
        
        if active.is_suspended():
            self._try_failover(active.suspend_reason() or "suspended")

        # Periodic health log if stale
        last_sig_age = (now - self._last_signal.observed_at) if self._last_signal else -1.0
        if (not self._last_signal or last_sig_age > self.signal_ttl_sec):
            if now - self._last_health_log_ts > 60.0:  # Log at most once per minute
                self._last_health_log_ts = now
                health_info = {p: prov.health() for p, prov in zip(self.provider_names, self.providers)}
                logger.info(
                    "CEX_LEAD_STALE: active_provider=%s signal_age=%.1f health=%s",
                    self.provider_names[self._active_idx], last_sig_age, health_info
                )
        
        return None

    def get_active_signal(self, now: Optional[float] = None) -> Optional[LeadSignal]:
        if not self.enabled or not self._last_signal:
            return None
        ts = now or time.monotonic()
        if (ts - self._last_signal.observed_at) > self.signal_ttl_sec:
            return None
        return self._last_signal

    def update_symbol(self, symbol: str):
        self.symbol = symbol
        for provider in self.providers:
            provider.update_symbol(symbol)
