# TODO — RealmAI-Engine

## Chantier en cours : Refonte Character System

Voir `tasks/README.md` pour l'orchestration et `tasks/agents/` pour les fiches détaillées.

- [ ] Agent 01 — Package split (`character.py` → `character/`)
- [ ] Agent 02 — Feature system + Skills
- [ ] Agent 03 — Standard Array + refonte `create_character()`
- [ ] Agent 04 — Discord wizard (stats + skills + flow complet)
- [ ] Agent 05 — DB migration + cleanup

## Character Creation (autres items)

- [ ] Permettre de remodifier son perso après création (pendant l'attente de la génération). Si la personne re-clique sur "Create Character"
- [ ] Clear le channel Discord des messages pour l'immersion
- [ ] Ajouter un compteur de démarrage pour avertir les joueurs que le jeu va commencer
- [ ] Ajouter un bouton pour forcer le lancement du jeu si la génération est finie mais que certains joueurs n'ont pas créé de perso (ils sont exclus mais peuvent voir le channel)
- [ ] Ajouter un message initial de contexte pour l'immersion (embed avec les personnages du groupe et intro de l'aventure)

## Différé (à faire plus tard)

- [ ] Backgrounds (Acolyte, Criminal, Noble, etc.) — 2 skill proficiencies + équipements + trait RP
- [ ] Feats (choix ASI-ou-feat aux niveaux 4/8/12/16/19)
- [ ] Multiclassing
- [ ] Système de langues
- [ ] Tool proficiencies
- [ ] Class features de niveau 2+ (progression complète)
- [ ] Point Buy et 4d6-drop-lowest comme méthodes alternatives de stats
- [ ] Boutique / système achat-vente
- [ ] Catalogue de sorts étendu (>20 sorts actuels)
