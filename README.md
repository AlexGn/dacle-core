# DACLE - David's Automated Crypto Learning Engine

**Discord bot that monitors crypto project mentions and builds a knowledge base for trading decisions**

---

## 🚀 Quick Start

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

## 📊 Current Features (Week 1-2 MVP)

### Message Monitoring
- Monitors Discord messages 24/7
- Tracks 3 key researchers: Austin, Phobia, Sebastien
- Extracts project names and symbols automatically
- Stores context of what was said

### AI-Powered Extraction
- Uses Together.ai LLM (Llama 3.3 70B) to identify crypto projects
- Generates embeddings (BGE 1024d) for semantic search
- Detects sentiment (positive/neutral/negative)

### Database Storage
- All mentions stored in Supabase
- Project records with embeddings
- Researcher attribution
- Full context preservation

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
