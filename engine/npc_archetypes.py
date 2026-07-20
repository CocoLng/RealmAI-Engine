"""Narrative NPC archetype library — game content, deterministic selection.

Spec: world-generation-variety §3, decided and authored in
``2026-07-20-npc-archetypes-and-quest-retirement-design.md``. 20 archetypes
in 5 categories. Each provides the three ingredients the NPC generator
prompt expects: contradictory traits, one narrative hook, one dialogue
pattern.

Editorial charter (applies to every entry):

- **Nobody is what they announce.** Each archetype lives in the gap
  between a social function and an inner life. No villains, no sages —
  people stuck in compromises.
- **The hook is a scene, not an attribute.** It must be playable at the
  table, tonight.
- **The dialogue pattern is performable by a 4b model.** One simple,
  repeatable tic — recognizable in a single line.

Selection is pure Python (randomness via the ``random`` module only —
no LLM ever decides an archetype).
"""

import random
from collections.abc import Collection
from enum import StrEnum

from pydantic import BaseModel, Field


class ArchetypeCategory(StrEnum):
    """The five social strata the library covers."""

    AUTHORITY = "authority"
    TRADE = "trade"
    LORE = "lore"
    FRINGE = "fringe"
    FOLK = "folk"


class NPCArchetype(BaseModel):
    """One authored personality framework for the NPC generator."""

    id: str = Field(min_length=1)
    category: ArchetypeCategory
    label: str = Field(min_length=1)
    traits: list[str] = Field(min_length=2, max_length=3)
    hook: str = Field(min_length=1)
    dialogue_pattern: str = Field(min_length=1)


