"""Per-campaign Markdown audit log — the "story bible".

Writes a human-readable Markdown file for each campaign combining:

1. A static **header** with the full narrative plan (arc, beats, villain,
   starting location, player characters), written once at campaign launch.
2. An append-only **journal** of every player turn with the current beat,
   command, mechanics, and narrative.
3. Periodic **coherence checks** from the existing ``StoryDirector`` every
   ten turns, giving the developer a quick view of narrative drift.

The file is intentionally text-only and written synchronously — volume is
low and we want durability in case of a bot crash. A ``threading.Lock``
serialises writes since Discord.py can dispatch handlers in parallel.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai.models import DirectorNote
    from bot.game_session import GameSession
    from engine.character import Character
    from world.campaign import Campaign
    from world.location import Location
    from world.story_arc import StoryArc


logger = logging.getLogger(__name__)

_DEFAULT_LOG_DIR = Path("logs/campaigns")
_DIRECTOR_INTERVAL = 20
_RECENT_TURNS_MAX = 10
_NARRATIVE_PREVIEW_CHARS = 400


class StoryBibleLogger:
    """Writes a per-campaign Markdown audit file.

    One instance per ``GameSession``. ``write_header`` is called once at
    campaign launch, ``log_turn`` on every player action, and
    ``log_coherence_check`` on each Story Director pass.
    """

    def __init__(
        self,
        campaign_id: str,
        log_dir: Path = _DEFAULT_LOG_DIR,
    ) -> None:
        self._campaign_id = campaign_id
        self._log_dir = log_dir
        self._path = log_dir / f"{campaign_id}.md"
        self._lock = threading.Lock()
        self._recent: deque[str] = deque(maxlen=_RECENT_TURNS_MAX)

    @property
    def path(self) -> Path:
        """Filesystem path of the Markdown file."""
        return self._path

    # ------------------------------------------------------------------
    # Header (written once at campaign launch)
    # ------------------------------------------------------------------

    def write_header(
        self,
        *,
        campaign: Campaign,
        story_arc: StoryArc | None,
        location: Location | None,
        characters: dict[int, Character],
        character_kits: dict[int, str] | None = None,
        character_motivations: dict[int, str] | None = None,
    ) -> None:
        """Write the static campaign plan as the file header.

        Creates ``logs/campaigns/`` if missing and overwrites any existing
        file for this campaign (useful when re-running dev campaigns).

        When ``character_kits`` and ``character_motivations`` are provided,
        a "Composition du groupe" section is rendered so the chosen roles
        are part of the frozen campaign audit trail.
        """
        lines: list[str] = []
        lines.append(f"# Campagne : {campaign.name}")
        lines.append(f"**ID:** `{campaign.id}`")
        lines.append(f"**Créée:** {campaign.created_at.strftime('%Y-%m-%d %H:%M')}")
        if characters:
            player_lines = [
                f"{char.name} ({char.race.value} {char.char_class.value})"
                for char in characters.values()
            ]
            lines.append(f"**Joueurs:** {', '.join(player_lines)}")
        lines.append("")

        # Party composition — kit + motivation per player (frozen facts for
        # the reframed opening). Emitted only when the launcher provided them.
        kits = character_kits or {}
        motivations = character_motivations or {}
        if characters and (kits or motivations):
            lines.append("## Composition du groupe")
            for user_id, char in characters.items():
                kit = kits.get(user_id, "?")
                motivation = motivations.get(user_id, "?")
                lines.append(
                    f"- **{char.name}** ({char.race.value} {char.char_class.value}) "
                    f"— Kit: {kit} — Motivation: {motivation}",
                )
            lines.append("")

        if story_arc is not None:
            lines.append("## Arc narratif")
            lines.append(f"**Thème:** {story_arc.theme}")
            lines.append(f"**Villain:** {story_arc.villain_name}")
            lines.append(f"> {story_arc.villain_motivation}")
            lines.append("")
            lines.append(f"**Premise:** {story_arc.premise}")
            if story_arc.situation.strip():
                lines.append("")
                lines.append(f"**Situation:** {story_arc.situation}")
            if story_arc.call_to_action.strip():
                lines.append("")
                lines.append(f"**Call to action:** {story_arc.call_to_action}")
            lines.append("")
            lines.append(f"### Beats ({len(story_arc.beats)})")
            for beat in story_arc.beats:
                twist = " **[twist]**" if beat.is_twist else ""
                lines.append(
                    f"{beat.beat_number}. **{beat.title}** "
                    f"*({beat.encounter_type})*{twist} — {beat.description}",
                )
                if beat.location_hint:
                    lines.append(f"   - Lieu suggéré: {beat.location_hint}")
                if beat.npc_names:
                    lines.append(f"   - PNJs: {', '.join(beat.npc_names)}")
            lines.append("")

        if location is not None:
            lines.append("## Lieu de départ")
            lines.append(f"**{location.name}**")
            if location.description:
                lines.append(f"> {location.description}")
            if location.arrival_hook.strip():
                lines.append(f"*Arrivée:* {location.arrival_hook}")
            if location.connections:
                lines.append(f"- Sorties: {', '.join(location.connections)}")
            if location.npcs_present:
                lines.append(f"- PNJs présents: {', '.join(location.npcs_present)}")
            if location.items_available:
                lines.append(f"- Objets visibles: {', '.join(location.items_available)}")
            lines.append("")

        lines.append("---")
        lines.append("")
        lines.append("## Journal")
        lines.append("")

        with self._lock:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            self._path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ------------------------------------------------------------------
    # Journal turn entry
    # ------------------------------------------------------------------

    def log_turn(
        self,
        *,
        user_name: str,
        command: str,
        args: str,
        mechanics: str,
        narrative: str,
        story_arc: StoryArc | None,
        turn_number: int,
    ) -> None:
        """Append one turn's worth of activity to the journal.

        Safe to call before ``write_header`` has run — the file will be
        created with just a journal section. ``story_arc`` may be ``None``;
        the entry then omits the beat marker.
        """
        beat_marker = self._format_beat_marker(story_arc)
        header = (
            f"### Turn {turn_number} — "
            f"[{datetime.now().strftime('%H:%M')}] `{user_name}`"
        )
        if beat_marker:
            header += f" · {beat_marker}"

        command_line = f"`{command}`" if not args else f"`{command} {args}`"

        lines = [
            header,
            f"**Commande:** {command_line}",
            f"**Mécaniques:** {mechanics}",
            f"**Narration:** *{narrative.strip()}*",
            "",
        ]
        self._append("\n".join(lines) + "\n")

        self._recent.append(
            f"Turn {turn_number} · {command}"
            + (f" {args}" if args else "")
            + f" → {narrative.strip()[:_NARRATIVE_PREVIEW_CHARS]}",
        )

    # ------------------------------------------------------------------
    # Coherence check
    # ------------------------------------------------------------------

    def log_coherence_check(
        self,
        *,
        note: DirectorNote,
        turn_number: int,
    ) -> None:
        """Append the Story Director's coherence verdict to the journal."""
        lines = [
            f"## Coherence Check — Turn {turn_number} · priority: {note.priority}",
        ]
        if note.coherence_issues:
            lines.append("**Issues détectés:**")
            for issue in note.coherence_issues:
                lines.append(f"- {issue}")
        else:
            lines.append("**Issues détectés:** (aucun)")
        if note.suggested_hooks:
            lines.append("")
            lines.append("**Suggested hooks:**")
            for hook in note.suggested_hooks:
                lines.append(f"- {hook}")
        lines.append("")
        lines.append("---")
        lines.append("")
        self._append("\n".join(lines) + "\n")

    # ------------------------------------------------------------------
    # World events (Lot E)
    # ------------------------------------------------------------------

    def log_event(self, text: str, *, turn_number: int | None = None) -> None:
        """Append a free-form world event (NPC death, faction shift, etc.)."""
        header = "## 🎭 Événement"
        if turn_number is not None:
            header += f" — Tour {turn_number}"
        self._append(f"\n{header}\n\n{text}\n\n---\n\n")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def recent_turns(self, n: int = _RECENT_TURNS_MAX) -> list[str]:
        """Return up to ``n`` most recent turn previews, newest last."""
        if n <= 0:
            return []
        items = list(self._recent)
        return items[-n:]

    def _append(self, text: str) -> None:
        with self._lock:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(text)

    @staticmethod
    def _format_beat_marker(story_arc: StoryArc | None) -> str:
        if story_arc is None or not story_arc.beats:
            return ""
        idx = story_arc.current_beat_index
        if idx >= len(story_arc.beats):
            idx = len(story_arc.beats) - 1
        beat = story_arc.beats[idx]
        return f"Beat {idx + 1}/{len(story_arc.beats)} « {beat.title} »"


