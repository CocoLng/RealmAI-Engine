# Character Creation Redesign — Design Spec

**Date** : 2026-04-26
**Status** : design (en attente d'implémentation)
**Author** : brainstorming session avec Claude Opus 4.7
**Replaces** : sections "Onboarding & character creation" de [2026-04-05-discord-bot-ux-design.md](./2026-04-05-discord-bot-ux-design.md) et [2026-04-06-onboarding-story-arc-design.md](./2026-04-06-onboarding-story-arc-design.md) — l'orchestration `CampaignLauncher`/`CharacterCreateView` est obsolète à partir de cette refonte.

---

## 1. Contexte & problème

Le flow de création de personnage actuel souffre de **friction UX** et de **dette de modèle** qui rendent l'onboarding peu engageant.

### Symptômes observés

- **8+ messages séparés** pour créer un personnage : race → classe → alignement → 6 stats (1 par 1) → skills → modal nom → kit → motivation. Chaque étape est une nouvelle vue, le contexte se perd.
- **Champ `alignment`** (Lawful Good, Chaotic Neutral, etc.) sélectionné en étape 3 — **aucun impact mécanique** dans tout le code (`engine/`, `ai/`). Pure friction.
- **Stats stat-par-stat** : 6 clics séquentiels pour appliquer le Standard Array (15/14/13/12/10/8). Pas de preset par classe, pas de génération aléatoire.
- **Aucune étape de récap** avant commit : l'utilisateur ne voit sa fiche complète qu'**après** validation, sans possibilité de revenir.
- **`/create_character` slash command parallèle** au flow d'onboarding du `CampaignLauncher` : duplication de logique, deux chemins pour faire la même chose.
- **Chaînage de vues fragile** : `CharacterCreateView → StatAssignmentView → SkillSelectionView → CharacterNameModal`. Un timeout sur n'importe quelle étape force le redémarrage complet.
- **Capacités discord.py 2.7 inutilisées** : modals multi-champs, `SelectOption.description`, Components V2 (Container/Section).

### Cause racine

Le design originel ([2026-04-05-discord-bot-ux-design.md](./2026-04-05-discord-bot-ux-design.md)) prévoyait `/create_character` comme **commande indépendante**, qui a ensuite été plaquée sur le `CampaignLauncher` ([bot/campaign_launcher.py:84](../../bot/campaign_launcher.py)) sans repenser l'expérience. Résultat : deux orchestrateurs, un flow chaîné par héritage.

---

## 2. Objectifs

- **G1** : Une seule expérience d'onboarding, du `/start_campaign` au premier narratif.
- **G2** : Réduire le nombre d'interactions Discord par joueur de 8+ à ≤ 5 messages visibles.
- **G3** : Supprimer les champs sans impact mécanique (`alignment`).
- **G4** : Lobby multi-joueur "ouvert" (anyone clicks Rejoindre) plutôt que liste pré-définie au lancement.
- **G5** : Étape de récap "what you're committing to" avant persistance.
- **G6** : Conserver tout le câblage mécanique existant (`proficiency_bonus`, `saving_throw_proficiencies`, `features`) qui fonctionne déjà en combat.

### Non-objectifs

- Backgrounds, Feats, Multiclassing (déjà différés dans `tasks/todo.md`).
- Nouvelles méthodes de stats type Point Buy complet (différé).
- Refonte du `StoryArc` et du `BeatProgressionEngine` (orthogonal).
- Système de langues, Tool proficiencies (différés).
- Refonte du combat (déjà câblé Phase 9).

---

## 3. Architecture cible

```
/start_campaign theme:<...>
        │
        ▼
[Lobby Message persistant dans le channel campaign]
        │
        │  Roster live + boutons : 🎭 Rejoindre / 🚪 Quitter / ▶️ Démarrer
        │
   ┌────┼────────────────────────────────────────────┐
   │    │                                            │
   ▼    ▼                                            ▼
 Player A clicks Rejoindre              Player B clicks Rejoindre
   │                                                  │
   ▼                                                  ▼
[CharacterSetupFlow ephemeral]                  [idem]
   │ State machine : 6 étapes sur UNE seule view    │
   │                                                  │
   ├─ 1. Identity (Modal multi-champs : Nom + Concept)│
   ├─ 2. Race + Classe (2 selects + descriptions)    │
   ├─ 3. Stats (3 boutons : Optimisé / Random / Custom)│
   ├─ 4. Skills (multi-select + descriptions)        │
   ├─ 5. Kit + Motivation (2 selects)                │
   └─ 6. Récap + Confirm (Components V2 Container)   │
                                                       │
   ▼ (commit) ────────────────────────────────────────▼
   [LobbyState.set_player_ready(user_id, character)]
                            │
                            ▼
   [Lobby roster mis à jour : 🛠️ → ✅]
                            │
   ┌────────────────────────┴───────────────────────┐
   │ (Host clique ▶️ Démarrer quand ≥ 1 player Ready)│
   ▼                                                  
[GameSession created] → [Opening narrative]
```

**Principes** :
- **Un seul orchestrateur** : `LobbyState` (refonte minimaliste de `CampaignLauncher`).
- **Un seul flow par joueur** : `CharacterSetupFlow` est une view qui s'auto-modifie via `edit_message`. Pas de chaînage.
- **Aucun champ vestigial** au sortir : ce qui est dans `Character` doit être utilisé en code.
- **Components V2 ciblés** : utilisés pour récap (étape 6) et roster du lobby. Reste des étapes en classic embeds.

---

## 4. Modèle de données

### 4.1 Suppressions

**`engine/character/models.py:Character`** :
- ❌ `alignment: Alignment = Alignment.TRUE_NEUTRAL` (ligne 32) — supprimé

**`engine/character/enums.py`** :
- ❌ `class Alignment(StrEnum)` (lignes 47-58, 9 valeurs) — supprimé entièrement

**`engine/character/creation.py:create_character`** :
- ❌ Paramètre `alignment: Alignment = Alignment.TRUE_NEUTRAL` (ligne 16) — supprimé

**`bot/i18n.py`** :
- ❌ `ALIGNMENT_LABELS` dict — supprimé

**`bot/views/character_create_view.py`** :
- ❌ Fichier supprimé entièrement (remplacé par `character_setup_flow.py`)

**Audit grep des 20 fichiers référençant `alignment`** (à nettoyer en Vague A) :
`tests/db/test_mappers.py`, `tests/bot/test_cog_character.py`, `tests/bot/test_test_bridge_views.py`, `tests/bot/test_cog_inventory.py`, `tests/bot/test_campaign_launcher_recreation.py`, `tests/bot/test_i18n.py`, `tests/bot/test_views.py`, `tests/engine/test_character.py`, `tests/engine/test_creation_flow.py`, `bot/campaign_launcher.py`, `bot/i18n.py`, `bot/views/character_edit_view.py`, `bot/views/character_edit_flow.py`, `bot/cogs/character.py`, `bot/cogs/test_bridge.py`, `bot/views/character_create_view.py`, `engine/character/enums.py`, `engine/character/models.py`, `engine/character/__init__.py`, `engine/character/creation.py`.

### 4.1bis Ajout : champ `concept` sur Character

**`engine/character/models.py:Character`** :
- ✅ **AJOUT** `concept: str = Field(default="", max_length=200)` — flavor RP libre, capturé via `IdentityModal`. Lu par les prompts narrateur pour la couleur RP. Aucun impact mécanique. Bilan net : `−1` champ (alignment + enum + i18n) `+1` champ utile (concept lu par LLM).

### 4.2 Nouveaux modèles

**`engine/character/presets.py`** (nouveau fichier) :

```python
"""Class-optimized stat presets using the Standard Array (15/14/13/12/10/8).

Each preset reorders the array based on the class's primary, secondary,
and tertiary stat priorities. Used by the 'Optimisé pour [Classe]'
button in the character setup flow.
"""

from .enums import Ability, CharacterClass

CLASS_STAT_PRESETS: dict[CharacterClass, dict[Ability, int]] = {
    CharacterClass.FIGHTER:   {Ability.STR: 15, Ability.CON: 14, Ability.DEX: 13, Ability.WIS: 12, Ability.INT: 10, Ability.CHA: 8},
    CharacterClass.BARBARIAN: {Ability.STR: 15, Ability.CON: 14, Ability.DEX: 13, Ability.WIS: 12, Ability.CHA: 10, Ability.INT: 8},
    CharacterClass.WIZARD:    {Ability.INT: 15, Ability.DEX: 14, Ability.CON: 13, Ability.WIS: 12, Ability.CHA: 10, Ability.STR: 8},
    CharacterClass.CLERIC:    {Ability.WIS: 15, Ability.CON: 14, Ability.STR: 13, Ability.DEX: 12, Ability.CHA: 10, Ability.INT: 8},
    CharacterClass.ROGUE:     {Ability.DEX: 15, Ability.CON: 14, Ability.INT: 13, Ability.CHA: 12, Ability.WIS: 10, Ability.STR: 8},
    CharacterClass.RANGER:    {Ability.DEX: 15, Ability.WIS: 14, Ability.CON: 13, Ability.STR: 12, Ability.INT: 10, Ability.CHA: 8},
}
# Vérifier exhaustivité : 6 classes (FIGHTER, BARBARIAN, WIZARD, CLERIC, ROGUE, RANGER) — match engine/character/enums.py:29

def get_class_preset(char_class: CharacterClass) -> dict[Ability, int]:
    """Return the optimized Standard Array assignment for a given class."""
    return dict(CLASS_STAT_PRESETS[char_class])
```

**`engine/character/random_stats.py`** (nouveau fichier) :

```python
"""4d6-drop-lowest stat generation with class-priority auto-assignment."""

import random
from .enums import Ability, CharacterClass

# Priority order per class (highest first)
CLASS_STAT_PRIORITY: dict[CharacterClass, list[Ability]] = {
    CharacterClass.FIGHTER:   [Ability.STR, Ability.CON, Ability.DEX, Ability.WIS, Ability.INT, Ability.CHA],
    CharacterClass.BARBARIAN: [Ability.STR, Ability.CON, Ability.DEX, Ability.WIS, Ability.CHA, Ability.INT],
    CharacterClass.WIZARD:    [Ability.INT, Ability.DEX, Ability.CON, Ability.WIS, Ability.CHA, Ability.STR],
    CharacterClass.CLERIC:    [Ability.WIS, Ability.CON, Ability.STR, Ability.DEX, Ability.CHA, Ability.INT],
    CharacterClass.ROGUE:     [Ability.DEX, Ability.CON, Ability.INT, Ability.CHA, Ability.WIS, Ability.STR],
    CharacterClass.RANGER:    [Ability.DEX, Ability.WIS, Ability.CON, Ability.STR, Ability.INT, Ability.CHA],
}

def roll_4d6_drop_lowest() -> list[int]:
    """Roll 4d6 and drop the lowest die, six times. Returns sorted descending."""
    rolls = []
    for _ in range(6):
        dice = sorted(random.randint(1, 6) for _ in range(4))
        rolls.append(sum(dice[1:]))  # drop lowest
    return sorted(rolls, reverse=True)

def auto_assign_random(char_class: CharacterClass, rolls: list[int]) -> dict[Ability, int]:
    """Assign 6 sorted-desc rolls to abilities by class priority."""
    priority = CLASS_STAT_PRIORITY[char_class]
    return dict(zip(priority, rolls, strict=True))
```

### 4.3 Champs conservés (déjà câblés, ne pas toucher)

| Champ | Utilisation actuelle | Statut |
|---|---|---|
| `proficiency_bonus` | combat.py:572,1042 (attack rolls) | ✅ câblé |
| `saving_throw_proficiencies` | combat.py:781, npc_ai/elite.py:410 (saves) | ✅ câblé |
| `features` | populated in creation.py, displayed in embed | display-only OK |
| `size`, `speed`, `hit_die`, `ac` | derived correctly, displayed | display-only OK |
| `xp` | tracked in level_up cog | exposé dans `/character` après refonte |

---

## 5. Composants UI Discord

### 5.1 LobbyView (nouveau)

**Fichier** : `bot/views/lobby_view.py`

**Responsabilités** :
- Vue persistante attachée au message Lobby dans le channel campaign.
- Boutons : `🎭 Rejoindre` (any), `🚪 Quitter` (visible si in roster), `▶️ Démarrer` (host only, enabled si ≥ 1 ready).
- Délègue à `LobbyState` pour la logique métier.

**API** :
```python
class LobbyView(LoggedView):
    def __init__(self, lobby_state: LobbyState, host_id: int, language: str): ...

    @ui.button(label="Rejoindre", emoji="🎭", style=ButtonStyle.primary)
    async def join(self, interaction, button): ...

    @ui.button(label="Quitter", emoji="🚪", style=ButtonStyle.secondary)
    async def leave(self, interaction, button): ...

    @ui.button(label="Démarrer l'aventure", emoji="▶️", style=ButtonStyle.success)
    async def launch(self, interaction, button): ...
```

### 5.2 CharacterSetupFlow (nouveau)

**Fichier** : `bot/views/character_setup_flow.py`

**Responsabilités** :
- Une seule view qui parcourt 6 étapes via `edit_message`.
- État interne tracké dans `self.state: SetupStep` (enum : IDENTITY, RACE_CLASS, STATS, SKILLS, KIT_MOTIV, REVIEW).
- Composants UI rebuild à chaque transition (les anciens children remplacés par les nouveaux).

**API** :
```python
class SetupStep(IntEnum):
    IDENTITY = 0
    RACE_CLASS = 1
    STATS = 2
    SKILLS = 3
    KIT_MOTIV = 4
    REVIEW = 5

class CharacterSetupFlow(LoggedView):
    def __init__(
        self,
        user_id: int,
        language: str,
        on_complete: Callable[[Character, str, str], Awaitable[None]],  # (char, kit_name, motivation_key)
    ): ...

    # State accumulators
    name: str | None
    concept: str | None  # NEW : remplace alignment narrativement
    race: Race | None
    char_class: CharacterClass | None
    ability_scores: AbilityScores | None
    skill_proficiencies: list[Skill] | None
    kit_name: str | None
    motivation_key: str | None

    async def transition_to(self, interaction, next_step: SetupStep) -> None:
        """Rebuild components for next_step and edit_message."""
```

### 5.3 IdentityModal (nouveau)

**Fichier** : `bot/views/character_setup_flow.py` (même fichier, classe interne)

```python
class IdentityModal(ui.Modal, title="Ton aventurier"):
    name = ui.TextInput(label="Nom du personnage", min_length=1, max_length=32, required=True)
    concept = ui.TextInput(
        label="Concept (optionnel)",
        placeholder="Ex: Un voleur repenti cherchant la rédemption",
        max_length=100,
        required=False,
        style=TextStyle.paragraph,
    )
```

### 5.4 LobbyEmbed (nouveau)

**Fichier** : `bot/embeds/lobby_embed.py`

```python
def build_lobby_embed(
    campaign_name: str,
    theme: str,
    host_name: str,
    roster: list[LobbyPlayer],  # contient user_id, status, char_summary
    language: str,
) -> discord.Embed:
    """Build the campaign lobby embed with live roster.

    Status badges: 🆕 Joined, 🛠️ Creating, ✅ Ready, ❌ Cancelled.
    """
```

### 5.5 Récap V2 (nouveau)

**Fichier** : `bot/embeds/character_setup_v2.py`

Utilise discord.py 2.7 Components V2 (`Container`, `Section`, `TextDisplay`, `Separator`) pour afficher la fiche complète à l'étape REVIEW.

Fallback : si l'API V2 pose problème, retomber sur un embed enrichi équivalent (déjà existant dans `bot/embeds/character_embed.py`).

---

## 6. Refonte du LobbyState (ex-CampaignLauncher)

**Fichier** : `bot/campaign_launcher.py` → renommé `bot/lobby_state.py`

**Suppressions** :
- ❌ `player_ids: list[int]` (plus de liste pré-définie)
- ❌ Enum `PlayerProgress` (PENDING / CHARACTER_DONE / KIT_DONE / GEAR_DONE) — remplacé par `LobbyPlayerStatus` (JOINED / CREATING / READY / CANCELLED). Le flow unifié englobe création + kit + motivation, donc plus besoin de tracker des sub-états.
- ❌ `raw_assignments`, `character_kits`, `character_motivations` (dicts dispersés) — encapsulés dans `LobbyPlayer` dataclass

**Conservé** :
- ✅ Génération `StoryArc` en background (`_generation_task`, `GenerationPhase`)
- ✅ `creator_id` (host)
- ✅ `language`
- ✅ Lifecycle : `bot.launchers[channel_id]` enregistrement, transition vers `GameSession` à `launch()`

**Nouveau** :
- `LobbyPlayer` dataclass : `{user_id, status: LobbyPlayerStatus, character, inventory, spellcaster, kit_name, motivation_key}`
- `LobbyPlayerStatus` enum : `JOINED / CREATING / READY / CANCELLED`
- `players: dict[int, LobbyPlayer]` (remplace les 4-5 dicts dispersés)

---

## 7. Cogs et command surface

### 7.1 `/start_campaign` (refacto)

**Avant** ([bot/cogs/session.py:90](../../bot/cogs/session.py)) :
```python
@app_commands.command(name="start_campaign")
async def start_campaign(
    self,
    interaction: discord.Interaction,
    name: str,
    theme: str,
    players: str,  # "@user1 @user2 @user3"
):
```

**Après** :
```python
@app_commands.command(name="start_campaign")
async def start_campaign(
    self,
    interaction: discord.Interaction,
    theme: str,
    name: str | None = None,  # auto-générée si absente
):
    """Crée le channel + le lobby. Les joueurs rejoignent via bouton."""
```

### 7.2 `/create_character` (suppression)

**Avant** ([bot/cogs/character.py:36-114](../../bot/cogs/character.py)) : slash command standalone (ligne 36, `@app_commands.command(name="create_character")`).

**Après** : ❌ **supprimé**. La création se fait UNIQUEMENT via le bouton "Rejoindre" du lobby.

Le cog `bot/cogs/character.py` conserve **uniquement** :
- ✅ `/character` (display, ligne 116)
- ✅ `/level_up` (ligne 141)

(Pas de `/character_edit` ni `/invite_player` dans le cog vérifié — confirmation par grep `app_commands.command` dans `bot/cogs/character.py`. Les vues `character_edit_view.py` / `character_edit_flow.py` existent mais sont des composants UI internes utilisés par le launcher, pas des commands.)

---

## 8. Tests

### 8.1 Tests engine (Vague A)

**Nouveaux** :
- `tests/engine/character/test_presets.py` : valide que chaque classe a un preset, sum des stats == 72 (Standard Array), priority order respectée.
- `tests/engine/character/test_random_stats.py` : `roll_4d6_drop_lowest` retourne 6 ints, chacun ∈ [3, 18], tri descendant. `auto_assign_random` mappe correctement par priorité.
- `tests/engine/character/test_create_no_alignment.py` : `create_character` ne prend plus `alignment`, le Character n'a plus `alignment` field.

**À supprimer / migrer** :
- Toutes les fixtures qui passent `alignment=...` → cleanup.
- Tests qui asserent `character.alignment == ...` → suppression.

### 8.2 Tests UI (Vague B)

**Nouveaux** :
- `tests/bot/views/test_character_setup_flow.py` : transitions entre étapes, `on_complete` callback déclenché, timeout reset l'état.
- `tests/bot/views/test_lobby_view.py` : join enregistre joueur, leave retire, launch host-only, launch désactivé si 0 ready.
- `tests/bot/embeds/test_lobby_embed.py` : roster affichage, badges status corrects.

### 8.3 Tests intégration (Vague C)

**Nouveau** :
- `tests/scenarios/character_creation_lobby.py` : scenario end-to-end via `ScenarioRunner`. Host /start_campaign, joueur join, complete flow, check character persisté + lobby roster mis à jour, host launch, GameSession créée.

**Live test Discord** (gate de fin) : test_bridge MCP + tester bot — un host + 2 joueurs créent et lancent.

### 8.4 À supprimer

- `tests/bot/test_cog_character.py::test_create_character_*` (tous les tests du slash command supprimé)
- `tests/bot/views/test_character_create_view.py` (si existe)
- `tests/bot/views/test_stat_assignment_view.py`
- `tests/bot/views/test_skill_selection_view.py`

---

## 9. Plan d'exécution — chantiers parallèles

| Vague | Agent | Périmètre | Dépendance | Estimation |
|---|---|---|---|---|
| **A** | `engine-cleaner` | Drop alignment + presets + random_stats + tests engine + i18n cleanup | indépendant | ~3-4h |
| **B** | `ui-builder` | LobbyView + CharacterSetupFlow + IdentityModal + LobbyEmbed + V2 récap + tests UI | indépendant | ~5-6h |
| **C** | `integrator` | Refonte session.py + LobbyState + suppression /create_character + scenario + live test | **après A + B** | ~3-4h |

**A et B en parallèle** (déclenchés ensemble), **C en série** après convergence des deux.

### Migration en 3 stages (suivant les leçons de [tasks/lessons.md](../../tasks/lessons.md))

1. **Stage 1 (Vague A)** : engine refactoré, ancien UI continue de fonctionner (`alignment` param devient `None` par défaut, retiré ensuite).
2. **Stage 2 (Vague B)** : nouvelles vues construites en parallèle, ancien path toujours par défaut.
3. **Stage 3 (Vague C)** : bascule du `/start_campaign` vers le lobby + suppression de l'ancien cog. Pas de "shadow mode" car le flow UI est mutuellement exclusif (impossible de faire les deux en même temps).

---

## 10. Décisions prises (autonomes)

1. **Components V2 scope limité** : utilisé uniquement pour le récap final + lobby roster. Réduit le risque (API récente, doc encore parcellaire).
2. **Random stats = auto-assignment par classe priority + 1 reroll** : pas de placement manuel des 6 jets pour préserver la rapidité (UX casual-friendly).
3. **Concept libre dans IdentityModal remplace alignment** : passé au prompt narrateur en flavor RP, zéro impact mécanique. Le LLM peut s'en servir pour la personnalité.
4. **Max 6 joueurs/lobby** : standard D&D 5e. Configurable via `GuildConfig` plus tard si besoin.
5. **Pas de save partiel mid-création** : timeout/déconnexion = clic "Rejoindre" pour recommencer. Évite la complexité d'un état persistant intermédiaire.
6. **Pas de Point Buy** : déjà différé dans `tasks/todo.md`. Trois options stats (preset / random / Standard Array manuel) suffisent.
7. **Kit + Motivation fusionnés en une seule étape** : remplace les `StarterGearView` + `MotivationView` séparées (réduction directe du nombre d'écrans).

---

## 11. Risques & mitigations

| Risque | Mitigation |
|---|---|
| Components V2 bug / incompatibilité runtime | Fallback embed déjà prévu (Section 5.5). Test V2 en isolation tôt en Vague B. |
| `Alignment` enum référencé dans des tests/prompts oubliés | grep exhaustif de `Alignment` et `alignment` avant Vague A close. Vague A inclut l'audit. |
| `LobbyState` casse les sessions existantes en cours | Migration scripted : sessions actives au moment du déploiement → message "veuillez relancer" + cleanup automatique. |
| Composants persistants Discord limités à 100 vues actives | Lobby = 1 view par campagne, reset à `launch()`. Bien dans la limite. |
| Race condition multi-joueur sur join simultané | `LobbyState.players` accédé sous `asyncio.Lock` (déjà pattern dans le codebase). |
| Suppression du `/create_character` casse les tests externes | `tests/bot/test_cog_character.py` listé en Section 8.4 pour cleanup. |

---

## 12. Open questions

Aucune en suspens — toutes les décisions architecturales ont été tranchées (cf. AskUserQuestion answers : Lobby ouvert / Recommandé classe + override / Câbler core mécanique).

---

## 13. Références

- Plan d'implémentation : `docs/superpowers/plans/2026-04-26-character-creation-redesign.md` (à créer après validation de ce spec)
- Spec UX D&D originel : [2026-04-05-discord-bot-ux-design.md](./2026-04-05-discord-bot-ux-design.md)
- Spec onboarding actuel : [2026-04-06-onboarding-story-arc-design.md](./2026-04-06-onboarding-story-arc-design.md)
- discord.py 2.7 Components V2 docs : https://discordpy.readthedocs.io/en/stable/interactions/api.html (Section "Layout components")
- Lessons learned : [tasks/lessons.md](../../tasks/lessons.md)
