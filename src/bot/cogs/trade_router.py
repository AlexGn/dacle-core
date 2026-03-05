from src.utils.logger import get_logger
import json
import re
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
import os
from pathlib import Path
from typing import Optional, Dict, Any

from src.utils.redis_lms import get_current_price
from src.utils.lifecycle_id import generate_lifecycle_id
from src.utils.lifecycle_store import record_setup

logger = get_logger(__name__)

# Patterns ported from deploy/openclaw/trade-router/index.js
SYMBOL_PATTERN = re.compile(r"\$([A-Z0-9]{2,10})|([A-Z0-9]{2,10})(?=\/(?:USDT|USD|USDC))", re.IGNORECASE)
DIRECTION_PATTERN = re.compile(r"\b(Short|Long|SHORT|LONG)\b", re.IGNORECASE)
ENTRY_PATTERN = re.compile(r"entry(?:\s*point)?\s*:\s*(?:entry\s*:)?\s*(?:limit\s*)?\$?([0-9]*\.?[0-9]+)", re.IGNORECASE | re.MULTILINE)
SL_PATTERN = re.compile(r"(?:stop\s*loss|sl)\s*:\s*\$?([0-9]*\.?[0-9]+)", re.IGNORECASE | re.MULTILINE)
TARGET_PATTERN = re.compile(r"(?:final\s*target|target|tp1|tp)\s*:\s*\$?([0-9]*\.?[0-9]+)", re.IGNORECASE | re.MULTILINE)

# Score card patterns
SCORE_CARD_HEADER_PATTERN = re.compile(r"^\s*\$?([A-Z0-9]{2,10})\s+(SHORT|LONG)\b", re.IGNORECASE | re.MULTILINE)
ENTRY_SCORE_PATTERN = re.compile(r"entry(?:\s*score)?\s*:?\s*([0-9]+(?:\.[0-9]+)?)\s*\/\s*10", re.IGNORECASE | re.MULTILINE)
RR_SCORE_PATTERN = re.compile(r"r:r\s*([0-9]+(?:\.[0-9]+)?)\s*:\s*1", re.IGNORECASE | re.MULTILINE)

# Default trades channel from Node.js code
TRADES_CHANNEL_ID = 1468948950412431598