# ----------------------------------------------------------------------
# Cog-facing helper
# ----------------------------------------------------------------------


async def record_turn_and_maybe_check(
    session: GameSession,
    *,
    user_name: str,
    command: str,
    args: str,
    mechanics: str,
    narrative: str,
) -> None:
    """Record one turn and, every ``_DIRECTOR_INTERVAL`` turns, run the Director.

    Called by cogs (exploration, combat) after each narrated action. Any
    error is swallowed and logged at WARNING — audit logging must never
    break gameplay.
    """
    bible = session.story_bible
    if bible is None:
        return

    try:
        session.campaign.interaction_count += 1
        turn_number = session.campaign.interaction_count
        bible.log_turn(
            user_name=user_name,
            command=command,
            args=args,
            mechanics=mechanics,
            narrative=narrative,
            story_arc=session.story_arc,
            turn_number=turn_number,
        )
    except Exception:
        logger.warning("story_bible log_turn failed", exc_info=True)
        return

    if turn_number > 0 and turn_number % _DIRECTOR_INTERVAL == 0:
        await _run_director(session, bible, turn_number)


async def _run_director(
    session: GameSession,
    bible: StoryBibleLogger,
    turn_number: int,
) -> None:
    """Run a coherence check and append the result to the journal."""
    director = session.story_director
    if director is None:
        return

    context = _build_director_context(session, bible)
    try:
        note = await asyncio.to_thread(
            director.check_coherence,
            session.campaign.id,
            context,
        )
    except Exception:
        logger.warning("story_director check_coherence failed", exc_info=True)
        return

    try:
        bible.log_coherence_check(note=note, turn_number=turn_number)
    except Exception:
        logger.warning("story_bible log_coherence_check failed", exc_info=True)


