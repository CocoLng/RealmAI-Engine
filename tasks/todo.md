# RealmAI-Engine — Task Board

## Character System Refactor (Agents 01–05) — DONE ✅

- [x] Agent 01 — Package split: `engine/character.py` → `engine/character/` package
- [x] Agent 02 — Features & skill proficiencies (`Feature`, `Skill`, `RACIAL_FEATURES`, `CLASS_FEATURES`)
- [x] Agent 03 — Standard Array + `create_character()` flow (stat assignment, starter gear, race/class defaults)
- [x] Agent 04 — Discord wizard: `/create_character` multi-step modal flow with skill selection
- [x] Agent 05 — DB compatibility: backfill function, isolation audit, documentation update

## Character Creation (UX backlog)

- [ ] Permettre de remodifier son perso apres création (pendant l'attente de la génération). Si la personne re clique sur "Create Character"
- [ ] Clear le channel discord des messages pour l'immersion
- [ ] Ajouter un compteur de démarrage pour avertir les joueurs que le jeu va commencer
- [ ] Ajouter un bouton sur le premier message pour forcer le lancement du jeu si la génération est fini mais que certains joueurs n'ont pas cliqué sur "Create Character" alors ils sont exclus du jeu
- [ ] Ajouter un message initial de contexte pour l'immersion lorsque le jeu commence
- [ ] Faire des stats de perso avec un level ou le joueur peut comme sur un DnD classique faire des choix pour augmenter certaines stats

## Deferred (future phases)

- [ ] Backgrounds (Acolyte, Criminal, Noble, etc.) — 2 skill proficiencies + equipment + RP trait
- [ ] Feats (ASI-or-feat at levels 4/8/12/16/19)
- [ ] Multiclassing
- [ ] Language system
- [ ] Tool proficiencies
- [ ] Class features level 2+ (full progression)
- [ ] Point Buy and 4d6-drop-lowest as alternative stat methods
- [ ] Shop / buy-sell system
- [ ] Extended spell catalog (>20 current spells)
