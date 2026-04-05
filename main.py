"""Terminal REPL — solo combat demo.

Proves the engine works end-to-end without AI or Discord.
"""

from __future__ import annotations

from engine.character import (
    Ability,
    AbilityScores,
    Alignment,
    Character,
    CharacterClass,
    Race,
    Size,
    compute_modifier,
    compute_max_hp,
    compute_proficiency_bonus,
    CLASS_HIT_DIE,
    CLASS_SAVING_THROWS,
    RACIAL_SIZE,
    RACIAL_SPEED,
)
from engine.combat import (
    AttackResult,
    CombatSide,
    CombatState,
    Combatant,
    DeathSaveResult,
    SpellCastResult,
    advance_turn,
    get_current_combatant,
    is_combat_over,
    resolve_attack,
    resolve_death_save,
    resolve_spell,
    start_combat,
)
from engine.conditions import ConditionType, has_condition
from engine.inventory import (
    DamageType,
    EquipmentSlot,
    Inventory,
    Weapon,
    WeaponCategory,
    WeaponProperty,
    add_item,
    compute_ac_from_equipment,
    create_inventory,
    equip_item,
    ITEM_CATALOG,
)
from engine.spells import (
    SPELL_CATALOG,
    can_cast_spell,
    create_spellcaster_state,
)
from engine.validators import Action, ActionType, validate_action


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_character(
    name: str,
    race: Race,
    char_class: CharacterClass,
    level: int,
    scores: AbilityScores,
    alignment: Alignment = Alignment.TRUE_NEUTRAL,
) -> Character:
    """Build a Character at the given level with correct derived stats."""
    con_mod = compute_modifier(scores.get(Ability.CON))
    dex_mod = compute_modifier(scores.get(Ability.DEX))
    max_hp = compute_max_hp(char_class, level, con_mod)
    return Character(
        name=name,
        race=race,
        char_class=char_class,
        level=level,
        xp=0,
        alignment=alignment,
        ability_scores=scores,
        hp=max_hp,
        max_hp=max_hp,
        ac=10 + dex_mod,
        speed=RACIAL_SPEED[race],
        proficiency_bonus=compute_proficiency_bonus(level),
        saving_throw_proficiencies=CLASS_SAVING_THROWS[char_class],
        hit_die=CLASS_HIT_DIE[char_class],
        size=RACIAL_SIZE[race],
    )


def _equip_and_set_ac(char: Character, inv: Inventory) -> None:
    """Update character AC based on equipped items (mutates char in place)."""
    dex_mod = compute_modifier(char.ability_scores.get(Ability.DEX))
    char.ac = compute_ac_from_equipment(inv.equipped, dex_mod)


def _get_input(prompt: str, valid: range) -> int:
    """Prompt until the player enters a valid integer in *valid*."""
    while True:
        try:
            choice = int(input(prompt))
            if choice in valid:
                return choice
        except (ValueError, EOFError):
            pass
        print(f"  Please enter a number between {valid.start} and {valid.stop - 1}.")


# ---------------------------------------------------------------------------
# Character / enemy factories
# ---------------------------------------------------------------------------


def create_fighter() -> Combatant:
    """Arden the Fighter — Longsword + Chain Mail + Shield."""
    scores = AbilityScores(STR=16, DEX=14, CON=14, INT=10, WIS=12, CHA=8)
    char = _make_character("Arden", Race.HUMAN, CharacterClass.FIGHTER, 3, scores)

    inv = create_inventory()
    inv = add_item(inv, ITEM_CATALOG["Longsword"].model_copy())
    inv = add_item(inv, ITEM_CATALOG["Chain Mail"].model_copy())
    inv = add_item(inv, ITEM_CATALOG["Shield"].model_copy())
    inv = equip_item(inv, "Longsword", EquipmentSlot.MAIN_HAND)
    inv = equip_item(inv, "Chain Mail", EquipmentSlot.ARMOR)
    inv = equip_item(inv, "Shield", EquipmentSlot.OFF_HAND)
    _equip_and_set_ac(char, inv)

    return Combatant(name="Arden", side=CombatSide.PLAYER, character=char, inventory=inv)


