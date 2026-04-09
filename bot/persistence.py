"""Standalone session persistence — extracted from SessionCog for reuse.

Called by both ``SessionCog._persist_session()`` and the auto-checkpoint
in ``ActionPipeline`` (B1 fix).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from db.repositories import (
    CampaignRepository,
    NPCRepository,
    PlayerCharacterRepository,
    QuestRepository,
    StoryArcRepository,
)

if TYPE_CHECKING:
    from bot.game_session import GameSession

logger = logging.getLogger(__name__)


def persist_session(db_factory: Callable[[], Any], session: GameSession) -> None:
    """Save campaign, characters, combat state, NPCs, quests, and story arc to DB.

    This is a **synchronous** helper designed to be called from
    ``asyncio.to_thread`` when used inside async code.
    """
    db_session = db_factory()
    try:
        # Campaign + combat state
        session.campaign.combat_state_json = (
            session.combat_state.model_dump_json()
            if session.combat_state is not None
            else None
        )
        camp_repo = CampaignRepository(db_session)
        camp_repo.update(session.campaign)

        # Player characters
        pc_repo = PlayerCharacterRepository(db_session)
        for user_id, char in session.characters.items():
            inv = session.inventories.get(user_id)
            spell = session.spellcasters.get(user_id)
            if inv is not None:
                try:
                    pc_repo.update(user_id, session.campaign.id, char, inv, spell)
                except ValueError:
                    pc_repo.save(user_id, session.campaign.id, char, inv, spell)

        # NPCs
        npc_repo = NPCRepository(db_session)
        for npc in session.npcs.values():
            try:
                npc_repo.update(npc, session.campaign.id)
            except ValueError:
                npc_repo.save(npc, session.campaign.id)

        # Quests
        quest_repo = QuestRepository(db_session)
        for quest in session.quests:
            try:
                quest_repo.update(quest, session.campaign.id)
            except ValueError:
                quest_repo.save(quest, session.campaign.id)

        # Story arc
        if session.story_arc:
            arc_repo = StoryArcRepository(db_session)
            try:
                arc_repo.update(session.story_arc)
            except ValueError:
                arc_repo.save(session.story_arc)

        db_session.commit()
    finally:
        db_session.close()
