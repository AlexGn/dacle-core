"""Utilities for resilient Discord interaction handling."""

from __future__ import annotations

from typing import Any, Optional

import discord


def _http_code(exc: BaseException) -> Optional[int]:
    if isinstance(exc, discord.HTTPException):
        return getattr(exc, "code", None)
    return None


async def safe_defer(
    interaction: discord.Interaction,
    *,
    ephemeral: bool = False,
    thinking: bool = False,
    command_name: str = "unknown",
    logger: Any = None,
) -> bool:
    """Best-effort defer that tolerates expired or already-acked interactions."""
    try:
        if interaction.response.is_done():
            return True
        await interaction.response.defer(ephemeral=ephemeral, thinking=thinking)
        return True
    except discord.NotFound:
        if logger:
            logger.warning(
                "Interaction expired before defer command=%s user_id=%s interaction_id=%s",
                command_name,
                getattr(getattr(interaction, "user", None), "id", None),
                getattr(interaction, "id", None),
            )
        return False
    except discord.HTTPException as exc:
        code = _http_code(exc)
        if code == 40060:
            # Already acknowledged.
            return True
        if logger:
            logger.error(
                "Failed to defer interaction command=%s user_id=%s code=%s",
                command_name,
                getattr(getattr(interaction, "user", None), "id", None),
                code,
            )
        return False
    except Exception as exc:
        if logger:
            logger.error(
                "Unexpected defer failure command=%s user_id=%s err=%s",
                command_name,
                getattr(getattr(interaction, "user", None), "id", None),
                exc,
            )
        return False


async def safe_send(
    interaction: discord.Interaction,
    *,
    content: Optional[str] = None,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
    ephemeral: bool = False,
    command_name: str = "unknown",
    logger: Any = None,
) -> bool:
    """Send response/followup; fallback to channel send when interaction token is invalid."""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                content=content,
                embed=embed,
                view=view,
                ephemeral=ephemeral,
            )
        else:
            await interaction.response.send_message(
                content=content,
                embed=embed,
                view=view,
                ephemeral=ephemeral,
            )
        return True
    except Exception as exc:
        if logger:
            logger.warning(
                "Interaction send failed command=%s user_id=%s code=%s err=%s",
                command_name,
                getattr(getattr(interaction, "user", None), "id", None),
                _http_code(exc),
                exc,
            )

    if ephemeral:
        return False

    channel = interaction.channel
    if channel and hasattr(channel, "send"):
        kwargs: dict[str, Any] = {}
        if content is not None:
            kwargs["content"] = content
        if embed is not None:
            kwargs["embed"] = embed
        if view is not None:
            kwargs["view"] = view
        try:
            await channel.send(**kwargs)
            return True
        except Exception as exc:
            if logger:
                logger.error(
                    "Channel fallback send failed command=%s user_id=%s err=%s",
                    command_name,
                    getattr(getattr(interaction, "user", None), "id", None),
                    exc,
                )
    return False