def create_wizard() -> Combatant:
    """Elara the Wizard — Quarterstaff + spells."""
    scores = AbilityScores(STR=8, DEX=14, CON=12, INT=16, WIS=13, CHA=10)
    char = _make_character("Elara", Race.ELF, CharacterClass.WIZARD, 3, scores)

    inv = create_inventory()
    inv = add_item(inv, ITEM_CATALOG["Quarterstaff"].model_copy())
    inv = equip_item(inv, "Quarterstaff", EquipmentSlot.MAIN_HAND)
    _equip_and_set_ac(char, inv)

    sc = create_spellcaster_state(CharacterClass.WIZARD, 3)
    assert sc is not None
    sc.spells_known = [
        "Fire Bolt",
        "Magic Missile",
        "Shield",
        "Burning Hands",
        "Scorching Ray",
        "Hold Person",
    ]

    return Combatant(
        name="Elara", side=CombatSide.PLAYER, character=char,
        inventory=inv, spellcaster=sc,
    )


def create_rogue() -> Combatant:
    """Shade the Rogue — Shortsword + Dagger + Leather Armor."""
    scores = AbilityScores(STR=10, DEX=16, CON=12, INT=14, WIS=10, CHA=13)
    char = _make_character("Shade", Race.HALFLING, CharacterClass.ROGUE, 3, scores)

    inv = create_inventory()
    inv = add_item(inv, ITEM_CATALOG["Shortsword"].model_copy())
    inv = add_item(inv, ITEM_CATALOG["Dagger"].model_copy())
    inv = add_item(inv, ITEM_CATALOG["Leather"].model_copy())
    inv = equip_item(inv, "Shortsword", EquipmentSlot.MAIN_HAND)
    inv = equip_item(inv, "Dagger", EquipmentSlot.OFF_HAND)
    inv = equip_item(inv, "Leather", EquipmentSlot.ARMOR)
    _equip_and_set_ac(char, inv)

    return Combatant(
        name="Shade", side=CombatSide.PLAYER, character=char, inventory=inv,
    )


def create_goblin() -> Combatant:
    """A Goblin enemy with a Scimitar."""
    scores = AbilityScores(STR=8, DEX=14, CON=10, INT=10, WIS=8, CHA=8)
    scimitar = Weapon(
        name="Scimitar",
        damage_dice="1d6",
        damage_type=DamageType.SLASHING,
        weapon_category=WeaponCategory.MARTIAL_MELEE,
        weight=3.0,
        properties=[WeaponProperty.FINESSE, WeaponProperty.LIGHT],
    )
    char = Character(
        name="Goblin",
        race=Race.HUMAN,
        char_class=CharacterClass.FIGHTER,
        level=1,
        xp=0,
        ability_scores=scores,
        hp=7,
        max_hp=7,
        ac=15,
        speed=30,
        proficiency_bonus=2,
        saving_throw_proficiencies=(Ability.STR, Ability.CON),
        hit_die="1d8",
        size=Size.SMALL,
    )
    inv = create_inventory()
    inv = add_item(inv, scimitar)
    inv = equip_item(inv, "Scimitar", EquipmentSlot.MAIN_HAND)

    return Combatant(name="Goblin", side=CombatSide.ENEMY, character=char, inventory=inv)


def create_skeleton() -> Combatant:
    """A Skeleton enemy with a Shortsword."""
    scores = AbilityScores(STR=10, DEX=14, CON=15, INT=6, WIS=8, CHA=5)
    char = Character(
        name="Skeleton",
        race=Race.HUMAN,
        char_class=CharacterClass.FIGHTER,
        level=1,
        xp=0,
        ability_scores=scores,
        hp=13,
        max_hp=13,
        ac=13,
        speed=30,
        proficiency_bonus=2,
        saving_throw_proficiencies=(Ability.STR, Ability.CON),
        hit_die="1d8",
        size=Size.MEDIUM,
    )
    inv = create_inventory()
    inv = add_item(inv, ITEM_CATALOG["Shortsword"].model_copy())
    inv = equip_item(inv, "Shortsword", EquipmentSlot.MAIN_HAND)

    return Combatant(
        name="Skeleton", side=CombatSide.ENEMY, character=char, inventory=inv,
    )


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def _hp_bar(current: int, maximum: int, width: int = 20) -> str:
    """Render a text-based HP bar."""
    ratio = max(0, current) / maximum if maximum > 0 else 0
    filled = round(ratio * width)
    empty = width - filled
    return f"[{'#' * filled}{'.' * empty}] {current}/{maximum}"


