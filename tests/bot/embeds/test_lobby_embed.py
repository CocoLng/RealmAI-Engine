"""Tests for the campaign lobby embed."""

from bot.embeds.lobby_embed import build_lobby_embed
from bot.lobby_state import GenerationPhase, LobbyPlayer, LobbyPlayerStatus


def test_empty_lobby_shows_zero_players():
    embed = build_lobby_embed(
        campaign_name="Eldoria",
        theme="Dark Fantasy",
        host_name="cocolng",
        roster=[],
        language="fr",
    )
    assert "Eldoria" in embed.title
    assert any("0/6" in (f.value or "") or "0/6" in (f.name or "") for f in embed.fields)


def test_roster_shows_joined_player_with_badge():
    p = LobbyPlayer(user_id=100, status=LobbyPlayerStatus.JOINED)
    embed = build_lobby_embed(
        campaign_name="Eldoria",
        theme="Dark Fantasy",
        host_name="cocolng",
        roster=[(p, "alice")],  # tuple of (player, display_name)
        language="fr",
    )
    rendered = "\n".join(f.value or "" for f in embed.fields)
    assert "🆕" in rendered
    assert "alice" in rendered


def test_creating_status_shows_wrench_emoji():
    p = LobbyPlayer(user_id=100, status=LobbyPlayerStatus.CREATING)
    embed = build_lobby_embed(
        campaign_name="X", theme="Y", host_name="h",
        roster=[(p, "bob")], language="fr",
    )
    rendered = "\n".join(f.value or "" for f in embed.fields)
    assert "🛠️" in rendered


def test_ready_status_shows_check_with_summary():
    from engine.character import (
        Character, CharacterClass, Race, Size, Ability, AbilityScores
    )
    char = Character(
        name="Sylphe", race=Race.ELF, char_class=CharacterClass.RANGER,
        ability_scores=AbilityScores(STR=12, DEX=15, CON=13, INT=10, WIS=14, CHA=8),
        hp=10, max_hp=10, ac=12, speed=30,
        proficiency_bonus=2,
        saving_throw_proficiencies=(Ability.STR, Ability.DEX),
        hit_die="d10", size=Size.MEDIUM,
    )
    p = LobbyPlayer(user_id=100, status=LobbyPlayerStatus.READY, character=char)
    embed = build_lobby_embed(
        campaign_name="X", theme="Y", host_name="h",
        roster=[(p, "alice")], language="fr",
    )
    rendered = "\n".join(f.value or "" for f in embed.fields)
    assert "✅" in rendered
    assert "Sylphe" in rendered
    assert "Ranger" in rendered or "Elf" in rendered


def test_cancelled_status_shows_cross():
    p = LobbyPlayer(user_id=100, status=LobbyPlayerStatus.CANCELLED)
    embed = build_lobby_embed(
        campaign_name="X", theme="Y", host_name="h",
        roster=[(p, "ghost")], language="fr",
    )
    rendered = "\n".join(f.value or "" for f in embed.fields)
    assert "❌" in rendered


def test_player_count_displayed():
    p1 = LobbyPlayer(user_id=1, status=LobbyPlayerStatus.JOINED)
    p2 = LobbyPlayer(user_id=2, status=LobbyPlayerStatus.READY)
    embed = build_lobby_embed(
        campaign_name="X", theme="Y", host_name="h",
        roster=[(p1, "a"), (p2, "b")], language="fr",
    )
    rendered_all = embed.title + "\n" + "\n".join((f.name or "") + (f.value or "") for f in embed.fields)
    assert "2/6" in rendered_all


# ---------------------------------------------------------------------------
# Pregen status line (chantier I / H8)
#
# The arc + starting location are generated in the background while players
# create their characters. The lobby embed can surface that progress so the
# bot never looks frozen during the long generation.
# ---------------------------------------------------------------------------


def test_no_pregen_status_renders_no_generation_field():
    """Backward compatible: omitting pregen_status changes nothing."""
    embed = build_lobby_embed(
        campaign_name="X", theme="Y", host_name="h",
        roster=[], language="fr",
    )
    rendered = "\n".join(f"{f.name}\n{f.value}" for f in embed.fields)
    assert "Génération" not in rendered


def test_pregen_arc_phase_shows_progress_line():
    embed = build_lobby_embed(
        campaign_name="X", theme="Y", host_name="h",
        roster=[], language="fr",
        pregen_status=GenerationPhase.ARC,
    )
    rendered = "\n".join(f"{f.name}\n{f.value}" for f in embed.fields)
    assert "⏳" in rendered
    assert "trame" in rendered.lower()


def test_pregen_location_phase_shows_progress_line():
    embed = build_lobby_embed(
        campaign_name="X", theme="Y", host_name="h",
        roster=[], language="fr",
        pregen_status=GenerationPhase.LOCATION,
    )
    rendered = "\n".join(f"{f.name}\n{f.value}" for f in embed.fields)
    assert "⏳" in rendered
    assert "lieu" in rendered.lower()


def test_pregen_ready_shows_checkmark():
    embed = build_lobby_embed(
        campaign_name="X", theme="Y", host_name="h",
        roster=[], language="fr",
        pregen_status=GenerationPhase.READY,
    )
    rendered = "\n".join(f"{f.name}\n{f.value}" for f in embed.fields)
    assert "✅" in rendered
    assert "prêt" in rendered.lower()


def test_pregen_failed_shows_error():
    embed = build_lobby_embed(
        campaign_name="X", theme="Y", host_name="h",
        roster=[], language="fr",
        pregen_status=GenerationPhase.FAILED,
    )
    rendered = "\n".join(f"{f.name}\n{f.value}" for f in embed.fields)
    assert "❌" in rendered
