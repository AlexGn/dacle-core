#!/usr/bin/env python3
"""
Validate conviction JSON files against v3.1 schema.

Usage:
    python scripts/helpers/conviction_schema_validator.py ALLOCA
    python scripts/helpers/conviction_schema_validator.py --all
    python scripts/helpers/conviction_schema_validator.py data/tokens/ALLOCA/analysis/conviction_2025-12-01.json

This script:
1. Loads conviction JSON files
2. Validates against templates/conviction_schema_v3.1.json
3. Reports schema violations, missing fields, and data quality issues
4. Returns exit code 0 if valid, 1 if invalid
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Any, List, Tuple

try:
    from jsonschema import validate, ValidationError, Draft7Validator
except ImportError:
    print("Error: jsonschema not installed. Run: pip install jsonschema")
    sys.exit(1)


# Project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = PROJECT_ROOT / "templates" / "conviction_schema_v3.1.json"
DATA_DIR = PROJECT_ROOT / "data" / "tokens"


def load_schema() -> Dict[str, Any]:
    """Load the v3.1 JSON schema."""
    if not SCHEMA_PATH.exists():
        print(f"Error: Schema not found at {SCHEMA_PATH}")
        sys.exit(1)

    try:
        with open(SCHEMA_PATH, 'r') as f:
            schema = json.load(f)
        return schema
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in schema: {e}")
        sys.exit(1)


def load_conviction_json(json_path: Path) -> Dict[str, Any]:
    """Load conviction JSON file."""
    if not json_path.exists():
        print(f"Error: File not found: {json_path}")
        sys.exit(1)

    try:
        with open(json_path, 'r') as f:
            data = json.load(f)
        return data
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {json_path}: {e}")
        sys.exit(1)


def validate_conviction_json(data: Dict[str, Any], schema: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Validate conviction JSON against schema.

    Returns:
        (is_valid, errors) tuple
    """
    errors = []
    validator = Draft7Validator(schema)

    # Check schema validation
    for error in validator.iter_errors(data):
        error_path = " > ".join(str(p) for p in error.path) if error.path else "root"
        errors.append(f"❌ {error_path}: {error.message}")

    # Additional v3.1-specific checks
    if "short_analysis" in data:
        short = data["short_analysis"]

        # Check breakdown sum equals score
        if "breakdown" in short and "score" in short:
            breakdown_sum = sum(
                v for k, v in short["breakdown"].items()
                if k != "base_score" and isinstance(v, (int, float))
            )
            score_diff = abs(breakdown_sum - short["score"])

            if score_diff > 0.01:  # Allow small floating point errors
                errors.append(
                    f"⚠️  Breakdown sum ({breakdown_sum:.2f}) != Final score ({short['score']:.2f}) "
                    f"(diff: {score_diff:.3f})"
                )

        # Check all 12 v3.1 components exist
        if "breakdown" in short:
            v3_1_components = {
                "fdv_mc_score", "float_score", "retail_sale_score",
                "vc_markup_score", "binance_listing_score", "pattern_match_score",
                "oi_orderbook_score", "dump_pressure_score", "vc_tier_score",
                "macro_context_score", "alpha_caller_score", "social_hype_score"
            }

            missing = v3_1_components - set(short["breakdown"].keys())
            if missing:
                errors.append(f"❌ Missing v3.1 components in breakdown: {missing}")

        # Check raw_scores exist for all components
        if "raw_scores" in short:
            v3_1_raw = {
                "fdv_mc", "float", "retail_sale", "vc_markup",
                "binance_listing", "pattern_match", "oi_orderbook",
                "dump_pressure", "vc_tier", "macro_context",
                "alpha_callers", "social_hype"
            }

            missing_raw = v3_1_raw - set(short["raw_scores"].keys())
            if missing_raw:
                errors.append(f"❌ Missing raw_scores: {missing_raw}")

    # Check version
    if "session_metadata" in data:
        version = data["session_metadata"].get("pipeline_version")
        if version != "v3.1":
            errors.append(f"⚠️  Pipeline version '{version}' should be 'v3.1'")

    # Check scorer
    if "session_metadata" in data:
        scorer = data["session_metadata"].get("scorer")
        if scorer != "TGEConvictionScorer":
            errors.append(f"⚠️  Scorer '{scorer}' should be 'TGEConvictionScorer'")

    return len(errors) == 0, errors