def display_combat_status(state: CombatState) -> None:
    """Print the initiative order and HP for all combatants."""
    print(f"\n--- Round {state.round_number} ---")
    print("Initiative order:")
    for i, c in enumerate(state.combatants):
        marker = " >> " if i == state.current_turn_index else "    "
        status = "DEAD" if not c.is_alive else _hp_bar(c.character.hp, c.character.max_hp)
        conds = ""
        if c.conditions:
            cond_names = [cond.condition_type.value for cond in c.conditions]
            conds = f" ({', '.join(cond_names)})"
        print(f"{marker}{c.name} (Init {c.initiative}) | {status}{conds}")
    print()


def display_attack_result(result: AttackResult) -> None:
    """Print the outcome of a weapon attack."""
    crit_tag = " CRITICAL!" if result.critical else ""
    print(f"  {result.attacker} attacks {result.defender} with {result.weapon_name}...")
    if result.hit:
        print(
            f"  Attack roll: {result.attack_roll} "
            f"(total {result.attack_total}) vs AC {result.ac}. Hit!{crit_tag}"
        )
        print(
            f"  Damage: {result.damage} {result.damage_type.value}. "
            f"{result.defender} HP: {result.defender_hp_remaining}"
        )
        if result.defender_hp_remaining <= 0:
            print(f"  {result.defender} falls!")
    else:
        print(
            f"  Attack roll: {result.attack_roll} "
            f"(total {result.attack_total}) vs AC {result.ac}. Miss!"
        )


def display_spell_result(result: SpellCastResult) -> None:
    """Print the outcome of a spell cast."""
    slot_info = f" (slot level {result.slot_used})" if result.slot_used else " (cantrip)"
    print(f"  {result.caster} casts {result.spell_name}{slot_info}!")
    if result.target:
        print(f"  Target: {result.target}")
    if result.damage > 0:
        if result.target_failed_save:
            print(f"  Damage: {result.damage}")
        else:
            print(f"  Target saved! Half damage: {result.damage}")
    if result.healing > 0:
        print(f"  Healing: {result.healing}")
    if result.condition_applied:
        print(f"  Condition applied: {result.condition_applied}")


def display_death_save_result(result: DeathSaveResult) -> None:
    """Print the outcome of a death saving throw."""
    outcome = "Success" if result.success else "Failure"
    print(f"  {result.character_name} rolls a death save: {result.roll} - {outcome}!")
    print(
        f"  Successes: {result.total_successes}/3  "
        f"Failures: {result.total_failures}/3"
    )
    if result.stabilized:
        print(f"  {result.character_name} has stabilized!")
    if result.died:
        print(f"  {result.character_name} has died!")
    if result.revived:
        print(f"  NAT 20! {result.character_name} springs back to consciousness at 1 HP!")


# ---------------------------------------------------------------------------
# Turn logic
# ---------------------------------------------------------------------------


def _get_alive_enemies(state: CombatState, attacker_side: CombatSide) -> list[Combatant]:
    """Return living combatants on the opposite side."""
    return [
        c for c in state.combatants
        if c.is_alive and c.side != attacker_side
    ]


def _find_weapon(combatant: Combatant) -> Weapon | None:
    """Return the main-hand weapon, or None."""
    item = combatant.inventory.equipped.get(EquipmentSlot.MAIN_HAND)
    if isinstance(item, Weapon):
        return item
    return None


