"""Character creation for the character system."""

from .abilities import compute_modifier
from .classes import CLASS_HIT_DIE, CLASS_SAVING_THROWS
from .enums import Ability, Alignment, CharacterClass, Race
from .models import AbilityScores, Character
from .progression import compute_max_hp, compute_proficiency_bonus
from .races import RACIAL_SIZE, RACIAL_SPEED


def create_character(
    name: str,
    race: Race,
    char_class: CharacterClass,
    ability_scores: AbilityScores,
    alignment: Alignment = Alignment.TRUE_NEUTRAL,
) -> Character:
    """Create a new level-1 character with computed derived stats.

    The provided ability_scores should already include racial bonuses.
    """
    con_mod = compute_modifier(ability_scores.get(Ability.CON))
    dex_mod = compute_modifier(ability_scores.get(Ability.DEX))
    max_hp = compute_max_hp(char_class, 1, con_mod)

    return Character(
        name=name,
        race=race,
        char_class=char_class,
        level=1,
        xp=0,
        alignment=alignment,
        ability_scores=ability_scores,
        hp=max_hp,
        max_hp=max_hp,
        ac=10 + dex_mod,
        speed=RACIAL_SPEED[race],
        proficiency_bonus=compute_proficiency_bonus(1),
        saving_throw_proficiencies=CLASS_SAVING_THROWS[char_class],
        hit_die=CLASS_HIT_DIE[char_class],
        size=RACIAL_SIZE[race],
    )
