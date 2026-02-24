#!/usr/bin/env python3
"""
Data Validators - Cross-field validation for consolidated token data

Detects impossible data patterns that indicate data lag or errors:
- circulating_supply=0 + price>0 (DATA_LAG from CoinGecko)
- circulating > total_supply (IMPOSSIBLE)
- float_percent mismatch with calculated value

Part of Phase 1: Pipeline Integration Fix (Session 80)
"""

from typing import Dict, List, Tuple, Optional
import logging

logger = logging.getLogger(__name__)


class DataValidator:
    """Validates consolidated data for impossible patterns"""

    @staticmethod
    def validate_circulating_supply(data: Dict) -> Tuple[bool, str]:
        """
        Detect impossible circulating supply patterns.

        Common issues:
        1. Token trading (price>0) but circulating_supply=0 → CoinGecko data lag
        2. circulating_supply > total_supply → Impossible
        3. circulating_supply_at_tge > total_supply → Impossible

        Args:
            data: Consolidated token data

        Returns:
            (is_valid, message) tuple
        """
        price = data.get('current_price', 0) or 0
        circ = data.get('circulating_supply', 0) or 0
        circ_tge = data.get('circulating_supply_at_tge', 0) or 0
        total = data.get('total_supply', 0) or 0

        # Pattern 1: Token trading but circulating = 0 (DATA LAG)
        if price > 0 and circ == 0:
            return (
                False,
                f"DATA_LAG: Token trading at ${price:.4f} but circulating_supply=0. "
                f"CoinGecko hasn't updated yet. Use whitepaper or calculate from float_percent."
            )

        # Pattern 2: Circulating > Total (IMPOSSIBLE)
        if circ > 0 and total > 0 and circ > total:
            return (
                False,
                f"IMPOSSIBLE: circulating_supply ({int(circ):,}) > total_supply ({int(total):,})"
            )

        # Pattern 3: TGE circulating > total (IMPOSSIBLE)
        if circ_tge > 0 and total > 0 and circ_tge > total:
            return (
                False,
                f"IMPOSSIBLE: circulating_supply_at_tge ({int(circ_tge):,}) > total_supply ({int(total):,})"
            )

        return (True, "OK")

    @staticmethod
    def validate_float_percent(data: Dict) -> Tuple[bool, str]:
        """
        Validate float calculation consistency.

        Checks if float_percent matches the calculated value from:
        (circulating_supply_at_tge / total_supply) * 100

        Args:
            data: Consolidated token data

        Returns:
            (is_valid, message) tuple
        """
        float_pct = data.get('float_percent')
        circ_tge = data.get('circulating_supply_at_tge')
        total = data.get('total_supply')

        # Skip if missing required fields
        if not all([float_pct, circ_tge, total]):
            return (True, "SKIP: Missing required fields for float validation")

        # Calculate expected float
        calculated_float = (circ_tge / total) * 100
        diff = abs(float_pct - calculated_float)

        # Allow 5% tolerance for rounding
        if diff > 5:
            return (
                False,
                f"MISMATCH: float_percent={float_pct:.2f}% but calculated="
                f"{calculated_float:.2f}% (diff={diff:.2f}%)"
            )

        return (True, "OK")

    @staticmethod
    def validate_fdv_consistency(data: Dict) -> Tuple[bool, str]:
        """
        Validate FDV calculation consistency.

        FDV should equal: total_supply * current_price

        Args:
            data: Consolidated token data

        Returns:
            (is_valid, message) tuple
        """
        fdv = data.get('fdv')
        total = data.get('total_supply')
        price = data.get('current_price')

        # Skip if missing required fields
        if not all([fdv, total, price]):
            return (True, "SKIP: Missing required fields for FDV validation")

        # Calculate expected FDV
        calculated_fdv = total * price
        diff_pct = abs((fdv - calculated_fdv) / calculated_fdv * 100) if calculated_fdv > 0 else 0

        # Allow 10% tolerance (some sources use different supply values)
        if diff_pct > 10:
            return (
                False,
                f"MISMATCH: fdv=${fdv:,.0f} but calculated=${calculated_fdv:,.0f} "
                f"(diff={diff_pct:.1f}%)"
            )

        return (True, "OK")

    @staticmethod
    def validate_market_cap_ratio(data: Dict) -> Tuple[bool, str]:
        """
        Validate FDV/MC ratio sanity.

        FDV/MC ratio should be >= 1.0 (FDV is always >= MC by definition)
        Common short signals are ratios >5x, but ratios >100x indicate data errors.

        Args:
            data: Consolidated token data

        Returns:
            (is_valid, message) tuple
        """
        fdv = data.get('fdv')
        mc = data.get('market_cap')

        # Skip if missing required fields
        if not all([fdv, mc]):
            return (True, "SKIP: Missing required fields for FDV/MC validation")

        # MC can't be zero
        if mc <= 0:
            return (False, "IMPOSSIBLE: market_cap is <= 0")

        ratio = fdv / mc

        # FDV must be >= MC
        if ratio < 0.95:  # Allow 5% tolerance for data timing
            return (
                False,
                f"IMPOSSIBLE: FDV/MC ratio={ratio:.2f}x (FDV must be >= MC)"
            )

        # Ratio >100x is suspicious (likely data error)
        if ratio > 100:
            return (
                False,
                f"SUSPICIOUS: FDV/MC ratio={ratio:.0f}x (>100x suggests data error)"
            )

        return (True, "OK")

    @staticmethod
    def validate_all(data: Dict) -> Dict:
        """
        Run all validation rules on consolidated data.

        Args:
            data: Consolidated token data

        Returns:
            {
                'valid': bool,
                'errors': [{'field': str, 'message': str}, ...],
                'warnings': [{'field': str, 'message': str}, ...]
            }
        """
        results = {
            'valid': True,
            'errors': [],
            'warnings': []
        }

        # Define validators
        validators = [
            ('circulating_supply', DataValidator.validate_circulating_supply),
            ('float_percent', DataValidator.validate_float_percent),
            ('fdv', DataValidator.validate_fdv_consistency),
            ('fdv_mc_ratio', DataValidator.validate_market_cap_ratio),
        ]

        # Run each validator
        for field_name, validator_func in validators:
            is_valid, message = validator_func(data)

            # Skip if validation not applicable
            if message.startswith('SKIP:'):
                continue

            if not is_valid:
                # Determine severity
                if 'IMPOSSIBLE' in message or 'DATA_LAG' in message:
                    results['valid'] = False
                    results['errors'].append({
                        'field': field_name,
                        'message': message
                    })
                elif 'SUSPICIOUS' in message or 'MISMATCH' in message:
                    # Don't block on warnings
                    results['warnings'].append({
                        'field': field_name,
                        'message': message
                    })

        return results

    @staticmethod
    def suggest_fixes(data: Dict, validation_results: Dict) -> List[Dict]:
        """
        Suggest automatic fixes for validation errors.

        Args:
            data: Consolidated token data
            validation_results: Results from validate_all()

        Returns:
            List of suggested fixes:
            [
                {
                    'field': str,
                    'current_value': Any,
                    'suggested_value': Any,
                    'reason': str,
                    'confidence': 0-100
                },
                ...
            ]
        """
        suggestions = []

        for error in validation_results.get('errors', []):
            message = error['message']

            # Fix 1: DATA_LAG - calculate circulating_supply from float
            if 'DATA_LAG' in message and 'circulating_supply' in error['field']:
                float_pct = data.get('float_percent')
                total = data.get('total_supply')

                if float_pct and total:
                    suggested = int(total * (float_pct / 100))
                    suggestions.append({
                        'field': 'circulating_supply',
                        'current_value': data.get('circulating_supply', 0),
                        'suggested_value': suggested,
                        'reason': f'Calculated from float_percent ({float_pct}%) * total_supply',
                        'confidence': 85
                    })

            # Fix 2: FDV mismatch - recalculate from total_supply * price
            if 'MISMATCH' in message and 'fdv' in error['field']:
                total = data.get('total_supply')
                price = data.get('current_price')

                if total and price:
                    suggested = int(total * price)
                    suggestions.append({
                        'field': 'fdv',
                        'current_value': data.get('fdv'),
                        'suggested_value': suggested,
                        'reason': f'Recalculated: total_supply ({total:,}) * price (${price:.4f})',
                        'confidence': 95
                    })

        return suggestions


