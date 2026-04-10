# Agent 03 — Launch Immersion (purge + countdown + opening crawl)

## Objectif

Ameliorer l'immersion au moment du lancement : (1) purger les messages d'onboarding du channel, (2) afficher un countdown dramatique, (3) remplacer le narrative embed generique par un "opening crawl" riche utilisant les donnees de l'arc narratif.

## Dependances

Agent 02 (force launch) doit etre termine — il modifie `_check_ready()` et ajoute des champs au meme dataclass.

## Fichiers a modifier

| Fichier | Modification |
|---------|-------------|
| `bot/campaign_launcher.py` | Modifier `_launch_campaign()` : ajouter purge + countdown avant les embeds. Remplacer le narrative embed generique par l'opening crawl. |
| `bot/embeds/narrative_embed.py` | Ajouter fonction `build_opening_crawl_embed()` |

## Regles critiques

- **Purge non bloquante** : si `channel.purge()` echoue (permissions, HTTPException), log warning et continuer
- **Countdown non bloquant** : si l'edit/delete echoue, log warning et continuer
- **Pas d'appel LLM** : l'opening crawl est deterministe, construit a partir des donnees deja generees (arc premise, location, first beat)
- **Limite purge** : `channel.purge(limit=200)` — suffisant pour l'onboarding, pas de risque de supprimer des messages hors campagne (channel dedie)
- **Ordre** : purge → countdown → opening crawl → scene embed (existant)

## Detail de l'implementation

### 1. `build_opening_crawl_embed()` dans `bot/embeds/narrative_embed.py`

```python
def build_opening_crawl_embed(
    campaign_name: str,
    story_arc: StoryArc | None,
    location: Location | None,
    language: str = "fr",
) -> discord.Embed:
    """Build an immersive opening embed from arc and location data."""
    premise = "Votre aventure commence..."
    if story_arc and story_arc.premise:
        premise = story_arc.premise

    embed = discord.Embed(
        title=f"\U0001f4dc {campaign_name}",
        description=premise,
        color=0xDAA520,
    )

    if location:
        loc_desc = location.description or location.name
        embed.add_field(
            name="Lieu de depart" if language == "fr" else "Starting Location",
            value=f"**{location.name}**\n{loc_desc}",
            inline=False,
        )

    if story_arc and story_arc.beats:
        first_beat = story_arc.beats[0]
        embed.add_field(
            name="Premier chapitre" if language == "fr" else "First Chapter",
            value=f"*{first_beat.description}*",
            inline=False,
        )

    return embed
```

Imports necessaires en haut du fichier : `StoryArc` et `Location` (TYPE_CHECKING).

### 2. Modifier `_launch_campaign()` dans `campaign_launcher.py`

Inserer APRES `self.bot.launchers.pop(...)` (ligne 547) et AVANT le bloc "Build opening narrative" (ligne 549) :

#### 2a. Purge
```python
# --- Purge onboarding messages for immersion ---
try:
    await self.channel.purge(limit=200)
except (discord.Forbidden, discord.HTTPException):
    logger.warning(
        "LAUNCH purge failed campaign=%s", self.campaign.id, exc_info=True,
    )
```

#### 2b. Countdown
```python
# --- Countdown ---
try:
    countdown_msg = await self.channel.send("**La partie commence dans 3...**")
    for i in (2, 1):
        await asyncio.sleep(1)
        await countdown_msg.edit(content=f"**La partie commence dans {i}...**")
    await asyncio.sleep(1)
    await countdown_msg.delete()
except Exception:
    logger.warning(
        "LAUNCH countdown failed campaign=%s", self.campaign.id, exc_info=True,
    )
```

#### 2c. Remplacer le narrative embed (lignes 549-558)

Remplacer :
```python
# Build opening narrative
desc = "Votre aventure commence..."
if self.current_location:
    desc = self.current_location.description or desc
if self.story_arc and self.story_arc.beats:
    first_beat = self.story_arc.beats[0]
    desc = f"{desc}\n\n*{first_beat.description}*"

embed = build_narrative_embed(desc, tone="dramatic", footer_override=f"Campagne : {self.campaign.name}")
await self.channel.send(embed=embed)
```

Par :
```python
# Opening crawl embed (replaces generic narrative)
from bot.embeds.narrative_embed import build_opening_crawl_embed

crawl_embed = build_opening_crawl_embed(
    campaign_name=self.campaign.name,
    story_arc=self.story_arc,
    location=self.current_location,
    language=self.language,
)
await self.channel.send(embed=crawl_embed)
```

## Fichiers impactes (imports, configs)

- `bot/embeds/narrative_embed.py` : nouveaux imports TYPE_CHECKING pour `StoryArc`, `Location`
- `bot/campaign_launcher.py` : import inline de `build_opening_crawl_embed` (ou ajouter en top-level a cote du `build_narrative_embed` existant)

## Tests a creer

| Fichier | Ce qu'il teste |
|---------|----------------|
| `tests/test_launch_immersion.py` | Purge, countdown, opening crawl embed |

Tests :
- `test_launch_purges_channel` — `channel.purge(limit=200)` est appele pendant `_launch_campaign()`
- `test_launch_continues_if_purge_fails` — purge leve `discord.HTTPException`, le lancement continue
- `test_launch_sends_countdown` — `channel.send` appele avec texte countdown, `edit` appele 2 fois
- `test_countdown_message_deleted` — le message countdown est supprime apres la sequence
- `test_countdown_failure_does_not_block_launch` — edit/delete echouent, lancement continue
- `test_opening_crawl_embed_contains_premise` — embed description contient `story_arc.premise`
- `test_opening_crawl_embed_contains_location` — embed a un field "Lieu de depart" avec le nom du lieu
- `test_opening_crawl_embed_contains_first_beat` — embed a un field "Premier chapitre"
- `test_opening_crawl_embed_fallback` — sans arc ni location, description par defaut "Votre aventure commence..."

## Mise a jour docs/internal/

Mettre a jour `docs/internal/DISCORD_BOT.md` et `docs/internal/CAMPAIGN_LIFECYCLE.md` pour documenter :
- La purge au lancement
- Le countdown
- Le nouveau format d'opening crawl

## Validation

```bash
uv run pytest
uv run ruff check .
uv run mypy .
```

Tout doit etre vert. Si un seul test casse, c'est un bug a corriger avant de continuer.

## Estimation

Complexite : Moyenne
