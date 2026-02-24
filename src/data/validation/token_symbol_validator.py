#!/usr/bin/env python3
"""
Token Symbol Validator - Ensures trading symbols are correctly configured.

Session 113: Created to catch folder name vs trading symbol mismatches early.

Problem this solves:
- Token folders use project names (RAYLS, MONAD, HUMIDIFI)
- Exchanges use trading symbols (RLS, MON, WET)
- If trading_symbol isn't set, OHLCV fetches will fail

Usage:
    # Validate all tokens
    python3 scripts/helpers/token_symbol_validator.py

    # Validate specific token
    python3 scripts/helpers/token_symbol_validator.py RAYLS

    # Auto-detect trading symbol from exchanges
    python3 scripts/helpers/token_symbol_validator.py RAYLS --detect
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).parent.parent.parent
TOKENS_DIR = PROJECT_ROOT / "data" / "tokens"


def detect_trading_symbol(token: str) -> Optional[str]:
    """
    Try to detect the actual trading symbol from exchanges.

    Returns the trading symbol if found, None otherwise.
    """
    try:
        import ccxt
    except ImportError:
        print("  [WARN] ccxt not installed - cannot auto-detect")
        return None

    # Common symbol variations to try
    variations = [
        token,  # RAYLS
        token[:3],  # RAY
        token[:4],  # RAYL
        token.replace("IDIFI", ""),  # HUMID -> HUM for HUMIDIFI
    ]

    # Remove duplicates while preserving order
    variations = list(dict.fromkeys(variations))

    exchanges = ["mexc", "gate", "kucoin", "binance", "bybit"]

    for ex_id in exchanges:
        try:
            ex = getattr(ccxt, ex_id)({'enableRateLimit': True, 'timeout': 10000})
            ex.load_markets()

            # Search for partial matches
            for var in variations:
                matches = [
                    s.split('/')[0] for s in ex.markets.keys()
                    if var.upper() in s.upper() and '/USDT' in s and ':' not in s
                ]
                if matches:
                    # Return the first spot match
                    return matches[0]
        except Exception:
            continue

    return None


def validate_token(token: str, auto_detect: bool = False) -> Tuple[bool, List[str]]:
    """
    Validate a token's trading symbol configuration.

    Returns (is_valid, list_of_issues)
    """
    issues = []
    token_dir = TOKENS_DIR / token

    if not token_dir.exists():
        return False, [f"Token directory not found: {token_dir}"]

    consolidated_path = token_dir / "consolidated.json"
    if not consolidated_path.exists():
        return False, [f"consolidated.json not found for {token}"]

    try:
        with open(consolidated_path) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return False, [f"Invalid JSON in consolidated.json: {e}"]

    folder_name = token
    trading_symbol = data.get("trading_symbol")
    symbol = data.get("symbol")
    status = data.get("status", "Unknown")

    # Check if trading_symbol is set
    if not trading_symbol:
        if symbol and symbol != folder_name:
            issues.append(f"'trading_symbol' not set but 'symbol' differs from folder name")
            issues.append(f"  folder: {folder_name}, symbol: {symbol}")
            issues.append(f"  Consider setting 'trading_symbol': '{symbol}'")
        elif auto_detect:
            detected = detect_trading_symbol(folder_name)
            if detected and detected != folder_name:
                issues.append(f"Detected trading symbol '{detected}' differs from folder '{folder_name}'")
                issues.append(f"  Add to consolidated.json: \"trading_symbol\": \"{detected}\"")

    # Check for mismatch
    effective_symbol = trading_symbol or symbol or folder_name
    if effective_symbol != folder_name and not trading_symbol:
        issues.append(f"Symbol mismatch without explicit trading_symbol")
        issues.append(f"  folder: {folder_name}, effective: {effective_symbol}")

    # For LIVE tokens, verify exchange listing
    if status == "LIVE" or status == "Confirmed":
        if auto_detect:
            detected = detect_trading_symbol(folder_name)
            if detected:
                if trading_symbol and trading_symbol != detected:
                    issues.append(f"trading_symbol '{trading_symbol}' may be incorrect")
                    issues.append(f"  Exchange has: '{detected}'")
            elif not detected and not trading_symbol:
                # Try with the trading_symbol if set
                if trading_symbol:
                    detected = detect_trading_symbol(trading_symbol)
                if not detected:
                    issues.append(f"Could not find {folder_name} on any exchange")
                    issues.append(f"  Token may not be listed yet or uses different symbol")

    return len(issues) == 0, issues


def validate_all_tokens(auto_detect: bool = False) -> Dict[str, List[str]]:
    """Validate all tokens and return issues by token."""
    all_issues = {}

    for token_dir in sorted(TOKENS_DIR.iterdir()):
        if not token_dir.is_dir():
            continue

        token = token_dir.name
        is_valid, issues = validate_token(token, auto_detect)

        if issues:
            all_issues[token] = issues

    return all_issues


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Validate token trading symbols")
    parser.add_argument("token", nargs="?", help="Specific token to validate")
    parser.add_argument("--detect", action="store_true", help="Auto-detect from exchanges")
    parser.add_argument("--fix", action="store_true", help="Attempt to fix issues")
    args = parser.parse_args()

    print("=" * 60)
    print("Token Symbol Validator")
    print("=" * 60)

    if args.token:
        # Validate single token
        print(f"\nValidating: {args.token}")
        is_valid, issues = validate_token(args.token, args.detect)

        if is_valid:
            print(f"  ✅ {args.token} - OK")
        else:
            print(f"  ❌ {args.token} - Issues found:")
            for issue in issues:
                print(f"     {issue}")

        if args.fix and issues:
            # Attempt auto-fix
            print("\n  Attempting fix...")
            detected = detect_trading_symbol(args.token)
            if detected and detected != args.token:
                consolidated_path = TOKENS_DIR / args.token / "consolidated.json"
                with open(consolidated_path) as f:
                    data = json.load(f)

                if "trading_symbol" not in data:
                    data["trading_symbol"] = detected
                    with open(consolidated_path, 'w') as f:
                        json.dump(data, f, indent=2)
                    print(f"  ✅ Added trading_symbol: {detected}")
                else:
                    print(f"  ⚠️  trading_symbol already set: {data['trading_symbol']}")
    else:
        # Validate all tokens
        print(f"\nScanning {TOKENS_DIR}...")
        all_issues = validate_all_tokens(args.detect)

        if not all_issues:
            print("\n✅ All tokens validated successfully!")
        else:
            print(f"\n❌ Found issues in {len(all_issues)} token(s):\n")
            for token, issues in all_issues.items():
                print(f"  {token}:")
                for issue in issues:
                    print(f"    - {issue}")
                print()

    print("=" * 60)


if __name__ == "__main__":
    main()
