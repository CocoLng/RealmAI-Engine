# Task 00 — Bugfix : protéger le villain du trivial resolve

**Phase** : 0 — Bugfix immédiat
**Dépendances** : aucune
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

Une campagne test a permis au joueur de tuer le villain d'arc (Vellus le Mentisseur) en **un seul message** via `(Attack) j'attaque vellus`. L'attaque a été résolue par `_trivial_kill` (voie deterministe 1-shot conçue pour les commoners insignifiants) sans passer par le système de combat complet.

**Cause racine** : [bot/scene_hydration.py:37-54](../../bot/scene_hydration.py) crée **tous** les NPCs (villain inclus) avec `hp=max_hp=4, ac=10, disposition=NEUTRAL`. Ça coche les deux conditions de `is_trivially_defeatable()` ([engine/combat.py:644-698](../../engine/combat.py)) et le filtre disposition de `_should_trivial_resolve()` ([bot/action_pipeline.py:568-584](../../bot/action_pipeline.py)) n'exclut que HOSTILE/UNFRIENDLY — donc un villain NEUTRAL passe.

Cette tâche est un **filet de sécurité minimal** pour bloquer le one-shot du villain sans attendre le refit complet des stats (qui vient en Phase 1/4). Elle doit être shippable **immédiatement** pour débloquer les campagnes actuellement en cours.

## Scope

Ajouter un check en tête de `ActionPipeline._should_trivial_resolve` qui refuse le trivial resolve pour :

1. Tout NPC dont le nom matche `session.story_arc.villain_name`.
2. Tout NPC dont le nom apparaît dans les `npc_names` du beat courant quand `encounter_type in ("combat", "boss")`.

## Fichier à modifier

- [bot/action_pipeline.py](../../bot/action_pipeline.py) — méthode `_should_trivial_resolve` (environ lignes 568-584).

## Implémentation — esquisse

Ajouter en tête de méthode, avant le check `disposition` :

```python
def _should_trivial_resolve(self, npc: NPC) -> bool:
    if not npc.is_alive:
        return False

    # Story-critical NPCs are never trivially resolved, even if they were
    # hydrated with weak stats (commoner-style). They must go through the
    # full combat system once it's bootstrapped.
    if self.session is not None and getattr(self.session, "story_arc", None) is not None:
        arc = self.session.story_arc
        if npc.name == arc.villain_name:
            return False
        current_beat = arc.beats[arc.current_beat_index]
        if (
            current_beat.encounter_type in ("combat", "boss")
            and npc.name in current_beat.npc_names
        ):
            return False

    # Existing disposition and stats checks continue below...
    if npc.disposition in (NPCDisposition.HOSTILE, NPCDisposition.UNFRIENDLY):
        return False
    return is_trivially_defeatable(npc)
```

Utiliser `getattr(self.session, "story_arc", None)` car les tests existants passent parfois des `SimpleNamespace` qui ne portent pas toujours cet attribut — voir commit `15c9555`.

## Acceptance criteria

- [ ] Attaquer un NPC dont `name == session.story_arc.villain_name` ne déclenche **pas** `_trivial_kill`.
- [ ] Attaquer un NPC listé dans `beats[current_beat].npc_names` avec `encounter_type == "combat"` ne déclenche **pas** `_trivial_kill`.
- [ ] Un attaque qui aurait dû être trivial-killed tombe désormais dans le chemin de bootstrap `_bootstrap_combat_against` (voie combat existante — elle marchait déjà, on la réactive juste).
- [ ] Les commoners normaux (pas villain, pas dans un beat combat) continuent d'être trivial-resolvables comme avant.

## Tests à ajouter

Dans `tests/bot/test_action_pipeline.py` :

- `test_trivial_resolve_blocked_for_villain_by_name` — build session avec story_arc.villain_name, crée NPC weak du même nom, vérifie `_should_trivial_resolve` retourne False.
- `test_trivial_resolve_blocked_for_combat_beat_npc` — build session avec beat combat contenant le NPC, vérifie False.
- `test_trivial_resolve_allowed_for_neutral_commoner_in_social_beat` — non-regression : un commoner faible hors beat combat reste trivial.
- `test_trivial_resolve_blocked_via_full_pipeline` — integration : appel de `_validate` avec ATTACK contre villain, vérifier que le chemin `_bootstrap_combat_against` est emprunté au lieu de `_trivial_kill`.

## Hors scope

- **Ne pas** refondre les stats du villain dans `scene_hydration` — c'est la tâche [43](43_hydration_dispatches_tier.md).
- **Ne pas** implémenter le nouveau trigger `detect_combat_trigger` — c'est la tâche [20](20_combat_entry_module.md).
- **Ne pas** bloquer MOVE en combat — c'est la tâche [01](01_bugfix_move_blocked_in_combat.md).
- **Ne pas** toucher à `engine/combat.py::trivial_resolve` — la fonction reste valide pour les vrais commoners.

## Validation finale

```bash
uv run pytest tests/bot/test_action_pipeline.py -v
uv run ruff check bot/action_pipeline.py
uv run mypy bot/action_pipeline.py
```
