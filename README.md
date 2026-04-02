# DACLE - Deep-Alpha Cryptoeconomic Leverage Engine

[![Tests](https://github.com/AlexGn/dacle/actions/workflows/test.yml/badge.svg)](https://github.com/AlexGn/dacle/actions/workflows/test.yml)
[![codecov](https://codecov.io/gh/AlexGn/dacle/branch/main/graph/badge.svg)](https://codecov.io/gh/AlexGn/dacle/branch/main/graph/badge.svg)

DACLE is a fail-closed crypto trading platform with three active pillars:
- Lighter: low-latency scalper and execution daemon
- Polymarket: binary arbitrage, market-microstructure, and promotion-gate research
- Swing / Blofin: slower execution and portfolio workflows

## Quick Start

Start a local session with:

```bash
bash scripts/start
```

Canonical AI context lives at the repo root:
- [CLAUDE.md](CLAUDE.md)
- [memory.md](memory.md)
- [techstack.md](techstack.md)
- [code_conventions.md](code_conventions.md)
- [skills.md](skills.md)

Legacy AI context was archived to:
- [docs/archived/legacy_ai_context/legacy_readme_2026-04-02.md](docs/archived/legacy_ai_context/legacy_readme_2026-04-02.md)
- [docs/archived/legacy_ai_context/legacy_claude_rules_2026-04-02.txt](docs/archived/legacy_ai_context/legacy_claude_rules_2026-04-02.txt)
- [docs/archived/legacy_ai_context/legacy_claude_flow_2026-04-02.txt](docs/archived/legacy_ai_context/legacy_claude_flow_2026-04-02.txt)
- [docs/archived/legacy_ai_context/legacy_claude_clean_2026-04-02.md](docs/archived/legacy_ai_context/legacy_claude_clean_2026-04-02.md)

## Operational Snapshot

Current production emphasis:
- Lighter SHADOW hardening and watchdog-driven service supervision
- fail-closed deployment wrappers with VPS attestation and service verification
- Polymarket research and promotion-gate hardening

Common VPS checks:

```bash
cd /home/clawd/dacle
PYTHONPATH=. venv/bin/python3 scripts/scalping/status_report.py
PYTHONPATH=. venv/bin/python3 scripts/scalping/status_report.py --strict
systemctl status dacle-api dacle-bot dacle-watchtower dacle-scalper
```

Common local checks:

```bash
pytest -q
bash scripts/validation/validate_doc_links.sh
bash deploy/scripts/safe_vps_deploy.sh <commit_sha>
```

## Repository Map

Runtime surfaces:
- `src/` core Python runtime
- `api/` HTTP API
- `dashboard/` dashboard views and visualization helpers
- `scripts/` operational, deployment, data, and audit scripts
- `deploy/` systemd, nginx, cron, and deployment wrappers

Documentation surfaces:
- `docs/guides/` operator and workflow guidance
- `docs/reference/` durable reference docs and learnings
- `docs/analysis/` research memos and audits
- `docs/archived/` archived legacy material

## Deployment Notes

Primary platform deploy path:

```bash
bash deploy/scripts/safe_vps_deploy.sh <commit_sha>
```

Properties:
- verifies the target SHA before deploy
- runs the remote deployment contract on the VPS
- verifies service health, attestation, and API bind contract
- re-runs from a clean detached temp worktree when the local checkout has tracked WIP

For branch-driven release flows, use the release-worktree wrappers in `scripts/ops/`.

## Documentation Rules

- Treat the root canonical docs (`techstack.md`, `code_conventions.md`, `skills.md`, `memory.md`) as authoritative for AI sessions.
- The legacy `.claude/rules.md`, `.claude/flow.md`, and `.claude/clean.md` are deprecated and exist as compatibility surfaces only.
- Prefer updating focused docs over appending session logs to `README.md`.

## Known Operational Gaps

- Some older docs still carry historical assumptions and need periodic pruning.
- VPS Git fetch still depends on correct SSH access for both `root` and `clawd`; the deploy path now falls back cleanly but should still be normalized operationally.
- Tunnel URL publication is non-fatal when the tunnel is healthy, but the publication path itself still needs root-cause cleanup.