class TradeRouter(commands.Cog):
    """
    Deterministic Trade Router (Ported from Node.js)
    Listens for trade setups in #trades and runs pre-trade diagnostics.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.api_url = os.getenv("DACLE_API_URL", "http://localhost:8000")
        self.api_key = os.getenv("DACLE_API_KEY", "").strip()
        logger.info("TradeRouter cog initialized")

    @staticmethod
    def _allowed_user_ids() -> set[str]:
        raw = os.getenv("DISCORD_ALLOWED_USER_IDS", "").strip()
        allowed = {item.strip() for item in raw.split(",") if item.strip()} if raw else set()
        owner_id = os.getenv("DISCORD_OWNER_ID", "").strip()
        if owner_id:
            allowed.add(owner_id)
        return allowed

    @staticmethod
    def _account_acl() -> Dict[str, set[str]]:
        raw = os.getenv("DISCORD_ACCOUNT_ACL", "").strip()
        if not raw:
            return {}
        acl: Dict[str, set[str]] = {}
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                for account_id, users in loaded.items():
                    aid = str(account_id).strip()
                    if not aid:
                        continue
                    if isinstance(users, str):
                        user_set = {u.strip() for u in users.split(",") if u.strip()}
                    elif isinstance(users, (list, tuple, set)):
                        user_set = {str(u).strip() for u in users if str(u).strip()}
                    else:
                        user_set = set()
                    if user_set:
                        acl[aid] = user_set
                if acl:
                    return acl
        except Exception:
            pass

        # Fallback parser: "manual:111,222;autonomous:333"
        for block in raw.split(";"):
            chunk = block.strip()
            if not chunk or ":" not in chunk:
                continue
            account_id, users_raw = chunk.split(":", 1)
            aid = account_id.strip()
            users = {u.strip() for u in users_raw.split(",") if u.strip()}
            if aid and users:
                acl[aid] = users
        return acl

    @staticmethod
    def _resolve_account_id(account_id: Optional[str] = None) -> str:
        candidate = str(account_id or "").strip()
        if candidate:
            return candidate
        cog_default = str(os.getenv("DISCORD_TRADE_ROUTER_ACCOUNT_ID", "") or "").strip()
        if cog_default:
            return cog_default
        fallback = str(os.getenv("DISCORD_DEFAULT_ACCOUNT_ID", "primary") or "").strip()
        return fallback or "primary"

    def _is_authorized(self, user_id: int, *, account_id: Optional[str] = None) -> bool:
        allowed = self._allowed_user_ids()
        if not allowed:
            globally_allowed = True
        else:
            globally_allowed = str(user_id) in allowed
        if not globally_allowed:
            return False

        account_acl = self._account_acl()
        if not account_acl:
            # Backward compatibility: do not hard-lock commands if ACL is unconfigured.
            return True
        resolved_account = self._resolve_account_id(account_id)
        allowed_for_account = account_acl.get(resolved_account) or account_acl.get("*")
        if not allowed_for_account:
            # Backward compatibility: only enforce when account has an explicit ACL entry.
            return True
        return str(user_id) in allowed_for_account

    async def _deny_interaction(self, interaction: discord.Interaction) -> None:
        try:
            await interaction.response.send_message("⛔ Not authorized.", ephemeral=True)
        except Exception:
            await interaction.followup.send("⛔ Not authorized.", ephemeral=True)

    async def _safe_defer(
        self,
        interaction: discord.Interaction,
        *,
        ephemeral: bool,
        command_name: str,
    ) -> bool:
        """Best-effort defer to avoid hard failures on expired interactions."""
        try:
            await interaction.response.defer(ephemeral=ephemeral)
            return True
        except discord.NotFound:
            logger.warning(
                "Interaction expired before defer command=%s user_id=%s interaction_id=%s",
                command_name,
                getattr(getattr(interaction, "user", None), "id", None),
                getattr(interaction, "id", None),
            )
            return False
        except discord.HTTPException as e:
            # Already acknowledged by Discord; treat as deferred path.
            if getattr(e, "code", None) == 40060:
                return True
            logger.error(
                "Failed to defer interaction command=%s user_id=%s code=%s",
                command_name,
                getattr(getattr(interaction, "user", None), "id", None),
                getattr(e, "code", None),
            )
            return False
        except Exception as e:
            logger.error(
                "Unexpected defer failure command=%s user_id=%s err=%s",
                command_name,
                getattr(getattr(interaction, "user", None), "id", None),
                e,
            )
            return False

    async def _send_command_message(
        self,
        interaction: discord.Interaction,
        *,
        deferred: bool,
        content: Optional[str] = None,
        embed: Optional[discord.Embed] = None,
        view: Optional[discord.ui.View] = None,
        ephemeral: bool = False,
    ) -> None:
        """
        Send via interaction when possible; fallback to channel send on token expiry.
        """
        try:
            if deferred:
                await interaction.followup.send(content=content, embed=embed, view=view, ephemeral=ephemeral)
            else:
                await interaction.response.send_message(content=content, embed=embed, view=view, ephemeral=ephemeral)
            return
        except Exception as e:
            logger.warning(
                "Interaction send failed; falling back to channel send user_id=%s err=%s",
                getattr(getattr(interaction, "user", None), "id", None),
                e,
            )

        channel = interaction.channel
        if channel and hasattr(channel, "send"):
            kwargs: Dict[str, Any] = {}
            if content is not None:
                kwargs["content"] = content
            if embed is not None:
                kwargs["embed"] = embed
            if view is not None:
                kwargs["view"] = view
            await channel.send(**kwargs)
            return

        logger.error(
            "Unable to send command response (no interaction/channel route) user_id=%s",
            getattr(getattr(interaction, "user", None), "id", None),
        )

    def _build_api_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def parse_setup(self, content: str) -> Optional[Dict[str, Any]]:
        symbol_match = SYMBOL_PATTERN.search(content)
        dir_match = DIRECTION_PATTERN.search(content)
        entry_match = ENTRY_PATTERN.search(content)
        sl_match = SL_PATTERN.search(content)
        target_match = TARGET_PATTERN.search(content)
        
        if not all([symbol_match, dir_match, entry_match, sl_match]):
            return None
            
        symbol = (symbol_match.group(1) or symbol_match.group(2)).upper()
        direction = dir_match.group(1).upper()
        
        try:
            entry = float(entry_match.group(1))
            sl = float(sl_match.group(1))
            target = float(target_match.group(1)) if target_match else None
            
            return {
                "token": symbol,
                "direction": direction,
                "entry": entry,
                "sl": sl,
                "target": target
            }
        except ValueError:
            return None

    def parse_score_card(self, content: str) -> Optional[Dict[str, Any]]:
        header_match = SCORE_CARD_HEADER_PATTERN.search(content)
        score_match = ENTRY_SCORE_PATTERN.search(content)
        
        if not header_match or not score_match:
            return None
            
        symbol = header_match.group(1).upper()
        direction = header_match.group(2).upper()
        
        try:
            score = float(score_match.group(1))
            rr_match = RR_SCORE_PATTERN.search(content)
            rr = float(rr_match.group(1)) if rr_match else None
            
            return {
                "token": symbol,
                "direction": direction,
                "entry_score": score,
                "rr_ratio": rr
            }
        except ValueError:
            return None

    async def call_pre_trade_check(self, setup: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        url = f"{self.api_url}/api/execution/pre-trade-check"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=setup, headers=self._build_api_headers()) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        logger.error(f"API error: {response.status}")
                        return None
        except Exception as e:
            logger.error(f"Failed to call API: {e}")
            return None

    def format_score_decision(self, card: Dict[str, Any]) -> str:
        threshold = 8.0 if card["direction"] == "SHORT" else 8.5
        approved = card["entry_score"] >= threshold
        status_emoji = "✅" if approved else "❌"
        status_word = "EXECUTE" if approved else "SKIP"
        
        rr_line = f"• R:R: {card['rr_ratio']:.1f}:1" if card["rr_ratio"] else "• R:R: N/A"
        if card["rr_ratio"]:
             rr_line += " ✅" if card["rr_ratio"] >= 2.0 else " ⚠️ (below 2.0)"

        return (
            f"{status_emoji} {card['token']} {card['direction']} — {status_word}\n"
            f"**David should trade now:** {'YES' if approved else 'NO'}\n\n"
            f"📊 **Score-Only Evaluation:**\n"
            f"• Entry Score: {card['entry_score']:.1f}/10\n"
            f"{rr_line}\n"
            f"• Threshold ({card['direction']}): {threshold:.1f}/10\n\n"
            f"**Decision:** {status_emoji} {status_word}\n"
            f"{'Meets score threshold. Post full Entry/SL/Target to run full risk diagnostics.' if approved else 'Below threshold. Post full Entry/SL/Target only if you want full risk diagnostics.'}"
        )

    @app_commands.command(name="rerun", description="Re-run trade analysis with current market data")
    async def rerun(self, interaction: discord.Interaction):
        account_id = self._resolve_account_id()
        if not self._is_authorized(interaction.user.id, account_id=account_id):
            await self._deny_interaction(interaction)
            return
        deferred = await self._safe_defer(interaction, ephemeral=False, command_name="rerun")

        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            await self._send_command_message(
                interaction,
                deferred=deferred,
                content="❌ `/rerun` must be used inside a trade thread.",
                ephemeral=True,
            )
            return
        if channel.parent_id != TRADES_CHANNEL_ID:
            await self._send_command_message(
                interaction,
                deferred=deferred,
                content="❌ `/rerun` only works in `#trades` threads.",
                ephemeral=True,
            )
            return

        setup = await self._extract_setup_from_thread(channel)
        if not setup:
            await self._send_command_message(
                interaction,
                deferred=deferred,
                content=(
                    "❌ Could not find a trade setup in this thread.\n"
                    "Expected format: `Entry: X.XX`, `SL: X.XX`, etc."
                ),
            )
            return

        proximity_note = await self._check_price_proximity(setup)

        setup["is_rerun"] = True
        try:
            api_res = await self.call_pre_trade_check(setup)
        except Exception as e:
            logger.error(f"Rerun API call failed: {e}")
            api_res = None

        if api_res and api_res.get("data", {}).get("formatted_response"):
            header = f"🔄 **RERUN** — {setup['token']} {setup['direction']}\n"
            if proximity_note:
                header += f"{proximity_note}\n"
            header += f"_Original setup: Entry ${setup['entry']}, SL ${setup['sl']}"
            if setup.get("target"):
                header += f", Target ${setup['target']}"
            header += "_\n\n"
            response_text = header + api_res["data"]["formatted_response"]
        else:
            response_text = "❌ Trade rerun failed — DACLE API unavailable."

        await self._send_command_message(interaction, deferred=deferred, content=response_text)

    async def _extract_setup_from_thread(self, thread: discord.Thread) -> Optional[Dict[str, Any]]:
        """Scan thread messages for the original trade setup."""
        # The starter message lives in the PARENT channel (not the thread) when
        # created via message.startThread(). Fetch from parent first.
        try:
            parent_channel = thread.parent
            if parent_channel:
                starter = await parent_channel.fetch_message(thread.id)
                logger.info(f"Rerun: parent starter by {starter.author.name}: {starter.content[:120]!r}")
                setup = self.parse_setup(starter.content)
                if setup:
                    return setup
        except Exception as e:
            logger.info(f"Rerun: parent starter fetch failed: {e}")

        # Fallback: scan thread history for any message with a parseable setup
        async for msg in thread.history(limit=50, oldest_first=True):
            setup = self.parse_setup(msg.content)
            if setup:
                logger.info(f"Rerun: found setup in msg by {msg.author.name}: {setup}")
                return setup

        logger.warning(f"Rerun: no setup found in thread {thread.name} ({thread.id})")
        return None

    async def _check_price_proximity(self, setup: Dict[str, Any]) -> Optional[str]:
        """Check if current price is within 10% of original entry. Returns warning or None."""
        try:
            current = get_current_price(setup["token"])

            # Fallback: API live-price (Blofin → Binance → DexScreener)
            if current is None:
                try:
                    url = f"{self.api_url}/api/tokens/{setup['token']}/live-price"
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url, headers=self._build_api_headers()) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                current = data.get("price")
                except Exception as e:
                    logger.debug(f"Live-price API fallback failed for {setup['token']}: {e}")

            if current is None:
                return "⚠️ Could not fetch current price — proximity check skipped."
            current = float(current)
            pct_diff = abs(current - setup["entry"]) / setup["entry"] * 100
            if pct_diff > 10:
                return f"⚠️ Current price ${current:.4g} is {pct_diff:.1f}% from entry ${setup['entry']} — setup may be stale."
            return None
        except Exception:
            return None

    @app_commands.command(name="setup", description="Post a trade setup from playbook to #trades")
    @app_commands.describe(
        token="Token symbol (e.g., ZRO, ALCH, DRIFT)",
        direction="Trade direction: SHORT or LONG",
    )
    @app_commands.choices(direction=[
        app_commands.Choice(name="SHORT", value="SHORT"),
        app_commands.Choice(name="LONG", value="LONG"),
    ])
    async def setup_command(self, interaction: discord.Interaction, token: str, direction: str):
        """Post a trade setup from playbook directly to #trades and run pre-trade-check."""
        account_id = self._resolve_account_id()
        if not self._is_authorized(interaction.user.id, account_id=account_id):
            await self._deny_interaction(interaction)
            return
        deferred = await self._safe_defer(interaction, ephemeral=False, command_name="setup")

        token = token.upper()
        direction = direction.upper()
        project_root = Path(__file__).resolve().parents[3]
        tokens_dir = project_root / "data" / "tokens"

        # Prefer David's manual levels from /levels command
        # Primary: discord_levels.json audit trail (survives data consolidation)
        # Fallback: consolidated.json fields (may be wiped by /analyze)
        from datetime import datetime, timezone
        discord_levels = None
        audit_path = tokens_dir / token / "discord_levels.json"
        if audit_path.exists():
            try:
                audit_list = json.loads(audit_path.read_text())
                if isinstance(audit_list, list) and audit_list:
                    for raw in reversed(audit_list):
                        # Audit trail uses "recommendation", not "direction"
                        entry_dir = raw.get("direction", "") or raw.get("recommendation", "")
                        if entry_dir.upper() != direction:
                            continue
                        expires = raw.get("expires_at", "")
                        if expires:
                            exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                            if exp_dt <= datetime.now(timezone.utc):
                                continue
                        # Normalize audit format to {entry, stop_loss, target}
                        entry_val = raw.get("entry")
                        if entry_val is None:
                            entry_levels = raw.get("entry_levels", [])
                            entry_val = entry_levels[0] if entry_levels else None
                        target_val = raw.get("target")
                        if target_val is None:
                            take_profits = raw.get("take_profits", [])
                            target_val = take_profits[0] if take_profits else None
                        discord_levels = {
                            "direction": direction,
                            "entry": entry_val,
                            "stop_loss": raw.get("stop_loss"),
                            "target": target_val,
                            "expires_at": expires,
                        }
                        break
            except Exception:
                pass
        if not discord_levels:
            consolidated_path = tokens_dir / token / "consolidated.json"
            if consolidated_path.exists():
                try:
                    cdata = json.loads(consolidated_path.read_text())
                    dl = cdata.get("latest_discord_levels") or cdata.get("latest_discord_setup")
                    if dl and dl.get("direction", "").upper() == direction:
                        expires = dl.get("expires_at", "")
                        if not expires or datetime.fromisoformat(expires.replace("Z", "+00:00")) > datetime.now(timezone.utc):
                            discord_levels = dl
                except Exception:
                    pass

        # Load playbook execution state as fallback
        playbooks_dir = tokens_dir / token / "playbooks"
        exec_state = None
        if playbooks_dir.exists():
            candidates = [
                playbooks_dir / f"{token}_{direction.lower()}_execution_state.json",
                playbooks_dir / f"{token}_execution_state.json",
            ]
            for path in candidates:
                if path.exists():
                    try:
                        exec_state = json.loads(path.read_text())
                        break
                    except Exception:
                        continue

        if not exec_state and not discord_levels:
            await self._send_command_message(
                interaction,
                deferred=deferred,
                content=(
                    f"No playbook found for **{token}** {direction}. "
                    f"Run `/analyze {token}` first to generate a playbook."
                ),
            )
            return

        # Extract levels — prefer David's /levels input over auto-generated playbook
        if discord_levels:
            entry_low = discord_levels.get("entry")
            stop_loss = discord_levels.get("stop_loss")
            target = discord_levels.get("target")
            entry_high = None
            logger.info(f"/setup using David's /levels for {token} {direction}")
        else:
            levels = exec_state.get("execution_levels", {})
            entry_low = levels.get("entry_low")
            stop_loss = levels.get("stop_loss")
            target = levels.get("target_1")
            entry_high = levels.get("entry_high")

        if not entry_low or not stop_loss:
            await self._send_command_message(
                interaction,
                deferred=deferred,
                content=(
                    f"Playbook for **{token}** {direction} is incomplete (missing entry or SL). "
                    f"Re-run `/analyze {token}` to regenerate."
                ),
            )
            return

        # Generate lifecycle ID for trade tracking
        lifecycle_id = generate_lifecycle_id(token, direction)
        logger.info(f"/setup generating lifecycle_id={lifecycle_id}")

        # Save lifecycle_id to playbook execution state (if playbook exists)
        if exec_state:
            try:
                exec_state["lifecycle_id"] = lifecycle_id
                exec_state_path = None
                for path in candidates:
                    if path.exists():
                        exec_state_path = path
                        break
                if exec_state_path:
                    exec_state_path.write_text(json.dumps(exec_state, indent=2))
            except Exception as e:
                logger.warning(f"/setup failed to save lifecycle_id to exec state: {e}")

        # Record setup in lifecycle store
        try:
            record_setup(lifecycle_id, token, direction)
        except Exception as e:
            logger.warning(f"/setup failed to record lifecycle: {e}")

        # Format setup message
        parts = [f"TAKE {direction} ${token}"]
        if entry_high and entry_high != entry_low:
            parts.append(f"Entry: {entry_low} - {entry_high}")
        else:
            parts.append(f"Entry: {entry_low}")
        parts.append(f"SL: {stop_loss}")
        if target:
            parts.append(f"Target: {target}")
        setup_msg = "\n".join(parts)

        # Post to #trades
        trades_channel = interaction.client.get_channel(TRADES_CHANNEL_ID)
        if not trades_channel:
            await self._send_command_message(
                interaction,
                deferred=deferred,
                content=f"Could not find #trades channel. Post manually:\n```\n{setup_msg}\n```",
            )
            return

        try:
            await trades_channel.send(setup_msg)
        except Exception as e:
            logger.error(f"/setup failed to post to #trades: {e}")
            await self._send_command_message(
                interaction,
                deferred=deferred,
                content=f"Failed to post to #trades: {e}\nManual setup:\n```\n{setup_msg}\n```",
            )
            return

        # Trade router (Node.js) handles pre-trade-check when it detects
        # the setup in #trades — no need to run it here (avoids duplicates).
        await self._send_command_message(
            interaction,
            deferred=deferred,
            content=(
                f"Setup posted to #trades for **{token}** {direction}. "
                f"Trade router will run pre-trade-check in the thread."
            ),
        )

    # NOTE: on_message listener removed — trade setup detection is handled by
    # Node.js trade-router (deploy/openclaw/trade-router/index.js) per Session 408.
    # This cog provides /rerun, /setup, and /levels slash commands.

    @app_commands.command(name="levels", description="Set manual entry/SL/TP levels with confluence validation")
    @app_commands.describe(
        token="Token symbol (e.g., TAO, ZRO)",
        direction="Trade direction",
        entry="Entry price",
        sl="Stop loss price",
        tp="Take profit price (optional)",
    )
    @app_commands.choices(direction=[
        app_commands.Choice(name="SHORT", value="SHORT"),
        app_commands.Choice(name="LONG", value="LONG"),
    ])
    async def levels_command(
        self,
        interaction: discord.Interaction,
        token: str,
        direction: app_commands.Choice[str],
        entry: float,
        sl: float,
        tp: Optional[float] = None,
    ):
        """Set David's manual levels, validate confluences, and run pre-trade check."""
        account_id = self._resolve_account_id()
        if not self._is_authorized(interaction.user.id, account_id=account_id):
            await self._deny_interaction(interaction)
            return
        deferred = await self._safe_defer(interaction, ephemeral=False, command_name="levels")

        token_upper = token.upper()
        direction_value = direction.value

        payload = {
            "token": token_upper,
            "direction": direction_value,
            "entry": entry,
            "sl": sl,
        }
        if tp is not None:
            payload["tp"] = tp

        try:
            url = f"{self.api_url}/api/execution/levels"
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=self._build_api_headers()) as response:
                    if response.status == 422:
                        error_data = await response.json()
                        detail = error_data.get("detail", "Validation error")
                        await self._send_command_message(
                            interaction,
                            deferred=deferred,
                            content=f"\u274c {detail}",
                        )
                        return
                    if response.status != 200:
                        await self._send_command_message(
                            interaction,
                            deferred=deferred,
                            content=f"API error ({response.status}). Check DACLE API logs.",
                        )
                        return
                    api_result = await response.json()
        except Exception as e:
            logger.error(f"/levels API call failed: {e}")
            await self._send_command_message(
                interaction,
                deferred=deferred,
                content="DACLE API unavailable.",
            )
            return

        # Extract results
        ptc = api_result.get("pre_trade_check") or {}
        confluence = api_result.get("confluence") or {}
        rr_ratio = api_result.get("rr_ratio", 0)
        approved = ptc.get("approved", False) if isinstance(ptc, dict) else False
        formatted = api_result.get("formatted_response") or ""

        # Build embed
        status_emoji = "\u2705" if approved else "\U0001f6d1"
        status_word = "APPROVED" if approved else "BLOCKED"
        color = discord.Color.green() if approved else discord.Color.red()

        embed = discord.Embed(
            title=f"{status_emoji} {token_upper} {direction_value} — {status_word}",
            color=color,
        )

        # Confluence line
        conf_count = confluence.get("confluence_count", 0)
        conf_sources = confluence.get("matching_sources", [])
        conf_quality = confluence.get("quality", "NO_DATA")
        # Filter out sl_confirmed/tp1_confirmed for display
        display_sources = [s for s in conf_sources if s not in ("sl_confirmed", "tp1_confirmed")]
        if conf_count > 0:
            conf_line = f"{conf_count} confluences ({', '.join(display_sources)}) — {conf_quality}"
        elif conf_quality == "NO_DATA":
            conf_line = "0 confluences — no computed data"
        else:
            conf_line = f"0 confluences — {conf_quality}"
        embed.add_field(name="Confluence", value=conf_line, inline=False)

        # Levels
        embed.add_field(name="Entry", value=f"${entry:g}", inline=True)
        embed.add_field(name="SL", value=f"${sl:g}", inline=True)
        if tp is not None:
            embed.add_field(name="TP", value=f"${tp:g}", inline=True)
            embed.add_field(name="R:R", value=f"{rr_ratio}:1", inline=True)
        else:
            embed.add_field(name="TP", value="Not set", inline=True)

        # Pre-trade check summary (truncated)
        # Strip first line — it duplicates the embed title (e.g. "🛑 ORCA SHORT — BLOCKED")
        if formatted:
            lines = formatted.split("\n")
            if lines and ("APPROVED" in lines[0] or "BLOCKED" in lines[0]):
                formatted = "\n".join(lines[1:]).lstrip("\n")
            if len(formatted) > 4000:
                formatted = formatted[:3997] + "..."
            embed.description = formatted

        embed.set_footer(text="Levels stored (expires 24h)")

        view = LevelsResultView(
            token=token_upper,
            direction=direction_value,
            entry=entry,
            sl=sl,
            tp=tp,
            bot=self.bot,
            formatted_response=api_result.get("formatted_response", ""),
            approved=approved,
            confluence=confluence,
            rr_ratio=rr_ratio,
        )
        await self._send_command_message(
            interaction,
            deferred=deferred,
            embed=embed,
            view=view,
        )


