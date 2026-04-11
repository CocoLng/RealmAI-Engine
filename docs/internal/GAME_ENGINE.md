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
| [combat.py](../../engine/combat.py) | 706 | Initiative, attaques, sorts, death saves, trivial resolve |
| [validators.py](../../engine/validators.py) | 352 | Légalité d'action (combat + exploration) |
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

Le plus gros module. 706 lignes. Couvre initiative, attaques, sorts, death saves, damage, trivial resolve.

### Modèles

- `Combatant(name, side, character, inventory, spellcaster, initiative, conditions, death_saves, is_alive)`
- `CombatState(combatants, round_number, current_turn_index, is_active)`
- `AttackResult` (complet : rolls, crit, outcome, damage, HP restant)
- `SpellCastResult`
- `DeathSaveResult`
- `TrivialResolveResult`

### Functions critiques

| Fonction | Rôle |
|---|---|
| `roll_initiative(combatant)` | 1d20 + DEX mod |
| `start_combat(combatants)` | Trie par initiative, init round |
| `advance_turn(state)` | Tick conditions, skip dead, incrément round |
| `resolve_attack(attacker, defender, weapon, advantage, disadvantage)` | Résolution complète. Double-dice on crit, auto-crit si défenseur UNCONSCIOUS/PARALYZED |
| `resolve_spell(caster, spell, target, slot_level)` | Damage (halved on save), healing, condition apply, upcasting |
| `resolve_death_save(combatant)` | Nat 1 = -2 échec, nat 20 = revive 1 HP, 3 succès = stabilize, 3 échecs = mort |
| `apply_damage(combatant, damage)` | Player → UNCONSCIOUS, enemy → instant death |
| `trivial_resolve(attacker, target_npc, weapon)` | 1 attack vs PNJ sans défense ; hardcode STR mod + 1d4 default damage. Pour Lot E. |

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

11 types : `ATTACK`, `CAST_SPELL`, `DEFEND`, `FLEE`, `USE_ITEM`, `LOOK`, `SEARCH`, `TALK`, `MOVE`, `INTERACT`, `PICKUP`, `IMPROVISE`.

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

L'intégration côté combat (tracking `Combatant.current_zone`, mouvement zone-à-zone, opportunity attacks) est portée par les tâches 22 et 24 du chantier combat — non câblée ici.

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
