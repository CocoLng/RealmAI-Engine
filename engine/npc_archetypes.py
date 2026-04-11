"""NPC Archetype Library — structured variety for NPC generation.

Provides ~20 archetypes across 5 categories. Python picks an archetype,
the LLM enriches it with campaign-specific details.
"""

import random

from pydantic import BaseModel


class NPCArchetype(BaseModel):
    """A template for generating distinctive NPCs."""

    name: str
    """Internal identifier, e.g. 'maire_corrompu'."""

    category: str
    """One of: authority, commerce, knowledge, trouble, commoner."""

    label_fr: str
    """Human-readable French label, e.g. 'Maire corrompu'."""

    contradictory_traits: list[str]
    """2-3 contradictory trait pairs, e.g. ['généreux mais paranoïaque']."""

    narrative_hook: str
    """A secret or situation that creates story potential."""

    dialogue_pattern: str
    """A distinctive speech quirk for the LLM to reproduce."""


# ---------------------------------------------------------------------------
# Archetype definitions
# ---------------------------------------------------------------------------

_ARCHETYPES: list[NPCArchetype] = [
    # === Authority ===
    NPCArchetype(
        name="maire_corrompu",
        category="authority",
        label_fr="Maire corrompu",
        contradictory_traits=["charismatique mais vénal", "protecteur mais égoïste"],
        narrative_hook="détourne les fonds de la milice",
        dialogue_pattern="ponctue ses phrases de références légales",
    ),
    NPCArchetype(
        name="capitaine_use",
        category="authority",
        label_fr="Capitaine usé",
        contradictory_traits=["courageux mais alcoolique", "loyal mais désabusé"],
        narrative_hook="a perdu un régiment entier par sa faute",
        dialogue_pattern="donne des ordres même en conversation banale",
    ),
    NPCArchetype(
        name="pretresse_dissidente",
        category="authority",
        label_fr="Prêtresse dissidente",
        contradictory_traits=["pieuse mais rebelle", "douce mais intransigeante"],
        narrative_hook="pratique un culte interdit en secret",
        dialogue_pattern="cite les écritures de manière détournée",
    ),
    NPCArchetype(
        name="juge_partial",
        category="authority",
        label_fr="Juge partial",
        contradictory_traits=["érudit mais corruptible", "imposant mais lâche"],
        narrative_hook="a condamné un innocent pour protéger un allié",
        dialogue_pattern="parle en tournures juridiques ampoulées",
    ),
    # === Commerce ===
    NPCArchetype(
        name="marchand_endette",
        category="commerce",
        label_fr="Marchand endetté",
        contradictory_traits=["jovial mais désespéré", "généreux mais calculateur"],
        narrative_hook="doit une fortune à un criminel local",
        dialogue_pattern="ramène tout à des métaphores commerciales",
    ),
    NPCArchetype(
        name="contrebandier_moral",
        category="commerce",
        label_fr="Contrebandier moral",
        contradictory_traits=["illégal mais juste", "méfiant mais fidèle"],
        narrative_hook="fait passer des réfugiés en zone sûre",
        dialogue_pattern="utilise un argot de marin incompréhensible",
    ),
    NPCArchetype(
        name="artisan_obsede",
        category="commerce",
        label_fr="Artisan obsédé",
        contradictory_traits=[
            "génial mais maniaque",
            "patient mais colérique si interrompu",
        ],
        narrative_hook="travaille sur un chef-d'œuvre impossible depuis des années",
        dialogue_pattern="compare tout à son artisanat",
    ),
    NPCArchetype(
        name="preteur_patient",
        category="commerce",
        label_fr="Prêteur patient",
        contradictory_traits=["poli mais impitoyable", "calme mais rancunier"],
        narrative_hook="possède les dettes de la moitié du quartier",
        dialogue_pattern="parle lentement en pesant chaque mot",
    ),
    # === Knowledge ===
    NPCArchetype(
        name="bibliothecaire_paranoiaque",
        category="knowledge",
        label_fr="Bibliothécaire paranoïaque",
        contradictory_traits=["érudit mais craintif", "méticuleux mais soupçonneux"],
        narrative_hook="protège un livre contenant un secret dangereux",
        dialogue_pattern="chuchote constamment et vérifie que personne n'écoute",
    ),
    NPCArchetype(
        name="oracle_frauduleux",
        category="knowledge",
        label_fr="Oracle frauduleux",
        contradictory_traits=[
            "charismatique mais menteur",
            "perspicace mais manipulateur",
        ],
        narrative_hook="ses fausses prophéties se réalisent par coïncidence",
        dialogue_pattern="parle en énigmes et questions rhétoriques",
    ),
    NPCArchetype(
        name="herboriste_ermite",
        category="knowledge",
        label_fr="Herboriste ermite",
        contradictory_traits=["bienveillant mais misanthrope", "sage mais rancunier"],
        narrative_hook="connaît un remède que personne d'autre ne peut préparer",
        dialogue_pattern="nomme les gens par des surnoms de plantes",
    ),
    NPCArchetype(
        name="cartographe_aveugle",
        category="knowledge",
        label_fr="Cartographe aveugle",
        contradictory_traits=["précis mais limité", "confiant mais vulnérable"],
        narrative_hook="ses cartes montrent des lieux qui n'existent pas encore",
        dialogue_pattern="décrit tout en distances et directions cardinales",
    ),
    # === Trouble ===
    NPCArchetype(
        name="voleur_repenti",
        category="trouble",
        label_fr="Voleur repenti",
        contradictory_traits=[
            "honnête mais tenté",
            "humble mais orgueilleux de son passé",
        ],
        narrative_hook="son ancien gang le recherche",
        dialogue_pattern="évalue instinctivement la valeur de tout ce qu'il voit",
    ),
    NPCArchetype(
        name="espion_double",
        category="trouble",
        label_fr="Espion double",
        contradictory_traits=["charmant mais insaisissable", "utile mais dangereux"],
        narrative_hook="ne sait plus pour qui il travaille vraiment",
        dialogue_pattern="change de sujet avec une aisance suspecte",
    ),
    NPCArchetype(
        name="noble_en_exil",
        category="trouble",
        label_fr="Noble en exil",
        contradictory_traits=["raffiné mais aigri", "fier mais démuni"],
        narrative_hook="porte un sceau royal qu'il ne montre à personne",
        dialogue_pattern="corrige la grammaire des autres",
    ),
    NPCArchetype(
        name="deserteur_traque",
        category="trouble",
        label_fr="Déserteur traqué",
        contradictory_traits=[
            "nerveux mais compétent",
            "solitaire mais en manque de compagnie",
        ],
        narrative_hook="possède des informations militaires cruciales",
        dialogue_pattern="sursaute au moindre bruit et parle vite",
    ),
    # === Commoner ===
    NPCArchetype(
        name="enfant_debrouillard",
        category="commoner",
        label_fr="Enfant débrouillard",
        contradictory_traits=[
            "innocent mais rusé",
            "courageux mais inconscient du danger",
        ],
        narrative_hook="a été témoin d'un crime que personne ne croit",
        dialogue_pattern="pose des questions embarrassantes avec candeur",
    ),
    NPCArchetype(
        name="veteran_traumatise",
        category="commoner",
        label_fr="Vétéran traumatisé",
        contradictory_traits=[
            "fort mais brisé",
            "protecteur mais violent par réflexe",
        ],
        narrative_hook="revit une bataille chaque nuit",
        dialogue_pattern="mélange présent et souvenirs de guerre",
    ),
    NPCArchetype(
        name="barde_menteur",
        category="commoner",
        label_fr="Barde menteur",
        contradictory_traits=[
            "éloquent mais mythomane",
            "divertissant mais peu fiable",
        ],
        narrative_hook="une de ses histoires inventées est en réalité vraie",
        dialogue_pattern="commence chaque phrase par 'On raconte que...'",
    ),
    NPCArchetype(
        name="veuve_vengeresse",
        category="commoner",
        label_fr="Veuve vengeresse",
        contradictory_traits=[
            "douce en apparence mais déterminée",
            "patiente mais implacable",
        ],
        narrative_hook="prépare méthodiquement la chute de l'assassin de son époux",
        dialogue_pattern="parle au passé comme si tout était déjà accompli",
    ),
]