def player_turn(player: Combatant, state: CombatState) -> None:
    """Handle the player's turn: menu selection, validation, resolution."""
    # Death save check
    if player.character.hp <= 0 and has_condition(player.conditions, ConditionType.UNCONSCIOUS):
        print(f"[{player.name}'s turn] HP: 0/{player.character.max_hp} (Unconscious)")
        print("  Rolling death saving throw...")
        result = resolve_death_save(player)
        display_death_save_result(result)
        return

    print(f"[{player.name}'s turn] HP: {player.character.hp}/{player.character.max_hp}")

    # Build menu
    options: list[tuple[str, ActionType]] = [("Attack", ActionType.ATTACK)]
    if player.spellcaster is not None:
        options.append(("Cast Spell", ActionType.CAST_SPELL))
    options.append(("Defend", ActionType.DEFEND))
    options.append(("Flee", ActionType.FLEE))

    while True:
        for i, (label, _) in enumerate(options, 1):
            print(f"  {i}. {label}")

        choice = _get_input("> ", range(1, len(options) + 1))
        action_type = options[choice - 1][1]

        if action_type == ActionType.ATTACK:
            if not _handle_attack(player, state):
                continue
            return

        if action_type == ActionType.CAST_SPELL:
            if not _handle_cast_spell(player, state):
                continue
            return

        if action_type == ActionType.DEFEND:
            action = Action(actor_name=player.name, action_type=ActionType.DEFEND)
            vr = validate_action(action, state)
            if not vr.is_valid:
                print(f"  Invalid: {vr.error_message}")
                continue
            print(f"  {player.name} takes the Dodge action (disadvantage on incoming attacks).")
            return

        if action_type == ActionType.FLEE:
            action = Action(actor_name=player.name, action_type=ActionType.FLEE)
            vr = validate_action(action, state)
            if not vr.is_valid:
                print(f"  Invalid: {vr.error_message}")
                continue
            print(f"  {player.name} attempts to flee... (not implemented, pick another action)")
            continue


def _handle_attack(player: Combatant, state: CombatState) -> bool:
    """Handle the attack sub-menu. Returns True if action was taken."""
    weapon = _find_weapon(player)
    if weapon is None:
        print("  No weapon equipped!")
        return False

    targets = _get_alive_enemies(state, player.side)
    if not targets:
        print("  No targets available!")
        return False

    print("  Choose target:")
    for i, t in enumerate(targets, 1):
        print(f"    {i}. {t.name} (HP: {t.character.hp}/{t.character.max_hp})")

    idx = _get_input("  > ", range(1, len(targets) + 1))
    target = targets[idx - 1]

    action = Action(
        actor_name=player.name,
        action_type=ActionType.ATTACK,
        target_name=target.name,
        weapon_name=weapon.name,
    )
    vr = validate_action(action, state)
    if not vr.is_valid:
        print(f"  Invalid: {vr.error_message}")
        return False

    result = resolve_attack(player, target, weapon)
    display_attack_result(result)
    return True


def _handle_cast_spell(player: Combatant, state: CombatState) -> bool:
    """Handle the spell casting sub-menu. Returns True if action was taken."""
    sc = player.spellcaster
    if sc is None:
        return False

    # Build castable spell list
    castable: list[tuple[str, int]] = []
    for spell_name in sc.spells_known:
        spell = SPELL_CATALOG.get(spell_name)
        if spell is None:
            continue
        if can_cast_spell(sc, spell):
            castable.append((spell_name, spell.level))

    if not castable:
        print("  No spells available (no slots remaining)!")
        return False

    # Show slots
    print("  Spell slots:", end="")
    for lvl in sorted(sc.spell_slots_remaining):
        print(f"  Lv{lvl}: {sc.spell_slots_remaining[lvl]}/{sc.spell_slots_max[lvl]}", end="")
    print()

    print("  Choose spell:")
    for i, (name, level) in enumerate(castable, 1):
        lvl_str = "cantrip" if level == 0 else f"level {level}"
        print(f"    {i}. {name} ({lvl_str})")
    print("    0. Back")

    choice = _get_input("  > ", range(0, len(castable) + 1))
    if choice == 0:
        return False

    spell_name = castable[choice - 1][0]
    spell = SPELL_CATALOG[spell_name]

    # Determine target
    target: Combatant | None = None
    needs_target = spell.damage_dice is not None or spell.condition_applied is not None
    is_healing = spell.healing_dice is not None

    if is_healing:
        # Self-target for healing in solo play
        target = player
    elif needs_target:
        targets = _get_alive_enemies(state, player.side)
        if not targets:
            print("  No targets available!")
            return False
        print("  Choose target:")
        for i, t in enumerate(targets, 1):
            print(f"    {i}. {t.name} (HP: {t.character.hp}/{t.character.max_hp})")
        idx = _get_input("  > ", range(1, len(targets) + 1))
        target = targets[idx - 1]

    action = Action(
        actor_name=player.name,
        action_type=ActionType.CAST_SPELL,
        spell_name=spell_name,
        target_name=target.name if target else None,
    )
    vr = validate_action(action, state)
    if not vr.is_valid:
        print(f"  Invalid: {vr.error_message}")
        return False

    result = resolve_spell(player, spell, target)
    display_spell_result(result)
    return True


