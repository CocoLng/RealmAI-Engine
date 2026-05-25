"""Tests for bot/story_bible_logger.py — per-campaign Markdown audit log."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ai.models import DirectorNote
from bot.game_session import GameSession
from bot.story_bible_logger import (
    StoryBibleLogger,
    record_turn_and_maybe_check,
)
from engine.character import AbilityScores, CharacterClass, Race, create_character
from world.campaign import Campaign
from world.location import Location
from world.story_arc import StoryArc, StoryBeat


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_arc(*, beats: int = 8, current: int = 0) -> StoryArc:
    return StoryArc(
        campaign_id="camp-1",
        theme="Sous une église",
        premise="Une cloche sonne le couvre-feu et les portes se referment.",
        villain_name="L'Archevêque Balthazar",
        villain_motivation="Invoquer une entité oubliée via un culte souterrain.",
        current_beat_index=current,
        beats=[
            StoryBeat(
                beat_number=i + 1,
                title=f"Beat {i + 1}",
                description=f"Description of beat {i + 1}.",
                location_hint="Parvis" if i == 0 else "",
                npc_names=["Frère Aldric"] if i == 0 else [],
                encounter_type="exploration",
                is_twist=(i == beats - 1),
            )
            for i in range(beats)
        ],
    )


def _make_location() -> Location:
    return Location(
        name="Le Parvis de Saint-Éloi",
        description="Le pavé retentit du bruit sourd d'une cloche funèbre.",
        connections=["Nef centrale", "Ruelle sombre"],
        npcs_present=["Frère Aldric"],
        items_available=["Cierge abandonné"],
    )


def _make_characters() -> dict[int, object]:
    scores = AbilityScores(STR=12, DEX=14, CON=13, INT=10, WIS=15, CHA=8)
    char = create_character("HumTest", Race.HUMAN, CharacterClass.CLERIC, scores)
    return {100: char}


def _make_campaign() -> Campaign:
    return Campaign(id="camp-1", name="Sous une église")


# ---------------------------------------------------------------------------
# write_header
# ---------------------------------------------------------------------------


class TestWriteHeader:
    """Header is written once at campaign launch."""

    def test_creates_file_and_parent_dir(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs" / "campaigns"
        logger = StoryBibleLogger("camp-1", log_dir=log_dir)
        logger.write_header(
            campaign=_make_campaign(),
            story_arc=_make_arc(),
            location=_make_location(),
            characters=_make_characters(),  # type: ignore[arg-type]
        )
        assert logger.path == log_dir / "camp-1.md"
        assert logger.path.exists()

    def test_header_contains_all_sections(self, tmp_path: Path) -> None:
        logger = StoryBibleLogger("camp-1", log_dir=tmp_path)
        logger.write_header(
            campaign=_make_campaign(),
            story_arc=_make_arc(beats=8),
            location=_make_location(),
            characters=_make_characters(),  # type: ignore[arg-type]
        )
        content = logger.path.read_text(encoding="utf-8")
        assert "# Campagne : Sous une église" in content
        assert "**ID:** `camp-1`" in content
        assert "## Arc narratif" in content
        assert "**Villain:** L'Archevêque Balthazar" in content
        assert "### Beats (8)" in content
        assert "1. **Beat 1**" in content
        assert "8. **Beat 8**" in content
        assert "**[twist]**" in content  # last beat is flagged
        assert "## Lieu de départ" in content
        assert "Le Parvis de Saint-Éloi" in content
        assert "HumTest (Human Cleric)" in content
        assert "## Journal" in content

    def test_header_without_arc_or_location(self, tmp_path: Path) -> None:
        logger = StoryBibleLogger("camp-2", log_dir=tmp_path)
        logger.write_header(
            campaign=_make_campaign(),
            story_arc=None,
            location=None,
            characters={},
        )
        content = logger.path.read_text(encoding="utf-8")
        assert "# Campagne : Sous une église" in content
        assert "## Arc narratif" not in content
        assert "## Lieu de départ" not in content
        assert "## Journal" in content

    def test_header_overwrites_existing_file(self, tmp_path: Path) -> None:
        path = tmp_path / "camp-1.md"
        path.write_text("stale content", encoding="utf-8")
        logger = StoryBibleLogger("camp-1", log_dir=tmp_path)
        logger.write_header(
            campaign=_make_campaign(),
            story_arc=None,
            location=None,
            characters={},
        )
        content = path.read_text(encoding="utf-8")
        assert "stale content" not in content
        assert "# Campagne :" in content

    def test_header_renders_party_composition(
        self, tmp_path: Path,
    ) -> None:
        """When kits + motivations are provided, the header surfaces them as a
        frozen fact trail for audits."""
        logger = StoryBibleLogger("camp-3", log_dir=tmp_path)
        logger.write_header(
            campaign=_make_campaign(),
            story_arc=_make_arc(),
            location=_make_location(),
            characters=_make_characters(),  # type: ignore[arg-type]
            character_kits={100: "Shadow Blade"},
            character_motivations={100: "Contract"},
        )
        content = logger.path.read_text(encoding="utf-8")
        assert "## Composition du groupe" in content
        assert "Kit: Shadow Blade" in content
        assert "Motivation: Contract" in content

    def test_header_skips_party_section_when_kits_absent(
        self, tmp_path: Path,
    ) -> None:
        """Legacy callers that don't pass kits/motivations must not see an
        empty 'Composition du groupe' section leaking into the output."""
        logger = StoryBibleLogger("camp-4", log_dir=tmp_path)
        logger.write_header(
            campaign=_make_campaign(),
            story_arc=_make_arc(),
            location=_make_location(),
            characters=_make_characters(),  # type: ignore[arg-type]
        )
        content = logger.path.read_text(encoding="utf-8")
        assert "## Composition du groupe" not in content


# ---------------------------------------------------------------------------
# log_turn
# ---------------------------------------------------------------------------


class TestLogTurn:
    """Per-turn journal appends."""

    def test_log_turn_appends_block(self, tmp_path: Path) -> None:
        logger = StoryBibleLogger("camp-1", log_dir=tmp_path)
        logger.write_header(
            campaign=_make_campaign(),
            story_arc=_make_arc(),
            location=_make_location(),
            characters=_make_characters(),  # type: ignore[arg-type]
        )
        logger.log_turn(
            user_name="cocolng",
            command="/search",
            args='target="trappe"',
            mechanics="Recherche de 'trappe' dans Parvis: rien trouvé.",
            narrative="Vous inspectez les dalles fissurées sans succès.",
            story_arc=_make_arc(beats=8, current=0),
            turn_number=1,
        )
        content = logger.path.read_text(encoding="utf-8")
        assert "### Turn 1 — " in content
        assert "`cocolng`" in content
        assert "Beat 1/8 « Beat 1 »" in content
        assert '`/search target="trappe"`' in content
        assert "Recherche de 'trappe' dans Parvis: rien trouvé." in content
        assert "*Vous inspectez les dalles fissurées sans succès.*" in content
        # Header still intact
        assert "# Campagne :" in content

    def test_log_turn_uses_current_beat_index(self, tmp_path: Path) -> None:
        logger = StoryBibleLogger("camp-1", log_dir=tmp_path)
        logger.write_header(
            campaign=_make_campaign(),
            story_arc=None,
            location=None,
            characters={},
        )
        logger.log_turn(
            user_name="cocolng",
            command="/look",
            args="",
            mechanics="m",
            narrative="n",
            story_arc=_make_arc(beats=10, current=3),
            turn_number=4,
        )
        content = logger.path.read_text(encoding="utf-8")
        assert "Beat 4/10 « Beat 4 »" in content

    def test_log_turn_without_arc(self, tmp_path: Path) -> None:
        logger = StoryBibleLogger("camp-1", log_dir=tmp_path)
        logger.write_header(
            campaign=_make_campaign(),
            story_arc=None,
            location=None,
            characters={},
        )
        logger.log_turn(
            user_name="cocolng",
            command="/look",
            args="",
            mechanics="m",
            narrative="n",
            story_arc=None,
            turn_number=1,
        )
        content = logger.path.read_text(encoding="utf-8")
        assert "### Turn 1 — " in content
        assert "Beat " not in content.split("## Journal")[1]

    def test_log_turn_creates_file_without_header(self, tmp_path: Path) -> None:
        """log_turn must not crash if write_header was never called."""
        logger = StoryBibleLogger("camp-1", log_dir=tmp_path)
        logger.log_turn(
            user_name="cocolng",
            command="/look",
            args="",
            mechanics="m",
            narrative="n",
            story_arc=None,
            turn_number=1,
        )
        assert logger.path.exists()

    def test_concurrent_log_turn_preserves_all_entries(
        self, tmp_path: Path,
    ) -> None:
        logger = StoryBibleLogger("camp-1", log_dir=tmp_path)
        logger.write_header(
            campaign=_make_campaign(),
            story_arc=None,
            location=None,
            characters={},
        )

        def write(n: int) -> None:
            logger.log_turn(
                user_name=f"user{n}",
                command="/look",
                args="",
                mechanics=f"m{n}",
                narrative=f"n{n}",
                story_arc=None,
                turn_number=n,
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(write, range(1, 21)))

        content = logger.path.read_text(encoding="utf-8")
        for n in range(1, 21):
            assert f"Turn {n} —" in content
            assert f"user{n}" in content


# ---------------------------------------------------------------------------
# log_coherence_check
# ---------------------------------------------------------------------------


class TestLogEvent:
    """Lot E — free-form world events."""

    def test_log_event_appends_section(self, tmp_path: Path) -> None:
        logger = StoryBibleLogger("camp-1", log_dir=tmp_path)
        logger.log_event("⚔️ MEURTRE — Aldric a tué Jeanne.", turn_number=3)
        content = logger.path.read_text(encoding="utf-8")
        assert "🎭 Événement" in content
        assert "Tour 3" in content
        assert "MEURTRE" in content
        assert "Aldric" in content

    def test_log_event_without_turn(self, tmp_path: Path) -> None:
        logger = StoryBibleLogger("camp-1", log_dir=tmp_path)
        logger.log_event("Une faction bascule.")
        content = logger.path.read_text(encoding="utf-8")
        assert "🎭 Événement" in content
        assert "Tour" not in content


class TestLogCoherenceCheck:
    """Story Director notes are rendered into the journal."""

    def test_empty_note(self, tmp_path: Path) -> None:
        logger = StoryBibleLogger("camp-1", log_dir=tmp_path)
        logger.write_header(
            campaign=_make_campaign(),
            story_arc=None,
            location=None,
            characters={},
        )
        note = DirectorNote(
            coherence_issues=[],
            suggested_hooks=[],
            priority="low",
        )
        logger.log_coherence_check(note=note, turn_number=10)
        content = logger.path.read_text(encoding="utf-8")
        assert "## Coherence Check — Turn 10 · priority: low" in content
        assert "**Issues détectés:** (aucun)" in content

    def test_note_with_issues_and_hooks(self, tmp_path: Path) -> None:
        logger = StoryBibleLogger("camp-1", log_dir=tmp_path)
        logger.write_header(
            campaign=_make_campaign(),
            story_arc=None,
            location=None,
            characters={},
        )
        note = DirectorNote(
            coherence_issues=["Villain not mentioned in 10 turns."],
            suggested_hooks=[
                "Have Frère Aldric drop a name.",
                "Echo of a chant from the crypt.",
            ],
            priority="high",
        )
        logger.log_coherence_check(note=note, turn_number=20)
        content = logger.path.read_text(encoding="utf-8")
        assert "priority: high" in content
        assert "Villain not mentioned in 10 turns." in content
        assert "Have Frère Aldric drop a name." in content
        assert "Echo of a chant from the crypt." in content


# ---------------------------------------------------------------------------
# recent_turns
# ---------------------------------------------------------------------------


class TestRecentTurns:
    """In-memory rolling window used to feed the Story Director."""

    def test_returns_tail_of_recent_turns(self, tmp_path: Path) -> None:
        logger = StoryBibleLogger("camp-1", log_dir=tmp_path)
        logger.write_header(
            campaign=_make_campaign(),
            story_arc=None,
            location=None,
            characters={},
        )
        for i in range(1, 6):
            logger.log_turn(
                user_name="cocolng",
                command="/look",
                args="",
                mechanics=f"m{i}",
                narrative=f"n{i}",
                story_arc=None,
                turn_number=i,
            )
        assert logger.recent_turns(n=3) == [
            "Turn 3 · /look → n3",
            "Turn 4 · /look → n4",
            "Turn 5 · /look → n5",
        ]

    def test_recent_turns_capped_at_window(self, tmp_path: Path) -> None:
        logger = StoryBibleLogger("camp-1", log_dir=tmp_path)
        for i in range(1, 20):
            logger.log_turn(
                user_name="cocolng",
                command="/look",
                args="",
                mechanics="m",
                narrative=f"n{i}",
                story_arc=None,
                turn_number=i,
            )
        # deque capped at 10
        turns = logger.recent_turns(n=100)
        assert len(turns) == 10
        assert turns[-1] == "Turn 19 · /look → n19"

    def test_recent_turns_zero(self, tmp_path: Path) -> None:
        logger = StoryBibleLogger("camp-1", log_dir=tmp_path)
        assert logger.recent_turns(n=0) == []


# ---------------------------------------------------------------------------
# record_turn_and_maybe_check (cog helper)
# ---------------------------------------------------------------------------


class TestRecordTurnAndMaybeCheck:
    """The cog-facing helper that drives log_turn + Story Director."""

    def _session(self, tmp_path: Path) -> GameSession:
        session = GameSession(campaign=_make_campaign())
        session.story_bible = StoryBibleLogger("camp-1", log_dir=tmp_path)
        session.story_bible.write_header(
            campaign=session.campaign,
            story_arc=_make_arc(),
            location=None,
            characters={},
        )
        session.story_arc = _make_arc()
        return session

    @pytest.mark.asyncio()
    async def test_no_bible_is_a_noop(self, tmp_path: Path) -> None:
        session = GameSession(campaign=_make_campaign())
        # No story_bible attached.
        await record_turn_and_maybe_check(
            session,
            user_name="cocolng",
            command="/look",
            args="",
            mechanics="m",
            narrative="n",
        )
        assert session.campaign.interaction_count == 0

    @pytest.mark.asyncio()
    async def test_increments_counter_and_writes_turn(
        self, tmp_path: Path,
    ) -> None:
        session = self._session(tmp_path)
        await record_turn_and_maybe_check(
            session,
            user_name="cocolng",
            command="/look",
            args="",
            mechanics="m1",
            narrative="narr1",
        )
        assert session.campaign.interaction_count == 1
        assert session.story_bible is not None
        content = session.story_bible.path.read_text(encoding="utf-8")
        assert "Turn 1 —" in content
        assert "narr1" in content

    @pytest.mark.asyncio()
    async def test_director_not_called_before_interval(
        self, tmp_path: Path,
    ) -> None:
        session = self._session(tmp_path)
        director = MagicMock()
        session.story_director = director
        for _ in range(9):
            await record_turn_and_maybe_check(
                session,
                user_name="cocolng",
                command="/look",
                args="",
                mechanics="m",
                narrative="n",
            )
        director.check_coherence.assert_not_called()

    @pytest.mark.asyncio()
    async def test_director_called_every_tenth_turn(
        self, tmp_path: Path,
    ) -> None:
        session = self._session(tmp_path)
        director = MagicMock()
        director.check_coherence.return_value = DirectorNote(
            coherence_issues=["drift"],
            suggested_hooks=["hook"],
            priority="medium",
        )
        session.story_director = director

        for _ in range(20):
            await record_turn_and_maybe_check(
                session,
                user_name="cocolng",
                command="/look",
                args="",
                mechanics="m",
                narrative="n",
            )
        director.check_coherence.assert_called_once()
        call_args = director.check_coherence.call_args
        assert call_args.args[0] == session.campaign.id
        assert "Theme: Sous une église" in call_args.args[1]

        assert session.story_bible is not None
        content = session.story_bible.path.read_text(encoding="utf-8")
        assert "## Coherence Check — Turn 20 · priority: medium" in content
        assert "drift" in content
        assert "hook" in content

    @pytest.mark.asyncio()
    async def test_missing_director_does_not_crash(
        self, tmp_path: Path,
    ) -> None:
        session = self._session(tmp_path)
        session.story_director = None
        for _ in range(20):
            await record_turn_and_maybe_check(
                session,
                user_name="cocolng",
                command="/look",
                args="",
                mechanics="m",
                narrative="n",
            )
        assert session.story_bible is not None
        content = session.story_bible.path.read_text(encoding="utf-8")
        assert "Coherence Check" not in content

    @pytest.mark.asyncio()
    async def test_director_exception_is_swallowed(
        self, tmp_path: Path,
    ) -> None:
        session = self._session(tmp_path)
        director = MagicMock()
        director.check_coherence.side_effect = RuntimeError("ollama down")
        session.story_director = director
        for _ in range(10):
            await record_turn_and_maybe_check(
                session,
                user_name="cocolng",
                command="/look",
                args="",
                mechanics="m",
                narrative="n",
            )
        assert session.story_bible is not None
        content = session.story_bible.path.read_text(encoding="utf-8")
        assert "Turn 10 —" in content  # turn recorded
        assert "Coherence Check" not in content  # but no broken section
