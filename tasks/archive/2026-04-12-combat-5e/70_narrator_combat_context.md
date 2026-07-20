# Task 70 — Narrateur : contexte combat

**Phase** : 7 — Narrateur & cohérence narrative
**Dépendances** : [22](22_multi_enemy_combat_state.md)
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

Le narrateur LLM reçoit aujourd'hui un contexte assemblé par [bot/scene_hydration.py::describe_scene_for_narrator](../../bot/scene_hydration.py) qui inclut la location, les NPCs présents, les items, le beat courant. Il **ne reçoit pas** :

- `beat.encounter_type`
- Le fait qu'un combat est actif
- Le round courant, le combattant actif
- Les HP des combattants (filtrés pour ne pas trop spoiler)
- Les derniers événements mécaniques (attaques ratées, conditions appliquées)

Résultat : le narrateur peut dire "le combat commence" dans un beat combat même si aucun combat mécanique n'est actif, ou inversement, narrer comme en exploration pendant un combat. Il faut lui donner les informations pour **cohérer** sa prose avec l'état réel.

## Scope

1. Étendre `describe_scene_for_narrator(session, actor_name)` pour injecter :
   - Le type de beat.
   - Une section `COMBAT ACTIVE` quand `session.combat_state.is_active`.
   - L'ordre de tour, le combattant actif, le round.
   - Les HP visibles (filtrés : pas d'exact HP sur les NPCs au-delà d'un tier "visible").
   - Les 3 derniers événements mécaniques (texte court).
2. Mettre à jour [ai/prompts/system_narrator.txt](../../ai/prompts/system_narrator.txt) avec une section "COMBAT ACTIVE — règles de narration" qui force :
   - Respecter les résultats mécaniques (un miss est un miss, pas "presque touché qui blesse quand même").
   - Structurer la narration tour par tour.
   - Terminer chaque narration de combat par une invitation au tour suivant ("À votre tour", "L'ennemi se prépare").
   - Ne jamais laisser un joueur "ignorer" le combat.
   - Ton tendu/urgent.

## Fichiers à modifier

- [bot/scene_hydration.py](../../bot/scene_hydration.py) — fonction `describe_scene_for_narrator`.
- [ai/prompts/system_narrator.txt](../../ai/prompts/system_narrator.txt) — ajouter section combat.

## Implémentation — esquisse

```python
# bot/scene_hydration.py

def describe_scene_for_narrator(
    session: "GameSession",
    actor_name: str,
) -> str:
    lines: list[str] = []
    # ... existing location / items / NPCs sections ...

    # Beat with type
    if getattr(session, "story_arc", None) is not None:
        arc = session.story_arc
        beat = arc.beats[arc.current_beat_index]
        lines.append(
            f"## Current story beat\n"
            f"{beat.title} — {beat.description}\n"
            f"Type: {beat.encounter_type}"
        )
        if beat.is_twist:
            lines.append("(Ce beat est un TWIST — reveal narratif attendu)")

    # Combat context
    if session.combat_state is not None and session.combat_state.is_active:
        lines.append(_describe_combat_for_narrator(session, actor_name))

    lines.append(f"## Acting character\n{actor_name}")
    return "\n\n".join(lines)


def _describe_combat_for_narrator(
    session: "GameSession",
    actor_name: str,
) -> str:
    state = session.combat_state
    assert state is not None

    lines: list[str] = ["## COMBAT ACTIVE"]
    lines.append(f"Round {state.round_number}")
    current = state.combatants[state.current_turn_index]
    lines.append(f"Tour actuel de : {current.name}")

    # Participants with filtered HP
    lines.append("\n### Combattants")
    for c in state.combatants:
        if not c.is_alive and not c.fled:
            lines.append(f"- {c.name}: MORT")
            continue
        if c.fled:
            lines.append(f"- {c.name}: a fui")
            continue
        # Filter HP visibility: PCs show exact, NPCs show descriptive
        if c.side.value == "Player":
            hp_str = f"{c.character.hp}/{c.character.max_hp} HP"
        else:
            hp_str = _describe_npc_hp_vague(c)
        zone = f" (zone: {c.current_zone})" if c.current_zone else ""
        conditions = ", ".join(cc.condition_type.value for cc in c.conditions)
        cond_str = f" [{conditions}]" if conditions else ""
        lines.append(f"- {c.name}: {hp_str}{zone}{cond_str}")

    # Recent events
    recent = getattr(session, "_recent_combat_events", [])[-3:]
    if recent:
        lines.append("\n### Derniers événements mécaniques")
        for ev in recent:
            lines.append(f"- {ev}")

    lines.append(
        "\n**Règle** : tu DOIS respecter l'état mécanique. "
        "Les règles de combat s'appliquent, aucun joueur ne peut "
        "ignorer la situation, les dégâts et échecs rolled sont canon."
    )
    return "\n".join(lines)


def _describe_npc_hp_vague(combatant) -> str:
    """Describe NPC HP in vague terms to avoid spoilers."""
    ratio = combatant.character.hp / max(1, combatant.character.max_hp)
    if ratio > 0.8:
        return "indemne"
    if ratio > 0.5:
        return "légèrement blessé"
    if ratio > 0.2:
        return "gravement blessé"
    return "à l'article de la mort"
```

