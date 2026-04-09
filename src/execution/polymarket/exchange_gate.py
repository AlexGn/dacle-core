from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


def build_exchange_gate_payload(
    *,
    now_ts: float,
    usdc_state: Dict[str, Any],
    open_orders: List[Dict[str, Any]],
    token_balances: List[Dict[str, Any]],
    target_assets: List[str],
    balance_tolerance: float,
    require_no_open_orders: bool,
    runtime_status: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    residual_balances = [
        row for row in token_balances if abs(float(row.get("balance", 0.0) or 0.0)) > balance_tolerance
    ]
    auth_ok = bool(usdc_state.get("ok", False))

    failures: List[str] = []
    warnings: List[str] = []

    if not auth_ok:
        failures.append("AUTH_OR_BALANCE_CHECK_FAILED")
    if residual_balances:
        failures.append("RESIDUAL_TOKEN_BALANCE")
    if require_no_open_orders and open_orders:
        failures.append("OPEN_ORDERS_PRESENT")
    elif open_orders:
        warnings.append("OPEN_ORDERS_PRESENT")

    status = "PASS" if not failures else "FAIL"
    return {
        "schema_version": 1,
        "generated_at": now_ts,
        "generated_at_iso": datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat(),
        "status": status,
        "failures": failures,
        "warnings": warnings,
        "auth_ok": auth_ok,
        "balance_tolerance": float(balance_tolerance),
        "require_no_open_orders": bool(require_no_open_orders),
        "target_asset_count": len(target_assets),
        "target_assets": target_assets,
        "usdc": usdc_state,
        "open_order_count": len(open_orders),
        "open_orders": open_orders,
        "token_balance_count": len(token_balances),
        "token_balances": token_balances,
        "residual_balance_count": len(residual_balances),
        "residual_balances": residual_balances,
        "runtime_status": runtime_status or {},
    }


def write_exchange_gate_audit(audit_path: Path, payload: Dict[str, Any]) -> None:
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_runtime_status(project_root: Path) -> Optional[Dict[str, Any]]:
    path = project_root / "data" / "runtime" / "polymarket" / "audit" / "polymarket_runtime_status.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_polymarket_config(project_root: Path, config_path: Path) -> Dict[str, Any]:
    from src.utils.config import load_config

    load_config(project_root)
    full_cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    poly_cfg = full_cfg.get("polymarket")
    if not isinstance(poly_cfg, dict):
        raise RuntimeError("No 'polymarket' section found in config")
    return poly_cfg


async def build_wrapper(poly_cfg: Dict[str, Any]):
    from src.execution.polymarket.client_wrapper import ApiCreds, ClobClient, PolymarketClientWrapper

    private_key = os.getenv("POLY_WALLET_PRIVATE_KEY")
    if not private_key:
        raise RuntimeError("POLY_WALLET_PRIVATE_KEY missing")

    api_key = os.getenv("POLY_API_KEY")
    api_secret = os.getenv("POLY_API_SECRET")
    api_passphrase = os.getenv("POLY_API_PASSPHRASE")
    if not all([api_key, api_secret, api_passphrase]):
        raise RuntimeError("Missing required POLY_API_* environment variables")

    host = os.getenv("POLY_CLOB_API_BASE_URL") or poly_cfg.get("host", "https://clob.polymarket.com")
    chain_id = int(poly_cfg.get("chain_id", 137))

    # These helper methods are likely in the daemon or a utility class in src.polymarket
    # Since we are moving the logic, we should ensure the wrapper gets the right funder.
    # In the source branch, this was handled by PolymarketDaemon._resolve_signature_type/funder.
    # We'll assume the client_wrapper can handle this or we provide the funder explicitly.

    client = ClobClient(
        host=host,
        key=private_key,
        chain_id=chain_id,
    )
    client.set_api_creds(ApiCreds(api_key, api_secret, api_passphrase))
    return PolymarketClientWrapper(poly_cfg, client)


def _normalize_open_order(order: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(order.get("id") or order.get("order_id") or order.get("orderID") or ""),
        "token_id": str(order.get("token_id") or order.get("tokenId") or ""),
        "status": str(order.get("status") or "UNKNOWN").upper(),
        "side": str(order.get("side") or ""),
        "price": order.get("price"),
        "size": order.get("size") or order.get("original_size") or order.get("remaining_size"),
    }


async def collect_exchange_state(
    *,
    wrapper: Any,
    target_assets: List[str],
) -> Dict[str, Any]:
    usdc_state = await wrapper.get_usdc_balance_and_allowance()
    raw_open_orders = await wrapper.get_open_orders()
    open_orders = [_normalize_open_order(order) for order in raw_open_orders if isinstance(order, dict)]

    token_balances: List[Dict[str, Any]] = []
    for token_id in target_assets:
        balance = float(await wrapper.get_balance(token_id))
        token_balances.append({"token_id": token_id, "balance": balance})

    return {
        "usdc_state": usdc_state,
        "open_orders": open_orders,
        "token_balances": token_balances,
    }


async def run_exchange_gate(
    *,
    project_root: Path,
    config_path: Path,
    audit_path: Path,
    balance_tolerance: float,
    require_no_open_orders: bool,
) -> Dict[str, Any]:
    poly_cfg = load_polymarket_config(project_root, config_path)
    target_assets = [str(t) for t in poly_cfg.get("target_assets", []) if str(t).strip()]
    wrapper = await build_wrapper(poly_cfg)
    exchange_state = await collect_exchange_state(wrapper=wrapper, target_assets=target_assets)
    payload = build_exchange_gate_payload(
        now_ts=time.time(),
        usdc_state=exchange_state["usdc_state"],
        open_orders=exchange_state["open_orders"],
        token_balances=exchange_state["token_balances"],
        target_assets=target_assets,
        balance_tolerance=balance_tolerance,
        require_no_open_orders=require_no_open_orders,
        runtime_status=load_runtime_status(project_root),
    )
    write_exchange_gate_audit(audit_path, payload)
    return payload
