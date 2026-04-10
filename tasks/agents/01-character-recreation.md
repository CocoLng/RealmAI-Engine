# Agent 01 — Character Re-creation

## Objectif

Permettre a un joueur de recliquer sur "Creer mon personnage" pour recommencer la creation de son perso, tant que la partie n'a pas demarre. Son ancien personnage est supprime (memoire + DB) et le wizard recommence.

## Dependances

Aucune.

## Fichiers a modifier

| Fichier | Modification |
|---------|-------------|
| `bot/campaign_launcher.py` | Modifier `_on_create_character_clicked()` : remplacer le guard qui bloque les re-clics par une logique de reset. Ajouter des guards dans `_on_character_created` et `_on_gear_selected` pour ignorer les callbacks stale apres un reset. |

## Regles critiques

- **Jamais apres le lancement** : si `_launched is True`, rejeter le re-clic avec "La partie a deja commence !"
- **Reset complet** : supprimer `characters[uid]`, `inventories[uid]`, `spellcasters[uid]`, remettre `player_progress[uid] = PENDING`
- **DB cleanup** : appeler `PlayerCharacterRepository.delete(user_id, campaign_id)` — la methode existe deja (`db/repositories/player_character_repo.py:68`)
- **Notification flag** : remettre `_notified_players_ready = False` (le decompte joueurs a change)
- **Message public** : poster "**@user** recommence la creation de son personnage." dans le channel
- **Callbacks stale** : dans `_on_character_created` et `_on_gear_selected`, si `player_progress[uid] == PENDING` (reset entre-temps), ignorer le callback silencieusement (log warning)

## Detail de l'implementation

### 1. Modifier `_on_create_character_clicked()` (ligne 159)

Remplacer le guard actuel (lignes 175-179) :
```python
# AVANT
if self.player_progress[user_id] != PlayerProgress.PENDING:
    await interaction.response.send_message(
        "Tu as deja cree ton personnage !", ephemeral=True,
    )
    return

# APRES
if self._launched:
    await interaction.response.send_message(
        "La partie a deja commence !", ephemeral=True,
    )
    return

if self.player_progress[user_id] != PlayerProgress.PENDING:
    # Reset player state for re-creation
    self.characters.pop(user_id, None)
    self.inventories.pop(user_id, None)
    self.spellcasters.pop(user_id, None)
    self.player_progress[user_id] = PlayerProgress.PENDING
    self._notified_players_ready = False

    # Delete from DB
    db_session = self.bot.db_factory()
    try:
        from db.repositories import PlayerCharacterRepository
        PlayerCharacterRepository(db_session).delete(user_id, self.campaign.id)
        db_session.commit()
    finally:
        db_session.close()

    await self.channel.send(
        f"**{interaction.user.display_name}** recommence la creation de son personnage.",
    )
    logger.info("ONBOARD reset user=%s campaign=%s", interaction.user, self.campaign.id)
```

### 2. Guard dans `_on_character_created()` (ligne 186)

Ajouter en debut de methode, apres `user_id = interaction.user.id` :
```python
if self.player_progress.get(user_id) == PlayerProgress.PENDING:
    logger.warning("ONBOARD stale character callback user=%s campaign=%s", interaction.user, self.campaign.id)
    return
```

### 3. Guard dans `_on_gear_selected()` (ligne 255)

Ajouter en debut de methode, apres `user_id = interaction.user.id` :
```python
if self.player_progress.get(user_id) == PlayerProgress.PENDING:
    logger.warning("ONBOARD stale gear callback user=%s campaign=%s", interaction.user, self.campaign.id)
    return
```

## Fichiers impactes (imports, configs)

Aucun nouveau import. `PlayerCharacterRepository` est deja importe inline dans le meme fichier.

## Tests a creer

| Fichier | Ce qu'il teste |
|---------|----------------|
| `tests/test_campaign_launcher_recreation.py` | Re-creation du personnage |

Tests :
- `test_recreate_resets_state_from_gear_done` — joueur GEAR_DONE reclique, etat revient a PENDING, dicts vides
- `test_recreate_resets_state_from_character_done` — joueur CHARACTER_DONE reclique, meme reset
- `test_recreate_deletes_db_record` — `PlayerCharacterRepository.delete` est appele
- `test_recreate_blocked_after_launch` — `_launched=True`, clic rejete
- `test_stale_character_callback_ignored` — callback `_on_character_created` ignore si progress == PENDING
- `test_stale_gear_callback_ignored` — callback `_on_gear_selected` ignore si progress == PENDING
- `test_recreate_resets_notified_flag` — `_notified_players_ready` revient a False

## Validation

```bash
uv run pytest
uv run ruff check .
uv run mypy .
```

Tout doit etre vert. Si un seul test casse, c'est un bug a corriger avant de continuer.

## Estimation

Complexite : Moyenne
