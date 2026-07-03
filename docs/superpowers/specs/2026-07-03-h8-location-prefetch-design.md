# H8 (suite) — Progression lobby + prefetch des lieux voisins

**Date** : 2026-07-03
**Chantier** : I « Latence » (finding H8, `docs/audits/2026-06-10-system-audit.md`)
**Branche** : `feat/h8-latency`

## Contexte

Le gros du chantier H8 est déjà mergé sur main (mesures :
`docs/audits/2026-06-10-h8-latency-measurements.md`) : arc gen amaigri
(359-398 s → 120-171 s), pré-génération arc+lieu pendant le lobby, prefetch
NPC en fond. Restent deux points :

1. **Câblage documenté** (« Reste à câbler » du doc de mesures) :
   `build_lobby_embed` accepte `pregen_status` mais `bot/cogs/session.py`
   ne le passe jamais — la progression de génération n'est pas visible dans
   le lobby.
2. **MOVE vers un lieu jamais généré : ~57-80 s bloquants** —
   `WorldGenerator` (9b) est appelé en synchrone dans la résolution de
   l'action (`bot/world_navigation.py:141-182`). C'est le plus gros point
   noir de latence restant en jeu.

## Objectifs

- La progression de la pré-génération (initialisation → arc → lieu → prêt)
  est visible en live dans l'embed du lobby.
- Un MOVE vers un lieu déjà préfetché est instantané (lecture DB).
- Au plus **une** génération LLM de fond en vol à tout instant (NPC ou
  lieu confondus) : le pire cas d'attente d'une action joueur derrière le
  fond est borné à ~60 s (un seul job).
- Un job de fond ne démarre jamais pendant qu'une action joueur est en
  cours (`session.action_lock` tenu).

## Non-objectifs

- Latence du narrateur lui-même (~25-29 s/action) et streaming/édition
  progressive de l'embed — hors périmètre, différé.
- Swap de modèle 4b↔9b dans le pipeline d'action (inhérent à
  interpret(4b) → narrate(9b)).
- File de génération unifiée (refactor de `npc_prefetch`) — écarté au
  profit du gate partagé, `npc_prefetch` vient d'être livré et testé.

## Design

### 1. Câblage embed lobby

`_pregenerate_campaign_world` (`bot/cogs/session.py:637` et `:649`) pose
déjà `lobby.pregen_phase` aux transitions ARC → LOCATION → READY.

- Passer `pregen_status=lobby.pregen_phase` dans `_refresh_lobby_embed`
  (le champ « 🌍 Génération du monde » existe déjà, commit `0eb9da2`).
- Rafraîchir l'embed du lobby à chaque transition de phase dans
  `_pregenerate_campaign_world` (best-effort : un échec d'édition Discord
  ne doit pas faire échouer la pré-génération).

### 2. Module `bot/location_prefetch.py`

Calqué sur `bot/npc_prefetch.py` (tâches fortement référencées, dédup,
exceptions piégées par job, LLM via `asyncio.to_thread`).

**Déclencheurs** (un appel `schedule_location_prefetch(session, db_factory=...)`) :
- au lancement de campagne, après le post de la première scène ;
- à la fin de `change_location` (après `hydrate_scene`) ;
- au `/resume` (le lieu courant peut avoir des voisins stubs).

**File de jobs** : les `connections` du lieu courant dont la ligne DB est
absente ou `generated=False`. Ordre : d'abord l'exit correspondant au
`location_hint` du beat **courant** de l'arc s'il figure dans la file,
sinon celui du beat **suivant** (destination la plus probable), puis les
autres dans l'ordre de la liste.

**Génération** : même logique que le chemin sync de
`bot/world_navigation.py:141-182` — arc_hints depuis `story_arc`,
`required_connections` (back-links existants du stub, sinon le parent),
nom forcé à la destination demandée, injection de sécurité des back-links.
Le helper de génération est **extrait de `change_location` et partagé**
(une seule implémentation, pas de copie). Persistance par
`LocationRepository.upsert` + `create_exit_stubs` (petits-enfants en
stubs, **aucun appel LLM récursif**).

**Invariant d'état** : le prefetch écrit le monde (DB), jamais l'état de
la partie — il ne touche ni `session.current_location`, ni
`session.npcs`, ni `session.campaign`.

