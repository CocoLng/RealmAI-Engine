# docs/superpowers/ — archive de design

Ce dossier est une **archive historique**. Il garde la trace du workflow
*brainstorm → spec → plan → implement* utilisé pendant le développement du
projet. Chaque feature non-triviale a généré un trio (spec, plan, code) ; les
deux premiers vivent ici, le troisième est dans le repo.

> ⚠️ **Le code fait foi.** Ensuite [`docs/internal/`](../internal/README.md)
> (synthèse à jour). Ces specs et plans peuvent diverger de l'implémentation
> finale — ils décrivent ce qui était *prévu* au moment où ils ont été écrits,
> pas ce qui est *en place* aujourd'hui.

## Convention de nommage

Tous les fichiers sont datés en préfixe `YYYY-MM-DD-`, dans l'ordre
chronologique d'écriture (et non d'implémentation). Les paires
spec/plan partagent le même slug :

```
specs/2026-04-25-beat-progression-engine-design.md
plans/2026-04-25-beat-progression-engine.md
```

## Sous-dossiers

| Dossier | Contenu | Quand le consulter |
|---|---|---|
| [`specs/`](specs/) | Specs de design (24) : intention, contraintes, alternatives écartées, schémas data | Comprendre **pourquoi** une feature existe sous sa forme actuelle, ou avant un refactor important du même domaine |
| [`plans/`](plans/) | Plans d'implémentation (21) : découpage en phases, ordre des PRs, gates, tests à écrire | Reconstituer **comment** une feature a été livrée, ou s'inspirer d'un plan similaire pour un nouveau chantier |

## Quand utiliser ces docs

✅ **Bon usage** :
- Comprendre la *motivation* derrière un module (la spec capture la
  conversation initiale et les alternatives rejetées).
- S'inspirer d'un plan passé pour structurer un nouveau chantier
  (découpage en phases, choix des gates, ordre des PRs).
- Reconstituer l'historique d'un design qui a évolué en plusieurs vagues
  (ex. `directors-cut-phase-{a,b,c,d}` pour Director's Cut).

❌ **Mauvais usage** :
- Référencer ces docs comme « la doc » d'un module — c'est
  [`docs/internal/`](../internal/) qui tient ce rôle.
- Croire qu'un plan listé ici est l'API actuelle : un plan décrit ce qui
  était à faire, pas ce qui est. Le code et `docs/internal/STATE.md` sont
  les sources autoritaires.

## Lien avec `tasks/lessons.md`

[`tasks/lessons.md`](../../tasks/lessons.md) est le *corollaire* de cette
archive : ce que **les implémentations** ont appris, pas ce que les
**designs** prévoyaient. Quand un plan d'ici a livré une surprise (bug
caché, contrainte sous-estimée, raccourci), la leçon en sort.

## Pourquoi garder cette archive

Trois usages concrets vérifiés au fil du projet :

1. Reprendre un chantier interrompu sans tout redécouvrir.
2. Auditer une décision contestée six mois plus tard (« pourquoi a-t-on
   choisi N+ChromaDB plutôt que pgvector ? » → la spec a la réponse).
3. Servir de template pour de nouveaux specs/plans — les meilleurs
   exemples sont `beat-progression-engine` et
   `character-creation-redesign`.

Le coût (~1,3 MB de markdown, zéro impact sur la prod) est négligeable face
au temps qu'on regagne quand on en a besoin.
