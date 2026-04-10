# Agent 02 — Force Launch

## Objectif

Quand la generation LLM est terminee mais que certains joueurs n'ont pas cree leur perso, le createur de la campagne peut forcer le lancement. Les joueurs non prets sont exclus. Minimum 1 joueur pret requis.

## Dependances

Agent 01 (character re-creation) doit etre termine — il modifie `_on_create_character_clicked()` dans le meme fichier.

## Fichiers a creer

| Fichier | Contenu |
|---------|---------|
| `bot/views/force_launch_view.py` | `ForceLaunchView` — vue Discord avec un seul bouton "Lancer la partie", restreint au createur de la campagne |

## Fichiers a modifier

| Fichier | Modification |
|---------|-------------|
| `bot/campaign_launcher.py` | Ajouter champ `creator_id: int = 0` + `_force_launch_offered: bool`. Modifier `_check_ready()` pour poster le bouton quand conditions remplies. Ajouter methode `_on_force_launch()`. |
| `bot/cogs/session.py` | Passer `creator_id=interaction.user.id` au constructeur `CampaignLauncher` (ligne 133) |

## Regles critiques

- **Createur uniquement** : seul le user qui a fait `/start_campaign` peut cliquer le bouton
- **Minimum 1 joueur pret** : pas de lancement a 0 joueurs
- **Offert une seule fois** : flag `_force_launch_offered` pour eviter les doublons
- **Ne pas bloquer le lancement normal** : si le dernier joueur finit son perso entre-temps, `_check_ready()` doit toujours pouvoir lancer normalement (la condition `all_ready and generation_done` est evaluee en premier)

## Detail de l'implementation

### 1. `bot/views/force_launch_view.py` (nouveau)

```python
"""Force-launch view — lets the campaign creator start without all players."""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any

import discord

from bot.views.logged_view import LoggedView

logger = logging.getLogger(__name__)


class ForceLaunchView(LoggedView):
    """Single-button view for the campaign creator to force-launch."""

    def __init__(
        self,
        *,
        creator_id: int,
        on_click: Callable[[discord.Interaction], Coroutine[Any, Any, None]],
        timeout: float = 600,
    ) -> None:
        super().__init__(timeout=timeout)
        self.creator_id = creator_id
        self._on_click = on_click

    @discord.ui.button(
        label="Lancer la partie",
        style=discord.ButtonStyle.danger,
        emoji="\u26a1",
    )
    async def launch_button(
        self, interaction: discord.Interaction, button: discord.ui.Button[ForceLaunchView],
    ) -> None:
        if interaction.user.id != self.creator_id:
            await interaction.response.send_message(
                "Seul le createur de la campagne peut lancer la partie.",
                ephemeral=True,
            )
            return
        self.stop()
        await self._on_click(interaction)
```

### 2. Modifier `CampaignLauncher` dataclass (ligne 74)

Ajouter les champs :
```python
creator_id: int = 0
_force_launch_offered: bool = field(default=False, repr=False)
```

### 3. Modifier `session.py` (ligne 133)

```python
launcher = CampaignLauncher(
    bot=self.bot,
    campaign=campaign,
    channel=channel,
    player_ids=player_ids,
    language=language,
    creator_id=interaction.user.id,  # <-- ajouter
)
```

### 4. Modifier `_check_ready()` (ligne 444)

Apres le bloc `if generation_done and not all_ready and not self._notified_generation_ready:` (ligne 468-472), ajouter :

```python
at_least_one_ready = any(
    p == PlayerProgress.GEAR_DONE for p in self.player_progress.values()
)
if (
    generation_done
    and not all_ready
    and at_least_one_ready
    and not self._force_launch_offered
):
    self._force_launch_offered = True
    from bot.views.force_launch_view import ForceLaunchView

    not_ready = [
        uid for uid, p in self.player_progress.items()
        if p != PlayerProgress.GEAR_DONE
    ]
    mentions = " ".join(f"<@{uid}>" for uid in not_ready)
    view = ForceLaunchView(
        creator_id=self.creator_id,
        on_click=self._on_force_launch,
    )
    await self.channel.send(
        f"Joueurs en attente : {mentions}\n"
        f"Le createur peut lancer la partie sans eux.",
        view=view,
    )
```

### 5. Ajouter methode `_on_force_launch()`

```python
async def _on_force_launch(self, interaction: discord.Interaction) -> None:
    """Force-launch the campaign, excluding non-ready players."""
    not_ready = [
        uid for uid, p in self.player_progress.items()
        if p != PlayerProgress.GEAR_DONE
    ]
    for uid in not_ready:
        self.player_ids.remove(uid)
        del self.player_progress[uid]
        self.characters.pop(uid, None)
        self.inventories.pop(uid, None)
        self.spellcasters.pop(uid, None)

    mentions = " ".join(f"<@{uid}>" for uid in not_ready)
    await interaction.response.send_message(
        f"Lancement force ! Joueurs exclus : {mentions}",
    )
    logger.info(
        "LAUNCH force creator=%s excluded=%s campaign=%s",
        interaction.user, not_ready, self.campaign.id,
    )
    await self._launch_campaign()
```

## Fichiers impactes (imports, configs)

- `bot/views/force_launch_view.py` importe `LoggedView` depuis `bot/views/logged_view.py`
- Import inline dans `campaign_launcher.py` (pas d'import top-level pour eviter le cycle)

## Tests a creer

| Fichier | Ce qu'il teste |
|---------|----------------|
| `tests/test_force_launch.py` | Force launch view + logique dans campaign_launcher |

Tests :
- `test_force_launch_button_shown_when_conditions_met` — generation done, 1/2 joueurs prets, bouton affiche
- `test_force_launch_not_shown_zero_ready` — 0 joueurs prets, pas de bouton
- `test_force_launch_not_shown_all_ready` — tous prets, lancement normal sans bouton
- `test_force_launch_excludes_non_ready` — apres clic, joueurs non prets retires des dicts
- `test_force_launch_creator_only` — non-createur rejete avec message ephemeral
- `test_force_launch_offered_once` — `_check_ready()` appele 2 fois, bouton poste une seule fois
- `test_force_launch_view_button_label` — vue a exactement 1 bouton avec le bon label

## Validation

```bash
uv run pytest
uv run ruff check .
uv run mypy .
```

Tout doit etre vert. Si un seul test casse, c'est un bug a corriger avant de continuer.

## Estimation

Complexite : Moyenne
