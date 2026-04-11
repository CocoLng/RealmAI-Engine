# TODO — RealmAI-Engine

Commit at the end of a phase, do not co author claude.

## Chantier en cours : Système de Combat D&D 5e

Voir `tasks/combat/README.md` pour l'orchestration complète et `tasks/combat/*.md` pour les fiches détaillées.

### Phase 0 — Bugfix immédiat (shippable dès maintenant)
- [x] Task 00 — Protéger le villain du trivial resolve (filet de sécurité minimal)
- [x] Task 01 — Bloquer MOVE en combat actif (filet, sans auto-convert pour l'instant)

### Phase 1 — Fondations NPC & engine (parallèle Phase 0)
- [x] Task 10 — NPCStatBlock model
- [x] Task 11 — Librairie d'archétypes NPCs
- [x] Task 12 — Zone model
- [x] Task 13 — Conditions SURPRISED et CONCENTRATING

### Phase 2 — Moteur de combat multi-ennemis
- [x] Task 20 — Module d'entrée en combat
- [x] Task 21 — Initiative & surprise (3 cas)
- [x] Task 22 — CombatState multi-enemies + turn mgmt + persistence
- [x] Task 23 — Action economy (Move + Action + Bonus + Reaction)
- [x] Task 24 — Zone movement + opportunity attacks

### Phase 3 — Validation & pipeline
- [ ] Task 30 — Validateurs de combat stricts
- [ ] Task 31 — ActionPipeline : dispatch combat-aware + auto-convert MOVE→FLEE
- [ ] Task 32 — Résolution de FLEE (check DEX)

### Phase 4 — Interprète & générateurs LLM (parallèle)
- [ ] Task 40 — Interprète : détection d'intention létale
- [ ] Task 41 — World generator : zones + triggers
- [ ] Task 42 — Arc generator : villain stat block complet
- [ ] Task 43 — Hydration : dispatch par tier d'archétype

### Phase 5 — IA tactique (NPC brains)
- [ ] Task 50 — IA scripted pour minions
- [ ] Task 51 — IA elite : behavior profiles + signatures
- [ ] Task 52 — Boss : LLM tactician
- [ ] Task 53 — Legendary actions off-turn
- [ ] Task 54 — Phase transitions

### Phase 6 — Discord UI
- [ ] Task 60 — Module d'embeds de jets de dés
- [ ] Task 61 — Embed "Combat commence"
- [ ] Task 62 — Refonte embed d'état combat
- [ ] Task 63 — Vues d'actions de combat (boutons)
- [ ] Task 64 — Ping de tour + timeout

### Phase 7 — Narrateur & cohérence narrative
- [ ] Task 70 — Narrateur : contexte combat
- [ ] Task 71 — Prompt narrateur pour transitions de phase

### Phase 8 — Fin de combat & intégration
- [ ] Task 80 — Conditions de fin de combat
- [ ] Task 81 — Résolution sociale mid-combat (truce)
- [ ] Task 82 — Test end-to-end Discord live (**gate de fin**)

### Phase 9 — Documentation
- [ ] Task 90 — Rédaction `docs/internal/COMBAT_SYSTEM.md`

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
