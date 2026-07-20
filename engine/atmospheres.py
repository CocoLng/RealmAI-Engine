"""Atmosphere pool — deterministic mood variety for generated locations.

Design spec §2.1 (`docs/superpowers/specs/2026-04-11-world-generation-variety-design.md`):
without a directive, the LLM converges on the same handful of ambiances
(la taverne chaleureuse, la ruine sinistre). The code picks the mood, the
LLM writes it — same division of labour as :mod:`engine.arc_recipes`.

Values are French, like :data:`engine.arc_recipes.COMPLICATIONS`: generator
prompts are English scaffolding with a ``language_instruction`` prefix, and
the injected content vocabulary is written in the game's default language.

Pure deterministic logic (randomness via the ``random`` module only).
"""

import random
from enum import StrEnum


class Atmosphere(StrEnum):
    """Mood suggestion handed to the world generator for a location."""

    oppressante = "oppressante"
    feerique = "féerique"
    delabree = "délabrée"
    vivante = "vivante"
    silencieuse = "silencieuse"
    chaotique = "chaotique"
    sacree = "sacrée"
    industrielle = "industrielle"
    souterraine = "souterraine"
    maritime = "maritime"
    aerienne = "aérienne"
    volcanique = "volcanique"


ATMOSPHERES: list[Atmosphere] = list(Atmosphere)
"""The full pool, in declaration order."""


def pick_atmosphere(previous: str | None = None) -> Atmosphere:
    """Draw an atmosphere at random, never repeating *previous*.

    Args:
        previous: The atmosphere served last time for this campaign, if
            any. Values outside the pool (legacy or foreign strings) are
            ignored rather than raising, so a stale session field can never
            break generation.

    Returns:
        An :class:`Atmosphere` member, guaranteed different from
        *previous* whenever *previous* belongs to the pool.
    """
    available = [a for a in ATMOSPHERES if a.value != previous]
    if not available:  # pragma: no cover — pool has 12 entries
        available = ATMOSPHERES
    return random.choice(available)
