# DACLE - David's Automated Crypto Learning Engine

**AI-powered TGE analysis system with 5-agent pipeline, multi-platform OTC tracking, and knowledge base integration**

---

## 🚀 Quick Start

### For Development Sessions

**Starting a new Claude Code session?**
```bash
bash scripts/start
```

This displays:
- Infrastructure status (bot, dashboard, DB)
- Recent git activity
- Ready-to-copy context prompt for Claude Code

See [.claude/session_start.md](.claude/session_start.md) for details.

---

## 📊 Current Status (v4.4 - Session 240-241, December 21, 2025)

**🚀 LATEST (Session 240-241)**: ML Classifier Training & VPS Deployment ✅
- **Binary Classification**: DUMP vs NOT_DUMP (Gemini recommended for N=38)
- **Time-Series CV**: Prevents look-ahead bias in temporal crypto data
- **Training Data**: 38 TGE samples, 23 features (numeric, boolean, one-hot)
- **RF CV F1**: 55.43% (honest out-of-sample evaluation)
- **VPS Deployed**: 37.27.217.82 with predict.py script
- **Integration**: Secondary validation for DACLE conviction scores
- 📄 **[ML Documentation →](./docs/reviews/ML_APPROACH_BREAKDOWN_FOR_REVIEW.md)**

**🚀 PREVIOUS (Session 146)**: Learning 024 HTF Index S/R & TradingView Integration ✅
- **Learning 024**: Daily timeframe S/R levels on indices (USDT.D, BTC.D, TOTAL3)
- **Predictive Capability**: Identifies WHERE macro state changes will occur
- **TradingView Setup Guide**: Created for David's Pro webhook integration
- **Infrastructure Ready**: Webhook server and MacroSRChecker already exist
- 📄 **[Setup Guide →](./docs/guides/TRADINGVIEW_MACRO_SR_SETUP.md)**

**🚀 PREVIOUS (Session 236)**: Conviction Staleness Check & Auto-Regeneration ✅
- **Staleness Detection**: 48-hour threshold for conviction analysis freshness
- **Playbook Blocking**: Stale convictions block playbook generation by default
- **Auto-Regeneration**: Watchtower 4H refresh automatically regenerates stale convictions
- **Freshness Display**: Clear "[FRESH] Xh old" or "[STALE]" status messages
- **Why 48h?**: TGE dumps occur in first 24-48h; macro conditions shift meaningfully in 2 days
- 📄 **[Session 236 Documentation →](./docs/sessions/SESSION_236_CONVICTION_STALENESS.md)**

**🚀 PREVIOUS (Session 232)**: Gemini External Review - 4 Safety Mechanisms Implemented ✅
- **External Review**: Architecture validated as PRODUCTION READY by Gemini
- **L020 VETO Upgrade**: Tier-1 exchange listings trigger 48h automatic pause (prevents "God Candle" losses)
- **L024 Multi-Timeframe Fractal**: 15m chart for 0-24h TGEs, 4H for 24h-21d (addresses 4H bias)
- **L025 First Green Day Trap**: Dead cat bounce detection (-10 confidence penalty for Day 3-5 pumps)
- **Slippage Protection**: Position capped at 2% of 5-minute volume (prevents self-slippage)
- 📄 **[Gemini Review →](./docs/reviews/GEMINI_EXTERNAL_REVIEW_2025_12_19.md)**

**🚀 PREVIOUS (Sessions 140-145)**: Model v1.0 Production Lock - OUTSTANDING Correlation Achieved ✅
- **Model Performance**: Spearman ρ=-0.612 (OUTSTANDING, target was -0.520)
- **Production Status**: Model v1.0 weights locked and frozen for production use
- **Forward Validation**: Out-of-sample (OOS) tracking system active for real-world validation
- **Pattern Coverage**: 100% (50/50 TGEs) with market regime data
- 📄 **[Model v1.0 Documentation →](./docs/sessions/SESSION_145_MODEL_LOCK.md)**

**🚀 PREVIOUS (Sessions 115-117)**: VPS Migration Complete - Real-time monitoring on Hetzner VPS ✅
- **Sniper Daemon**: Continuous conviction scanning on VPS with systemd integration
- **Watchtower Migration**: Alert-based monitoring moved from GitHub Actions to VPS
- **Historical Pattern Analysis**: Verbose component breakdown, Month 1 scoring overhaul
- **Infrastructure**: Both daemons running as systemd services with auto-restart
- 📄 **[VPS Commands →](./docs/reference/command_line.md)**

**🚀 PREVIOUS (Session 89B Phase 3 Week 1)**: Agent 7 Infrastructure Complete - Learning loop foundation ready for N≥10 ✅
- **Hybrid Learning Architecture**: Cold Start (N<10) → Warm Start (N=10-50) → Hot (N≥50)
- **Agent 7 Query Layer**: 632 lines - conviction validation, category wisdom, skip patterns, TA profitability
- **TA Validation Backfill**: 375 lines - links TA checks → TGE outcomes for profitability analysis
- **Infrastructure Migration**: 282 lines - 6 indexes + 3 materialized views (5-20x faster queries)

