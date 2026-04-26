"""Character creation for the character system."""

from .abilities import compute_modifier
from .classes import CLASS_FEATURES, CLASS_HIT_DIE, CLASS_SAVING_THROWS
from .enums import Ability, CharacterClass, Race, Skill
from .models import AbilityScores, Character
from .progression import compute_max_hp, compute_proficiency_bonus
from .races import RACIAL_FEATURES, RACIAL_SIZE, RACIAL_SPEED


def create_character(
    name: str,
    race: Race,
    char_class: CharacterClass,
    ability_scores: AbilityScores,
    skill_proficiencies: list[Skill] | None = None,
    concept: str = "",
) -> Character:
    """Create a new level-1 character with derived stats, features, and skills.

    - ability_scores: already with racial bonuses applied
    - skill_proficiencies: if None, empty list (Discord wizard will provide)
    - concept: optional RP flavor text (max 200 chars), passed to narrator prompts
    - Features: automatically populated from RACIAL_FEATURES and CLASS_FEATURES
    """
    con_mod = compute_modifier(ability_scores.get(Ability.CON))
    dex_mod = compute_modifier(ability_scores.get(Ability.DEX))
    max_hp = compute_max_hp(char_class, 1, con_mod)

    racial_features = RACIAL_FEATURES[race]
    class_features = CLASS_FEATURES[char_class]
    features = list(racial_features) + list(class_features)

    return Character(
        name=name,
        concept=concept,
        race=race,
        char_class=char_class,
        level=1,
        xp=0,
        ability_scores=ability_scores,
        hp=max_hp,
        max_hp=max_hp,
        ac=10 + dex_mod,
        speed=RACIAL_SPEED[race],
        proficiency_bonus=compute_proficiency_bonus(1),
        saving_throw_proficiencies=CLASS_SAVING_THROWS[char_class],
        hit_die=CLASS_HIT_DIE[char_class],
        size=RACIAL_SIZE[race],
        features=features,
        skill_proficiencies=skill_proficiencies if skill_proficiencies is not None else [],
    )