# Build a lookup by name for O(1) access
_BY_NAME: dict[str, NPCArchetype] = {a.name: a for a in _ARCHETYPES}

# All valid categories
CATEGORIES: list[str] = sorted({a.category for a in _ARCHETYPES})


def get_all_archetypes() -> list[NPCArchetype]:
    """Return a copy of the full archetype list."""
    return list(_ARCHETYPES)


def pick_archetype(exclude: list[str] | None = None) -> NPCArchetype:
    """Randomly select one archetype from the pool.

    Args:
        exclude: Archetype names to skip. If all are excluded,
                 falls back to picking from the full pool.

    Returns:
        A randomly chosen NPCArchetype.
    """
    exclude_set = set(exclude) if exclude else set()
    candidates = [a for a in _ARCHETYPES if a.name not in exclude_set]
    if not candidates:
        candidates = _ARCHETYPES
    return random.choice(candidates)


def pick_archetypes_for_location(
    count: int,
    exclude: list[str] | None = None,
) -> list[NPCArchetype]:
    """Pick multiple archetypes for a location, favouring category variety.

    Args:
        count: How many archetypes to pick.
        exclude: Archetype names to skip.

    Returns:
        A list of *count* distinct NPCArchetype instances with varied
        categories where possible.
    """
    exclude_set = set(exclude) if exclude else set()
    candidates = [a for a in _ARCHETYPES if a.name not in exclude_set]
    if not candidates:
        candidates = list(_ARCHETYPES)

    if count >= len(candidates):
        return list(candidates)

    # Group by category
    by_category: dict[str, list[NPCArchetype]] = {}
    for a in candidates:
        by_category.setdefault(a.category, []).append(a)

    picked: list[NPCArchetype] = []
    picked_names: set[str] = set()

    # Round-robin across shuffled categories for variety
    category_keys = list(by_category.keys())
    random.shuffle(category_keys)

    cat_index = 0
    while len(picked) < count:
        cat = category_keys[cat_index % len(category_keys)]
        pool = [a for a in by_category.get(cat, []) if a.name not in picked_names]
        if pool:
            choice = random.choice(pool)
            picked.append(choice)
            picked_names.add(choice.name)
        cat_index += 1
        # Safety: if we've cycled through all categories without finding
        # anything, break and fill from remaining candidates
        if cat_index > len(category_keys) * len(candidates):
            remaining = [a for a in candidates if a.name not in picked_names]
            needed = count - len(picked)
            picked.extend(random.sample(remaining, min(needed, len(remaining))))
            break

    return picked
