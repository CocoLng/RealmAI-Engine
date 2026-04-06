"""Channel creation, permission management, and archival utilities."""

import logging
import re
import unicodedata

import discord

logger = logging.getLogger(__name__)

ARCHIVE_CATEGORY_NAME: str = "RealmAI Archives"

_CHANNEL_NAME_RE = re.compile(r"[^a-z0-9-]")
_MULTI_HYPHEN_RE = re.compile(r"-{2,}")
_MAX_CHANNEL_NAME_LEN = 100
_PREFIX = "campagne-"


def _slugify(name: str) -> str:
    """Convert a campaign name to a Discord-safe channel slug.

    Strips accents, lowercases, replaces non-alphanumeric with hyphens,
    collapses runs, and prefixes with ``campagne-``.
    """
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = _CHANNEL_NAME_RE.sub("-", normalized.lower().strip())
    slug = _MULTI_HYPHEN_RE.sub("-", slug).strip("-")
    if not slug:
        return "campagne-sans-nom"
    full = f"{_PREFIX}{slug}"
    return full[:_MAX_CHANNEL_NAME_LEN]


async def get_or_create_category(
    guild: discord.Guild,
    category_name: str,
) -> discord.CategoryChannel:
    """Find an existing category by name (case-insensitive) or create one.

    Args:
        guild: The Discord guild to search/create in.
        category_name: The desired category name.

    Returns:
        The existing or newly created CategoryChannel.

    Raises:
        discord.Forbidden: Bot lacks ``manage_channels`` permission.
        discord.HTTPException: Discord API error.
    """
    lower = category_name.lower()
    for cat in guild.categories:
        if cat.name.lower() == lower:
            return cat
    return await guild.create_category_channel(name=category_name)


async def create_session_channel(
    guild: discord.Guild,
    campaign_name: str,
    players: list[discord.Member],
    bot_member: discord.Member,
    category_name: str = "RealmAI Sessions",
) -> discord.TextChannel:
    """Create a private session channel for a campaign.

    The channel is placed in *category_name* with permission overrides:
    ``@everyone`` denied, each player allowed read/send, bot allowed
    read/send/manage_messages.

    Args:
        guild: The Discord guild.
        campaign_name: Human-readable campaign name (will be slugified).
        players: Members to grant access.
        bot_member: The bot's own Member in this guild.
        category_name: Category for session channels.

    Returns:
        The newly created TextChannel.

    Raises:
        discord.Forbidden: Bot lacks ``manage_channels`` permission.
        discord.HTTPException: Discord API error.
    """
    category = await get_or_create_category(guild, category_name)

    overwrites: dict[discord.Role | discord.Member | discord.Object, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(
            read_messages=False,
            send_messages=False,
        ),
        bot_member: discord.PermissionOverwrite(
            read_messages=True,
            send_messages=True,
            manage_messages=True,
        ),
    }
    for player in players:
        overwrites[player] = discord.PermissionOverwrite(
            read_messages=True,
            send_messages=True,
        )

    channel = await guild.create_text_channel(
        name=_slugify(campaign_name),
        category=category,
        overwrites=overwrites,
    )
    logger.info(
        "CHANNEL created campaign=%r channel=%s guild=%s",
        campaign_name, channel.name, guild.name,
    )
    return channel


async def archive_channel(
    channel: discord.TextChannel,
    guild: discord.Guild,
) -> None:
    """Archive a session channel (move to archives, set read-only).

    Moves the channel to the *RealmAI Archives* category and removes
    write permissions for all non-bot members while preserving read access.

    Args:
        channel: The session channel to archive.
        guild: The guild containing the channel.

    Raises:
        discord.Forbidden: Bot lacks ``manage_channels`` permission.
        discord.HTTPException: Discord API error.
    """
    logger.info("CHANNEL archived channel=%s guild=%s", channel.name, guild.name)
    archive_category = await get_or_create_category(guild, ARCHIVE_CATEGORY_NAME)
    await channel.edit(category=archive_category)

    bot_id = guild.me.id if guild.me else None
    default_role_id = guild.default_role.id

    for target, _overwrite in channel.overwrites.items():
        if target.id == default_role_id:
            continue
        if target.id == bot_id:
            continue
        await channel.set_permissions(
            target,  # type: ignore[arg-type]  # target may be Object from stale cache
            read_messages=True,
            send_messages=False,
        )
