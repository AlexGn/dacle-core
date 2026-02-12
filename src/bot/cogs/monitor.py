"""
Message Monitor Cog
Monitors Discord messages for crypto project mentions and stores them in Supabase

Session 261: Together.ai is DEPRECATED - embedding features disabled.
"""

import re
from collections import deque
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord.ext import commands

# Session 261: Together.ai DEPRECATED - make import optional
try:
    from src.ai.together_client import get_together_client
    TOGETHER_AVAILABLE = True
except ImportError:
    get_together_client = None  # type: ignore
    TOGETHER_AVAILABLE = False

from src.knowledge.knowledge_base import KnowledgeBase
from src.knowledge.supabase_client import get_knowledge_base
from src.scoring.mention_conviction_scorer import MentionConvictionScorer
from src.utils.logger import get_logger
from src.orchestration.trade_workflow import full_pipeline
import asyncio
from src.bot.cogs.analysis_formatter import AnalysisFormatter
from src.bot.cogs.analysis_views import TradeApprovalView
from src.agent.reasoning.evolver import CapabilityEvolver
from api.routers.macro import get_btc_regime_widget

logger = get_logger(__name__)


class EvolutionApprovalView(discord.ui.View):
    """View for approving autonomous capability evolution."""

    def __init__(self, spec: Dict[str, Any]):
        super().__init__(timeout=3600)  # 1 hour timeout
        self.spec = spec

    @discord.ui.button(label="Approve Evolution", style=discord.ButtonStyle.green, emoji="✅")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        
        try:
            from src.agent.reasoning.evolver import CapabilityEvolver
            from src.agent.reasoning.deployer import SandboxDeployer
            import os
            
            evolver = CapabilityEvolver()
            scaffold = evolver.scaffold_capability(self.spec)
            
            project_root = str(Path(__file__).parent.parent.parent.parent)
            deployer = SandboxDeployer(project_root)
            
            # Deploy
            res = deployer.deploy(scaffold)
            
            if res["status"] == "SUCCESS":
                msg = (
                    f"✅ **EVOLUTION COMPLETE**\n\n"
                    f"I have successfully built and deployed `{self.spec['name']}`.\n\n"
                    f"**Steps taken:**\n" + "\n".join(f"• {s}" for s in res["steps"]) + "\n\n"
                    f"Tests passed! The new tool is now live in my sandbox. You can try asking about it now."
                )
            else:
                msg = f"❌ **EVOLUTION FAILED**: {res.get('status')}\n\n```\n{res.get('test_output', 'Unknown error')[:500]}\n```"
                
            await interaction.followup.send(msg)
            self.stop()
            
        except Exception as e:
            logger.error(f"Evolution failed: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Critical evolution error: {str(e)}")


