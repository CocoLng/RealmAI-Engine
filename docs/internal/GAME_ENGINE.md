# Game Engine — `engine/`

Python pur, déterministe, **sans aucun appel LLM**. ~3 150 lignes, ~11 800 lignes de tests. Couverture ~98%.

Inspiration SRD 5e simplifié. Chaque module porte une responsabilité unique.

## Arborescence

| Module | Lignes | Responsabilité |
|---|---|---|
| [dice.py](../../engine/dice.py) | 135 | Parseur `"2d6+3"`, jets d20 avec 6 tiers d'outcome |
| [character.py](../../engine/character.py) | 373 | Personnages, races, classes, stats, niveaux, XP |
| [inventory.py](../../engine/inventory.py) | 631 | Items, équipement, armures, weapons, slots, attunement |
| [spells.py](../../engine/spells.py) | 557 | Slots, cantrips scaling, catalogue ~20 sorts |
| [conditions.py](../../engine/conditions.py) | 325 | 17 conditions SRD (+ SURPRISED, CONCENTRATING), durées, interactions |
| [combat.py](../../engine/combat.py) | 1 100+ | Initiative 3 cas, turn management multi-ennemis, attaques (PC + NPC stat-block), sorts, death saves, action economy, zone movement + OOA, trivial resolve |
| [combat_trigger.py](../../engine/combat_trigger.py) | 90 | Modèle `CombatTrigger` / `CombatTriggerKind` / `InitiativeSide` consommé par `start_combat` |
| [validators.py](../../engine/validators.py) | 380 | Légalité d'action (combat + exploration) |
| [starter_gear.py](../../engine/starter_gear.py) | 177 | 15 kits pré-construits (6 classes × 2-3 kits) |
| [npc_stat_block.py](../../engine/npc_stat_block.py) | 170 | Stat block D&D 5e-style pour NPCs de combat (attacks, signatures, legendary, phases) |
| [npc_library.py](../../engine/npc_library.py) | 400 | 11 archétypes de combat (minions/elites/generic_boss) avec `get_archetype()` |

## `dice.py`

```python
roll("2d6+3") -> DiceResult(expression, rolls=[4,5], modifier=3, total=12)
roll_check("1d20+5", dc=15) -> D20CheckResult(dc, outcome, margin, natural_roll)
```

### `RollOutcome` enum

| Outcome | Condition |
|---|---|
| `CRITICAL_FAILURE` | nat 1 OU margin ≤ -5 |
| `FAILURE` | margin < 0 (non near) |
| `NEAR_FAILURE` | -5 < margin < 0 |
| `NEAR_SUCCESS` | 0 ≤ margin < 5 |
| `SUCCESS` | margin ≥ 5 (non crit) |
| `CRITICAL_SUCCESS` | nat 20 OU margin ≥ 10 |

Thresholds hardcoded (pas de constantes nommées). Nat 1/20 overrides margin.

## `character.py`

### Modèles

- `AbilityScores(str, dex, con, int_, wis, cha)` — 1-30, strict bounds.
- `Character(name, race, char_class, level, xp, alignment, scores, hp, max_hp, ac, speed, proficiency_bonus, saving_throw_proficiencies, hit_die, size)`.

### Enums

- `Ability` : STR, DEX, CON, INT, WIS, CHA
- `Race` : 7 races (HUMAN, ELF, DWARF, HALFLING, HALF_ORC, GNOME, TIEFLING)
- `CharacterClass` : 6 classes (FIGHTER, WIZARD, ROGUE, CLERIC, RANGER, BARBARIAN)
- `Alignment` : 9 alignements SRD
- `Size` : SMALL, MEDIUM

### Tables de lookup

- `RACIAL_ABILITY_BONUSES`, `RACIAL_SIZE`, `RACIAL_SPEED`
- `CLASS_HIT_DIE`, `CLASS_SAVING_THROWS`
- `XP_THRESHOLDS` (20 niveaux, 0 → 355 000)
- `PROFICIENCY_BONUS_BY_LEVEL` (2 → 6)

### Functions clés

- `roll_ability_scores()` — 4d6 drop lowest × 6
- `apply_racial_bonuses(scores, race)` — retourne une copie modifiée
- `create_character(name, race, char_class, scores, alignment)` — factory avec stats calculées
- `compute_max_hp(char_class, level, con_mod)` — average hit die per level + CON
- ~~`compute_ac(character)`~~ — supprimé (dead code). L'AC est calculée par `inventory.compute_ac_from_equipment()`.
- `add_xp(character, amount)` + `level_up(character)` — mutent en place

