# dacle-core

Shared core package for the DACLE pillar repos:

- [`AlexGn/dacle-polymarket`](https://github.com/AlexGn/dacle-polymarket)
- [`AlexGn/dacle-lighter`](https://github.com/AlexGn/dacle-lighter)

## Scope

This repo contains genuinely shared code consumed by both pillars:

- `dacle_core/trading_shared/` — capital models, allocation, risk ledger, shared contracts, base interfaces
- `dacle_core/utils/` — atomic writes, config, Redis, logging, network, exception handling
- `dacle_core/governance/` — shared governance contracts (KillSwitch)
- `dacle_core/ta/` — Market Cipher engine and shared TA primitives
- `dacle_core/data/` — OHLCV/cache services and shared fetchers
- `dacle_core/analysis/` — market direction cache, capital rotation detector, binary context
- `dacle_core/execution/` — shared execution primitives (v2 models, reconciliation, venue adapter, etc.)
- `dacle_core/monitoring/` — shared heartbeat primitives
- `dacle_core/bot/` — minimal shared bot utilities (not cogs)

## Usage

Install from git in each pillar repo:

```bash
pip install "git+https://github.com/AlexGn/dacle-core.git@main"
```

Or add to `requirements.txt` / `pyproject.toml`:

```text
dacle-core @ git+https://github.com/AlexGn/dacle-core.git@main
```

## Versioning

This package uses git-SHA pinning from the pillar repos. Pin to a specific SHA in production; use `@main` only during active development.

## Development

```bash
git clone git@github.com:AlexGn/dacle-core.git
cd dacle-core
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

Run the import boundary smoke test:

```bash
python -c "from dacle_core.trading_shared import contracts; from dacle_core.utils import atomic_write; print('ok')"
```
