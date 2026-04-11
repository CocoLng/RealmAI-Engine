# Task 43 — Scene hydration : dispatch par tier d'archétype

**Phase** : 4 — Interprète & générateurs LLM (parallèle)
**Dépendances** : [11](11_npc_library_archetypes.md), [42](42_arc_generator_villain_stat_block.md)
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

`bot/scene_hydration.py::_build_default_npc` crée **tous** les NPCs avec les stats d'un commoner (`hp=4, ac=10, disposition=NEUTRAL`). C'est la cause racine du bug "villain one-shot" qui a motivé ce chantier.

Maintenant qu'on a :
- La librairie d'archétypes ([11](11_npc_library_archetypes.md)),
- Le stat block custom du villain généré par l'arc generator ([42](42_arc_generator_villain_stat_block.md)),
- (Optionnellement) les `role` taggés par le world generator (non défini dans [41](41_world_generator_zones_triggers.md) qui se concentre sur zones/triggers — peut nécessiter un ajustement mineur du prompt world generator pour ajouter `role` dans `npc_details`),

…on peut refondre l'hydration pour **dispatcher sur le bon stat block** selon le contexte.

## Scope

1. Refondre `_build_default_npc` en `_build_npc_by_context(name, location_name, arc, world_role_hint)`.
2. Priorité de détection :
   - Si `arc and name == arc.villain_name` → attacher `arc.villain_stat_block` (de la tâche [42](42_arc_generator_villain_stat_block.md)).
   - Si `world_role_hint in engine.npc_library.list_archetypes()` → `get_archetype(world_role_hint)`.
   - Si `name` apparaît dans un beat `combat`/`boss` → fallback sur `get_archetype("guard")` ou `"soldier"` selon cohérence.
   - Sinon → `get_archetype("commoner")` (comportement actuel).
3. L'NPC résultant est construit avec **les stats du stat_block** (hp, ac, ability_scores dérivés de l'archétype) **et** le stat_block attaché en champ.
4. **Upgrade idempotent** : si un NPC existe déjà en DB avec des stats commoner mais que son nom matche le villain OU un beat combat, le re-hydrater avec les bonnes stats.

## Fichiers à modifier

- [bot/scene_hydration.py](../../bot/scene_hydration.py) — refonte complète de `_build_default_npc` et `hydrate_scene`.

## Implémentation — esquisse

```python
# bot/scene_hydration.py

from engine.character import AbilityScores, Race, CharacterClass
from engine.npc_library import ARCHETYPE_BUILDERS, get_archetype
from engine.npc_stat_block import NPCStatBlock, NPCTier
from world.story_arc import StoryArc


def _resolve_archetype(
    name: str,
    arc: StoryArc | None,
    world_role_hint: str | None,
) -> tuple[str, NPCStatBlock | None]:
    """Pick the right archetype and optionally a custom stat_block for an NPC.

    Priority:
    1. Villain by name → custom stat_block from arc.villain_stat_block
    2. world_role_hint matches a library archetype → use it
    3. Name appears in a combat/boss beat → fallback archetype (guard)
    4. Default → commoner

    Returns (archetype_name, custom_stat_block_or_None).
    """
    if arc is not None and name == arc.villain_name:
        if arc.villain_stat_block is not None:
            return (arc.villain_stat_block.archetype, arc.villain_stat_block)
        return ("generic_boss", None)

    if world_role_hint is not None and world_role_hint in ARCHETYPE_BUILDERS:
        return (world_role_hint, None)

    if arc is not None:
        for beat in arc.beats:
            if beat.encounter_type in ("combat", "boss") and name in beat.npc_names:
                return ("guard", None)

    return ("commoner", None)


def _build_npc_by_context(
    name: str,
    location_name: str,
    arc: StoryArc | None,
    world_role_hint: str | None = None,
) -> NPC:
    """Create an NPC with the right stat block for its narrative role."""
    archetype_name, custom_stat_block = _resolve_archetype(name, arc, world_role_hint)
    stat_block = custom_stat_block if custom_stat_block is not None else get_archetype(archetype_name)

    hp, max_hp, ac, ability_scores = _stats_from_stat_block(stat_block)
    disposition = (
        NPCDisposition.HOSTILE
        if stat_block.tier != NPCTier.MINION
        else NPCDisposition.NEUTRAL
    )

    return NPC(
        name=name,
        race=Race.HUMAN,
        char_class=None,
        level=_level_from_tier(stat_block.tier),
        ability_scores=ability_scores,
        hp=hp,
        max_hp=max_hp,
        ac=ac,
        disposition=disposition,
        is_alive=True,
        description="",
        personality="",
        location_name=location_name,
        aliases=[],
        stat_block=stat_block,
    )


def _stats_from_stat_block(sb: NPCStatBlock) -> tuple[int, int, int, AbilityScores]:
    """Derive legacy NPC fields from a stat block.

    Since the tier defines the expected toughness, we pick baseline HP/AC
    per tier. These are overridden only if the stat_block carries explicit
    values (future extension).
    """
    if sb.tier == NPCTier.MINION:
        return (8, 8, 12, AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10))
    if sb.tier == NPCTier.ELITE:
        return (25, 25, 14, AbilityScores(STR=14, DEX=12, CON=13, INT=10, WIS=12, CHA=12))
    # BOSS
    return (55, 55, 16, AbilityScores(STR=16, DEX=14, CON=14, INT=12, WIS=14, CHA=14))


def _level_from_tier(tier: NPCTier) -> int:
    return {NPCTier.MINION: 1, NPCTier.ELITE: 3, NPCTier.BOSS: 6}[tier]


# ----- hydrate_scene: use the new builder -----

def hydrate_scene(
    session: "GameSession",
    *,
    db_factory: Callable[[], Any],
) -> None:
    location = session.current_location
    if location is None:
        return
    campaign_id = session.campaign.id
    arc = getattr(session, "story_arc", None)

    db_session = db_factory()
    try:
        npc_repo = NPCRepository(db_session)
        loc_repo = LocationRepository(db_session)

        created = 0
        for name in location.npcs_present:
            if not name or not name.strip():
                continue
            existing = npc_repo.get_by_name(name, campaign_id)
            world_role = _get_world_role_hint(location, name)
            if existing is None:
                npc = _build_npc_by_context(name, location.name, arc, world_role)
                npc_repo.save(npc, campaign_id)
                created += 1
            else:
                # Idempotent upgrade: if the existing NPC is commoner but
                # should now be stronger (arc villain, boss beat), rebuild
                # and update.
                _, new_stat_block = _resolve_archetype(name, arc, world_role)
                needs_upgrade = (
                    existing.stat_block is None
                    and (
                        (arc is not None and existing.name == arc.villain_name)
                        or (
                            arc is not None
                            and any(
                                beat.encounter_type in ("combat", "boss")
                                and existing.name in beat.npc_names
                                for beat in arc.beats
                            )
                        )
                    )
                )
                if needs_upgrade:
                    upgraded = _build_npc_by_context(name, location.name, arc, world_role)
                    # Preserve narrative fields (dialogue_history, secrets)
                    upgraded.description = existing.description
                    upgraded.personality = existing.personality
                    upgraded.secrets = existing.secrets
                    upgraded.dialogue_history = existing.dialogue_history
                    npc_repo.update(upgraded, campaign_id)
                elif existing.location_name != location.name:
                    existing.location_name = location.name
                    npc_repo.update(existing, campaign_id)

        # ... items sync ...
    finally:
        db_session.close()


def _get_world_role_hint(location: Location, npc_name: str) -> str | None:
    """Extract the world generator's role hint for an NPC, if any.

    World generator (task 41) may emit a role per NPC in npc_details.
    This helper looks it up without crashing if missing.
    """
    details = getattr(location, "npc_details", None) or {}
    if isinstance(details, dict):
        entry = details.get(npc_name)
        if isinstance(entry, dict):
            return entry.get("role")
    elif isinstance(details, list):
        for entry in details:
            if isinstance(entry, dict) and entry.get("name") == npc_name:
                return entry.get("role")
    return None
```