**Prompt** `ai/prompts/system_narrator.txt` — section à ajouter :

```
## COMBAT ACTIVE — règles spéciales

Quand le contexte contient une section `## COMBAT ACTIVE`, tu dois adapter
ta narration aux règles D&D 5e en cours :

1. **Respect mécanique absolu** : tu narres CE QUI S'EST PASSÉ d'après les
   résultats d'engine, jamais l'inverse. Un "jet raté" est un miss franc,
   pas "presque touché qui blesse quand même". Un "5 dégâts" est exactement
   5, pas "terriblement blessé" si c'est une égratignure pour un combattant
   à 60 HP.

2. **Tour par tour** : tu narres UNIQUEMENT l'action en cours. Pas de prévisions
   des tours futurs. Pas de "pendant ce temps, ailleurs". Le combat est ici
   et maintenant, un tour à la fois.

3. **Invitation au tour suivant** : termine ta narration par une phrase qui
   invite le prochain combattant à agir. Exemples :
   - "À votre tour, [nom]."
   - "L'ennemi se prépare à frapper..."
   - "Le silence retombe ; qui ose le premier mouvement ?"

4. **Pas d'évasion passive** : tu NE DOIS PAS laisser un joueur "passer à
   autre chose" comme s'il était en exploration. Si un joueur tente
   d'ignorer le combat (regarder autour, fouiller un coffre), tu narres
   qu'il est interrompu par la violence ambiante.

5. **Ton tendu et urgent** : phrases courtes, verbes d'action, rythme
   saccadé. Pas de longues descriptions contemplatives pendant un combat.

6. **Les HP NPC sont vagues** : tu vois "légèrement blessé", "gravement
   blessé", "à l'article de la mort" — tu NE vois PAS les HP exacts des
   ennemis. Respecte cette imprécision pour préserver la tension.

7. **Phases de boss** : si une "pending_phase_transition" apparaît, c'est
   un MOMENT DRAMATIQUE à souligner. Utilise le `narrative_cue` fourni
   comme base et amplifie-le. Description d'une phrase minimum, trois
   maximum.

Si le type de beat est "combat" ou "boss", maintiens ce ton même entre les
tours mécaniques — c'est le contexte général de la scène.
```

## Acceptance criteria

- [ ] `describe_scene_for_narrator` inclut `beat.encounter_type`.
- [ ] Quand combat actif, une section `## COMBAT ACTIVE` apparaît.
- [ ] Les HP des NPCs sont vagues ("gravement blessé").
- [ ] Les HP des PCs sont exacts.
- [ ] Les 3 derniers événements mécaniques sont inclus.
- [ ] Le prompt narrateur contient la section "COMBAT ACTIVE — règles spéciales".
- [ ] Les tests de regression sur les narrations hors combat passent (pas de breaking change).

## Tests à ajouter

Dans `tests/bot/test_scene_hydration.py` :

- `test_describe_scene_includes_beat_encounter_type`.
- `test_describe_scene_includes_combat_section_when_active`.
- `test_describe_scene_no_combat_section_when_inactive`.
- `test_describe_scene_pc_hp_exact_npc_hp_vague`.
- `test_describe_scene_includes_recent_events`.
- `test_describe_scene_combat_rule_reminder_present`.

Dans `tests/ai/test_narrator_prompt.py` (nouveau ou existant) :

- `test_narrator_prompt_contains_combat_active_section` — charger le fichier et vérifier que la section existe.

## Hors scope

- **Ne pas** appeler le narrateur avec un prompt différent pendant combat — on ajuste seulement le contexte, pas le prompt de base (sauf la section ajoutée).
- **Ne pas** implémenter le prompt de phase transition — tâche [71](71_narrator_phase_transition_prompt.md).

## Validation finale

```bash
uv run pytest tests/bot/test_scene_hydration.py tests/ai/test_narrator_prompt.py -v
uv run ruff check bot/scene_hydration.py
uv run mypy bot/scene_hydration.py
```
