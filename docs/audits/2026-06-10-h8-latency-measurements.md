# H8 — Mesures avant/après (chantier I « latence »)

Référence : finding **H8** de `2026-06-10-system-audit.md`. Baseline extraite
des logs réels (`logs/realm_20260426_192127.log`, `realm_20260427_011313.log`,
sessions d'avril) ; « après » mesuré le 2026-06-10 sur la même machine
(M3 Pro 18 GB, qwen3.5:9b) via deux runs réels d'`ArcGenerator` +
`WorldGenerator` (scripts directs, mêmes paramètres que `_pregenerate_campaign_world`).

## Démarrage de campagne (pregen arc + location)

| Mesure | Baseline (logs réels) | Après (runs réels) | Gain |
|---|---|---|---|
| Arc 10-11 beats | 359,0 s — 4 712 tok générés | **120,3 s** — 1 589 tok | **3,0×** |
| Arc 14-15 beats | 398,1 s — 5 496 tok | **170,8 s** — 2 064 tok | **2,3×** |
| Prompt arc (entrée) | 3 243-3 278 tok | 1 610-1 675 tok | −50 % |
| Location (starting_area) | 79,8 s — 992 tok | **56,6 s** — 621 tok | **1,4×** |
| **Pregen total** | **438-478 s** | **177-227 s** | **~2,2×** |

Le débit du 9b est inchangé (~12-13 tok/s) : tout le gain vient de la
réduction de la sortie. Ce qui a été retiré de la sortie LLM (commit
`feat(arc-gen)`) :

- `villain_stat_block` (~1 000-1 500 tok) — les logs réels montraient un
  fallback `generic_boss:*` sur 100 % des sessions : on payait un blob
  systématiquement jeté. Désormais directement déterministe.
- `objectives`/`advance_rule`/`judge_rubric`/`player_visible_hint`
  (~150-250 tok/beat) — scaffoldés par recette `(type, sous-type)`
  (c'était déjà le chemin de fallback).
- `on_complete` (~60-80 tok/beat) — `unlock_exits` chaîné vers la
  `location_hint` du beat suivant ; les gates `HAS_ITEM` sont semées dans
  les `add_items` du beat précédent (satisfiabilité garantie, ce que le
  LLM ne faisait presque jamais).

Caps de sécurité : `num_predict=3072` (arc), `1536` (location) — bornent
les générations pathologiques qui couraient jusque-là vers `num_ctx`.

Qualité vérifiée sur les runs : chaîne premise→situation→call_to_action
conforme (villain jamais nommé, hook pragmatique, PJ outsiders), twist à
la position exacte de la recette, exits déverrouillés cohérents avec la
location du beat suivant, prose française correcte.

## Actions libres

Le coût caché de 18-27 s (fiche NPC générée paresseusement au premier
TALK, en plein milieu du pipeline d'action) sort du chemin chaud : commit
`feat(npc-prefetch)` pré-génère les fiches en tâche de fond dès
l'hydratation de scène (lancement + chaque MOVE). Le chemin TALK de
`bot/pipeline/resolve.py` (non modifié) saute sa génération paresseuse dès
que la fiche est remplie.

Action TALK attendue : ~73 s → ~50 s (interprète ~11 s + npc_agent
~10-12 s + narration ~25-29 s). À confirmer sur une session Discord réelle.

Non traité (hors périmètre du chantier) : le swap de modèle 4b↔9b par
action (~6-8 s dans l'interprète, inhérent au pipeline interpret(4b) →
narrate(9b)) et la latence du narrateur lui-même.

## Suite du chantier (2026-07-03)

Câblé : `bot/cogs/session.py` passe `pregen_status=lobby.pregen_phase` à
`build_lobby_embed` et rafraîchit l'embed à chaque transition de phase —
la progression est visible en live dans le lobby.

Prefetch des lieux voisins (`bot/location_prefetch.py` + gate global
`bot/prefetch_gate.py`) : après chaque arrivée (lancement, MOVE, /resume),
les voisins non générés du lieu courant sont générés en tâche de fond —
un MOVE vers un lieu préfetché devient une lecture DB (~57-80 s → <2 s
attendu). Au plus une génération de fond en vol (gate partagé avec le
prefetch NPC) ; un job ne démarre jamais pendant une action joueur ;
`change_location` attend un job déjà démarré pour sa destination (une
génération au lieu de deux).

## Mesures live (2026-07-19) — session Discord réelle, M3 Pro, qwen3.5

Campagne « Dark Fantasy », personnage créé via le vrai lobby (smoke C8),
bot en TEST_MODE piloté par tester bot. Sources : transcripts
`tasks/logs/2026-04-26-lobby-live-test.txt` et
`tasks/logs/2026-07-19-h8-live-session2.txt`, croisés avec les
`logs/realm_*.log` du bot (lignes `OLLAMA time=`, `LOCATION changed`,
`Location prefetch`).

| Mesure | Attendu | Mesuré live | Verdict |
|---|---|---|---|
| Lancement : clic Démarrer → narration d'ouverture | pregen prête en fond | **33 s** (pregen finie pendant la création du perso) | ✅ vs 438-478 s bloquants en avril |
| Prefetch post-arrivée (2 voisins) | ~56 s/lieu | **45 s les deux** (~22 s/lieu) | ✅ |
| MOVE vers lieu préfetché — part génération | < 2 s | **0 appel** world-gen (lecture DB) | ✅ |
| MOVE vers lieu préfetché — wall-clock total | — | **35,7 s** (interprète 4b 11 s + narration 9b 22 s) | ✅ le coût restant est le pipeline LLM, plus la génération |
| MOVE en plein prefetch (job démarré sur la destination) | 1 génération, pas 2 | **1 seule** (le job du prefetch, 34,9 s) ; `change_location` l'attend puis lit la row (`generated=False` au log = rien régénéré) | ✅ verrouillé par le fix « relecture après `wait_for_started_job` » + test déterministe (2026-07-19) |
| Entrée en combat (action libre → bootstrap + initiative + narration) | — | **39 s** | ✅ |
| Round de combat suivant (prefetch actif en fond) | — | **36 s**, round 2 atteint, aucun blocage par le gate | ✅ |
| Action TALK (fiche NPC préfetchée) | ~50 s | non mesuré (aucun PNJ dans les lieux générés de cette session) | ⏳ |

Notes : le `/resume` post-redémarrage recharge la campagne en **6 s** ;
pendant les hints, la contention Ollama (9b de prefetch en vol) a fait
timeouter le BeatJudge 4b — dégradation propre depuis le fix
`fix(hint)` du même jour (avant : sentinel `_judge_timeout_` affiché
et cooldown consommé).