**Session 84**: Execution Readiness Complete - All 3 Critical Blockers Resolved ✅
- **Alert Integration**: Auto-alert pipeline sends Telegram notifications for conviction ≥8.0/10
- **Position Calculator**: Dollar amounts in alerts ("Enter SHORT: $96.00" vs "Position size: 4.8%")
- **Exit Monitoring**: NOT NEEDED - User handles via TradingView (manual monitoring preferred)
- **Portfolio Size**: Updated to $2,000 (user's actual portfolio)
- **Full Automation**: Discovery → Analysis → Alert → Execution pipeline complete
- **Time Saved**: 30-60 seconds per trade (no manual position calculation)
- **Mental Load**: Dollar amounts eliminate manual math
- 📄 **[Complete Documentation →](./docs/SESSION_84_COMPLETE.md)**

**Session 84 Phase 2**: Profitability-Based Condition Optimization ✅
- **avg_pnl_per_trigger Tracking**: THE key metric - optimizes for profitability, not accuracy
- **Agent 6 & Agent 7**: Playbook generation + profitability analytics (947 lines)
- **Trade-Condition Linking**: Attribution system (48h lookback, influence weighting)
- **Example Impact**: check_trendline_break (+$15.25/trigger) now prioritized over check_usdt_dominance (80% accuracy, -$5.20/trigger) → saves $285.60/90 days

**Session 84 Phase 1**: Market Regime Tagging + Weighted Similarity ✅
- **Market Regime**: Segregate condition performance by BULL/BEAR/CHOP regimes
- **Weighted Similarity**: 0.0-1.0 scoring (FDV 30%, Float 25%, VC 20%, Exchange 15%, Vesting 10%)

**Session 81**: Repository Cleanup & Organization
- **Templates Consolidated**: All templates moved to `/templates` folder (11 templates total)
- **Data Cleanup**: Archived unused `security/` folder and incomplete MONAD token data
- **Infrastructure**: Removed obsolete Replit deployment files and webhook workflow

**Session 80-STAGE7**: Entry Timing Monitor (Stage 7)
- **Stage 7 Added**: Automated entry timing monitor for high-conviction tokens (≥8.0)
- **48-Hour Lifecycle**: Pre-TGE → 6h (🔴 Critical) → 48h (🟡 Extended) → Auto-remove
- **14 TA Indicators**: Macro (7) + Core TA (5) + Advanced (2) via TADataAggregator
- **Entry Scoring**: 0-10 scale, Telegram alerts when score ≥6.5
- **GitHub Actions**: Automated monitoring every 15 minutes
- **Currently Tracking**: RAYLS (10/10 conviction), IRYS (8.5/10 conviction)

**Session 79K-TA**: TA Aggregator for Agent 4
- **TADataAggregator**: Collects 19 TA/macro indicators into structured JSON for execution decisions
- **17/19 Indicators Live**: All via free APIs (Binance Futures, CCXT, CoinGecko, Alternative.me)
- **2 N/A for TGE**: Long/Short liquidations irrelevant for new tokens (no perps at TGE)
- **Stage 4.5**: New pipeline stage collecting TA snapshot between Analysis and Execution
- **LiquidationTracker**: Funding Rate, OI Change, Cascade Risk from Binance Futures

**Session 79J**: TGE Data Quality Foundation
- **Field Alias System**: Validator recognizes alternative field names (e.g., `community_allocation_pct` → `token_allocation`)
- **Alias Verification Report**: Transparency on which aliases were matched during validation
- **Individual Token Insertion**: `add_token_to_tge_calendar.py` for manual token additions
- **IRYS Validation**: 100% CRITICAL (14/14), 100% IMPORTANT (9/9)

**Session 76** (Nov 29): Agent 5 Guardrails - MM Detection + Perpetuals Verification
- **MM Detection**: Auto-widen stop loss by 50% when top-tier MM detected
- **Perpetuals Verification**: Block execution if perpetual futures unavailable

**Session 52B - CRITICAL** (Nov 25): 4-Tier Conviction System
- Unlocked 7.5-7.9 "Execute with Caution" tier (3-4% position)
- Direction-aware FDV penalty (shorts vs longs)
- Validated: +$48 GAIB profit captured (previously blocked by binary logic)

**System**: 7-Stage Automated Pipeline (Discovery → Data → Analysis → Execution → Persistence → Learning → Entry Timing) + 6-Agent TGE Analysis v3.6 + 5 Exchange Perpetuals
**Performance**: High-conviction (9-10/10) analysis in **90 minutes** (was 3h) + Data confidence 58% → 80% + Exchange coverage: 5 platforms
**Live Dashboard**: https://dacletge.netlify.app/

### 🎯 Real-World Validation Results

**GAIB Short Trade** (€200, Nov 18, 2025):
- ✅ **David's Entry**: $0.1602 (PERFECT - 0.66 fib from listing $0.2239)
- ❌ **System's Initial**: $0.125 (WRONG - 22% error using ATH $0.34)
- ✅ **Current Status**: -28.7% dump from listing → Thesis validated (9/10 conviction)
- ✅ **R:R**: 1.5:1 confirmed acceptable for 9-10/10 conviction trades
- 📈 **Learning**: Always use listing price for Fibonacci, never speculative ATH

**Perplexity Scan Quality Evolution**:
- **Overall Grade**: B+ (85%) → A+ (97%) (+12% improvement)
- **OTC Coverage**: 0% → 100% (4 platforms: Whales, MEXC, Aevo, Hyperliquid)
- **Fibonacci Accuracy**: 60% → 95% (fixed to use listing price)
- **Pattern Confidence**: Qualitative → Quantitative (0.0-1.0 scale)
- **Post-TGE Detection**: 0% → 100% (0-7 day tracking window)
- **Trading Setup**: 40% → 95% (entry/SL/TP/R:R now provided)

**Next Trade: MON (Monad) - Nov 24 TGE**:
- **Conviction**: 8.5/10 (0.75 similarity to STRK pattern)
- **OTC Signal**: -62.6% decline = STRONG SHORT SIGNAL (0.76 strength)
- **Entry Strategy**: Gap-up to $0.045-0.050 (4:1 R:R) OR skip if $0.025 (0.46:1 R:R)
- **Predicted**: -45% to -55% dump in first week

### 🎯 Arsh TA Integration v3.4 (Session 41)

**Phase 2: TA Scoring & Position Sizing** ✅
- **Agent 2**: Added macro_market_conditions scoring (±0.5 to conviction)
- **Agent 5**: TA-aware position sizing overlay (±0.5-1% based on BTC/ETH structure)
- **Pipeline**: Full TA data flow integration
- **Testing**: 8/8 integration tests passing

**Phase 3: Learning Loop Foundation** ✅
- **Database**: `ta_correlation_tracker` schema with 4 helper views
- **Scripts**: Logging (`log_ta_correlation.py`) + Analysis (`analyze_ta_correlation.py`)
- **Workflows**: Daily/Weekly/Monthly procedures documented
- **Goal**: Track TA signal accuracy to validate Arsh methodology (target: >80% accuracy)

**Example Scenarios**:
- **Perfect Alignment** (BTC↓ + ETH↓ + Bearish + Resistance): +2.0 conviction, 5% position
- **TA Headwinds** (BTC↑ + ETH↑ + Bullish): -2.0 conviction, 3% position
- **Neutral TA**: No adjustment, baseline 4% position

**Files Created** (Session 41):
- [docs/implementation/TA_LEARNING_LOOP.md](docs/implementation/TA_LEARNING_LOOP.md) - Technical guide
- [docs/workflows/TA_CORRELATION_WORKFLOW.md](guides/TA_CORRELATION_WORKFLOW.md) - User workflow

**Next Phase**: Begin prospective TA tracking on next TGE discovery

### ✅ Production-Ready Features

**7-Agent TGE Analysis System v3.9** (Session 84 - Profitability-Based Optimization):

| Agent | Version | Model | Purpose | Last Updated |
|-------|---------|-------|---------|--------------|
| **Agent 0.5** | v1.1 | Sonnet 4.5 | Knowledge Base Lookup | Session 76 |
| **Agent 0** | v3.6 | Sonnet 4.5 | Data Retrieval & Validation | Session 76 |
| **Agent 1** | v2.5 | Haiku | OTC Volume Trends | Session 49 |
| **Agent 2** | v3.6 | Sonnet 4.5 | Conviction Scoring (4-tier system) | Session 76 |
| **Agent 3** | v3.0 | Sonnet 4.5 | Multi-Source Convergence | Session 50 |
| **Agent 4** | v3.6 | Opus | Reality Check | Session 76 |
| **Agent 5** | v1.4 | Sonnet 4.5 | Position Sizing + Risk (MM detection) | Session 76 |
| **Agent 6** | v1.0 | Sonnet 4.5 | Playbook Generator (profitability-based) | Session 84 |
| **Agent 7** | v1.1 | Sonnet 4.5 | Learning Analytics (avg_pnl_per_trigger) | Session 84 |

### 🆕 Recent Major Updates (Sessions 45-76)

**v3.6 - Agent 5 Guardrails** (Session 76, Nov 29):
- **MM Detection**: Auto-widen SL by 50% for top-tier market makers
- **Perpetuals Verification**: Block execution if perpetual futures unavailable
- **Pipeline Validation**: Enhanced error handling, graceful fallbacks

**v3.5 - TA Analysis Repositioning** (Session 75, Nov 27):
- Moved TA analysis BEFORE Agent 2 scoring (was after)
- Macro indices now inform conviction calculation
- Example: Stablecoin dominance bearish → +0.5 conviction for shorts

**v3.4 - 4-Tier Conviction System** (Session 52B, Nov 25) ⭐ **CRITICAL**:
- Unlocked 7.5-7.9 "Execute with Caution" tier (was binary execute/skip)
- Direction-aware FDV penalty (shorts: -0.5 penalty, longs: blocker)
- Data uncertainty position sizing (single gap: -20%, multiple gaps: cap at 3%)
- **Validated**: +$48 GAIB profit captured (previously blocked)

**v3.3 - Exchange Perpetuals & 6-Agent Pipeline** (Sessions 49-51, Nov 24):
- Added 5 exchange perpetual futures tracking (Binance, OKX, MEXC, Bybit, Gate.io)
- Agent 0.5 Knowledge Base Lookup introduced (121-project database)
- Data confidence: 58% → 80% (+22%)
- Analysis time: 3h → 90min (-50%)

**v3.2 - Documentation Audit & Data Governance** (Sessions 45-48, Nov 22-23):
- 156 files reviewed, 165 → 139 markdown files (-16%)
- Reference data consolidation (VC tiers, alpha callers, exchange tiers)
- Enhanced data quality controls

**Agent Details**:
- **Agent 0.5** - Knowledge Base Access Layer (semantic search, 950 lines)
- **Agent 0** - Data Retrieval & Validation (1,790 lines, **11 OTC platforms** 🆕)
  - **Session 31**: 11 pre-market sources (Whales Market, Hyperliquid, Aster, 8 CEX pre-markets)
  - **Session 31**: 3-source TGE date validation (CryptoRank, ICODrops, ICOAnalytics)
  - **Session 31**: Reward Type classification (Airdrop/Presale/ICO/IDO → dump pressure estimation)
  - **Session 31**: 6 VC funding sources (added crypto-fundraising.info, AlphaPacked, newsletters)
  - **Session 31**: Platform-specific guidance (270 lines - red flags, best practices, limitations)
  - **Session 31**: Data coverage 78% → 92%+ (14-point improvement)
  - **Note**: OTC price validation operational ✅ | OTC volume trend analysis ⏳ (Phase 2 roadmap)
- **Agent 2** - TGE Short Signal Generation + **Arsh TA Scoring** + **Alpha Callers (5%) + Social Hype (3%)** 🆕 v3.4
  - Pre-TGE scoring with dump pressure estimation
  - Macro market conditions overlay (±0.5 conviction)
  - BTC/ETH structure integration
  - Alpha caller mentions (54 Twitter accounts across 3 tiers)
  - Social hype intelligence (Twitter, CryptoRank, CoinGecko)
- **Agent 4** - Execution Reality Check (fast-path optimization, 951 lines)
  - **Session 25**: 2-minute analysis for 9-10/10 conviction trades ⚡
  - **Session 25**: Pipeline trust protocol (eliminates redundant validation)
  - **Session 25**: 3-tier analysis paths (FAST/STANDARD/REJECTION)
  - **Session 25**: Decision-first output format
  - **Session 25**: 50-70% speed improvement with 0% quality loss
- **Agent 5** - Position Sizing & Risk Calculator + **TA Overlay** 🆕 v3.4
  - Risk/reward ratio calculation (≥3.3:1 threshold)
  - Position size recommendations (3-5% with TA overlay)
  - **TA-aware position sizing** (±0.5-1% based on BTC/ETH structure)
  - Exchange availability verification
  - Entry window timing optimization

**TGE Report Generation Workflow** (Sessions 28-30):
- **PRE-GENERATION CHECKLIST** - 8-point verification (50 sec, saves 15-30 min)
  - Token folder created, template files identified, dashboard update planned
  - File output locations confirmed, template type determined, commit message drafted
  - Exchange availability checked (Session 29), HTML data population plan confirmed (Session 30)
- **POST-GENERATION VALIDATION** - Automated quality checks (5 sec, saves 15-30 min)
  - 5 checks: File locations, dashboard integration, template consistency, HTML data populated (Check 3B - Session 30), no misplaced files
  - Detects {{PLACEHOLDER}} tokens, generic text, [SYMBOL] tokens in HTML
  - Script: `bash scripts/validate_report.sh TOKEN`
- **Template Consistency Rule** - Always use standard template (500+ lines)
  - PRE-TGE: Standard template with MONITOR styling
  - POST-TGE: Standard template with EXECUTE/SKIP styling
  - HTML must have complete data (NOT just sed placeholder replacement)
- **Impact**: 70% → 95%+ accuracy on first generation, 30-38 min saved per report (Sessions 28+30 learnings)

**Multi-Platform OTC Integration** (Sessions 18 + 31):
- ✅ **11 Pre-Market Platforms** (Session 31 - Notion Checklist Integration):
  - Primary: Whales Market, Hyperliquid, Aster
  - CEX Pre-Markets: Binance, MEXC, Bybit, OKX, Gate.io, KuCoin, BingX, Coinbase
- ✅ **3-Source TGE Date Validation** (CryptoRank, ICODrops, ICOAnalytics)
- ✅ **Reward Type Classification** (8 categories: Airdrop/Presale/ICO/IEO/IDO/SHO/INO/Fair Launch)
- ✅ **6 VC Funding Sources** (Crunchbase, Messari, The Block, crypto-fundraising.info, AlphaPacked, newsletters)
- 🎯 **100% pre-market coverage** (up from 9% in Session 18)
- 🎯 **92%+ data confidence** (up from 78% before Session 31)

**Data Coverage**:
- 121 airdrop projects in master database
- 9 case studies embedded in knowledge base
- 220+ tokens on Hyperliquid
- 3,000+ DeFi protocols (DeFiLlama)
- 11-source pre-market validation (median OTC pricing)
- 3-source TGE date validation (eliminates single-source errors)

**Feedback Loop System** (Session 16):
- Agent quantitative analysis + Human qualitative review
- Consolidated scoring (Agent 0.0 + David 3.5 = Consolidated 2.5)
- Product viability scoring for pre-TGE projects (TVL, funding, VCs, team, hype)
- Edge Case 5a/5b split (vaporware vs product-first)
- Notion MCP integration for manual research extraction

**Dashboard** (Streamlit Multi-Page):
- TGE Opportunities (conviction scores 0-10)
- OTC Signals (volume trends, fading interest)
- Airdrop Analysis (121 projects)

### ✅ Execution Readiness: COMPLETE (Session 84)

**All 3 Critical Blockers Resolved**:

1. ✅ **Alert Integration** (Blocker #1 - FIXED)
   - Auto-alert pipeline sends Telegram notifications when conviction ≥8.0/10
   - Integrated into `run_tge_analysis.py` (automatic after analysis)
   - File: [scripts/tge/auto_alert_pipeline.py](scripts/tge/auto_alert_pipeline.py) - 416 lines

2. ✅ **Position Size Calculator** (Blocker #2 - FIXED)
   - Dollar amounts in alerts ("Enter SHORT: $96.00" vs "Position size: 4.8%")
   - Integrated into alert messages (no manual calculation needed)
   - File: [scripts/tge/position_calculator.py](scripts/tge/position_calculator.py) - 241 lines

3. ✅ **Exit Monitoring** (Blocker #3 - NOT NEEDED)
   - User handles via TradingView alerts
   - Manual monitoring is preferred workflow
   - No automation needed

**What We Built** (Analysis Infrastructure):
- ✅ 7-agent TGE analysis system (Agents 0, 0.5, 2, 5, 6, 7)
- ✅ Multi-platform OTC price tracking (11 platforms)
- ✅ Alpha Caller mentions (54 Twitter accounts integrated into Agent 2)
- ✅ Social Hype scoring (integrated into Agent 2)
- ✅ Dashboard with conviction scores
- ✅ Auto-alert pipeline (Telegram notifications)
- ✅ Position calculator (dollar amounts)
- ✅ 10,000+ lines of implementation

**Agent Architecture** (See [AGENT_MAPPING.md](docs/architecture/AGENT_MAPPING.md)):
- `src/agents/` - Modular processing units (validation, position sizing, playbooks, analytics)
- `src/conviction/` - Core scoring engine (Agent 2 + supporting modules)
- `src/integrations/` - Data extraction (10+ sources, conceptual "Agent 1")
- Agent 3 (manual feedback) and Agent 4 (execution check) deprecated/archived

**Phase 2 Features** (Advanced - Deferred):
- ⏳ **OTC Volume Trend Analysis** (MET pattern detection) - Agent 1 archived, Phase 2 roadmap
- ⏳ **Multi-Source Convergence Alerts** - Agent 3 archived, Phase 2 roadmap
- ⏳ **Trade Logger Integration** (execution rate tracking)

**David's Real Workflow**:
- Uses 10+ sources (not just Discord)
- Research time: 90-120 min/project (not 8 min)
- Primary strategy: **Convergence Signals** (3+ sources = high conviction)
- Execution rate: 30% (needs push alerts, not pull dashboards)

**Phase 2 Roadmap** (Deferred Advanced Features):
- **OTC Volume Tracking** (4-6 hours) - Catch MET-like patterns (-42% volume → -60% dump)
- **Multi-Source Convergence Alerts** (8-12 hours) - Real-time alerts when 3+ sources converge
- **TGE Execution Alerts** - Telegram push notifications for 8-10/10 conviction trades
- **Trade Logger Integration** - Track execution rate vs. alert delivery

---

## 🎯 System Architecture

### 7-Stage Automated TGE Pipeline (Session 80-STAGE7)

```
Input: Token Symbol (e.g., "MONAD")
   ↓
┌─────────────────────────────────────────────────────────────┐
│ Agent 0.5: Knowledge Base Access Layer                     │
│ • Semantic search across case studies (9 embedded)          │
│ • Historical pattern matching (TGE dumps, VC markups)       │
│ • KB confidence scoring (0-100%)                            │
│ • Cross-validation with external data                       │
│ Output: kb_context.json (confidence + patterns)             │
└─────────────────────────────────────────────────────────────┘
   ↓
┌─────────────────────────────────────────────────────────────┐
│ Agent 0: Data Retrieval & Validation (1,790 lines) - v3.4  │
│ • 11 OTC platforms (Whales, Hyperliquid, Aster, 8 CEX)     │
│ • OTC PRICE validation ✅ (median across 5+ platforms)      │
│ • OTC VOLUME tracking ⏳ (Phase 2 - MET pattern detection)  │
│ • 3-source TGE date validation (CryptoRank, ICODrops, ICO)  │
│ • Reward Type classification (8 categories + dump pressure) │
│ • 6 VC funding sources (Crunchbase, Messari, crypto-fund)   │
│ • Platform-specific guidance (270 lines - red flags, tips)  │
│ • 3-source unlock validation (CMC, Dropstab, Tokenomist)   │
│ • DeFiLlama TVL (3,000+ protocols)                          │
│ • Product viability scoring (5 components)                  │
│ • Edge Case 5a/5b detection (vaporware vs product-first)    │
│ Output: tge_data.json (92%+ confidence, 100% pre-coverage)  │
└─────────────────────────────────────────────────────────────┘
   ↓
┌─────────────────────────────────────────────────────────────┐
│ Agent 2: TGE Short Signal Generation + Conviction Scoring   │
│ • 9-Component Conviction Formula (Session 39 v3.0):         │
│   - FDV/MC Ratio (25%), Float % (20%), VC Markup (12%)     │
│   - VC Tier (5%), OI + Order Book (10%), Pattern (12%)     │
│   - Dump Pressure (8%), Alpha Callers (5%), Hype (3%)      │
│ • Arsh TA Integration (Session 42):                         │
│   - BTC/ETH market structure overlay                        │
│   - Macro market conditions (±0.5 conviction)               │
│ • Alpha Caller Mentions (54 Twitter accounts, 3 tiers)      │
│ • Social Hype Intelligence (Twitter, CryptoRank, CoinGecko) │
│ • Historical pattern matching (Wormhole, Starknet, ZKSync)  │
│ Output: conviction_score.json (0.0-10.0 + EXECUTE/SKIP)     │
└─────────────────────────────────────────────────────────────┘
   ↓
┌─────────────────────────────────────────────────────────────┐
│ Agent 4: Execution Reality Check                            │
│ • 3-Tier Analysis Paths (Session 25):                       │
│   - FAST-PATH: 9-10/10 conviction (2 min analysis)          │
│   - STANDARD: 7-8.9/10 conviction (3-4 min analysis)        │
│   - REJECTION: <7/10 conviction (1 min rejection)           │
│ • BTC/ETH TA Validation (Session 42)                        │
│ • KB confidence-based quality gates (Session 15)            │
│ • Data completeness check (missing fields = penalties)      │
│ • Execution recommendation (EXECUTE/WAIT/REJECT)            │
│ Output: execution_decision.json                             │
└─────────────────────────────────────────────────────────────┘
   ↓
┌─────────────────────────────────────────────────────────────┐
│ Agent 5: Position Sizing & Risk Calculator                  │
│ • R/R ratio calculation (≥3.3:1 threshold)                  │
│ • Position size base (3-8% based on 7-10/10 conviction)     │
│ • TA-aware position overlay (±0.5-1% Session 42)            │
│ • Exchange availability verification                         │
│ • Entry window timing optimization                           │
│ Output: final_report.md (HTML + Markdown)                   │
└─────────────────────────────────────────────────────────────┘
   ↓
┌─────────────────────────────────────────────────────────────┐
│ STAGE 7: Entry Timing Monitor (Session 80-STAGE7)          │
│ • Automated monitoring for high-conviction tokens (≥8.0)    │
│ • 48-hour lifecycle: Pre-TGE → 6h 🔴 → 48h 🟡 → Remove    │
│ • 14 TA indicators every 15 minutes (GitHub Actions)        │
│ • Entry score 0-10 (≥6.5 triggers Telegram alert)          │
│ • TGE-Zero mode fallback (no price history = 2.0/10)        │
│ Output: Telegram alerts with entry timing recommendations   │
└─────────────────────────────────────────────────────────────┘
   ↓
Output: Final TGE Analysis Report + Real-Time Entry Alerts

NOTE: Agents 1 (OTC Volume) & 3 (Convergence) archived Session 43.
      Basic functionality absorbed into Agents 0 & 2.
      Advanced features (volume trend analysis, convergence alerts) → Phase 2 roadmap.
```

### Test Results (Real TGEs)

| Token | Agent Score | David Score | Outcome | Match |
|-------|-------------|-------------|---------|-------|
| **MONAD** | 6.45/10 | TBD (Nov 13) | Pending | - |
| **ENDLESS** | 0.0/10 REJECT | - | Correct (insufficient data) | ✅ |
| **NEUTRL** | 2.5/10 | 3.5/10 | Consolidated | 71% agreement |
| **ARIA** | TBD | TBD | In progress | - |

**Agent-Human Agreement**: 71% (improving from 50%)

---

## 🛠️ Installation & Setup

### Prerequisites
- Python 3.9+
- Discord bot configured (token in `.env`)
- Supabase database setup
- Together.ai API key

### Quick Setup

```bash
# 1. Clone repository
git clone https://github.com/yourusername/dacle.git
cd dacle

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -e .

# 4. Configure environment variables
cp .env.example .env
# Edit .env with your API keys:
# - DISCORD_BOT_TOKEN
# - SUPABASE_URL & SUPABASE_SERVICE_ROLE_KEY
# - TOGETHER_API_KEY

# 5. Run database migrations (if needed)
# See docs/database/ for migration scripts

# 6. Test connections
python scripts/test_connection.py  # Test Supabase
python scripts/test_together.py    # Test Together.ai
```

### Running the System

```bash
# 1. Start Discord bot (24/7 monitoring)
source venv/bin/activate
python run_bot.py

# 2. Run TGE Analysis (on-demand)
python scripts/tge/run_tge_analysis.py

# 3. View Dashboard
# Live at: https://dacletge.netlify.app/
```

---

## 📁 Project Structure

```
dacle/
├── .claude/
│   ├── agents/
│   │   ├── README.md                    # 5-agent system overview
│   │   ├── 0-data-retrieval-validator.md
│   │   ├── 0.5-knowledge-base-lookup.md
│   │   ├── 2-tge-short-signal-executor.md
│   │   ├── 4-execution-reality-check.md
│   │   ├── 5-position-sizing-risk-calculator.md
│   │   ├── archived/                    # Agents 1 & 3 (Session 43)
│   │   │   ├── 1-otc-volume-analyzer.md (functionality → Agent 0 + Phase 2)
│   │   │   └── 3-multi-source-convergence-detector.md (functionality → Agent 2 + Phase 2)
│   │   ├── MASTER_WORKFLOW.md           # Complete workflow guide
│   │   ├── EXECUTION_CHECKLIST.md       # Quality gates
│   │   └── AGENT_IMPROVEMENTS.md        # v2.4 enhancements
│   └── session_start.md                 # Session context loader
├── src/
│   ├── ai/
│   │   └── together_client.py           # Together.ai LLM integration
│   ├── bot/
│   │   ├── dacle_bot.py                 # Main Discord bot
│   │   └── cogs/
│   │       └── monitor.py               # Message monitoring
│   ├── knowledge/
│   │   ├── supabase_client.py           # Database operations
│   │   └── agent_0_5_kb_access.py       # KB semantic search (950 lines)
│   ├── agents/
│   │   └── agent_0_data_validation.py   # Data retrieval (1,390 lines)
│   └── utils/
│       ├── config.py                    # Configuration management
│       └── logger.py                    # Logging setup
├── scripts/
│   ├── test_premarket_apis.py           # OTC platform tests
│   ├── embed_case_studies.py            # KB population
│   ├── sync_master_database.py          # Airdrop DB sync
│   └── start                            # Session startup helper
├── docs/
│   ├── PRD.md                           # Product requirements
│   ├── STATUS.md                        # Current status (always up-to-date)
│   ├── CHANGELOG.md                     # Version history
│   ├── PROJECT_STRUCTURE.md             # Architecture overview
│   ├── analysis/                        # Strategic analysis docs
│   │   ├── MASTER_AIRDROP_DATABASE.md   # 121 projects
│   │   ├── AIRDROP_PRIORITY_MATRIX.md
│   │   ├── FORCED_EXECUTION_FLOW.md     # Execution trigger design
│   │   └── WORKFLOW_VERIFICATION_WITH_SESSION_18.md
│   ├── sessions/                        # Implementation session logs
│   │   ├── SESSION_18_PREMARKET_INTEGRATION.md
│   │   ├── SESSION_18_QUICK_REFERENCE.md
│   │   └── [27 total session files]
│   ├── knowledge/                       # Knowledge base
│   │   ├── patterns/                    # Case studies (9 embedded)
│   │   ├── context/                     # Trading strategies
│   │   └── README.md
│   └── database/
│       ├── schema.sql                   # Database schema
│       └── migrations/                  # Migration scripts
├── reports/
│   ├── MONAD_TGE_ANALYSIS_2025-11-06.md # First real TGE analysis
│   ├── report-viewer.html              # Interactive report viewer
│   └── README.md                        # Feedback loop guide
├── dashboard/
│   ├── app.py                           # Streamlit multi-page app
│   └── pages/
│       ├── 1_🔥_TGE_Opportunities.py
│       ├── 2_📊_OTC_Signals.py
│       └── 3_🎯_Airdrop_Analysis.py
├── run_bot.py                           # Bot launcher
├── pyproject.toml                       # Python dependencies & project config
└── .env                                 # Environment variables (not in git)
```

---

## 🔧 Configuration

### Environment Variables (.env)

```bash
# Discord
DISCORD_BOT_TOKEN=your_bot_token
DISCORD_PRIVATE_SERVER_ID=your_server_id

# Supabase (use SERVICE_ROLE_KEY for bot, not anon key)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key

# Together.ai
TOGETHER_API_KEY=your_together_api_key
TOGETHER_MODEL=meta-llama/Llama-3.3-70B-Instruct-Turbo
TOGETHER_EMBEDDING_MODEL=BAAI/bge-large-en-v1.5

# Redis (optional)
REDIS_URL=redis://localhost:6379

# Environment
ENV=development
LOG_LEVEL=INFO
```

### Database Setup

```bash
# 1. Create Supabase project at https://supabase.com

# 2. Enable pgvector extension
# In Supabase SQL Editor:
CREATE EXTENSION IF NOT EXISTS vector;

# 3. Run schema migration
# Execute: docs/database/schema.sql

# 4. Apply Row Level Security
# Execute: docs/database/migrations/security_fixes.sql

# 5. Apply performance indexes
# Execute: docs/database/migrations/performance_improvements_fixed.sql

# 6. Verify setup
python scripts/test_connection.py
```

---

## 📈 Success Metrics

### System Performance (v2.4)
| Metric | Target | Current | Status |
|--------|--------|---------|--------|
| Agent-Human Agreement | 80% | 71% | 🟡 Improving |
| OTC Coverage | 90% | 90% | ✅ Met |
| Data Confidence | 95% | 95% | ✅ Met |
| KB Confidence (new tokens) | 50% | 25-35% | 🟡 Expected |
| Agent 0.5 Speed | <2s | ~1.1s | ✅ Met |

### Business Impact (Original Goals)
| Metric | Baseline | Target | Current | Status |
|--------|----------|--------|---------|--------|
| Execution Rate | 30% | 75% | 30% | ❌ Blocked by missing execution triggers |
| Research Time | 90-120 min | 30 min | 90-120 min | ❌ Dashboard not in David's workflow |
| Monthly Income | $2,500 | $5,000 | $2,500 | ❌ No trades executed via system |

**⚠️ Critical Finding**: Built powerful analysis infrastructure, but David's wallet = $0. Missing execution triggers (HAV convergence, TGE alerts) are the blocker, not missing analysis.

---

## 🚀 Roadmap

### ✅ Completed (Sessions 1-43)
- [x] **5-Agent TGE Analysis System v3.4** (Agents 0, 0.5, 2, 4, 5 - Session 43 consolidation)
- [x] **Agent 4 Optimization** (50-70% speed improvement, fast-path protocol)
- [x] **Multi-Platform OTC Integration** (11 platforms - price validation operational)
- [x] **Alpha Caller Integration** (54 Twitter accounts, 5% conviction weight)
- [x] **Social Hype Integration** (3% conviction weight, Kaito.ai + metrics)
- [x] **Arsh TA Integration v3.4** (BTC/ETH structure, ±0.5-1% position sizing)
- [x] **Feedback Loop System** (Agent-human collaboration)
- [x] **Knowledge Base** (9 case studies embedded, semantic search)
- [x] **Product Viability Scoring** (Pre-TGE projects: TVL, funding, VCs, team)
- [x] **Dashboard** (3 pages: TGE, OTC, Airdrops)
- [x] **Master Airdrop Database** (121 projects)
- [x] **Discord Bot** (24/7 monitoring)

### 🔜 Phase 2: Missing Advanced Features (Deferred from Session 43)

**High Priority - OTC Volume Trend Analysis** (4-6 hours):
- [ ] **Enhance Agent 0 with Volume Tracking**
  - [ ] Extend Agent 0 to track 7-day OTC volume history (not just price)
  - [ ] Hyperliquid API integration for volume data
  - [ ] MET pattern baseline comparison (-42% volume decline detection)
  - [ ] Fading interest detection (>30% decline = SHORT signal)
- [ ] **Update Conviction Formula to v3.1.2**
  - [ ] Add Component 10: OTC Volume Trend (2% weight)
  - [ ] Update CONVICTION_SCORING_v3.1.md documentation
- [ ] **Create Volume Scraper Infrastructure**
  - [ ] Daily scraper: `scripts/scrape_otc_volumes.py`
  - [ ] Supabase schema: `otc_volume_history` table (30-day rolling window)
  - [ ] Integrate with Agent 0 data package
- **Validated Pattern**: MET case study (-42% volume → -60% dump, Gabew caught it, David missed it)

**Medium Priority - Multi-Source Convergence Alerts** (8-12 hours, **VALIDATE WITH DAVID FIRST**):
- [ ] **Evaluate Necessity**
  - [ ] Ask David: "Do you still prioritize 3+ source convergence?"
  - [ ] Confirm it's a real execution blocker vs. current 9-component formula
- [ ] **If Validated: Enhance Agent 2 with Convergence Detection**
  - [ ] Track 54 Twitter alpha callers for project mentions
  - [ ] Cross-reference CryptoRank, ICODrops, DropLabs
  - [ ] Generate convergence alerts when 3+ sources mention same project
  - [ ] Telegram push notification integration
- **Current Status**: Scoring components (Alpha Callers 5%, Social Hype 3%) integrated into Agent 2. Alert system not implemented.

### 🔜 Priority 1: Execution Triggers (Ongoing)
- [ ] **HAV Convergence Detector** (Discord + Twitter integration)
  - [ ] HAV Discord tracker (copy-paste with #hav tag)
  - [ ] HAV Twitter tracker (@HavAirdrops API)
  - [ ] Top 15 Twitter caller monitoring
  - [ ] Convergence alert logic (3+ sources = high conviction)
  - [ ] Telegram DM push notifications
- [ ] **TGE Execution Alerts** (MONAD Nov 13 test)
  - [ ] CryptoRank daily scanner (8-10/10 conviction TGEs)
  - [ ] 2-hour pre-TGE alert window
  - [ ] Hyperliquid entry readiness check
- [ ] **Trade Logger Integration**
  - [ ] Alert delivery tracking
  - [ ] Execution rate measurement
  - [ ] Opportunity cost calculation

### 🔜 Priority 2: System Refinements (Ongoing)
- [ ] Improve KB confidence for new tokens (25% → 50%)
- [ ] Add more case studies (9 → 20+)
- [ ] Refine pre-TGE scoring (agent-human agreement 71% → 80%+)
- [ ] Historical pattern library expansion

### 🔮 Future Enhancements
- [ ] YouTube summarization (4 channels, 45-60 min/day → 10 min)
- [ ] Twitter advanced monitoring (52 callers)
- [ ] Automated trade execution (Phase 3, requires extensive testing)
- [ ] Web dashboard (Phase 2, if pull workflow proves useful)

---

## 📚 Key Documentation

### Getting Started
- **[.claude/session_start.md](.claude/session_start.md)** - Session context loader
- **docs/PRD.md** - Product requirements
- **[docs/STATUS.md](docs/STATUS.md)** - Current status (always up-to-date)

### TGE Analysis System
- **[.claude/agents/README.md](README.md)** - 6-agent system overview
- **[.claude/agents/MASTER_WORKFLOW.md](.claude/agents/MASTER_WORKFLOW.md)** - Complete workflow
- **[.claude/agents/AGENT_IMPROVEMENTS.md](.claude/agents/AGENT_IMPROVEMENTS.md)** - v2.4 enhancements

### Real TGE Analysis Reports
See `reports/` directory for TGE analysis outputs.

### Session Documentation
Recent session documentation is available in [docs/sessions/](docs/sessions/). Historical session summaries (3+ months old) are preserved in git history.

---

## 🐛 Troubleshooting

### Bot Won't Connect
- Check `DISCORD_BOT_TOKEN` in `.env`
- Verify **Message Content Intent** enabled in Discord Developer Portal
- Confirm bot added to Discord server with proper permissions

### Database Errors
```bash
# Test connection
python scripts/test_connection.py

# Common fixes:
# 1. Use SUPABASE_SERVICE_ROLE_KEY (not anon key) for bot
# 2. Verify RLS policies applied: docs/database/migrations/security_fixes.sql
# 3. Check database URL format: https://[project].supabase.co
```

### OTC Platform Errors
```bash
# Test all 4 platforms
python scripts/test_premarket_apis.py

# Platform-specific fixes:
# - Aevo: Check pre_launch boolean field (not naming patterns)
# - MEXC: Use DNS-over-HTTPS bypass (IP: 95.101.110.171)
# - Hyperliquid: Public API, no auth required
# - Gate.io: Fallback platform, usually works
```

### Agent 0.5 Knowledge Base Issues
```bash
# Re-embed case studies if semantic search fails
python scripts/embed_case_studies.py

# Verify embeddings in Supabase:
# SELECT COUNT(*) FROM knowledge_embeddings;
# Should return 9 (one per case study)
```

### Dashboard Won't Start
```bash
# Install Streamlit if missing
pip install streamlit

# Run with verbose output
streamlit run dashboard/app.py --logger.level=debug
```

---

## 🔒 Security

- ✅ **Row Level Security (RLS)** - 100% coverage (22 policies across 11 tables)
- ✅ **Service Role Key** - Bot uses service_role, dashboard uses authenticated
- ✅ **API Keys** - Stored in `.env` (gitignored)
- ✅ **Virtual Environment** - Dependency isolation
- ✅ **Minimal Bot Permissions** - Read/send messages only
- ✅ **No Credentials in Code** - All secrets in environment variables

---

## 🤝 Contributing

This is a personal trading system for David. Not currently accepting external contributions.

For questions about the system architecture or implementation, see documentation in `docs/` and `.claude/agents/`.

---

## 📝 License

See [LICENSE](LICENSE)

---

## 🏗️ Built With

- **Python 3.9+** - Core language
- **Discord.py** - Discord bot framework
- **Supabase** - PostgreSQL database + pgvector semantic search
- **Together.ai** - LLM (Llama 3.3 70B) + Embeddings (BGE Large)
- **Streamlit** - Dashboard UI
- **Playwright** - Browser automation (MEXC, Aevo)
- **Notion MCP** - Manual research extraction
- **claude-mem** - Persistent development session memory (Claude Code plugin)

---

## 📊 Project Stats

- **Total Lines of Code**: 18,500+ (agents, scripts, documentation, validation)
- **Agent System**: 7 agents (Agents 6 & 7 added Session 84), 5,450+ lines of logic
- **Documentation**: 135+ markdown files (PRD, guides, sessions, analysis, reviews)
- **Knowledge Base**: 9 case studies embedded (Wormhole, Starknet, ZKSync, Meteora, etc.)
- **Test Coverage**: 50+ real TGE analyses with correlation validation
- **Sessions Completed**: 232+ major development sessions
- **Database Tables**: 11+ (projects, mentions, trades, patterns, OTC data, validation, etc.)
- **Model Status**: v1.0 LOCKED (ρ=-0.612 OUTSTANDING)
- **Validation System**: Forward validation (OOS) tracking active
- **Learnings Documented**: 26 (L001-L026) with governance framework
- **Safety Mechanisms**: 4 (News VETO, Multi-Timeframe, First Green Day Trap, Slippage)

---

**Last Updated**: December 21, 2025
**Version**: v4.3
**Status**: Session 146 Complete ✅ (L024 HTF Index S/R & TradingView Integration - Predictive Macro Signals) 🚀
