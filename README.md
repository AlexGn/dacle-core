# DACLE - David's Automated Crypto Learning Engine

**Discord bot that monitors crypto project mentions and builds a knowledge base for trading decisions**

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

### Prerequisites
- Python 3.9+
- Discord bot configured (token in `.env`)
- Supabase database setup
- Together.ai API key

### Installation

```bash
# 1. Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment variables
cp .env.example .env
# Edit .env with your API keys
```

### Running the Bot

```bash
# Activate virtual environment
source venv/bin/activate

# Run the bot
python run_bot.py
```

The bot will:
- ✅ Connect to Discord
- ✅ Monitor messages from Austin, Phobia, and Sebastien
- ✅ Extract crypto project mentions using AI
- ✅ Store mentions in Supabase database
- ✅ Generate embeddings for semantic search

---

## 📊 Current Features (v1.5 - Execution-First Pivot)

**Status**: Infrastructure 95% complete → Pivoting to execution triggers

### ✅ Built (Infrastructure Layer)
- **5-Agent TGE Analysis System** - Comprehensive TGE short opportunity analyzer
- **Master Airdrop Database** - 121 projects, 95% synced to Supabase
- **Dashboard** - TGE Opportunities + OTC Signals pages (Streamlit multi-page)
- **Discord Bot** - 24/7 monitoring with message context aggregation
- **Database** - 11 tables, pgvector semantic search, 79 indexes

### 🚨 Critical Gap Identified (Session 13-14)
- **Problem**: Built 5,000+ lines of analysis code but David's wallet = $0
- **Opportunity Cost**: -$2,000 (MONAD TGE missed while building)
- **Root Cause**: Missing EXECUTION TRIGGER, not missing analysis
- **Discovery**: David uses 10+ sources, needs push alerts (Telegram), not pull dashboards

### 🎯 Next Priority (Execution Triggers)
1. **Multi-Source Convergence Scanner** - Alert when 3+ sources mention same project (Telegram DM)
2. **Daily TGE Scanner** - CryptoRank scan → Execute alert for 8-10/10 conviction TGEs
3. **Trade Logger Integration** - Track alerts vs. actual trades to measure execution rate

**Principle**: Ship 80% solution that triggers trades in 3 days > 100% analysis that David ignores in 2 weeks

---

## 📁 Project Structure

```
dacle/
├── src/
│   ├── ai/
│   │   └── together_client.py      # Together.ai integration
│   ├── bot/
│   │   ├── dacle_bot.py            # Main bot
│   │   └── cogs/
│   │       └── monitor.py          # Message monitoring
│   ├── knowledge/
│   │   └── supabase_client.py      # Database operations
│   └── utils/
│       ├── config.py                # Configuration management
│       └── logger.py                # Logging setup
├── docs/
│   ├── PRD.md                       # Product requirements
│   ├── analysis/
│   │   ├── MASTER_AIRDROP_DATABASE.md           # 121 airdrop projects
│   │   ├── AIRDROP_PRIORITY_MATRIX.md           # Prioritization framework
│   │   └── AIRDROP_STRATEGY_EXTRACTION_TRACKER.md  # Progress tracking
│   └── database/
│       ├── schema.sql               # Database schema
│       └── enable_rls.sql           # Security policies
├── scripts/
│   ├── test_connection.py           # Test Supabase
│   └── test_together.py             # Test Together.ai
├── run_bot.py                       # Bot launcher
├── requirements.txt                 # Python dependencies
└── .env                             # Environment variables (not in git)
```

---

## 🎯 Roadmap

### ✅ Phase 1 - Week 1-2: Discord Monitoring (Current)
- [x] Infrastructure setup (Supabase, Together.ai)
- [x] Discord bot foundation
- [x] Message monitoring and project extraction
- [x] Database storage with embeddings
- [ ] Live testing with real Discord messages
- [ ] Researcher detection accuracy improvements

### 🔜 Phase 1 - Week 3-4: Conviction Scoring
- [ ] Scoring algorithm (1-10 scale)
- [ ] VC backing analysis
- [ ] Tokenomics checker
- [ ] Position size calculator

### 🔜 Phase 1 - Week 5-6: Daily Briefing
- [ ] Top opportunities digest
- [ ] Active position tracking
- [ ] Macro alerts
- [ ] Execution tracker

---

## 🔧 Configuration

### Environment Variables (.env)

```bash
# Discord
DISCORD_BOT_TOKEN=your_bot_token
DISCORD_PRIVATE_SERVER_ID=your_server_id

# Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_anon_key

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

---

## 🐛 Troubleshooting

### Bot won't connect
- Check `DISCORD_BOT_TOKEN` in `.env`
- Verify bot has **Message Content Intent** enabled in Discord Developer Portal
- Check bot is added to your Discord server

### Can't read messages
- Enable **Message Content Intent** in Discord Developer Portal (Bot → Privileged Gateway Intents)
- Verify bot has "Read Message History" permission

### Database errors
- Test connection: `python scripts/test_connection.py`
- Check Supabase URL and key in `.env`
- Verify RLS policies are enabled (see `docs/database/enable_rls.sql`)

### Together.ai errors
- Test API: `python scripts/test_together.py`
- Verify API key is valid
- Check API rate limits

---

## 📚 Documentation

- **[PRD.md](docs/PRD.md)** - Product Requirements Document
- **[STATUS.md](docs/STATUS.md)** - Current project status
- **[CHANGELOG.md](docs/CHANGELOG.md)** - Version history and updates
- **[SYSTEM_SPECIFICATION.md](docs/references/SYSTEM_SPECIFICATION.md)** - Technical architecture
- **[IMPLEMENTATION_GUIDE.md](docs/references/IMPLEMENTATION_GUIDE.md)** - Build guide

### TGE Analysis System
- **[5-Agent System README](.claude/agents/README.md)** - Complete agent workflow guide
- **[MONAD Analysis Report](reports/MONAD_TGE_ANALYSIS_2025-11-06.md)** - First real-world analysis
- **[HTML Report Viewer](reports/report-viewer.html)** - Apple-style interactive viewer

---

## 🔒 Security

- ✅ Row Level Security (RLS) enabled on Supabase
- ✅ API keys stored in `.env` (not in git)
- ✅ Virtual environment for dependency isolation
- ✅ Minimal bot permissions (read/send messages only)

---

## 📈 Success Metrics (6-Week MVP Target)

| Metric | Target |
|--------|--------|
| Discord capture rate | 95%+ of mentions |
| Researcher attribution | 80%+ accuracy |
| Research time saved | 4 hours → 2.5 hours/day |
| Execution rate | 50% → 65%+ |
| Monthly income | $2,500 → $3,500+ |

---

## 🤝 Contributing

This is a personal project for David's crypto trading. Not currently accepting external contributions.

---

## 📝 License

See [LICENSE](docs/references/LICENSE)

---

**Built with**: Python, Discord.py, Supabase (PostgreSQL + pgvector), Together.ai (LLM + Embeddings)