## `inventory.py`

### Modèles

- `Item` : name, type, weight, value_gp, rarity, description, requires_attunement, magical, stackable, quantity
- `Weapon(Item)` : + damage_dice, damage_type, category, properties, range_ft
- `Armor(Item)` : + category, base_ac, dex_cap, strength_required, stealth_disadvantage
- `Inventory(items, equipped: dict[EquipmentSlot, Item], attuned: list[str], gold)`

### Enums

- `ItemType` : WEAPON, ARMOR, SHIELD, POTION, SCROLL, ADVENTURING_GEAR, TOOL, AMMUNITION
- `EquipmentSlot` : 9 slots (MAIN_HAND, OFF_HAND, ARMOR, HEAD, HANDS, FEET, NECK, RING_1, RING_2)
- `WeaponCategory`, `ArmorCategory`, `WeaponProperty`, `DamageType` (11 types), `Rarity`

### `ITEM_CATALOG`

~25 items pré-définis (Longsword, Greataxe, Shortbow, Chain Mail, Leather Armor, Shield, Healing Potion, etc.). Utilisé par `starter_gear` et tests.

### Functions

- `add_item`, `remove_item` (retourne une copie)
- `equip_item(inventory, name, slot)` : two-handed clear OFF_HAND, swaps slot
- `attune_item` : max 3 items (hardcoded), raise si dépassé
- `compute_ac_from_equipment(equipped, dex_mod)` : la vraie fonction AC. Light = base + DEX, Medium = base + min(DEX, dex_cap ou 2), Heavy = base + 0 ; +2 si shield.
- `compute_carrying_capacity(strength, size)` : `str × 15`, halved si SMALL
- `is_encumbered(inventory, strength, size)`
- `default_weapon_for_class(char_class)` : retourne une arme par défaut depuis `ITEM_CATALOG` selon la classe. Utilisé par `build_npc_combatant()` pour armer les PNJs bootstrappés en combat. Mapping : Fighter/Ranger → Longsword, Rogue → Shortsword, Barbarian → Greataxe, Wizard → Quarterstaff, Cleric → Mace. Fallback → Shortsword.

## `spells.py`

### Modèles

- `Spell` : name, level (0-9), school, casting_time, range, components, duration_rounds, concentration, description, damage_dice, damage_type, healing_dice, saving_throw, condition_applied, higher_level_dice
- `SpellcasterState` : spellcasting_ability, spells_known, spell_slots_max, spell_slots_remaining, concentration_spell

### Enums

- `SpellSchool` : 8 écoles SRD
- `CastingTime` : ACTION, BONUS_ACTION, REACTION, MINUTE_1, MINUTE_10
- `SpellRange` : SELF, TOUCH, 30/60/90/120/150 ft

### Tables

- `CLASS_SPELLCASTING_ABILITY` — Fighter/Rogue/Barbarian → None
- `FULL_CASTER_SLOTS` — 20 niveaux × 9 spell levels (Wizard, Cleric)
- `HALF_CASTER_SLOTS` — Ranger (pas de slots niveau 1)
- `_CANTRIP_SCALE` — `[(17, 4), (11, 3), (5, 2), (1, 1)]`

### `SPELL_CATALOG`

~20 sorts :
- Cantrips : Fire Bolt, Sacred Flame, Eldritch Blast, Mage Hand, Light
- Level 1 : Magic Missile, Cure Wounds, Healing Word, Shield
- Level 2 : Hold Person, Scorching Ray, Mirror Image
- Level 3 : Fireball, Counterspell, Lightning Bolt

### Functions

- `can_cast_spell(state, spell)` — cantrip OU slot dispo à spell.level+
- `cast_spell(state, spell, slot_level)` — consomme slot, set concentration ⚠ **ne casse pas l'ancienne concentration**
- `get_cantrip_damage_dice(spell, caster_level)` — scaling via `_CANTRIP_SCALE`, parsing `"1dX"` ⚠ fragile
- `restore_spell_slots(state)` — long rest

## `conditions.py`

### `ConditionType` enum

17 conditions : BLINDED, CHARMED, DEAFENED, FRIGHTENED, GRAPPLED, INCAPACITATED, INVISIBLE, PARALYZED, PETRIFIED, POISONED, PRONE, RESTRAINED, STUNNED, UNCONSCIOUS, EXHAUSTION, **SURPRISED**, **CONCENTRATING**.

