"""Shared Market Cipher display normalization for Discord-facing surfaces."""

from __future__ import annotations

from typing import Any, Dict, Optional


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_cipher_context(payload: Dict[str, Any] | None) -> Dict[str, Any]:
    unavailable = {
        "available": False,
        "label": "UNAVAILABLE",
        "confidence_pct": None,
        "timeframe": None,
        "score": None,
        "interpretation": "Market Cipher unavailable or stale",
    }
    if not isinstance(payload, dict):
        return unavailable

    discovery_ta = payload.get("discovery_ta")
    if isinstance(discovery_ta, dict) and discovery_ta.get("cipher_signal"):
        confidence = _to_float(discovery_ta.get("cipher_confidence"))
        return {
            "available": True,
            "label": str(discovery_ta.get("cipher_signal", "UNAVAILABLE")),
            "confidence_pct": round(confidence * 100) if confidence is not None and confidence <= 1 else confidence,
            "timeframe": discovery_ta.get("cipher_timeframe") or "4H",
            "score": None,
            "interpretation": f"Discovery TA bias via {discovery_ta.get('cipher_signal', 'UNAVAILABLE')}",
        }

    if payload.get("cipher_signal"):
        confidence = _to_float(payload.get("cipher_confidence"))
        return {
            "available": True,
            "label": str(payload.get("cipher_signal", "UNAVAILABLE")),
            "confidence_pct": round(confidence * 100) if confidence is not None and confidence <= 1 else confidence,
            "timeframe": payload.get("cipher_timeframe") or "4H",
            "score": _to_float(payload.get("cipher_score")),
            "interpretation": str(payload.get("cipher_interpretation") or payload.get("cipher_signal")),
        }

    macro = payload.get("macro")
    if isinstance(macro, dict) and macro.get("cipher_signal"):
        confidence = _to_float(macro.get("cipher_confidence"))
        return {
            "available": True,
            "label": str(macro.get("cipher_signal", "UNAVAILABLE")),
            "confidence_pct": round(confidence * 100) if confidence is not None and confidence <= 1 else confidence,
            "timeframe": macro.get("cipher_timeframe") or "4H",
            "score": _to_float(macro.get("cipher_score")),
            "interpretation": str(macro.get("cipher_interpretation") or macro.get("cipher_signal")),
        }

    signals = payload.get("signals")
    if isinstance(signals, list):
        signal = next(
            (
                s for s in signals
                if isinstance(s, dict) and s.get("name") == "Cipher Composite"
            ),
            None,
        )
        if signal:
            label = str(signal.get("label") or "Cipher Composite")
            return {
                "available": True,
                "label": label,
                "confidence_pct": None,
                "timeframe": "4H",
                "score": _to_float(signal.get("score")),
                "interpretation": label,
            }

    return unavailable