**Note sur `world_role_hint`** : la tâche [41](41_world_generator_zones_triggers.md) se concentre sur zones et triggers. Pour que les roles d'NPC soient produits par le world generator, il faut **un petit add-on** au prompt `system_world_generator.txt` demandant `role: <archetype>` dans chaque `npc_details`. Cet add-on peut être fait dans cette tâche [43] directement (puisqu'elle touche déjà l'hydration) ou bundlé avec [41]. **Décision** : le faire ici pour garder [41] focalisé.

## Acceptance criteria

- [ ] `_build_npc_by_context` dispatche correctement sur villain / world_role_hint / combat beat / commoner.
- [ ] Un NPC villain est hydraté avec son stat_block custom de l'arc.
- [ ] Un NPC avec `role=captain` dans npc_details est hydraté avec `get_archetype("captain")`.
- [ ] Un commoner sans contexte spécial reste fragile (hp=8, ac=12).
- [ ] Upgrade idempotent : relancer hydrate_scene sur un villain déjà hydraté avec les mauvaises stats le corrige.
- [ ] L'upgrade préserve `dialogue_history`, `secrets`, `description`, `personality` de l'NPC existant.
- [ ] Le prompt world generator demande un `role` dans `npc_details` (add-on mineur dans cette tâche).

## Tests à ajouter

Dans `tests/bot/test_scene_hydration.py` (nouveau ou étendu) :

- `test_hydrate_villain_uses_arc_stat_block`.
- `test_hydrate_villain_fallback_to_generic_boss_if_stat_block_none`.
- `test_hydrate_world_role_captain_uses_archetype`.
- `test_hydrate_commoner_default_when_no_context`.
- `test_hydrate_upgrades_existing_weak_villain`.
- `test_hydrate_preserves_narrative_fields_on_upgrade`.
- `test_hydrate_commoner_in_social_beat_stays_commoner`.

## Hors scope

- **Ne pas** toucher au code world generator au-delà de l'add-on mineur du `role` dans `npc_details`.
- **Ne pas** implémenter le combat AI — tâches [50](50_scripted_minion_ai.md)/[51](51_elite_behavior_profiles.md)/[52](52_boss_llm_tactician.md).
- **Ne pas** hydrater les NPCs spawnés par un combat_trigger (ambush) — ces NPCs sont créés à la volée par `enter_combat` en tâche [20](20_combat_entry_module.md), qui peut appeler `_build_npc_by_context` directement.

## Validation finale

```bash
uv run pytest tests/bot/test_scene_hydration.py -v
uv run ruff check bot/scene_hydration.py
uv run mypy bot/scene_hydration.py
```