- `SURPRISED` — une créature surprise ne peut ni agir ni réagir pendant son premier tour. Le turn manager la retire via `consume_surprise_if_present()` à la fin de ce tour (helper dédié, pas via `tick_durations` — la "durée" de surprise n'est pas un round complet).
- `CONCENTRATING` — utilisée par la future couche de sorts. Helpers : `check_concentration_save(combatant, damage)` retourne un `D20CheckResult` sur un CON save DC `max(10, damage // 2)`, et `drop_concentration(combatant)` retire la condition sans toucher aux effets liés (responsabilité de l'appelant).

### `ActiveCondition`

```python
ActiveCondition(
    type: ConditionType,
    source: str,
    duration_rounds: int | None,  # None = indéfini
    save_ability: Ability | None,  # réservé pour future mécanique de saves de fin de condition
    save_dc: int | None,           # réservé pour future mécanique de saves de fin de condition
    exhaustion_level: int,         # 0-6 pour EXHAUSTION
)
```

### Functions utilitaires

- `apply_condition`, `remove_condition` (no-op si absent, log warning), `has_condition`, `get_condition`
- `tick_durations` — décrémente et retire quand ≤ 0
- `has_disadvantage_on_attacks`, `grants_advantage_to_attackers`
- `is_incapacitated`, `cannot_move`
- `auto_fails_str_dex_saves` — pour PARALYZED/PETRIFIED/STUNNED/UNCONSCIOUS

Frozensets de classification hardcodées. EXHAUSTION stack (1-6) ; les autres remplacent.

## `combat.py`

Module principal du combat — couvre initiative (3 cas surprise), turn management multi-ennemis, action economy 5e, zone movement + OOA, attaques (PC + NPC stat-block), sorts, death saves, damage, concentration hook, trivial resolve. Le LLM n'y met jamais les pieds.

### Modèles

- `Combatant(name, side, character, inventory, spellcaster, initiative, conditions, death_saves, is_alive, stat_block, fled, current_zone, action_budget, legendary_points_remaining, phase_save_bonus)` — les derniers champs (stat_block, fled, current_zone, action_budget, legendary_points_remaining, phase_save_bonus) ont été ajoutés en Phase 2 Task 22 pour que les tâches aval (32, 53, 54) n'aient pas à reshape le modèle.
- `CombatState(combat_id, combatants, round_number, current_turn_index, is_active, end_reason, pending_phase_narrations)` — `combat_id` est un UUID généré auto, `end_reason` (StrEnum `CombatEndReason`) est renseigné par `check_combat_end`/`advance_turn`, `pending_phase_narrations: list[PhaseTransitionEvent]` est la queue consommée par le narrateur (tâche 71).
- `ActionBudget(movement_remaining_feet, action_used, bonus_action_used, reaction_used_this_round, disengaged_this_turn)` — budget 5e par tour. `reset_for_new_turn(speed_feet)` refill Move/Action/Bonus sans toucher à la Reaction (persiste entre tours, reset au wrap de round).
- `CombatEndReason` StrEnum : `VICTORY`, `DEFEAT`, `FLED`, `TRUCE`.
- `PhaseTransitionEvent(combatant_name, phase_index, narrative_cue)` — événement queuing pour la tâche 54/71.
- `AttackResult`, `SpellCastResult`, `DeathSaveResult`, `TrivialResolveResult` (inchangés).

### Initiative — 3 cas de surprise

`start_combat(combatants, trigger: CombatTrigger | None = None)` délègue à un helper selon `trigger.surprise_side` :

| Cas | `InitiativeSide` | Ordre | SURPRISED |
|---|---|---|---|
| 1 — Agression joueur | `PLAYERS` | Aggresseur PC en tête, puis roll standard | Enemies nommés dans `trigger.enemy_names` |
| 2 — Ambush | `NPCS` | Ambushers NPC en tête (tri interne par initiative + DEX), puis roll standard | Tous les PCs |
| 3 — Face-à-face | `BOTH_READY` (ou `trigger=None`) | Roll `1d20 + DEX` standard, tiebreak DEX score | Personne |

La condition `SURPRISED` est consommée par `advance_turn` à la **fin** du premier tour du combattant surpris via `consume_surprise_if_present` — leur tour est un no-op (validator rejette), puis la condition est nettoyée. L'initiative rollée est stockée sur chaque `Combatant.initiative` pour reprise de session.

### Turn management

`advance_turn(state)` fait dans l'ordre : (1) tick conditions + consume SURPRISED sur le combattant sortant, (2) walk forward à l'index suivant en skippant les morts **ET** les `fled=True`, (3) si wrap → `round_number += 1` et reset `reaction_used_this_round` pour tous, (4) reset `action_budget` du nouveau combattant via `reset_for_new_turn(speed)`, (5) `check_combat_end` → set `is_active=False` + `end_reason` si terminal.

`check_combat_end(state)` retourne la raison de fin ou `None` : VICTORY si aucun ENEMY debout (non mort, non fui), DEFEAT si aucun PC debout, FLED si **tous** les PCs ont `fled=True` (distinction vs DEFEAT), mutual wipe → DEFEAT. Les cases TRUCE et l'override explicite sont réservés aux tâches 32/81. `is_combat_over(state)` reste un wrapper booléen pour la rétro-compatibilité.

### Action economy (`ActionBudget` + consume helpers)

| Helper | Effet |
|---|---|
| `consume_action(c)` | Flag `action_used` ; raise si déjà utilisé |
| `consume_bonus_action(c)` | Flag `bonus_action_used` ; raise si déjà utilisé |
| `consume_movement(c, feet)` | Soustrait de `movement_remaining_feet` ; raise si insuffisant ou si `feet<0` |
| `consume_reaction(c)` | Flag `reaction_used_this_round` ; raise si déjà utilisé ce round |

Ces helpers sont les seuls points de mutation du budget. Task 30 câblera les validators ; Task 50-52 les appelleront depuis les NPC brains.

### Zone movement + attaques d'opportunité

- `move_combatant_to_zone(state, combatant, target_zone, location)` : valide l'adjacence via `location.are_adjacent`, calcule le coût (15 ft/step, doublé sur `DIFFICULT_TERRAIN`), consomme le mouvement, déclenche une OOA de chaque ennemi vivant en mêlée dans la zone source (sauf si `Disengage` pris ce tour), relocalise le combattant. Retourne la liste des `AttackResult` OOA.
- `_resolve_opportunity_attack(attacker, defender)` : si l'attaquant a un `stat_block` avec au moins un `NPCAttack`, utilise `resolve_npc_attack` ; sinon retombe sur `resolve_attack` avec l'arme main hand (silent skip si rien d'équipé).
- `disengage(combatant)` : consomme l'Action et set `disengaged_this_turn`. `ActionType.DISENGAGE` existe côté validators (valid par common checks seulement — task 30 durcira).
- Si un combattant meurt pendant un move (OOA létale), la boucle OOA s'arrête et `current_zone` n'est **pas** mis à jour — le corps reste où il tombe.

### NPC attacks (`resolve_npc_attack`)

`resolve_npc_attack(attacker, defender, npc_attack)` miroir de `resolve_attack` mais tire ses numéros de `NPCAttack` (`to_hit_bonus`, `damage_dice`, `damage_type`) au lieu d'une `Weapon`. Même contrat `AttackResult`, mêmes règles (nat 1 auto-miss, nat 20 auto-crit, advantage/disadvantage depuis conditions, auto-crit sur cible UNCONSCIOUS/PARALYZED, doublement des dés sur crit). Le modificateur de dégâts est supposé déjà inclus dans `damage_dice` — aucun calcul de STR/DEX superposé.

### Concentration hook

`apply_damage(combatant, damage)` appelle `_on_damage_taken(combatant, damage)` avant la transition death/unconscious. Si le combattant est `CONCENTRATING`, un CON save est roulé via `check_concentration_save` (DC `max(10, damage // 2)`) ; sur échec, `drop_concentration` retire la condition. Le hook est idempotent si non-concentrating ou si `damage <= 0`. Toutes les sources de dégâts qui passent par `apply_damage` déclenchent le hook gratuitement (attacks, spells, futures zones HAZARD).

### Règles appliquées

- Advantage + disadvantage → cancel.
- Crit : double les dés **seulement**, modificateur ajouté une fois.
- Auto-crit si cible UNCONSCIOUS/PARALYZED (SRD).
- Upcast : +N × `higher_level_dice` par slot au-dessus.
- Save ability auto-fail si condition bloquante.
- ⚠ **Pas de check de proficiency** — bonus toujours ajouté.
- ⚠ `_double_dice()` parse fragile de `"NdM+X"`.

## `validators.py`

### `ActionType` enum

`ATTACK`, `CAST_SPELL`, `DEFEND`, `DISENGAGE`, `FLEE`, `USE_ITEM`, `LOOK`, `SEARCH`, `TALK`, `MOVE`, `INTERACT`, `PICKUP`, `IMPROVISE`, `QUESTION`.

### Functions

- `validate_action(action, state)` — dispatch par action_type (combat context)
- `validate_exploration_action(action)` — non-combat, règles simples
- `_validate_common` — acteur existe, vivant, son tour, pas incapacitated

### Gaps

- Pas de check de conflit de concentration (cast pendant concentration active).
- Pas de validation des deux-mains / off-hand.
- `IMPROVISE` toujours valide.
- `validate_cast_spell` compare `"Self"` en string hardcoded pour décider si target requise.

## `npc_stat_block.py`

Stat block optionnel attaché à un `NPC` via `NPC.stat_block: NPCStatBlock | None`. Les commoners purement narratifs laissent ce champ à `None`. Les NPCs combattables (minion/elite/boss) portent la payload complète.

### Modèles

- `NPCAttack(name, damage_dice, damage_type, to_hit_bonus, range_type, range_value)` — une entrée d'attaque nommée.
- `SignatureAbility(name, description, usage, uses_remaining, is_reaction, action_cost, effects)` — capacité tactique d'elite/boss.
- `SignatureAbilityEffect(kind, dice, damage_type, condition_name, condition_duration_rounds, save_ability, save_dc, target_scope)` — effet atomique déterministe résolu par l'engine.
- `LegendaryAction(name, cost, description, effects)` — action off-turn pour les boss. `cost` ∈ [1, 3].
- `PhaseTransition(trigger_hp_percent, narrative_cue, unlock_signatures, attack_bonus, save_bonus, triggered)` — seuil HP (1-99) qui débloque une nouvelle phase.
- `NPCStatBlock(tier, archetype, multiattack_count, attacks, signature_abilities, legendary_actions, legendary_points_per_round, phases, behavior_profile, aggression_threshold)`.

### Enums

- `NPCTier` : MINION, ELITE, BOSS.
- `BehaviorProfile` : AGGRESSIVE, DEFENSIVE, SUPPORT, TACTICAL (utilisé par les AI minion/elite scripted ; les bosses ignorent ce profile).
- `TargetScope` (Literal) : `single`, `zone`, `all_enemies`, `all_allies_in_zone`, `self`.

## `npc_library.py`

Librairie précalculée de 11 archétypes de combat. `get_archetype(name)` retourne une **nouvelle instance** à chaque appel (pas de shared state — utile pour `uses_remaining` et `phases[].triggered`).

- **Minions (1 attack, 0 signature, 0 legendary)** : `commoner`, `guard`, `bandit`, `cultist`.
- **Elites (2 attacks, 1 signature)** : `soldier` (Shield Wall), `captain` (Rally), `brute` (Reckless Charge), `mage` (Counterspell), `assassin` (Death Strike), `shaman` (Spirit Guardians).
- **Boss fallback** : `generic_boss` (multiattack 3, 3 signatures, 3 legendary actions, 2 phases HP, 3 legendary points/round). Utilisé par le hydration layer quand l'arc generator n'a pas produit de stat block custom pour le villain.

`list_archetypes()` retourne la liste triée, `ARCHETYPE_BUILDERS` expose le dict builder. `KeyError` si l'archétype est inconnu — les appelants doivent guarder ou catch.

Les archétypes narratifs (voir [`engine/npc_archetypes.py`](../../engine/npc_archetypes.py)) sont orthogonaux : ils décrivent la personnalité et le dialogue RP, pas les stats de combat.

## Combat zones (modèle `world/`)

Positionnement abstrait par **zones nommées** plutôt qu'une grille 5-pieds. `Location.combat_zones: list[Zone]` porte le graphe (voir [`world/combat_zone.py`](../../world/combat_zone.py)). Chaque `Zone` a un `name`, une `description`, une liste `adjacent_zone_names`, et des `tags: list[ZoneTag]`.

- `ZoneTag` : `COVER` (+2 AC vs ranged), `DIFFICULT_TERRAIN` (coût de mouvement doublé), `ELEVATED` (advantage sur attaques à distance depuis la zone), `HAZARD` (1d4 dégâts en entrant), `OBSCURED` (disadvantage sur attaques ciblant la zone).
- Le validator Pydantic de `Location` vérifie à la construction : pas de noms de zones dupliqués, pas d'auto-adjacence, chaque voisin existe, et **l'adjacence est symétrique** (si A liste B, alors B liste A).
- Helpers : `Location.has_combat_zones()`, `get_zone(name)`, `are_adjacent(a, b)`.
- `combat_zones=[]` est le défaut ; les locations existantes sans combat fonctionnent comme avant.

L'intégration côté combat est implémentée : `Combatant.current_zone: str | None`, `engine.combat.move_combatant_to_zone(state, combatant, target_zone, location)` pour le mouvement zone-à-zone avec validation d'adjacence, coût `DIFFICULT_TERRAIN` x2, déclenchement d'opportunity attacks, et `engine.combat.disengage(combatant)` pour l'action Disengage (voir la section « combat.py » ci-dessus).

## NPC AI — tactical brains (`engine/npc_ai/`)

Chaque NPC avec un `NPCStatBlock` a un cerveau tactique qui décide de son action à son tour. Le cerveau est dispatché par `tier` :

- **Minion** → [`engine/npc_ai/scripted.py::decide_minion_action`](../../engine/npc_ai/scripted.py) — heuristique pure, 3 règles (1) attaque la cible en range avec le moins de HP (tiebreak AC ascendant), (2) sinon step BFS vers la zone ennemie la plus proche, (3) sinon `Dodge` (DEFEND). Aucun appel LLM, pas de multi-attaques (un minion = `multiattack_count=1` par contrat de tier).
- **Elite** → [`engine/npc_ai/elite.py::decide_elite_action`](../../engine/npc_ai/elite.py) — dispatcher par `behavior_profile` : **AGGRESSIVE** priorise un signature damage si dispo puis attaque le weakest ; **DEFENSIVE** Dodge si HP < 30% sinon attaque prudemment ; **SUPPORT** soigne les alliés blessés via une signature heal (fallback attaque) ; **TACTICAL** cible en priorité les ennemis avec condition exploitable (FRIGHTENED / PRONE / PARALYZED / RESTRAINED / STUNNED). Fallback sur `decide_minion_action` si `stat_block is None`.
- **Boss** → `engine/npc_ai/boss_brain.py` + LLM tactician `ai/npc_tactician.py` (task 52, à venir).

`NPCActionPlan` (Pydantic) est le contrat de sortie commun : `action_type`, `target_name`, `weapon_name`, `move_to_zone`, `signature_name`, `rationale`. Le resolver `execute_action_plan(combatant, plan, state, location)` consomme l'Action via `consume_action`, route ATTACK standard via `resolve_npc_attack` (l'engine roule les dés, jamais le brain), ATTACK avec `signature_name` vers `execute_signature_ability`, MOVE via `move_combatant_to_zone` (OOA inclus), DEFEND via `consume_action` simple. Les ranged attacks du stat block permettent au brain de cibler à travers n'importe quelle zone (pas de LOS en MVP).

**Signature executor** (`execute_signature_ability(caster, signature, targets, state)`) résout les 3 kinds MVP : `damage` roule les dés puis `apply_damage`, `heal` roule puis `apply_healing` (clamp max HP), `condition` applique la `ActiveCondition` après échec d'un save (save_ability + save_dc sur l'effet, inclut le `phase_save_bonus` du combattant). `uses_remaining` est décrémenté quand c'est un `int`, laissé à `None` pour les `at_will`. Les 4 kinds restants (`aoe_damage`, `buff`, `debuff`, `move`) logguent un WARNING et retournent un summary de fallback — le caller peut alors réinvoquer `resolve_npc_attack` pour une attaque standard.

`decide_action_for(combatant, state, location)` est le **point d'entrée unique** côté `scripted.py` : il regarde `stat_block.tier` et route vers le bon brain (minion/elite, boss = fallback elite pour l'instant avant task 52). Le TurnManager (task 64) consommera ce dispatcher.

## Dépendances inter-modules

```
dice ← character ← {combat, starter_gear, validators}
inventory ← {combat, spells, starter_gear, validators}
conditions ← {combat, validators}
spells ← {combat, validators}
```

Aucun import depuis `ai/`, `bot/`, `memory/`, `world/`, `db/` (sauf combat.py qui touche `world.npc` pour `trivial_resolve` — unique exception).

## Idiomes / style

**Convention mutation vs copie** (documentée par docstrings) : `engine/inventory.py` retourne des copies (pattern immutable), tous les autres modules mutent en place et retournent l'objet.

### Points d'amélioration

Voir [ISSUES.md](ISSUES.md). Synthèse :
- Constantes magiques restantes (attunement max, cantrip scale, fuzzy thresholds).
- Pas de loader pour custom spells / items.