class LevelsResultView(discord.ui.View):
    """Interactive view for /levels result with 'Post to #trades' button."""

    def __init__(
        self,
        token: str,
        direction: str,
        entry: float,
        sl: float,
        tp: Optional[float],
        bot,
        formatted_response: str = "",
        approved: bool = False,
        confluence: Optional[dict] = None,
        rr_ratio: float = 0,
    ):
        super().__init__(timeout=300)
        self.token = token
        self.direction = direction
        self.entry = entry
        self.sl = sl
        self.tp = tp
        self._bot = bot
        self._formatted_response = formatted_response
        self._approved = approved
        self._confluence = confluence or {}
        self._rr_ratio = rr_ratio

    @discord.ui.button(label="Post to #trades", style=discord.ButtonStyle.primary, emoji="\U0001f4e8")
    async def post_to_trades(self, interaction: discord.Interaction, button: discord.ui.Button):
        trades_channel = self._bot.get_channel(TRADES_CHANNEL_ID)
        if not trades_channel:
            await interaction.response.send_message(
                "Could not find #trades channel.", ephemeral=True
            )
            return

        parts = [f"TAKE {self.direction} ${self.token}"]
        parts.append(f"Entry: {self.entry}")
        parts.append(f"SL: {self.sl}")
        if self.tp is not None:
            parts.append(f"Target: {self.tp}")
        setup_msg = "\n".join(parts)

        # Generate lifecycle_id for /levels → #trades flow
        lifecycle_id = generate_lifecycle_id(self.token, self.direction)
        try:
            record_setup(lifecycle_id, self.token, self.direction)
        except Exception as e:
            logger.warning(f"[levels] Failed to record lifecycle: {e}")

        try:
            setup_message = await trades_channel.send(setup_msg)
            button.disabled = True
            button.label = "Posted to #trades"
            button.style = discord.ButtonStyle.secondary
            await interaction.response.edit_message(view=self)

            # Create thread and post PTC summary
            await self._post_thread_summary(setup_message)
        except Exception as e:
            logger.error(f"[levels] Failed to post to #trades: {e}")
            await interaction.response.send_message(
                f"Failed to post: {e}\n```\n{setup_msg}\n```", ephemeral=True
            )

    async def _post_thread_summary(self, setup_message: discord.Message):
        """Create a thread on the setup message and post PTC + confluence summary."""
        try:
            thread = await setup_message.create_thread(
                name=f"{self.token}-{self.direction}-{setup_message.created_at.strftime('%H%M')}",
                auto_archive_duration=60,  # 1h (Session 456 request)
            )

            # Build condensed PTC summary for thread
            status_emoji = "\u2705" if self._approved else "\U0001f6d1"
            status_word = "APPROVED" if self._approved else "BLOCKED"

            lines = [f"{status_emoji} **Pre-Trade Check: {status_word}**"]

            # Confluence line
            conf_count = self._confluence.get("confluence_count", 0)
            conf_sources = self._confluence.get("matching_sources", [])
            display_sources = [s for s in conf_sources if s not in ("sl_confirmed", "tp1_confirmed")]
            if conf_count > 0:
                lines.append(f"\U0001f4d0 Confluences: {conf_count}x ({', '.join(display_sources)})")
            else:
                lines.append("\U0001f4d0 Confluences: 0")

            # R:R
            if self._rr_ratio > 0:
                lines.append(f"\U0001f4ca R:R: {self._rr_ratio}:1")

            # Add the full formatted response (truncated for thread)
            if self._formatted_response:
                # Strip the header line (already shown above)
                resp_lines = self._formatted_response.split("\n")
                if resp_lines and ("APPROVED" in resp_lines[0] or "BLOCKED" in resp_lines[0]):
                    resp_lines = resp_lines[1:]
                body = "\n".join(resp_lines).strip()
                if body:
                    # Discord message limit is 2000 chars
                    available = 1900 - len("\n".join(lines)) - 10
                    if len(body) > available:
                        body = body[:available] + "..."
                    lines.append("")
                    lines.append(body)

            await thread.send("\n".join(lines))
        except Exception as e:
            logger.error(f"[levels] Failed to create thread: {e}")

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


async def setup(bot: commands.Bot):
    await bot.add_cog(TradeRouter(bot))
    logger.info("TradeRouter cog loaded")
