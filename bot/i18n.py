"""UI translation tables for Discord display labels.

Engine enum values and DB storage remain in English.
Only display strings shown to users are translated here.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Race labels
# ---------------------------------------------------------------------------

RACE_LABELS: dict[str, dict[str, str]] = {
    "fr": {
        "Human": "Humain",
        "Elf": "Elfe",
        "Dwarf": "Nain",
        "Halfling": "Halfelin",
        "Half-Orc": "Demi-Orc",
        "Gnome": "Gnome",
        "Tiefling": "Tieffelin",
    },
}

# ---------------------------------------------------------------------------
# Class labels
# ---------------------------------------------------------------------------

CLASS_LABELS: dict[str, dict[str, str]] = {
    "fr": {
        "Fighter": "Guerrier",
        "Wizard": "Mage",
        "Rogue": "Roublard",
        "Cleric": "Clerc",
        "Ranger": "Rôdeur",
        "Barbarian": "Barbare",
    },
}

# ---------------------------------------------------------------------------
# Alignment labels
# ---------------------------------------------------------------------------

ALIGNMENT_LABELS: dict[str, dict[str, str]] = {
    "fr": {
        "Lawful Good": "Loyal Bon",
        "Neutral Good": "Neutre Bon",
        "Chaotic Good": "Chaotique Bon",
        "Lawful Neutral": "Loyal Neutre",
        "True Neutral": "Neutre Vrai",
        "Chaotic Neutral": "Chaotique Neutre",
        "Lawful Evil": "Loyal Mauvais",
        "Neutral Evil": "Neutre Mauvais",
        "Chaotic Evil": "Chaotique Mauvais",
    },
}

# ---------------------------------------------------------------------------
# Editable field labels (character edit menu)
# ---------------------------------------------------------------------------

EDIT_FIELD_LABELS: dict[str, dict[str, str]] = {
    "fr": {
        "race": "Race",
        "class": "Classe",
        "alignment": "Alignement",
        "stats": "Statistiques",
        "skills": "Competences",
        "name": "Nom",
    },
}

# ---------------------------------------------------------------------------
# Starter kit labels
# ---------------------------------------------------------------------------

KIT_LABELS: dict[str, dict[str, dict[str, str]]] = {
    "fr": {
        # Fighter
        "Sword & Shield": {
            "name": "Épée & Bouclier",
            "description": "Un guerrier équilibré avec une solide défense.",
        },
        "Two-Handed Warrior": {
            "name": "Guerrier à deux mains",
            "description": "Un guerrier puissant maniant une hache imposante.",
        },
        "Archer": {
            "name": "Archer",
            "description": "Un guerrier à distance avec une lame de secours.",
        },
        # Wizard
        "Classic Arcanist": {
            "name": "Arcaniste classique",
            "description": "Un mage traditionnel avec bâton et armure légère.",
        },
        "War Scholar": {
            "name": "Érudit de guerre",
            "description": "Un mage de combat favorisant l'agilité.",
        },
        # Rogue
        "Shadow Blade": {
            "name": "Lame de l'ombre",
            "description": "Un roublard en double lame pour le combat rapproché.",
        },
        "Scout": {
            "name": "Éclaireur",
            "description": "Un roublard à distance avec une dague de secours.",
        },
        # Cleric
        "Battle Priest": {
            "name": "Prêtre de bataille",
            "description": "Un clerc lourdement armé en première ligne.",
        },
        "Healer": {
            "name": "Guérisseur",
            "description": "Un clerc légèrement armé, axé sur le soutien.",
        },
        # Ranger
        "Woodland Archer": {
            "name": "Archer sylvestre",
            "description": "Un rôdeur à distance avec une arme de corps à corps.",
        },
        "Dual Wielder": {
            "name": "Combattant double",
            "description": "Un rôdeur au corps à corps avec lame et dague.",
        },
        # Barbarian
        "Berserker": {
            "name": "Berserker",
            "description": "Un barbare enragé maniant une puissante hache.",
        },
        "Savage Fighter": {
            "name": "Combattant sauvage",
            "description": "Un barbare maniant deux haches à main.",
        },
    },
}

# ---------------------------------------------------------------------------
# Countdown & party card labels
# ---------------------------------------------------------------------------

COUNTDOWN_LABELS: dict[str, dict[int, str]] = {
    "fr": {
        3: "Préparez-vous, aventuriers...",
        2: "Les destins convergent...",
        1: "L'aventure commence...",
    },
    "en": {
        3: "Prepare yourselves, adventurers...",
        2: "Fates converge...",
        1: "The adventure begins...",
    },
}

ABILITY_LABELS: dict[str, dict[str, str]] = {
    "fr": {
        "STR": "FOR",
        "DEX": "DEX",
        "CON": "CON",
        "INT": "INT",
        "WIS": "SAG",
        "CHA": "CHA",
    },
}

PARTY_CARD_LABELS: dict[str, dict[str, str]] = {
    "fr": {
        "level": "Niveau",
        "hp": "PV",
        "ac": "CA",
    },
    "en": {
        "level": "Level",
        "hp": "HP",
        "ac": "AC",
    },
}

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def get_label(
    table: dict[str, dict[str, Any]],
    language: str,
    key: str,
) -> str:
    """Return the translated label for *key* in *language*, or *key* as fallback."""
    return table.get(language, {}).get(key, key)


def get_kit_label(language: str, kit_name: str, field: str) -> str:
    """Return a translated kit field ('name' or 'description'), or fallback.

    For 'name', fallback is the original kit_name. For 'description',
    fallback is an empty string (caller should use kit.description directly).
    """
    return KIT_LABELS.get(language, {}).get(kit_name, {}).get(field, kit_name if field == "name" else "")