ARCHETYPES: list[NPCArchetype] = [
    # ------------------------------------------------------------------
    # AUTHORITY — le pouvoir, vécu comme un compromis permanent
    # ------------------------------------------------------------------
    NPCArchetype(
        id="juge_qui_negocie",
        category=ArchetypeCategory.AUTHORITY,
        label="Juge qui négocie",
        traits=[
            "incorruptible sur les principes",
            "marchande chaque verdict en coulisses",
            "dort trois heures par nuit",
        ],
        hook=(
            "Garde dans son coffre l'aveu signé d'un notable, qu'il n'a "
            "jamais osé produire au procès."
        ),
        dialogue_pattern=(
            "Pose toujours deux questions avant de répondre à une seule."
        ),
    ),
    NPCArchetype(
        id="capitaine_comptable",
        category=ArchetypeCategory.AUTHORITY,
        label="Capitaine qui compte ses morts",
        traits=[
            "courage physique intact",
            "refuse désormais tout risque pour ses hommes",
            "tient un registre obsessionnel de chaque perte",
        ],
        hook=(
            "Paie de sa poche la solde d'un déserteur qu'il couvre depuis "
            "des mois."
        ),
        dialogue_pattern=(
            "Chiffre tout — distances, effectifs, chances de survie — "
            "avant de donner un avis."
        ),
    ),
    NPCArchetype(
        id="pretresse_en_greve",
        category=ArchetypeCategory.AUTHORITY,
        label="Prêtresse en grève",
        traits=[
            "foi intacte",
            "refuse de célébrer le culte depuis un scandale",
            "charité clandestine",
        ],
        hook=(
            "Continue de bénir en secret ceux qui viennent la voir la "
            "nuit, contre l'interdit de son propre ordre."
        ),
        dialogue_pattern=(
            "Cite les textes sacrés uniquement pour les contredire."
        ),
    ),
    NPCArchetype(
        id="bourgmestre_otage",
        category=ArchetypeCategory.AUTHORITY,
        label="Bourgmestre otage de sa ville",
        traits=[
            "aime sincèrement ses administrés",
            "obéit à quelqu'un d'autre",
            "optimisme de façade épuisant",
        ],
        hook=(
            "Envoie chaque semaine un rapport scellé à un destinataire "
            "que personne en ville n'a jamais vu."
        ),
        dialogue_pattern=(
            "Répond aux questions gênantes en vantant un détail de sa "
            "ville."
        ),
    ),
    # ------------------------------------------------------------------
    # TRADE — l'échange, et ce qu'on accepte d'y perdre
    # ------------------------------------------------------------------
    NPCArchetype(
        id="marchande_de_dettes",
        category=ArchetypeCategory.TRADE,
        label="Marchande qui achète des dettes",
        traits=[
            "générosité calculée",
            "mémoire implacable des comptes",
            "déteste manipuler l'argent liquide",
        ],
        hook=(
            "Détient la reconnaissance de dette d'un personnage puissant "
            "du lieu, et attend le bon moment pour s'en servir."
        ),
        dialogue_pattern=(
            "Reformule toute conversation en termes de crédit et de dû."
        ),
    ),
    NPCArchetype(
        id="forgeron_sans_commande",
        category=ArchetypeCategory.TRADE,
        label="Forgeron qui refuse les commandes",
        traits=[
            "meilleur artisan de la région",
            "ne vend plus qu'aux gens qu'il juge dignes",
            "peur panique de son propre talent",
        ],
        hook=(
            "La dernière lame qu'il a forgée a servi à un meurtre — il "
            "veut la récupérer avant qu'on remonte jusqu'à lui."
        ),
        dialogue_pattern=(
            "Parle du métal comme d'une personne qui décide à sa place."
        ),
    ),
    NPCArchetype(
        id="colporteuse_reglee",
        category=ArchetypeCategory.TRADE,
        label="Colporteuse qui revient toujours",
        traits=[
            "bavarde inarrêtable",
            "ne dit jamais rien d'important gratuitement",
            "connaît toutes les routes sauf celle où elle est née",
        ],
        hook=(
            "Transporte sous son étal un paquet qu'elle a juré de ne "
            "jamais ouvrir ni remettre en retard."
        ),
        dialogue_pattern=(
            "Commence chaque phrase par le nom d'un village qu'elle a "
            "traversé."
        ),
    ),
    NPCArchetype(
        id="apothicaire_endette",
        category=ArchetypeCategory.TRADE,
        label="Apothicaire qui soigne à crédit",
        traits=[
            "refuse qu'on meure faute d'argent",
            "ruiné par sa propre bonté",
            "tient une seconde comptabilité, celle des poisons",
        ],
        hook=(
            "Quelqu'un rachète ses dettes une à une pour le tenir — il "
            "vend depuis peu des mixtures qu'il désapprouve."
        ),
        dialogue_pattern=(
            "Décrit les gens par leurs symptômes plutôt que par leur nom."
        ),
    ),
    # ------------------------------------------------------------------
    # LORE — le savoir, et le prix de ce qu'on choisit d'en faire
    # ------------------------------------------------------------------
    NPCArchetype(
        id="archiviste_censeure",
        category=ArchetypeCategory.LORE,
        label="Archiviste qui brûle des pages",
        traits=[
            "vénère les documents",
            "en détruit certains sur ordre",
            "mémoire parfaite de tout ce qu'elle a brûlé",
        ],
        hook=(
            "A conservé une copie unique de chaque page censurée, "
            "cachées hors des archives."
        ),
        dialogue_pattern=(
            "Corrige les dates et les noms des autres, même en pleine "
            "dispute."
        ),
    ),
    NPCArchetype(
        id="cartographe_du_vide",
        category=ArchetypeCategory.LORE,
        label="Cartographe des lieux disparus",
        traits=[
            "précision maniaque",
            "ne dessine que ce qui n'existe plus",
            "refuse de cartographier le présent",
        ],
        hook=(
            "Sa dernière carte montre un bâtiment que personne d'autre "
            "ne se rappelle avoir jamais vu."
        ),
        dialogue_pattern=(
            "Situe chaque événement par rapport à un lieu détruit."
        ),
    ),
    NPCArchetype(
        id="oracle_sceptique",
        category=ArchetypeCategory.LORE,
        label="Oracle qui ne croit pas ses visions",
        traits=[
            "visions authentiques",
            "scepticisme féroce envers lui-même",
            "honnêteté brutale sur ses tarifs",
        ],
        hook=(
            "Sa dernière vision concernait quelqu'un de présent dans la "
            "région, et il refuse de la raconter en entier."
        ),
        dialogue_pattern=(
            "Annonce ses prédictions puis énumère les raisons d'en "
            "douter."
        ),
    ),
    NPCArchetype(
        id="precepteur_banni",
        category=ArchetypeCategory.LORE,
        label="Précepteur banni des grandes maisons",
        traits=[
            "pédagogie brillante",
            "a enseigné à un élève quelque chose d'interdit",
            "nostalgie sans regret",
        ],
        hook=(
            "Son ancien élève est devenu quelqu'un de dangereux, et lui "
            "écrit encore."
        ),
        dialogue_pattern=(
            "Transforme toute réponse en leçon en trois points."
        ),
    ),
    # ------------------------------------------------------------------
    # FRINGE — la marge, où la loi et la morale divergent
    # ------------------------------------------------------------------
    NPCArchetype(
        id="contrebandiere_a_principes",
        category=ArchetypeCategory.FRINGE,
        label="Contrebandière à principes",
        traits=[
            "enfreint la loi méthodiquement",
            "code moral plus strict que la loi",
            "fierté d'artisan du travail bien fait",
        ],
        hook=(
            "A refusé une cargaison récente — et sait donc exactement "
            "qui a accepté de la passer à sa place."
        ),
        dialogue_pattern=(
            "Distingue toujours ce qui est illégal de ce qui est mal."
        ),
    ),
    NPCArchetype(
        id="espion_au_repos",
        category=ArchetypeCategory.FRINGE,
        label="Espion retraité deux fois",
        traits=[
            "paranoïa domestiquée en habitudes",
            "sincèrement las des secrets",
            "réflexes intacts",
        ],
        hook=(
            "Quelqu'un a réactivé son ancien signal de contact, et il "
            "fait semblant de ne pas l'avoir vu."
        ),
        dialogue_pattern=(
            "S'assoit toujours dos au mur et le fait remarquer aux "
            "autres."
        ),
    ),
    NPCArchetype(
        id="heritiere_sans_nom",
        category=ArchetypeCategory.FRINGE,
        label="Héritière qui a vendu son nom",
        traits=[
            "manières aristocratiques indélébiles",
            "mépris des aristocrates",
            "panique à l'idée d'être reconnue",
        ],
        hook=(
            "L'acheteur de son titre s'en sert pour couvrir des crimes, "
            "et elle seule peut le prouver."
        ),
        dialogue_pattern=(
            "Vouvoie absolument tout le monde et s'en excuse à chaque "
            "fois."
        ),
    ),
    NPCArchetype(
        id="deserteur_decore",
        category=ArchetypeCategory.FRINGE,
        label="Déserteur décoré",
        traits=[
            "héroïsme avéré",
            "a fui juste après sa plus grande victoire",
            "générosité brusque envers les inconnus",
        ],
        hook=(
            "Porte la médaille d'un camarade mort à sa place, et cherche "
            "sa famille sans oser la trouver."
        ),
        dialogue_pattern=(
            "Change de sujet dès qu'on parle de batailles — toujours "
            "vers la nourriture."
        ),
    ),
    # ------------------------------------------------------------------
    # FOLK — les petites gens, qui voient tout passer
    # ------------------------------------------------------------------
    NPCArchetype(
        id="gamine_des_raccourcis",
        category=ArchetypeCategory.FOLK,
        label="Gamine qui vend des raccourcis",
        traits=[
            "connaît chaque passage de la ville",
            "invente la moitié de ce qu'elle raconte",
            "loyauté féroce une fois achetée",
        ],
        hook=(
            "A vu par un soupirail quelque chose qu'elle ne comprend "
            "pas, mais qu'elle décrit très bien."
        ),
        dialogue_pattern=(
            "Négocie tout en trois tiers : un tiers maintenant, un tiers "
            "à mi-chemin, un tiers à l'arrivée."
        ),
    ),
    NPCArchetype(
        id="fossoyeur_bavard",
        category=ArchetypeCategory.FOLK,
        label="Fossoyeur qui parle aux absents",
        traits=[
            "gaieté déconcertante",
            "respect scrupuleux des morts",
            "mémoire des enterrements qu'on voudrait oublier",
        ],
        hook=(
            "Une tombe récente qu'il a creusée lui-même sonne creux, et "
            "il n'en a encore parlé à personne."
        ),
        dialogue_pattern=(
            "Rapporte l'opinion des morts comme s'ils suivaient la "
            "conversation."
        ),
    ),
    NPCArchetype(
        id="lavandiere_arbitre",
        category=ArchetypeCategory.FOLK,
        label="Lavandière qui arbitre le quartier",
        traits=[
            "autorité morale sans aucun titre",
            "impartialité féroce",
            "collectionne les secondes chances accordées",
        ],
        hook=(
            "Lave le linge de tout le quartier et a reconnu du sang sur "
            "une chemise qui n'aurait pas dû en porter."
        ),
        dialogue_pattern=(
            "Tranche les disputes par un proverbe, puis explique "
            "pourquoi le proverbe a tort."
        ),
    ),
    NPCArchetype(
        id="veteran_jardinier",
        category=ArchetypeCategory.FOLK,
        label="Vétéran qui plante des arbres",
        traits=[
            "violence enterrée sous la routine",
            "douceur volontaire, presque appliquée",
            "sommeil détruit",
        ],
        hook=(
            "Reconnaît dans un nouveau venu quelqu'un croisé sur un "
            "champ de bataille — du mauvais côté."
        ),
        dialogue_pattern=(
            "Mesure le temps en saisons de plantation, jamais en années."
        ),
    ),
]


