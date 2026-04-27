"""Per-(encounter_type, encounter_subtype) recipes for native beat objectives.

Defines the deterministic blueprint used by the Arc Generator to:

1. Tell the LLM (via prompt examples) how to shape ``BeatObjective`` lists for
   each kind of beat.
2. Scaffold a fallback objective list when the LLM omits ``objectives`` for a
   beat — guarantees every beat is functionally completable by the
   ``BeatProgressionEngine`` even on partial LLM output.

This is pure deterministic Python — no LLM calls. Recipes encode design
choices about what "completion" should mean per beat type.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

from pydantic import BaseModel, Field

from world.story_arc import (
    AdvanceRule,
    BeatObjective,
    GateKind,
    ObjectiveGate,
    ObjectiveKind,
)


class ObjectiveTemplate(BaseModel):
    """One objective template inside a recipe — has placeholders for runtime
    target resolution but the kind/gate/required fields are fixed."""

    kind: ObjectiveKind
    target_source: str = Field(
        description=(
            "Where to pull the target string at scaffolding time. One of: "
            "'first_npc' (npc_names[0]), 'villain' (arc.villain_name), "
            "'location' (location_hint), 'beat_title' (slugified), "
            "'literal:<value>' (use <value> verbatim)."
        ),
    )
    description_template: str = Field(
        description=(
            "Human-readable description with placeholders {target}, "
            "{location}, {beat_title}."
        ),
    )
    required: bool = True
    gate_kind: GateKind | None = None
    gate_value: int | str | None = None
    fuzzy_threshold: float = 0.7


class BeatRecipe(BaseModel):
    """Composed objective shape for a (type, subtype) tuple."""

    encounter_type: str
    encounter_subtype: str | None
    objectives: list[ObjectiveTemplate]
    advance_rule: AdvanceRule = AdvanceRule.ALL_REQUIRED
    advance_threshold: int | None = None
    judge_rubric: str = ""
    """Hint for the BeatJudge LLM when partial matches need adjudication."""
    player_visible_hint: str = ""
    """Player-facing /hint level-1 sentence."""


# ---------------------------------------------------------------------------
# Recipe table
# ---------------------------------------------------------------------------

# Each recipe encodes: what does "this beat is done" mean, mechanically.
# The order matters — the scaffold uses the FIRST recipe whose
# (type, subtype) matches; if no subtype matches, it falls back to
# (type, None) wildcard.

_RECIPES: dict[tuple[str, str | None], BeatRecipe] = {
    # ===== SOCIAL =====
    ("social", "negotiation"): BeatRecipe(
        encounter_type="social",
        encounter_subtype="negotiation",
        objectives=[
            ObjectiveTemplate(
                kind=ObjectiveKind.TALK,
                target_source="first_npc",
                description_template=(
                    "Engager {target} dans une négociation et lui faire "
                    "révéler ses véritables conditions."
                ),
                gate_kind=GateKind.MIN_REVEALS,
                gate_value=2,
            ),
            ObjectiveTemplate(
                kind=ObjectiveKind.TALK,
                target_source="first_npc",
                description_template=(
                    "Faire pencher {target} en notre faveur (geste, concession, "
                    "preuve)."
                ),
                gate_kind=GateKind.MIN_DISPOSITION,
                gate_value=1,
                required=False,
            ),
        ],
        advance_rule=AdvanceRule.ALL_REQUIRED,
        judge_rubric=(
            "Avancer si le joueur a engagé une véritable négociation — pas "
            "un simple 'bonjour'. La conversation doit toucher les enjeux "
            "et faire bouger l'attitude du PNJ."
        ),
        player_visible_hint=(
            "Le PNJ a des conditions cachées — il faut creuser, pas se "
            "contenter de saluer."
        ),
    ),
    ("social", "interrogation"): BeatRecipe(
        encounter_type="social",
        encounter_subtype="interrogation",
        objectives=[
            ObjectiveTemplate(
                kind=ObjectiveKind.TALK,
                target_source="first_npc",
                description_template=(
                    "Soutirer à {target} les informations qu'il dissimule."
                ),
                gate_kind=GateKind.MIN_REVEALS,
                gate_value=3,
            ),
        ],
        judge_rubric=(
            "Avancer si le joueur a réellement extrait des éléments concrets "
            "de l'interrogé (au moins trois faits substantiels), pas juste "
            "des banalités."
        ),
        player_visible_hint="Soutirez plusieurs informations distinctes au PNJ.",
    ),
    ("social", "seduction"): BeatRecipe(
        encounter_type="social",
        encounter_subtype="seduction",
        objectives=[
            ObjectiveTemplate(
                kind=ObjectiveKind.TALK,
                target_source="first_npc",
                description_template=(
                    "Gagner la confiance ou l'affection de {target}."
                ),
                gate_kind=GateKind.MIN_DISPOSITION,
                gate_value=2,
            ),
        ],
        judge_rubric=(
            "Avancer si l'attitude du PNJ a clairement basculé dans la "
            "complicité — petits gestes, demi-aveux, intimité accordée."
        ),
        player_visible_hint="Faites monter la disposition du PNJ vers vous.",
    ),
    ("social", "deception"): BeatRecipe(
        encounter_type="social",
        encounter_subtype="deception",
        objectives=[
            ObjectiveTemplate(
                kind=ObjectiveKind.TALK,
                target_source="first_npc",
                description_template=(
                    "Faire avaler à {target} une fausse identité ou un "
                    "mensonge crédible."
                ),
                gate_kind=GateKind.MIN_REVEALS,
                gate_value=2,
            ),
        ],
        judge_rubric=(
            "Avancer si la cible a relâché sa garde et révélé ce qu'elle "
            "n'aurait pas dit à un inconnu."
        ),
        player_visible_hint=(
            "Construisez votre mensonge pour pousser le PNJ à se livrer."
        ),
    ),
    ("social", "ceremony"): BeatRecipe(
        encounter_type="social",
        encounter_subtype="ceremony",
        objectives=[
            ObjectiveTemplate(
                kind=ObjectiveKind.TALK,
                target_source="first_npc",
                description_template=(
                    "Participer rituellement à la cérémonie présidée par {target}."
                ),
                gate_kind=GateKind.MIN_REVEALS,
                gate_value=1,
            ),
            ObjectiveTemplate(
                kind=ObjectiveKind.FLAG,
                target_source="literal:ceremony_completed",
                description_template=(
                    "Achever la cérémonie selon le protocole — décision "
                    "explicitement prise par le joueur."
                ),
            ),
        ],
        advance_rule=AdvanceRule.ALL_REQUIRED,
        judge_rubric=(
            "Avancer si le joueur a explicitement engagé l'acte rituel "
            "(prêté serment, déposé l'offrande, prononcé la formule)."
        ),
        player_visible_hint=(
            "La cérémonie demande un engagement explicite, pas une simple "
            "présence."
        ),
    ),
    # Fallback social with no specific subtype.
    ("social", None): BeatRecipe(
        encounter_type="social",
        encounter_subtype=None,
        objectives=[
            ObjectiveTemplate(
                kind=ObjectiveKind.TALK,
                target_source="first_npc",
                description_template="Discuter avec {target} et obtenir des informations utiles.",
                gate_kind=GateKind.MIN_REVEALS,
                gate_value=2,
            ),
        ],
        judge_rubric=(
            "Avancer si la conversation a réellement produit du contenu — "
            "pas juste un échange de civilités."
        ),
        player_visible_hint="Le dialogue doit livrer du contenu substantiel.",
    ),
    # ===== COMBAT =====
    ("combat", None): BeatRecipe(
        encounter_type="combat",
        encounter_subtype=None,
        objectives=[
            ObjectiveTemplate(
                kind=ObjectiveKind.DEFEAT,
                target_source="first_npc",
                description_template="Vaincre {target}.",
            ),
        ],
        judge_rubric=(
            "Avancer dès que la cible est mécaniquement vaincue. Pas "
            "d'autre voie — le combat se résout aux dés."
        ),
        player_visible_hint="L'affrontement doit se conclure mécaniquement.",
    ),
    # ===== BOSS =====
    ("boss", None): BeatRecipe(
        encounter_type="boss",
        encounter_subtype=None,
        objectives=[
            ObjectiveTemplate(
                kind=ObjectiveKind.DEFEAT,
                target_source="villain",
                description_template="Abattre {target}, l'antagoniste de l'arc.",
            ),
        ],
        judge_rubric=(
            "L'arc se conclut sur la chute du villain. Avancer uniquement "
            "lorsque le villain est mécaniquement vaincu."
        ),
        player_visible_hint="Le boss doit tomber au combat — aucune issue alternative.",
    ),
    # ===== EXPLORATION =====
    ("exploration", "tracking"): BeatRecipe(
        encounter_type="exploration",
        encounter_subtype="tracking",
        objectives=[
            ObjectiveTemplate(
                kind=ObjectiveKind.ARRIVE,
                target_source="location",
                description_template="Suivre la piste jusqu'à {target}.",
            ),
        ],
        judge_rubric="Avancer si le groupe atteint le lieu de destination.",
        player_visible_hint="Suivez les indices jusqu'au lieu indiqué.",
    ),
    ("exploration", "infiltration"): BeatRecipe(
        encounter_type="exploration",
        encounter_subtype="infiltration",
        objectives=[
            ObjectiveTemplate(
                kind=ObjectiveKind.ARRIVE,
                target_source="location",
                description_template="Infiltrer {target} sans déclencher l'alerte.",
            ),
            ObjectiveTemplate(
                kind=ObjectiveKind.EXAMINE,
                target_source="beat_title",
                description_template=(
                    "Repérer un détail clé sur les lieux ({target})."
                ),
            ),
        ],
        advance_rule=AdvanceRule.M_OF_N,
        advance_threshold=2,
        judge_rubric=(
            "Avancer dès que le groupe a pénétré le lieu ET pris le temps "
            "d'observer un élément significatif."
        ),
        player_visible_hint=(
            "Atteignez les lieux et inspectez quelque chose d'utile."
        ),
    ),
    ("exploration", "navigation"): BeatRecipe(
        encounter_type="exploration",
        encounter_subtype="navigation",
        objectives=[
            ObjectiveTemplate(
                kind=ObjectiveKind.ARRIVE,
                target_source="location",
                description_template="Atteindre {target} en traversant le terrain.",
            ),
        ],
        judge_rubric="Avancer dès que le groupe arrive à destination.",
        player_visible_hint="Tracez votre route et atteignez le lieu visé.",
    ),
    ("exploration", "discovery"): BeatRecipe(
        encounter_type="exploration",
        encounter_subtype="discovery",
        objectives=[
            ObjectiveTemplate(
                kind=ObjectiveKind.ARRIVE,
                target_source="location",
                description_template="Découvrir {target}.",
            ),
            ObjectiveTemplate(
                kind=ObjectiveKind.EXAMINE,
                target_source="beat_title",
                description_template="Étudier la singularité du lieu ({target}).",
            ),
        ],
        advance_rule=AdvanceRule.M_OF_N,
        advance_threshold=2,
        judge_rubric=(
            "Avancer si le groupe atteint le lieu de la découverte ET "
            "examine effectivement la nouveauté."
        ),
        player_visible_hint=(
            "Trouvez le lieu et prenez le temps d'examiner ce qui rend "
            "l'endroit unique."
        ),
    ),
    ("exploration", "survival"): BeatRecipe(
        encounter_type="exploration",
        encounter_subtype="survival",
        objectives=[
            ObjectiveTemplate(
                kind=ObjectiveKind.ARRIVE,
                target_source="location",
                description_template="Survivre à la traversée et atteindre {target}.",
            ),
        ],
        judge_rubric=(
            "Avancer dès que le groupe a atteint le lieu sûr — chaque "
            "passage est une victoire."
        ),
        player_visible_hint="Tenez bon et atteignez l'abri.",
    ),
    ("exploration", None): BeatRecipe(
        encounter_type="exploration",
        encounter_subtype=None,
        objectives=[
            ObjectiveTemplate(
                kind=ObjectiveKind.ARRIVE,
                target_source="location",
                description_template="Atteindre {target}.",
            ),
        ],
        judge_rubric="Avancer dès l'arrivée du groupe sur les lieux.",
        player_visible_hint="Le but est d'atteindre le lieu indiqué.",
    ),
    # ===== PUZZLE =====
    ("puzzle", "riddle"): BeatRecipe(
        encounter_type="puzzle",
        encounter_subtype="riddle",
        objectives=[
            ObjectiveTemplate(
                kind=ObjectiveKind.INTERACT,
                target_source="beat_title",
                description_template="Manipuler l'énigme ({target}) pour proposer une réponse.",
            ),
            ObjectiveTemplate(
                kind=ObjectiveKind.FLAG,
                target_source="literal:riddle_solved",
                description_template=(
                    "Le joueur affirme explicitement la solution — pas de "
                    "demi-tentative."
                ),
            ),
        ],
        advance_rule=AdvanceRule.ALL_REQUIRED,
        judge_rubric=(
            "Avancer seulement si la réponse formulée par le joueur "
            "correspond clairement à l'énigme et a été engagée comme "
            "réponse définitive."
        ),
        player_visible_hint=(
            "L'énigme attend une réponse explicite — formulez-la clairement."
        ),
    ),
    ("puzzle", "mechanism"): BeatRecipe(
        encounter_type="puzzle",
        encounter_subtype="mechanism",
        objectives=[
            ObjectiveTemplate(
                kind=ObjectiveKind.INTERACT,
                target_source="beat_title",
                description_template="Activer le mécanisme ({target}).",
            ),
        ],
        judge_rubric=(
            "Avancer dès que le joueur engage l'action mécanique — "
            "tirer le levier, tourner la clé, presser le sceau."
        ),
        player_visible_hint="Trouvez le mécanisme et actionnez-le.",
    ),
    ("puzzle", "investigation"): BeatRecipe(
        encounter_type="puzzle",
        encounter_subtype="investigation",
        objectives=[
            ObjectiveTemplate(
                kind=ObjectiveKind.SEARCH,
                target_source="location",
                description_template="Fouiller {target} méthodiquement.",
            ),
            ObjectiveTemplate(
                kind=ObjectiveKind.EXAMINE,
                target_source="beat_title",
                description_template="Examiner les indices trouvés ({target}).",
            ),
        ],
        advance_rule=AdvanceRule.M_OF_N,
        advance_threshold=2,
        judge_rubric=(
            "Avancer si le joueur a recoupé au moins deux investigations "
            "distinctes — fouille puis observation, ou plusieurs indices."
        ),
        player_visible_hint=(
            "Combinez fouille et observation pour boucler l'enquête."
        ),
    ),
    ("puzzle", "ritual"): BeatRecipe(
        encounter_type="puzzle",
        encounter_subtype="ritual",
        objectives=[
            ObjectiveTemplate(
                kind=ObjectiveKind.INTERACT,
                target_source="beat_title",
                description_template="Accomplir le rituel ({target}).",
                gate_kind=GateKind.HAS_ITEM,
                gate_value="composant rituel",
            ),
        ],
        judge_rubric=(
            "Avancer si le joueur a engagé le rituel ET dispose des "
            "composants nécessaires à sa réalisation."
        ),
        player_visible_hint=(
            "Rassemblez les composants puis exécutez le rituel sur place."
        ),
    ),
    ("puzzle", "cipher"): BeatRecipe(
        encounter_type="puzzle",
        encounter_subtype="cipher",
        objectives=[
            ObjectiveTemplate(
                kind=ObjectiveKind.EXAMINE,
                target_source="beat_title",
                description_template="Décrypter le code ({target}).",
            ),
            ObjectiveTemplate(
                kind=ObjectiveKind.FLAG,
                target_source="literal:cipher_decoded",
                description_template=(
                    "Le joueur énonce explicitement la traduction — engagement clair."
                ),
            ),
        ],
        advance_rule=AdvanceRule.ALL_REQUIRED,
        judge_rubric=(
            "Avancer seulement si le joueur a déchiffré et énoncé la "
            "traduction comme réponse définitive."
        ),
        player_visible_hint=(
            "Étudiez le code et énoncez votre traduction sans détour."
        ),
    ),
    ("puzzle", None): BeatRecipe(
        encounter_type="puzzle",
        encounter_subtype=None,
        objectives=[
            ObjectiveTemplate(
                kind=ObjectiveKind.INTERACT,
                target_source="beat_title",
                description_template="Résoudre l'énigme ({target}).",
            ),
        ],
        judge_rubric=(
            "Avancer dès que le joueur engage la résolution mécanique."
        ),
        player_visible_hint="Identifiez l'élément clé et interagissez avec.",
    ),
}


def get_recipe(encounter_type: str, encounter_subtype: str | None) -> BeatRecipe:
    """Return the recipe for ``(encounter_type, encounter_subtype)``.

    Falls back to the wildcard ``(encounter_type, None)`` recipe if the
    subtype is unknown. Falls back to the social wildcard if the type
    is itself unknown — caller may instead reject such beats upstream.
    """
    if (encounter_type, encounter_subtype) in _RECIPES:
        return _RECIPES[(encounter_type, encounter_subtype)]
    if (encounter_type, None) in _RECIPES:
        return _RECIPES[(encounter_type, None)]
    return _RECIPES[("social", None)]


def all_recipes() -> dict[tuple[str, str | None], BeatRecipe]:
    """Return a snapshot of the recipe table — used by tests and prompt builder."""
    return dict(_RECIPES)


# ---------------------------------------------------------------------------
# Scaffolding
# ---------------------------------------------------------------------------

_SLUG_NON_ALNUM = re.compile(r"[^\w]+", re.UNICODE)


def _slugify(text: str) -> str:
    """Lowercase ASCII slug suitable for objective ids."""
    if not text:
        return "x"
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii").lower()
    slug = _SLUG_NON_ALNUM.sub("_", ascii_text).strip("_")
    return slug or "x"


def _resolve_target(
    template: ObjectiveTemplate,
    *,
    npc_names: Sequence[str],
    villain_name: str,
    location_hint: str,
    beat_title: str,
) -> str:
    """Resolve the placeholder target for a template against beat context."""
    src = template.target_source
    if src.startswith("literal:"):
        return src[len("literal:"):]
    if src == "first_npc":
        return npc_names[0] if npc_names else "le PNJ clé"
    if src == "villain":
        return villain_name or "l'antagoniste"
    if src == "location":
        return location_hint or "le lieu visé"
    if src == "beat_title":
        return beat_title or "l'élément clé"
    return src


def scaffold_objectives(
    *,
    beat_number: int,
    encounter_type: str,
    encounter_subtype: str | None,
    npc_names: Sequence[str],
    villain_name: str,
    location_hint: str,
    beat_title: str,
) -> tuple[list[BeatObjective], AdvanceRule, int | None, str, str]:
    """Build a deterministic objective list for a beat from its recipe.

    Returns:
        A tuple of (objectives, advance_rule, advance_threshold, judge_rubric,
        player_visible_hint). The caller writes these onto the beat dict before
        Pydantic validation.
    """
    recipe = get_recipe(encounter_type, encounter_subtype)
    objectives: list[BeatObjective] = []
    for idx, tmpl in enumerate(recipe.objectives, start=1):
        target = _resolve_target(
            tmpl,
            npc_names=npc_names,
            villain_name=villain_name,
            location_hint=location_hint,
            beat_title=beat_title,
        )
        description = tmpl.description_template.format(
            target=target,
            location=location_hint,
            beat_title=beat_title,
        )
        oid = f"b{beat_number}_{tmpl.kind.value}_{_slugify(target)[:24]}_{idx}"
        gate: ObjectiveGate | None = None
        if tmpl.gate_kind is not None and tmpl.gate_value is not None:
            gate = ObjectiveGate(kind=tmpl.gate_kind, value=tmpl.gate_value)
        objectives.append(
            BeatObjective(
                id=oid,
                kind=tmpl.kind,
                target=target,
                description=description,
                required=tmpl.required,
                fuzzy_threshold=tmpl.fuzzy_threshold,
                gate=gate,
            ),
        )
    return (
        objectives,
        recipe.advance_rule,
        recipe.advance_threshold,
        recipe.judge_rubric,
        recipe.player_visible_hint,
    )