**Fraîcheur** : avant chaque job, si `session.current_location` n'est plus
le lieu parent de la file, abandonner le reste de la file (les lieux déjà
générés restent en DB). Avant chaque job, relire la ligne DB : si
`generated=True` entre-temps (MOVE sync l'a générée), sauter le job.

### 3. Gate partagé `bot/prefetch_gate.py`

- Un `asyncio.Lock` module-level.
- `npc_prefetch` (autour de son `to_thread` par NPC) et
  `location_prefetch` (autour de son `to_thread` par lieu) l'acquièrent
  avant chaque appel LLM → au plus une génération de fond en vol.
- **Politesse** : après acquisition du gate et avant l'appel LLM, attendre
  que `session.action_lock` soit libre via un acquire/release immédiat
  (`async with session.action_lock: pass`) — événementiel, pas de polling ;
  le lock n'est tenu qu'un instant et tout joueur en attente derrière ne
  subit qu'un no-op. Invariant : un job ne démarre pas pendant qu'une
  action joueur est en vol.
- Ordre de scheduling à l'arrivée : NPC d'abord (4b — les joueurs parlent
  avant de bouger), lieux ensuite (9b) → un seul swap 4b→9b par arrivée.

### 4. Course MOVE vs prefetch

Registre `_IN_FLIGHT: dict[tuple[str, str], asyncio.Task[...]]`
(clé `(campaign_id, location_name)`), où une entrée n'apparaît **qu'au
démarrage effectif de l'appel LLM** (après gate + politesse), et
disparaît en `finally`.

Dans `change_location` :
- destination avec job **démarré** → `await` ce job, puis relire la DB
  (~57 s au pire, au lieu de ~114 s si on empilait une 2e génération dans
  la file Ollama). Si le job attendu a échoué ou que la relecture DB ne
  donne pas un lieu généré → fallback génération sync (comportement
  actuel), `LocationChangeError` inchangé.
- destination **en file mais non démarrée** (pas dans `_IN_FLIGHT`) →
  génération sync comme aujourd'hui ; le prefetch sautera ce lieu
  (relecture DB avant chaque job).

Cette distinction démarré/en-file élimine le deadlock
`action_lock` ↔ politesse : MOVE tient `action_lock` ; un job non démarré
attendrait sa libération — on ne l'attend donc jamais.

### 5. Erreurs

- Chaque job piège ses exceptions : log + passage au job suivant.
- Échec ou absence de prefetch = comportement actuel (génération sync au
  MOVE). Aucun nouveau mode d'échec visible joueur.
- Tâches fortement référencées (registre module-level + done callback),
  comme `npc_prefetch`.

## Tests

`tests/bot/test_location_prefetch.py` :
1. Les voisins stub du lieu courant sont générés et persistés
   (`generated=True`, back-links préservés, stubs petits-enfants créés).
2. Dédup : un lieu en cours de génération n'est pas re-généré par un
   second schedule.
3. MOVE vers un job **démarré** : `change_location` attend le job et ne
   paye qu'une génération (le générateur n'est appelé qu'une fois).
4. MOVE vers un job **en file non démarré** : génération sync immédiate,
   pas d'attente (anti-deadlock), le prefetch saute ensuite ce lieu.
5. Le gate sérialise un prefetch NPC et un prefetch lieu concurrents.
6. Politesse : aucun job ne démarre tant que `session.action_lock` est
   tenu.
7. Fraîcheur : après un départ du lieu parent, la file restante est
   abandonnée.
8. L'échec d'un voisin n'empêche pas la génération des suivants.
9. Le prefetch ne mutate ni `session.current_location` ni `session.npcs`.

Câblage lobby (dans les tests session existants) :
10. `_refresh_lobby_embed` passe `pregen_status` ; l'embed est rafraîchi
    aux transitions de phase.

Tests de non-régression : suite complète verte (`uv run pytest`),
`ruff check .`, `mypy` sans nouvelle erreur.

## Vérification / mesures

Ajouter au doc `docs/audits/2026-06-10-h8-latency-measurements.md` une
section « prefetch lieux » : MOVE vers lieu préfetché (attendu : <2 s,
lecture DB) vs MOVE à froid (baseline ~57-80 s), et le pire cas mesuré
d'une action derrière un job de fond.