class MessageMonitor(commands.Cog):
    """
    Cog for monitoring Discord messages and extracting project mentions
    """

    # Known researchers (fallback when DB is unavailable)
    DEFAULT_RESEARCHERS = {
        "austin": {"name": "Austin", "tier": 1},
        "phobia": {"name": "Phobia", "tier": 1},
        "sebastien": {"name": "Sebastien", "tier": 1},
        "seb": {"name": "Sebastien", "tier": 1},  # Alias
        "davt97": {"name": "Davt97", "tier": 1},
    }

    # Message context settings
    CONTEXT_WINDOW_SECONDS = 30  # Look back 30 seconds for related messages
    MAX_CACHED_MESSAGES_PER_USER = 5  # Keep last 5 messages per user

    def __init__(self, bot: commands.Bot):
        """Initialize the monitor cog"""
        self.bot = bot
        # Session 261: Together.ai deprecated - make optional
        self.together = get_together_client() if TOGETHER_AVAILABLE and get_together_client else None
        self.kb = get_knowledge_base()

        self.researchers = self._load_researchers()

        # Initialize conviction scorer with knowledge base
        knowledge_base = KnowledgeBase(supabase_client=self.kb, together_client=self.together)
        tiers = {}
        for info in self.researchers.values():
            name = info.get("name")
            if name and name not in tiers:
                tiers[name] = int(info.get("tier", 1))
        self.conviction_scorer = MentionConvictionScorer(
            together_client=self.together,
            knowledge_base=knowledge_base,
            researcher_tiers=tiers,
        )

        # Message cache: {user_id: deque([(timestamp, content), ...])}
        self.message_cache: Dict[int, deque] = {}
        # Thread setup cache: {thread_id: {"symbol": str, "direction": str, "entry": float, "sl": float, "target": float}}
        self.thread_setups: Dict[int, Dict[str, Any]] = {}
        # Track whether a final decision was posted for a thread
        self.thread_decision_sent: Dict[int, bool] = {}

        logger.info("MessageMonitor cog initialized with conviction scoring")

    def _load_researchers(self) -> Dict[str, Dict[str, str]]:
        """
        Load researchers from DB if available, otherwise fall back to defaults.
        """
        researchers = dict(self.DEFAULT_RESEARCHERS)
        try:
            db_researchers = self.kb.list_researchers()
            for r in db_researchers:
                name = (r.get("name") or "").strip()
                if not name:
                    continue
                key = name.lower()
                researchers[key] = {"name": name, "tier": 1}
                discord_username = (r.get("discord_username") or "").strip()
                if discord_username:
                    researchers[discord_username.lower()] = {"name": name, "tier": 1}
        except Exception as e:
            logger.warning(f"Failed to load researchers from DB, using defaults: {e}")

        return researchers

    def _cache_message(self, user_id: int, content: str):
        """
        Cache a message for context aggregation

        Args:
            user_id: Discord user ID
            content: Message content
        """
        if user_id not in self.message_cache:
            self.message_cache[user_id] = deque(maxlen=self.MAX_CACHED_MESSAGES_PER_USER)

        # Add message with timestamp
        self.message_cache[user_id].append((datetime.now(), content))

    def _get_recent_context(
        self, user_id: int, current_message: str, time_window_seconds: int = None
    ) -> str:
        """
        Get aggregated context from recent messages by the same user

        Args:
            user_id: Discord user ID
            current_message: Current message content
            time_window_seconds: Time window to look back (default: CONTEXT_WINDOW_SECONDS)

        Returns:
            Aggregated message content from recent context
        """
        if time_window_seconds is None:
            time_window_seconds = self.CONTEXT_WINDOW_SECONDS

        # Start with current message
        aggregated_content = current_message

        # Check if user has cached messages
        if user_id not in self.message_cache:
            return aggregated_content

        # Get cutoff time
        cutoff_time = datetime.now() - timedelta(seconds=time_window_seconds)

        # Collect recent messages within time window (excluding current one)
        recent_messages = []
        for timestamp, content in self.message_cache[user_id]:
            if timestamp >= cutoff_time and content != current_message:
                recent_messages.append(content)

        # If we have recent messages (other than current), prepend them
        if recent_messages:
            logger.info(
                f"Found {len(recent_messages)} recent message(s) from user {user_id} "
                f"within {time_window_seconds}s window"
            )
            # Combine: older messages first, then current message
            aggregated_content = "\n".join(recent_messages + [current_message])

        return aggregated_content

    def _cleanup_old_messages(self):
        """
        Clean up messages older than 5 minutes from cache to prevent memory bloat
        """
        cutoff_time = datetime.now() - timedelta(minutes=5)
        cleaned_users = []

        for user_id, messages in self.message_cache.items():
            # Filter out old messages
            messages_to_keep = deque(
                [(ts, content) for ts, content in messages if ts >= cutoff_time],
                maxlen=self.MAX_CACHED_MESSAGES_PER_USER,
            )

            if len(messages_to_keep) < len(messages):
                self.message_cache[user_id] = messages_to_keep
                cleaned_users.append(user_id)

        if cleaned_users:
            logger.debug(f"Cleaned old messages from {len(cleaned_users)} user(s)")

    def _detect_researcher(self, author_name: str, content: str) -> Optional[str]:
        """
        Detect which researcher sent the message

        Args:
            author_name: Discord username
            content: Message content

        Returns:
            Researcher name if detected, None otherwise
        """
        # First check for hashtag in message (e.g., #austin, #phobia)
        content_lower = content.lower()
        for key, info in self.researchers.items():
            if f"#{key}" in content_lower:
                logger.info(f"Detected researcher via hashtag: #{key} -> {info['name']}")
                return info["name"]

        # Also check for @mention (e.g., @austin, @phobia)
        # Note: @ alone is enough, as Discord converts @austin to <@USER_ID> but user might type it
        for key, info in self.researchers.items():
            if f"@{key}" in content_lower:
                logger.info(f"Detected researcher via @mention: @{key} -> {info['name']}")
                return info["name"]

        # Then check username
        author_lower = author_name.lower()
        for key, info in self.researchers.items():
            if key in author_lower:
                logger.info(f"Detected researcher via username: {author_name} -> {info['name']}")
                return info["name"]

        return None

    def _extract_crypto_symbol(self, text: str) -> Optional[str]:
        """
        Extract crypto symbol from text (e.g., $BTC, $ETH)

        Args:
            text: Message text

        Returns:
            Symbol without $, or None
        """
        # Match $SYMBOL pattern
        match = re.search(r"\$([A-Z]{2,10})", text)
        if match:
            return match.group(1)
        return None

    async def _extract_projects_with_ai(self, content: str) -> List[Dict[str, str]]:
        """
        Use Together.ai LLM to extract project mentions from message

        Args:
            content: Message content

        Returns:
            List of dicts with 'name', 'symbol', 'sentiment'
        """
        try:
            prompt = f"""Extract cryptocurrency project mentions from this message.
Return ONLY valid JSON array with this exact format:
[{{"name": "Project Name", "symbol": "SYMBOL", "sentiment": "positive/neutral/negative"}}]

Message: {content}

If no crypto projects mentioned, return: []
"""

            messages = [
                {
                    "role": "system",
                    "content": "You are a crypto project extractor. Return only valid JSON arrays.",
                },
                {"role": "user", "content": prompt},
            ]

            response = self.together.chat_completion(messages, temperature=0.3, max_tokens=300)

            # Parse JSON response
            import json

            projects = json.loads(response.strip())

            # Validate it's a list
            if isinstance(projects, list):
                return projects
            else:
                logger.warning(f"AI returned non-list: {response}")
                return []

        except Exception as e:
            logger.error(f"Error extracting projects with AI: {e}")
            return []

    def _extract_projects_with_regex(self, content: str) -> List[Dict[str, str]]:
        """
        Regex-based fallback for extracting symbols from trade messages.
        Supports patterns like $DUSK, $DUSK/USDT, and infers direction.
        """
        projects = []
        symbols = re.findall(r"\$([A-Z0-9]{2,10})(?:/USDT|/USD|/USDC)?", content.upper())
        if not symbols:
            return projects

        direction = "neutral"
        if "SHORT" in content.upper():
            direction = "negative"
        elif "LONG" in content.upper():
            direction = "positive"

        for sym in symbols:
            projects.append({"name": sym, "symbol": sym, "sentiment": direction})
        return projects

    def _parse_trade_setup(self, content: str) -> Optional[Dict[str, Any]]:
        """
        Parse entry/SL/target and direction from a trade setup message.
        Returns dict with entry, sl, target, direction if found.
        """
        text = content.strip()
        if not text:
            return None

        upper = text.upper()
        direction = None
        if "SHORT" in upper:
            direction = "SHORT"
        elif "LONG" in upper:
            direction = "LONG"

        def _extract_first(patterns: List[str]) -> Optional[float]:
            for pat in patterns:
                m = re.search(pat, text, flags=re.IGNORECASE | re.MULTILINE)
                if m:
                    try:
                        return float(m.group(1))
                    except ValueError:
                        continue
            return None

        entry_patterns = [
            r"entry\s*:\s*(?:limit\s*)?\$?([0-9]*\.?[0-9]+)",
            r"entry\s*point\s*:\s*(?:limit\s*)?\$?([0-9]*\.?[0-9]+)",
        ]
        sl_patterns = [
            r"stop\s*loss\s*:\s*\$?([0-9]*\.?[0-9]+)",
            r"\bsl\s*:\s*\$?([0-9]*\.?[0-9]+)",
        ]
        target_patterns = [
            r"final\s*target\s*:\s*\$?([0-9]*\.?[0-9]+)",
            r"target\s*:\s*(?:final\s*)?\$?([0-9]*\.?[0-9]+)",
            r"\btp1\s*:\s*\$?([0-9]*\.?[0-9]+)",
        ]

        entry = _extract_first(entry_patterns)
        sl = _extract_first(sl_patterns)
        target = _extract_first(target_patterns)

        if entry and sl and target and direction:
            return {
                "entry": entry,
                "sl": sl,
                "target": target,
                "direction": direction,
            }

        return None

    def _is_trade_setup(self, content: str) -> bool:
        """
        Heuristic detection of a structured trade setup post.
        """
        text = content.lower()
        has_symbol = bool(re.search(r"\$[a-z0-9]{2,10}", text))
        has_entry = "entry" in text
        has_stop = "stop" in text and ("loss" in text or "sl" in text)
        has_target = "target" in text or "tp" in text
        has_direction = "short" in text or "long" in text
        return has_symbol and has_entry and has_stop and has_target and has_direction

    def _is_bqs_followup(self, content: str) -> bool:
        """
        Detects a follow-up BQS/TA summary message posted in the thread.
        """
        upper = content.upper()
        return ("BREAKOUT QUALITY" in upper) or ("BQS" in upper)

    def _parse_bq_breakdown(self, content: str) -> Dict[str, Any]:
        """
        Parse a BQ/TA breakdown message for summary stats.
        Returns dict with overall score, grade, and component scores when present.
        """
        text = content
        parsed: Dict[str, Any] = {"components": {}}

        overall_match = re.search(
            r"breakout\s+quality\s*:?[\s\r\n]*([0-9]+(?:\.[0-9]+)?)\s*/\s*([0-9]+(?:\.[0-9]+)?)",
            text,
            flags=re.IGNORECASE,
        )
        if overall_match:
            parsed["overall_score"] = float(overall_match.group(1))
            parsed["overall_max"] = float(overall_match.group(2))

        grade_match = re.search(r"\(([A-C][+-]?)\)", text)
        if grade_match:
            parsed["grade"] = grade_match.group(1)

        component_patterns = {
            "Technical Quality": r"technical\s+quality\s*:?[\s\r\n]*([0-9]+(?:\.[0-9]+)?)\s*/\s*([0-9]+(?:\.[0-9]+)?)",
            "POC Alignment": r"poc\s+alignment\s*:?[\s\r\n]*([0-9]+(?:\.[0-9]+)?)\s*/\s*([0-9]+(?:\.[0-9]+)?)",
            "Timing Precision": r"timing\s+precision\s*:?[\s\r\n]*([0-9]+(?:\.[0-9]+)?)\s*/\s*([0-9]+(?:\.[0-9]+)?)",
            "Risk Management": r"risk\s+management\s*:?[\s\r\n]*([0-9]+(?:\.[0-9]+)?)\s*/\s*([0-9]+(?:\.[0-9]+)?)",
            "Macro Context": r"macro\s+context\s*:?[\s\r\n]*([0-9]+(?:\.[0-9]+)?)\s*/\s*([0-9]+(?:\.[0-9]+)?)",
        }

        for label, pattern in component_patterns.items():
            m = re.search(pattern, text, flags=re.IGNORECASE)
            if m:
                try:
                    parsed["components"][label] = (float(m.group(1)), float(m.group(2)))
                except ValueError:
                    continue

        return parsed

    def _build_bq_summary(self, content: str, pre_trade_approved: Optional[bool]) -> str:
        """
        Build a concise BQ summary for the thread follow-up.
        """
        parsed = self._parse_bq_breakdown(content)
        if not parsed.get("overall_score") and not parsed.get("components"):
            return ""

        parts = ["Thanks for the detailed BQ breakdown."]

        overall_score = parsed.get("overall_score")
        overall_max = parsed.get("overall_max")
        grade = parsed.get("grade")
        if overall_score is not None and overall_max is not None:
            overall_text = f"Overall score is {overall_score:.2f}/{overall_max:.0f}"
            if grade:
                overall_text += f" ({grade})"
            parts.append(overall_text + ".")

        components = parsed.get("components", {})
        if components:
            comp_text = []
            for label in [
                "Technical Quality",
                "Timing Precision",
                "POC Alignment",
                "Risk Management",
                "Macro Context",
            ]:
                if label in components:
                    score, max_score = components[label]
                    comp_text.append(f"{label} {score:.1f}/{max_score:.0f}")
            if comp_text:
                parts.append("Key components: " + ", ".join(comp_text) + ".")

        if pre_trade_approved is True:
            parts.append("This adds to the earlier pre‑trade approval and reinforces the setup.")

        return " ".join(parts)

    async def _recover_thread_setup(self, thread: discord.Thread) -> Optional[Dict[str, Any]]:
        """
        Attempt to reconstruct trade setup from recent thread history.
        Useful if the bot restarted and in-memory cache is gone.
        """
        try:
            async for msg in thread.history(limit=25, oldest_first=False):
                if not msg or not msg.content:
                    continue
                setup = self._parse_trade_setup(msg.content)
                if setup:
                    return setup
        except Exception as e:
            logger.warning(f"Failed to recover thread setup for {thread.id}: {e}")
        return None

    async def _store_mention(
        self,
        project_name: str,
        researcher_name: str,
        content: str,
        symbol: Optional[str] = None,
        trigger_channel: Optional[discord.abc.Messageable] = None,
        force_trigger: bool = False,
        trade_setup: Optional[Dict[str, Any]] = None,
        trigger_message: Optional[discord.Message] = None,
    ):
        """
        Store project mention in Supabase with conviction scoring

        Args:
            project_name: Name of the project
            researcher_name: Who mentioned it
            content: What they said
            symbol: Project symbol (optional)
        """
        try:
            # Session 345: Deduplicate by symbol first (more reliable), then by name
            project = None
            if symbol:
                project = self.kb.get_project_by_symbol(symbol)
            
            if not project:
                project = self.kb.get_project_by_name(project_name)
                
            if not project:
                logger.info(f"Creating new project: {project_name} ({symbol or 'NO SYMBOL'})")
                project = self.kb.create_project(
                    name=project_name,
                    symbol=symbol,
                    data={"source": "discord"},
                    first_mentioned_at=datetime.now(),
                )
            elif symbol and not project.get("symbol"):
                # Update existing project with symbol if missing
                logger.info(f"Updating project {project_name} with symbol {symbol}")
                self.kb.update_project(project["id"], {"symbol": symbol})

            # Get researcher
            researcher = self.kb.get_researcher_by_name(researcher_name)
            if not researcher:
                if force_trigger:
                    try:
                        researcher = self.kb.create_researcher(
                            name=researcher_name,
                            discord_username=researcher_name,
                            data={"source": "discord_auto"},
                        )
                        logger.info(f"Created researcher on the fly: {researcher_name}")
                    except Exception as e:
                        logger.warning(f"Failed to create researcher {researcher_name}: {e}")
                        return
                else:
                    logger.warning(f"Researcher not found: {researcher_name}")
                    return

            # Calculate conviction score using AI + knowledge base
            logger.info(f"🤖 Calculating conviction score for {project_name}...")
            conviction_score = self.conviction_scorer.score_mention(
                message_text=content,
                researcher_name=researcher_name,
                project_name=project_name,
                project_symbol=symbol,
            )

            logger.info(
                f"📊 Conviction: {conviction_score.final_score}/10 "
                f"(confidence: {conviction_score.confidence}, "
                f"position: {conviction_score.position_size}%)"
            )

            # Prepare conviction data for database
            conviction_data = {
                "conviction_score": float(conviction_score.final_score),
                "conviction_confidence": float(conviction_score.confidence),
                "position_size": float(conviction_score.position_size),
                "conviction_signals": conviction_score.conviction_signals,
                "red_flags": conviction_score.red_flags,
                "green_flags": conviction_score.green_flags,
                "knowledge_citations": conviction_score.knowledge_citations,
                "scoring_reasoning": conviction_score.reasoning,
            }

            # Create mention record with conviction scoring
            mention = self.kb.create_project_mention(
                project_id=project["id"],
                researcher_id=researcher["id"],
                mentioned_at=datetime.now(),
                context=content[:500],  # Limit context length
                source="discord",
                data=conviction_data,
            )

            # Generate and store embedding for semantic search (if available)
            if self.together and hasattr(self.together, "generate_embedding"):
                embedding = self.together.generate_embedding(f"{project_name}: {content[:300]}")
                self.kb.update_project(project["id"], {"embedding": embedding})
            else:
                logger.debug("Skipping embedding generation (Together.ai not available)")

            logger.info(
                f"✅ Stored mention: {project_name} by {researcher_name} "
                f"(mention ID: {mention['id']}, score: {conviction_score.final_score}/10)"
            )

            # Session 334: Automatic Workflow Trigger for high-conviction mentions
            if (conviction_score.final_score >= 7.0 or force_trigger) and symbol:
                async def run_and_report(target_channel: Optional[discord.abc.Messageable] = None):
                    # Session 345: Deduplication - skip only when NOT a structured trade setup
                    if not trade_setup:
                        from src.orchestration.trade_workflow import _get_token_dir
                        import json
                        from datetime import datetime, timezone

                        token_dir = _get_token_dir(symbol)
                        consolidated_path = token_dir / "consolidated.json"
                        
                        if consolidated_path.exists():
                            try:
                                with open(consolidated_path) as f:
                                    data = json.load(f)
                                    ts_str = data.get("analysis_timestamp")
                                    if ts_str:
                                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                                        age_hours = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
                                        if age_hours < 12:
                                            logger.info(f"⏭️ Skipping redundant trigger for {symbol} (Analyzed {age_hours:.1f}h ago)")
                                            if target_channel:
                                                await target_channel.send(
                                                    f"ℹ️ **Recent Analysis Exists**: {symbol} was analyzed `{age_hours:.1f}h` ago. "
                                                    f"Check `#analysis-updates` for the candidate report."
                                                )
                                            return
                            except Exception as e:
                                logger.warning(f"Failed to check staleness for {symbol}: {e}")

                    loop = asyncio.get_event_loop()

                    # If this is a structured trade setup, run execution pre-trade check
                    if trade_setup:
                        logger.info(f"🚀 Running pre-trade check for {symbol} from #trades setup...")

                        def _call_pre_trade():
                            import requests
                            import os
                            payload = {
                                "token": symbol,
                                "direction": trade_setup["direction"],
                                "entry": trade_setup["entry"],
                                "sl": trade_setup["sl"],
                                "target": trade_setup["target"],
                            }
                            api_base = os.getenv("DACLE_API_URL", "http://localhost:8000")
                            resp = requests.post(
                                f"{api_base}/api/execution/pre-trade-check",
                                json=payload,
                                timeout=20,
                            )
                            return resp.status_code, resp.json()

                        try:
                            status_code, data = await loop.run_in_executor(None, _call_pre_trade)
                        except Exception as e:
                            logger.warning(f"Pre-trade API unreachable for {symbol}: {e}")
                            fallback_msg = (
                                "⚠️ **Execution API unavailable** — unable to run pre‑trade check right now. "
                                "Please retry in a few minutes or notify Alex."
                            )
                            if target_channel:
                                await target_channel.send(fallback_msg)
                            return
                        if status_code == 200 and isinstance(data, dict):
                            formatted = data.get("data", {}).get("formatted_response")
                            if formatted:
                                final_target = None
                                trades_channel_id = 1468948950412431598
                                if isinstance(target_channel, discord.Thread):
                                    final_target = target_channel
                                elif (
                                    target_channel
                                    and getattr(target_channel, "name", "") == "trades"
                                    or (getattr(target_channel, "id", None) == trades_channel_id)
                                ):
                                    # For #trades, reply in a thread if possible
                                    if trigger_message:
                                        try:
                                            thread_name = f"{symbol} {trade_setup['direction']} setup"
                                            final_target = await trigger_message.create_thread(
                                                name=thread_name,
                                                auto_archive_duration=1440,
                                            )
                                        except Exception as e:
                                            logger.warning(f"Failed to create thread for {symbol}: {e}")
                                            final_target = target_channel
                                    else:
                                        final_target = target_channel

                                if final_target:
                                    # Cache setup for thread follow-up decision
                                    if isinstance(final_target, discord.Thread):
                                        self.thread_setups[final_target.id] = {
                                            "symbol": symbol,
                                            "direction": trade_setup["direction"],
                                            "entry": trade_setup["entry"],
                                            "sl": trade_setup["sl"],
                                            "target": trade_setup["target"],
                                            "pre_trade_approved": data.get("data", {}).get("approved", False),
                                        }
                                        self.thread_decision_sent[final_target.id] = False
                                    logger.info(f"📤 Sending pre-trade check summary for {symbol} to trades")
                                    if len(formatted) <= 1900:
                                        await final_target.send(formatted)
                                    else:
                                        # Split long responses safely by lines
                                        chunk = []
                                        total = 0
                                        for line in formatted.splitlines():
                                            if total + len(line) + 1 > 1900:
                                                await final_target.send("\n".join(chunk))
                                                chunk = [line]
                                                total = len(line) + 1
                                            else:
                                                chunk.append(line)
                                                total += len(line) + 1
                                        if chunk:
                                            await final_target.send("\n".join(chunk))
                                    return
                        logger.warning(f"Pre-trade check failed for {symbol} (status={status_code}); falling back to pipeline")

                    logger.info(f"🚀 Triggering full pipeline for {symbol}...")
                    result = await loop.run_in_executor(
                        None,
                        lambda: full_pipeline(symbol=symbol, force_refresh=True, notify_discord=False)
                    )
                    
                    
                    # Target channel determination:
                    # 1. Use trigger channel if it's a thread (stays in thread)
                    # 2. Otherwise fallback to #analysis-updates
                    final_target = None
                    if target_channel:
                         logger.info(f"🔍 DEBUG: run_and_report target_channel type: {type(target_channel)}, ID: {target_channel.id}")

                    trades_channel_id = 1468948950412431598
                    if isinstance(target_channel, discord.Thread):
                        final_target = target_channel
                    elif (
                        target_channel
                        and getattr(target_channel, "name", "") == "trades"
                        or (getattr(target_channel, "id", None) == trades_channel_id)
                    ):
                        final_target = target_channel
                    else:
                        from src.utils.config import get_discord_config
                        discord_cfg = get_discord_config()
                        # Fallback to known #analysis-updates ID if not in config
                        analysis_channel_id = discord_cfg.analysis_channel_id or 1470403542253703369
                        final_target = self.bot.get_channel(analysis_channel_id)

                    if final_target:
                        logger.info(f"📤 Sending rich candidate report for {symbol} to {final_target}")
                        
                        # Fetch macro data for the report
                        try:
                            macro = await get_btc_regime_widget()
                        except Exception as e:
                            logger.warning(f"Failed to fetch macro data for report: {e}")
                            macro = None

                        embed = AnalysisFormatter.format_candidate_embed(result, macro)
                        view = TradeApprovalView(symbol, result.conviction_score)
                        await final_target.send(embed=embed, view=view)
                    else:
                        logger.warning(f"❌ Could not find target channel for report")

                # Launch as task
                asyncio.create_task(run_and_report(trigger_channel))

            # Log conviction details for visibility
            if conviction_score.conviction_signals:
                logger.info(f"  Signals: {', '.join(conviction_score.conviction_signals[:2])}")
            if conviction_score.green_flags:
                logger.info(f"  ✅ Green: {', '.join(conviction_score.green_flags[:2])}")
            if conviction_score.red_flags:
                logger.warning(f"  🚩 Red: {', '.join(conviction_score.red_flags[:2])}")

        except Exception as e:
            logger.error(f"Error storing mention for {project_name} by {researcher_name}: {e}")
            logger.exception("Full traceback:")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        Listen to all messages and extract project mentions

        Args:
            message: Discord message object
        """
        # Ignore bot messages
        if message.author.bot:
            return

        # Log ALL messages for debugging
        logger.info(
            f"📨 Message received from {message.author.name} in "
            f"{'#' + message.channel.name if message.guild else 'DM'}: {message.content[:100]}"
        )

        # Only process messages from configured private server
        if not message.guild or message.guild.id != self.bot.private_server_id:
            logger.warning(
                f"⚠️  Message NOT from private server (guild_id: {message.guild.id if message.guild else 'None'})"
            )
            return

        logger.info(f"✅ Message is from private server, processing for mentions")

        # Extract actual message content (handle forwarded messages)
        message_content = message.content

        # Skip forwarded messages - researchers will use copy-paste workflow
        if not message_content.strip() and message.flags.value & 16384:
            logger.debug(
                f"Ignoring forwarded message from {message.author.name} (no content accessible)"
            )
            return

        # Try to get referenced message content (for replies, not forwards)
        # Note: Forwards from other servers will fail with 404 - this is expected
        if message.reference and not message_content.strip() and not (message.flags.value & 16384):
            try:
                # Only try for non-forwarded messages (replies)
                if message.reference.resolved:
                    referenced_msg = message.reference.resolved
                    if referenced_msg and referenced_msg.content:
                        message_content = referenced_msg.content
                        logger.info(
                            f"📨 Extracted content from reply reference: {message_content[:100]}"
                        )
            except Exception as e:
                logger.debug(f"Could not extract referenced message content: {e}")

        # If still no content, check embeds (forwarded messages might use embeds)
        if not message_content.strip() and message.embeds:
            for embed in message.embeds:
                if embed.description:
                    message_content = embed.description
                    logger.info(f"📎 Extracted content from embed: {message_content[:100]}")
                    break

        # Note: Forwarded messages (flag 16384) are ignored - researchers use copy-paste workflow

        # Cache this message for context aggregation (use extracted content)
        self._cache_message(message.author.id, message_content)

        # Periodically clean up old messages (every ~20th message)
        import random

        if random.randint(1, 20) == 1:
            self._cleanup_old_messages()

        # Detect researcher (check both username and content - use extracted content)
        researcher_name = self._detect_researcher(message.author.name, message_content)

        # Allow structured trade setups in #trades even if researcher is unknown
        trades_channel_id = 1468948950412431598
        is_trades_channel = (
            getattr(message.channel, "name", "") == "trades" or message.channel.id == trades_channel_id
        )
        if not researcher_name and not is_trades_channel:
            return

        logger.info(
            f"📨 Message from {researcher_name} in #{message.channel.name}: {message_content[:100]}"
        )

        # If this is a follow-up message inside a thread, and we have a cached setup,
        # produce a single final enter/skip decision (no repetition).
        if isinstance(message.channel, discord.Thread):
            thread_id = message.channel.id
            if self._is_bqs_followup(message_content) and thread_id in self.thread_setups:
                if not self.thread_decision_sent.get(thread_id, False):
                    setup = self.thread_setups[thread_id]
                    try:
                        bq_summary = self._build_bq_summary(
                            message_content,
                            pre_trade_approved=setup.get("pre_trade_approved"),
                        )
                        import requests
                        import os
                        payload = {
                            "token": setup["symbol"],
                            "direction": setup["direction"],
                            "entry": setup["entry"],
                            "sl": setup["sl"],
                            "target": setup["target"],
                        }
                        api_base = os.getenv("DACLE_API_URL", "http://localhost:8000")
                        resp = requests.post(
                            f"{api_base}/api/execution/full-analysis",
                            json=payload,
                            timeout=20,
                        )
                        if resp.status_code == 200:
                            data = resp.json().get("data", {})
                            approved = data.get("approved", False)
                            signal = data.get("signal", "UNKNOWN")
                            decision_line = (
                                f"✅ **ENTER** — {signal}"
                                if approved
                                else f"⛔ **SKIP** — {signal}"
                            )
                            combined = (
                                f"{bq_summary}\n\n{decision_line}"
                                if bq_summary
                                else decision_line
                            )
                            await message.channel.send(combined)
                            self.thread_decision_sent[thread_id] = True
                            return
                        if bq_summary:
                            await message.channel.send(
                                f"{bq_summary}\n\n⚠️ Final decision unavailable — API error."
                            )
                            self.thread_decision_sent[thread_id] = True
                        logger.warning(f"Full-analysis failed for thread {thread_id}: {resp.status_code}")
                    except Exception as e:
                        logger.warning(f"Full-analysis exception for thread {thread_id}: {e}")
                        await message.channel.send(
                            "⚠️ **Execution API unavailable** — unable to post final decision. "
                            "Please retry in a few minutes or notify Alex."
                        )
            elif self._is_bqs_followup(message_content):
                if not self.thread_decision_sent.get(thread_id, False):
                    recovered = await self._recover_thread_setup(message.channel)
                    if recovered:
                        self.thread_setups[thread_id] = recovered
                        self.thread_decision_sent[thread_id] = False
                        # Re-run follow-up logic now that we recovered setup
                        try:
                            bq_summary = self._build_bq_summary(
                                message_content,
                                pre_trade_approved=recovered.get("pre_trade_approved"),
                            )
                            import requests
                            import os
                            payload = {
                                "token": recovered["symbol"],
                                "direction": recovered["direction"],
                                "entry": recovered["entry"],
                                "sl": recovered["sl"],
                                "target": recovered["target"],
                            }
                            api_base = os.getenv("DACLE_API_URL", "http://localhost:8000")
                            resp = requests.post(
                                f"{api_base}/api/execution/full-analysis",
                                json=payload,
                                timeout=20,
                            )
                            if resp.status_code == 200:
                                data = resp.json().get("data", {})
                                approved = data.get("approved", False)
                                signal = data.get("signal", "UNKNOWN")
                                decision_line = (
                                    f"✅ **ENTER** — {signal}"
                                    if approved
                                    else f"⛔ **SKIP** — {signal}"
                                )
                                combined = (
                                    f"{bq_summary}\n\n{decision_line}"
                                    if bq_summary
                                    else decision_line
                                )
                                await message.channel.send(combined)
                                self.thread_decision_sent[thread_id] = True
                                return
                            if bq_summary:
                                await message.channel.send(
                                    f"{bq_summary}\n\n⚠️ Final decision unavailable — API error."
                                )
                                self.thread_decision_sent[thread_id] = True
                            logger.warning(f"Full-analysis failed for recovered thread {thread_id}: {resp.status_code}")
                        except Exception as e:
                            logger.warning(f"Full-analysis exception for recovered thread {thread_id}: {e}")
                            await message.channel.send(
                                "⚠️ **Execution API unavailable** — unable to post final decision. "
                                "Please retry in a few minutes or notify Alex."
                            )

        # Get aggregated context (current message + recent messages from same user)
        aggregated_content = self._get_recent_context(message.author.id, message_content)
        trade_setup = self._parse_trade_setup(aggregated_content) if is_trades_channel else None
        force_trigger = is_trades_channel and (trade_setup is not None or self._is_trade_setup(aggregated_content))
        if force_trigger and not researcher_name:
            researcher_name = "Community"

        # For structured #trades posts, force a single canonical symbol to avoid
        # false positives from copied transcripts/usernames (e.g., "Davt97").
        trade_symbol = None
        if is_trades_channel and force_trigger:
            m = re.search(r"\$([A-Z0-9]{2,10})(?:/USDT|/USD|/USDC)?", aggregated_content.upper())
            if m:
                trade_symbol = m.group(1)

        # Log if we're using context from multiple messages
        if aggregated_content != message_content:
            logger.info(
                f"🔗 Using aggregated context from multiple messages "
                f"(length: {len(aggregated_content)} chars)"
            )

        if trade_symbol:
            direction = "neutral"
            if "SHORT" in aggregated_content.upper():
                direction = "negative"
            elif "LONG" in aggregated_content.upper():
                direction = "positive"
            projects = [{"name": trade_symbol, "symbol": trade_symbol, "sentiment": direction}]
        else:
            # Extract projects using AI (from aggregated content)
            if self.together:
                projects = await self._extract_projects_with_ai(aggregated_content)
            else:
                projects = self._extract_projects_with_regex(aggregated_content)

            if not projects:
                projects = self._extract_projects_with_regex(aggregated_content)

        if not projects:
            logger.debug(f"No projects found in message(s) from {researcher_name}")
            
            # Tier 6: Proactive Gap Detection (only if bot is mentioned)
            if self.bot.user.mentioned_in(message):
                # Clean mention from content for analysis
                clean_content = message_content.replace(f"<@{self.bot.user.id}>", "").replace(f"<@!{self.bot.user.id}>", "").strip().lower()
                
                # Blacklist common commands and greetings
                blacklist = ["sync", "help", "hello", "hi", "hey", "what up", "status", "test"]
                if any(word == clean_content or clean_content.startswith(f"{word} ") for word in blacklist):
                    return

                try:
                    evolver = CapabilityEvolver()
                    gap = evolver.analyze_gap(message_content)
                    
                    if gap["status"] == "NEW_CAPABILITY_NEEDED":
                        spec = evolver.generate_spec(gap)
                        proposal = evolver.propose_upgrade(spec)
                        view = EvolutionApprovalView(spec)
                        await message.reply(proposal, view=view)
                except Exception as e:
                    logger.error(f"Gap detection failed: {e}")
            return

        # Store each mention
        for project_info in projects:
            project_name = project_info.get("name")
            symbol = project_info.get("symbol")

            if project_name:
                logger.info(f"🔍 DEBUG: Trigger channel type: {type(message.channel)}, ID: {message.channel.id}")
                await self._store_mention(
                    project_name=project_name,
                    researcher_name=researcher_name,
                    content=aggregated_content,  # Store full context
                    symbol=symbol,
                    trigger_channel=message.channel,
                    force_trigger=force_trigger,
                    trade_setup=trade_setup,
                    trigger_message=message,
                )

        # No need to call process_commands in a listener
        pass


async def setup(bot: commands.Bot):
    """Setup function called by bot when loading this cog"""
    await bot.add_cog(MessageMonitor(bot))
    logger.info("MessageMonitor cog loaded")