def find_all_conviction_files() -> List[Path]:
    """Find all conviction JSON files in data/tokens/*/analysis/."""
    conviction_files = []

    if not DATA_DIR.exists():
        return []

    for token_dir in DATA_DIR.iterdir():
        if not token_dir.is_dir():
            continue

        analysis_dir = token_dir / "analysis"
        if not analysis_dir.exists():
            continue

        # Find conviction_*.json files
        conviction_files.extend(analysis_dir.glob("conviction_*.json"))

    return sorted(conviction_files)


def validate_file(json_path: Path, schema: Dict[str, Any], verbose: bool = False) -> bool:
    """Validate a single file and print results."""

    print(f"\n📄 Validating: {json_path.relative_to(PROJECT_ROOT)}")

    # Load JSON
    try:
        data = load_conviction_json(json_path)
    except Exception as e:
        print(f"   ❌ Failed to load JSON: {e}")
        return False

    # Get metadata
    token = data.get("token", "UNKNOWN")
    score = data.get("short_analysis", {}).get("score", "N/A")
    decision = data.get("recommendation", {}).get("primary_signal", "N/A")
    version = data.get("session_metadata", {}).get("pipeline_version", "unknown")

    print(f"   Token: {token} | Score: {score}/10 | Decision: {decision} | Version: {version}")

    # Validate
    is_valid, errors = validate_conviction_json(data, schema)

    if is_valid:
        print(f"   ✅ VALID - Conforms to v3.1 schema")
        return True
    else:
        print(f"   ❌ INVALID - {len(errors)} issues found:")
        for error in errors:
            print(f"      {error}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Validate conviction JSON files against v3.1 schema"
    )
    parser.add_argument(
        "target",
        nargs="?",
        help="Token symbol, file path, or '--all' to validate all tokens"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Validate all conviction files in data/tokens/*/analysis/"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed validation output"
    )

    args = parser.parse_args()

    # Load schema
    print(f"📋 Loading schema: {SCHEMA_PATH.relative_to(PROJECT_ROOT)}")
    schema = load_schema()
    print(f"   ✅ Schema loaded (v3.1)")

    # Determine files to validate
    if args.all or (args.target and args.target.lower() == "--all"):
        # Validate all files
        files = find_all_conviction_files()
        if not files:
            print("⚠️  No conviction files found in data/tokens/*/analysis/")
            sys.exit(0)

        print(f"\n🔍 Found {len(files)} conviction files to validate")

    elif args.target:
        # Check if it's a file path
        target_path = Path(args.target)

        if target_path.exists() and target_path.is_file():
            files = [target_path]
        else:
            # Assume it's a token symbol
            token_dir = DATA_DIR / args.target / "analysis"
            if not token_dir.exists():
                print(f"Error: Token directory not found: {token_dir}")
                sys.exit(1)

            files = list(token_dir.glob("conviction_*.json"))
            if not files:
                print(f"Error: No conviction files found in {token_dir}")
                sys.exit(1)

    else:
        parser.print_help()
        sys.exit(1)

    # Validate files
    results = []
    for file_path in files:
        is_valid = validate_file(file_path, schema, args.verbose)
        results.append((file_path, is_valid))

    # Summary
    print("\n" + "=" * 80)
    print("VALIDATION SUMMARY")
    print("=" * 80)

    valid_count = sum(1 for _, valid in results if valid)
    invalid_count = len(results) - valid_count

    print(f"✅ Valid:   {valid_count}/{len(results)}")
    print(f"❌ Invalid: {invalid_count}/{len(results)}")

    if invalid_count > 0:
        print("\nInvalid files:")
        for file_path, is_valid in results:
            if not is_valid:
                print(f"  - {file_path.relative_to(PROJECT_ROOT)}")

    # Exit code
    sys.exit(0 if invalid_count == 0 else 1)


if __name__ == "__main__":
    main()
