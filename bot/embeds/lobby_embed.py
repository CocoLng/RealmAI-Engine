"""Campaign lobby embed — live roster with status badges.

Status badges:
- 🆕 JOINED — clicked Rejoindre, not started
- 🛠️ CREATING — character setup in progress
- ✅ READY — character persisted
- ❌ CANCELLED — bailed mid-creation
"""

from __future__ import annotations

import discord

from bot.lobby_state import LobbyPlayer, LobbyPlayerStatus, MAX_PLAYERS_PER_LOBBY

STATUS_BADGES = {
    LobbyPlayerStatus.JOINED: "🆕",
    LobbyPlayerStatus.CREATING: "🛠️",
    LobbyPlayerStatus.READY: "✅",
    LobbyPlayerStatus.CANCELLED: "❌",
}


def build_lobby_embed(
    campaign_name: str,
    theme: str,
    host_name: str,
    roster: list[tuple[LobbyPlayer, str]],  # (player, display_name)
    language: str,
) -> discord.Embed:
    """Build the campaign lobby embed.

    The roster is displayed line-by-line with a status badge prefix; READY
    players also show their character name + class summary.
    """
    embed = discord.Embed(
        title=f"🏰 Campagne : {campaign_name}",
        description=f"**Thème** : {theme}\n**Host** : {host_name}",
        color=discord.Color.purple(),
    )

    if not roster:
        roster_text = "_Personne n'a encore rejoint. Clique 🎭 Rejoindre pour entrer._"
    else:
        lines = []
        for player, display_name in roster:
            badge = STATUS_BADGES[player.status]
            if player.status == LobbyPlayerStatus.READY and player.character is not None:
                char = player.character
                summary = f"{char.name} ({char.race.value} {char.char_class.value})"
                lines.append(f"{badge} **{display_name}** — {summary}")
            elif player.status == LobbyPlayerStatus.CREATING:
                lines.append(f"{badge} **{display_name}** — Création en cours...")
            elif player.status == LobbyPlayerStatus.CANCELLED:
                lines.append(f"{badge} ~~{display_name}~~ — Annulé")
            else:  # JOINED
                lines.append(f"{badge} **{display_name}**")
        roster_text = "\n".join(lines)

    embed.add_field(
        name=f"Aventuriers ({len(roster)}/{MAX_PLAYERS_PER_LOBBY})",
        value=roster_text,
        inline=False,
    )
    return embed
