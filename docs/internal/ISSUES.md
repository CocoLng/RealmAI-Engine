# Anomalies, bugs et points d'amelioration

Classement par severite.

**Legende** : 🔴 bloquant · 🟠 eleve · 🟡 moyen · 🟢 mineur 

---

NE PAS ECRIRE D'ISSUE SI CETTE DERNIERE EST RESOLUE, ce document sert a noter les problemes connus et leur statut de resolution. Si un point est resolu il ne doit pas rester dans cette liste, quand on en résoud un on doit supprimer la ligne correspondante.

---

## 🟡 Severite moyenne (restants)

### M2. Parsing de fragile
**Ou** :
- [engine/combat.py](../../engine/combat.py) `_double_dice()` — parse string sur `d`
- [engine/spells.py](../../engine/spells.py) `get_cantrip_damage_dice()` — suppose `"1dX"`
**Probleme** : fail sur formats inattendus (`"2d6+1"`, `"1d10+DEX"`).
**Note** : les deux utilisent deja `parse_dice()` de `dice.py` (parseur canonique regex). Risque faible tant que les expressions restent simples.

### M9. Validators ne checkent pas la proficiency / concentration conflict
**Ou** : [engine/validators.py](../../engine/validators.py).
**Statut** : Partiellement resolu.
- Concentration conflict : deja implemente (log info quand conflit detecte).
- Weapon proficiency : TODO ajoute, en attente du systeme de `weapon_proficiencies` sur Character.

---

## 🟢 Mineurs (restants)

Aucun.

---

## Ameliorations non-bugs (nice-to-have)

- **Streaming** du Narrator pour latence percue (actuellement tout le narratif arrive en un bloc apres ~10-20s).
- **Narrator cache** pour actions repetitives (LOOK sur meme location).
- **Prompt tokenizer reel** (tiktoken-like) pour remplacer `word_count * 1.3`.
- **Extract `ITEM_CATALOG` et `SPELL_CATALOG`** dans des YAML editables.
- **Config du budget memoire** par campagne (actuellement global dans `ContextBudget`).
- **Alembic** si besoin de renommage/suppression de colonnes (migrations actuelles via `PRAGMA user_version` suffisent pour les ajouts).
- **Metriques** Prometheus/OpenTelemetry.
- **Dashboard admin** pour inspecter `GameSession` live.
- **Tests d'integration vrai Ollama** en CI optionnelle.
- **Entity resolver multilingue** — actuellement les lemmes sont FR only, extensibles via strategie pluggable.
