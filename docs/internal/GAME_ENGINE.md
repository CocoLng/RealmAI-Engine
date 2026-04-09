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
| [conditions.py](../../engine/conditions.py) | 219 | 15 conditions SRD, durées, interactions |
| [combat.py](../../engine/combat.py) | 706 | Initiative, attaques, sorts, death saves, trivial resolve |
| [validators.py](../../engine/validators.py) | 352 | Légalité d'action (combat + exploration) |
| [starter_gear.py](../../engine/starter_gear.py) | 177 | 15 kits pré-construits (6 classes × 2-3 kits) |

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
- `compute_ac(character)` — ⚠ retourne 10 + DEX mod, **ignore l'armure**. Dead code de fait ; combat utilise `inventory.compute_ac_from_equipment()`. Voir [ISSUES.md](ISSUES.md).
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

15 conditions : BLINDED, CHARMED, DEAFENED, FRIGHTENED, GRAPPLED, INCAPACITATED, INVISIBLE, PARALYZED, PETRIFIED, POISONED, PRONE, RESTRAINED, STUNNED, UNCONSCIOUS, EXHAUSTION.

### `ActiveCondition`

```python
ActiveCondition(
    type: ConditionType,
    source: str,
    duration_rounds: int | None,  # None = indéfini
    save_ability: Ability | None,  # ⚠ dead field
    save_dc: int | None,           # ⚠ dead field
    exhaustion_level: int,         # 0-6 pour EXHAUSTION
)
```

### Functions utilitaires

- `apply_condition`, `remove_condition` (raise ValueError si absent), `has_condition`, `get_condition`
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

## Dépendances inter-modules

```
dice ← character ← {combat, starter_gear, validators}
inventory ← {combat, spells, starter_gear, validators}
conditions ← {combat, validators}
spells ← {combat, validators}
```

Aucun import depuis `ai/`, `bot/`, `memory/`, `world/`, `db/` (sauf combat.py qui touche `world.npc` pour `trivial_resolve` — unique exception).

## Idiomes / style

⚠ **Inconsistance mutation vs copie** : certaines fonctions mutent en place ET retournent (`level_up`, `apply_damage`, `cast_spell`), d'autres retournent une copie (`add_item`, `equip_item`). Aucune convention claire.

### Points d'amélioration

Voir [ISSUES.md](ISSUES.md). Synthèse :
- Constantes magiques non extraites (thresholds outcome, attunement max, cantrip scale).
- Dead fields (`ActiveCondition.save_ability`, `save_dc`) et dead function (`character.compute_ac`).
- Parsing dé fragile (combat `_double_dice`, spells cantrip).
- Pas de loader pour custom spells / items.
- Mutation patterns incohérents.
