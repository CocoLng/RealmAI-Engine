# Design : Traduction UI des labels de création de personnage

**Date :** 2026-04-06  
**Scope :** UI layer uniquement — valeurs engine/DB restent en anglais

---

## Problème

Quand `language = "fr"` est configuré sur le serveur, les menus de sélection (race, classe, alignement) et les boutons d'équipement de départ affichent les valeurs anglaises des enums (`Human`, `Fighter`, `Lawful Good`, `Sword & Shield`, etc.) au lieu de labels français.

## Périmètre

- Traduire les **labels d'affichage** dans `CharacterCreateView` et `StarterGearView`
- Les **valeurs canoniques** (enum `value`, clés DB, logs) restent en anglais
- Aucun changement dans `engine/`

---

## Architecture

### `bot/i18n.py` (nouveau fichier)

Tables de traduction indexées `language → clé_anglaise → label_affiché` :

- `RACE_LABELS` — 7 races (Human → Humain, Elf → Elfe, etc.)
- `CLASS_LABELS` — 6 classes (Fighter → Guerrier, Wizard → Mage, etc.)
- `ALIGNMENT_LABELS` — 9 alignements (Lawful Good → Loyal Bon, etc.)
- `KIT_LABELS` — noms et descriptions de tous les kits par classe

Fonction utilitaire :
```python
def get_label(table, language: str, key: str) -> str:
    """Retourne le label traduit, ou la clé en fallback si absent."""
    return table.get(language, {}).get(key, key)
```

Le fallback garantit qu'une clé manquante n'est jamais une erreur.

### `CharacterCreateView` (modifié)

- Accepte `language: str = "en"` dans `__init__`
- Les `SelectOption` sont construites **dynamiquement** dans `__init__` :
  - `label` = label traduit via `get_label`
  - `value` = valeur enum anglaise (inchangée — utilisée pour parser le retour)
- Les messages intermédiaires (`"Race: **X** | Classe: **Y**"`) affichent aussi le label traduit

### `StarterGearView` / `_KitButton` (modifié)

- `StarterGearView` accepte `language: str = "en"`
- `_KitButton` reçoit `display_name` et `display_description` traduits
- L'objet `StarterKit` original est conservé intégralement pour la logique

### `CampaignLauncher` (modifié)

- Passe `language=self.language` à `CharacterCreateView`
- Traduit les noms/descriptions de kits dans le message `"Choisis ton équipement de départ"` via `KIT_LABELS`
- Passe `language=self.language` à `StarterGearView`

---

## Flux de données

```
CampaignLauncher.language = "fr"
  → CharacterCreateView(language="fr")
      SelectOption(label="Humain", value="Human")  ← affiché en français
      select.values[0] == "Human"                   ← enum parsing inchangé
      Race("Human") == Race.HUMAN                   ← engine inchangé
  → StarterGearView(language="fr")
      _KitButton(display_name="Épée & Bouclier", kit=StarterKit(name="Sword & Shield"))
```

---

## Fichiers touchés

| Fichier | Action |
|---------|--------|
| `bot/i18n.py` | Créer |
| `bot/views/character_create_view.py` | Modifier |
| `bot/views/starter_gear_view.py` | Modifier |
| `bot/campaign_launcher.py` | Modifier |

**Aucun fichier engine/ ni DB n'est modifié.**

---

## Tests

- Ajouter `tests/bot/test_i18n.py` : vérifier que tous les enums et kits ont une traduction FR, et que le fallback fonctionne pour une langue inconnue.