def enemy_turn(enemy: Combatant, state: CombatState) -> None:
    """Simple enemy AI: attack the player with their equipped weapon."""
    print(f"[{enemy.name}'s turn] HP: {enemy.character.hp}/{enemy.character.max_hp}")

    weapon = _find_weapon(enemy)
    if weapon is None:
        print(f"  {enemy.name} has no weapon and does nothing.")
        return

    targets = _get_alive_enemies(state, enemy.side)
    if not targets:
        return

    # Attack the first living player
    target = targets[0]
    result = resolve_attack(enemy, target, weapon)
    display_attack_result(result)


# ---------------------------------------------------------------------------
# Game loop
# ---------------------------------------------------------------------------


def game_loop() -> None:
    """Run the full combat encounter."""
    print("=" * 50)
    print("  RealmAI Engine — Solo Combat Demo")
    print("=" * 50)
    print()
    print("Choose your character:")
    print("  1. Arden the Fighter (Longsword + Chain Mail + Shield)")
    print("  2. Elara the Wizard  (Quarterstaff + Spells)")
    print("  3. Shade the Rogue   (Shortsword + Dagger + Leather)")

    choice = _get_input("> ", range(1, 4))
    factories = {1: create_fighter, 2: create_wizard, 3: create_rogue}
    player = factories[choice]()

    print(
        f"\nYou are {player.name} the {player.character.char_class.value} "
        f"(Level {player.character.level})"
    )
    print(
        f"HP: {player.character.hp}/{player.character.max_hp}  "
        f"AC: {player.character.ac}"
    )
    if player.spellcaster:
        print(f"Spells: {', '.join(player.spellcaster.spells_known)}")

    goblin = create_goblin()
    skeleton = create_skeleton()
    print("\nA Goblin and a Skeleton emerge from the shadows!\n")

    state = start_combat([player, goblin, skeleton])

    # Main combat loop
    while state.is_active:
        display_combat_status(state)
        current = get_current_combatant(state)

        if not current.is_alive:
            state = advance_turn(state)
            continue

        if current.side == CombatSide.PLAYER:
            player_turn(current, state)
        else:
            enemy_turn(current, state)

        state = advance_turn(state)

        if is_combat_over(state):
            state.is_active = False

    # Outcome
    print()
    print("=" * 50)
    players_alive = any(
        c.is_alive for c in state.combatants if c.side == CombatSide.PLAYER
    )
    if players_alive:
        print("  VICTORY!")
    else:
        print("  DEFEAT...")
    print("=" * 50)

    # Final stats
    print("\nFinal state:")
    for c in state.combatants:
        status = "DEAD" if not c.is_alive else f"HP {c.character.hp}/{c.character.max_hp}"
        print(f"  {c.name}: {status}")


def main() -> None:
    """Entry point."""
    try:
        game_loop()
    except KeyboardInterrupt:
        print("\n\nGame interrupted. Farewell, adventurer!")
    except EOFError:
        print("\n\nNo more input. Farewell, adventurer!")


if __name__ == "__main__":
    main()
