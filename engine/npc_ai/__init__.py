"""NPC tactical AI — decision brains for NPCs on their combat turn.

The scripted brain (:mod:`engine.npc_ai.scripted`) handles minion and elite
tiers via pure heuristics. The LLM tactician (task 52) layers on top for
boss NPCs. Every brain returns an :class:`~engine.npc_ai.scripted.NPCActionPlan`
that the engine validates and resolves — the LLM never touches dice.
"""