def draw_archetypes(
    count: int,
    exclude: Collection[str] = (),
    rng: random.Random | None = None,
) -> list[NPCArchetype]:
    """Draw ``count`` archetypes, category-balanced, without replacement.

    Each round picks at most one archetype per category (categories in
    random order), so a location's cast spreads across social strata
    before any category repeats. ``exclude`` removes already-assigned ids
    from the initial pool. If the pool runs dry (overdraw, or everything
    excluded), it recycles to the full library rather than returning
    fewer than ``count`` — an archetype twice beats an NPC without one.
    """
    if count <= 0:
        return []
    if rng is None:
        rng = random.Random()

    excluded = set(exclude)
    available = [a for a in ARCHETYPES if a.id not in excluded]
    result: list[NPCArchetype] = []

    while len(result) < count:
        if not available:
            available = list(ARCHETYPES)
        by_category: dict[ArchetypeCategory, list[NPCArchetype]] = {}
        for arch in available:
            by_category.setdefault(arch.category, []).append(arch)
        categories = list(by_category)
        rng.shuffle(categories)
        for category in categories:
            if len(result) >= count:
                break
            chosen = rng.choice(by_category[category])
            result.append(chosen)
            available.remove(chosen)
    return result


def format_archetype_context(archetype: NPCArchetype) -> str:
    """Format an archetype as the prompt block ``system_npc_generator.txt``
    announces: contradictory traits, narrative hook, dialogue pattern."""
    traits = " ; ".join(archetype.traits)
    return (
        f"Archetype: {archetype.label}\n"
        f"Contradictory traits: {traits}\n"
        f"Narrative hook: {archetype.hook}\n"
        f"Dialogue pattern: {archetype.dialogue_pattern}"
    )