if __name__ == '__main__':
    # Test with ALLOCA data
    test_data = {
        'token_symbol': 'ALLOCA',
        'total_supply': 100000000,
        'circulating_supply': 0.0,  # DATA_LAG issue
        'current_price': 0.15345,
        'fdv': 15255817,
        'market_cap': 4477320,
        'float_percent': 29.34,
        'circulating_supply_at_tge': 29340000
    }

    print("Testing DataValidator with ALLOCA data:")
    print("=" * 50)

    results = DataValidator.validate_all(test_data)

    print(f"\nValidation Status: {'✅ PASS' if results['valid'] else '❌ FAIL'}")
    print(f"Errors: {len(results['errors'])}")
    print(f"Warnings: {len(results['warnings'])}")

    if results['errors']:
        print("\n🚨 Errors:")
        for error in results['errors']:
            print(f"  - {error['field']}: {error['message']}")

    if results['warnings']:
        print("\n⚠️  Warnings:")
        for warning in results['warnings']:
            print(f"  - {warning['field']}: {warning['message']}")

    # Test fix suggestions
    suggestions = DataValidator.suggest_fixes(test_data, results)
    if suggestions:
        print("\n💡 Suggested Fixes:")
        for fix in suggestions:
            print(f"  - {fix['field']}: {fix['current_value']} → {fix['suggested_value']}")
            print(f"    Reason: {fix['reason']}")
            print(f"    Confidence: {fix['confidence']}%")
