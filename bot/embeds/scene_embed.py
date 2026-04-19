"""Scene awareness embed — describes the current location to the players.

Posted at campaign launch and after each successful MOVE so players know
who/what is in the scene. Crucial for grounding free-text player actions
in the real world state.

NPCs are sourced from ``Location.npcs_present`` (a list of strings, currently
the only NPC representation in production). When richer NPC objects exist
in the future, callers can pass an ``npcs_present`` override.
"""

from __future__ import annotations

import unicodedata

import discord

from world.location import Location

_SCENE_COLOR = 0xDAA520  # dramatic gold — same family as narrative_embed

_LABELS: dict[str, dict[str, str]] = {
    "fr": {
        "arrival": "🕯️ Votre arrivée",
        "npcs": "👥 Personnages présents",
        "no_npcs": "Aucun",
        "exits": "🚪 Sorties",
        "no_exits": "Aucune",
        "items": "🔍 Objets visibles",
        "more": "… et {n} autre(s)",
        "footer": "Tape `@bot` suivi de ce que tu veux faire.",
        "no_description": "Lieu sans description.",
    },
    "en": {
        "arrival": "🕯️ Your arrival",
        "npcs": "👥 Characters present",
        "no_npcs": "None",
        "exits": "🚪 Exits",
        "no_exits": "None",
        "items": "🔍 Visible items",
        "more": "… and {n} more",
        "footer": "Type `@bot` followed by what you want to do.",
        "no_description": "Location without description.",
    },
}

# (keywords, emoji) — first match wins, comparison is lowercase + accent-stripped.
_TYPE_EMOJI: tuple[tuple[tuple[str, ...], str], ...] = (
    (("donjon", "dungeon", "crypte", "crypt", "cave", "caverne", "mine"), "⚔️"),
    (("chateau", "castle", "fort", "fortress", "forteresse", "tour", "tower"), "🏰"),
    (("village", "hameau", "bourg", "ville", "cite", "town", "city"), "🏘️"),
    (("foret", "forest", "bois", "wood", "clairiere", "clearing", "jungle"), "🌲"),
    (("temple", "church", "eglise", "chapelle", "chapel", "sanctuaire", "sanctuary", "monastere", "monastery"), "⛪"),
    (("taverne", "tavern", "auberge", "inn"), "🍺"),
    (("port", "harbor", "harbour", "dock", "quai", "rivage", "plage", "shore", "beach"), "⚓"),
    (("montagne", "mountain", "col", "pic", "peak", "summit"), "🏔️"),
    (("marais", "swamp", "marsh", "marecage", "bog"), "🌿"),
)
_DEFAULT_EMOJI = "📍"

_MAX_NPCS_DISPLAYED = 5
_MAX_DESCRIPTION_CHARS = 1000
_MAX_FIELD_CHARS = 1024  # Discord hard limit for field values


def _normalize(text: str) -> str:
    """Lowercase + strip accents for keyword matching."""
    nfkd = unicodedata.normalize("NFKD", text)
    return nfkd.encode("ascii", "ignore").decode("ascii").lower()


def _pick_emoji(name: str) -> str:
    norm = _normalize(name)
    for keywords, emoji in _TYPE_EMOJI:
        if any(kw in norm for kw in keywords):
            return emoji
    return _DEFAULT_EMOJI


def build_scene_embed(
    location: Location,
    npcs_present: list[str] | None = None,
    language: str = "fr",
    arrival_hook: str | None = None,
) -> discord.Embed:
    """Build a Discord embed describing the current scene.

    Args:
        location: The location to render. ``name``/``description``/
            ``connections``/``npcs_present``/``items_available`` are read.
        npcs_present: Optional override for the NPC list (display strings).
            Defaults to ``location.npcs_present``. Provided so a future
            caller can pass enriched display lines without mutating the
            location.
        language: Label language — currently ``"fr"`` (default) or ``"en"``.
            Falls back to ``"fr"`` for unknown values.
        arrival_hook: Optional 1-2 sentence bridge placing the party in this
            scene at this moment. Rendered as a dedicated field between the
            description and the NPC list. Only passed at campaign launch;
            post-MOVE callers omit it so the field does not clutter
            subsequent scenes.

    Returns:
        A ``discord.Embed`` with title, description, and fields for NPCs,
        exits and (optionally) items. Footer carries a usage hint.
    """
    labels = _LABELS.get(language, _LABELS["fr"])

    emoji = _pick_emoji(location.name)
    description = location.description or labels["no_description"]
    if len(description) > _MAX_DESCRIPTION_CHARS:
        description = description[: _MAX_DESCRIPTION_CHARS - 1] + "…"

    embed = discord.Embed(
        title=f"{emoji} {location.name}",
        description=description,
        color=_SCENE_COLOR,
    )

    if arrival_hook and arrival_hook.strip():
        hook_text = arrival_hook.strip()
        if len(hook_text) > _MAX_FIELD_CHARS:
            hook_text = hook_text[: _MAX_FIELD_CHARS - 1] + "…"
        embed.add_field(name=labels["arrival"], value=hook_text, inline=False)

    npcs = npcs_present if npcs_present is not None else list(location.npcs_present)
    if npcs:
        head = npcs[:_MAX_NPCS_DISPLAYED]
        lines = [f"• {name}" for name in head]
        remaining = len(npcs) - len(head)
        if remaining > 0:
            lines.append(labels["more"].format(n=remaining))
        npcs_value = "\n".join(lines)
    else:
        npcs_value = labels["no_npcs"]
    embed.add_field(name=labels["npcs"], value=npcs_value, inline=False)

    if location.connections:
        exits_value = ", ".join(location.connections)
    else:
        exits_value = labels["no_exits"]
    embed.add_field(name=labels["exits"], value=exits_value, inline=False)

    if location.items_available:
        embed.add_field(
            name=labels["items"],
            value=", ".join(location.items_available),
            inline=False,
        )

    embed.set_footer(text=labels["footer"])
    return embed