def _build_director_context(
    session: GameSession,
    bible: StoryBibleLogger,
) -> str:
    """Assemble a lightweight context prompt for the Story Director.

    The real ``ContextAssembler`` pipeline is not wired through
    ``GameSession`` yet; for the coherence pass we build a minimal prompt
    from the campaign plan plus the recent turn previews held by the
    logger itself.
    """
    lines: list[str] = []
    lines.append(f"Campaign: {session.campaign.name}")

    arc = session.story_arc
    if arc is not None and arc.beats:
        lines.append(f"Theme: {arc.theme}")
        lines.append(f"Villain: {arc.villain_name} — {arc.villain_motivation}")
        idx = min(arc.current_beat_index, len(arc.beats) - 1)
        current = arc.beats[idx]
        lines.append(
            f"Current beat ({idx + 1}/{len(arc.beats)}): "
            f"{current.title} — {current.description}",
        )
        if idx + 1 < len(arc.beats):
            upcoming = arc.beats[idx + 1]
            lines.append(f"Next beat: {upcoming.title}")

    location = session.current_location
    if location is not None:
        lines.append(f"Current location: {location.name}")

    recent = bible.recent_turns()
    if recent:
        lines.append("")
        lines.append("Recent turns (oldest first):")
        for entry in recent:
            lines.append(f"- {entry}")

    return "\n".join(lines)
