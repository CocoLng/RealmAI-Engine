# Launch Immersion Redesign

## Context

The current campaign launch sequence has a bare-bones countdown (`**La partie commence dans 3...**` edited in place) and jumps straight into the opening crawl. There's no moment for the party to discover each other, and no visual structure separating reference material from the narrative flow. This redesign improves the countdown aesthetics and adds persistent character cards at the top of the quest channel.

## Design

### Launch Sequence (after purge)

```
1. Animated countdown embed (edited 3→2→1, then DELETED)
2. Party character cards — 1 condensed embed per player (PERMANENT)
3. Unicode separator message (PERMANENT)
4. Opening crawl embed (PERMANENT, unchanged)
5. Scene awareness embed (PERMANENT, unchanged)
6. → Normal narrative flow begins
```

### 1. Animated Countdown Embed

A single embed message, edited 3 times with 1.5s intervals, then deleted.

| Step | Title | Description | Color |
|------|-------|-------------|-------|
| 3 | `「 3 」` | *Préparez-vous, aventuriers...* | Gold `0xDAA520` |
| 2 | `「 2 」` | *Les destins convergent...* | Orange `0xCC7000` |
| 1 | `「 1 」` | *L'aventure commence...* | Red `0xCC0000` |

- **Footer**: Campaign name
- **Timing**: 1.5s sleep between each edit, 1.5s after last step before delete
- **Total duration**: ~6s (vs current 4s)
- **Error handling**: Same pattern as current — `try/except (Forbidden, HTTPException, NotFound)`, log and continue

### 2. Party Character Cards (Condensed)

One embed per player, posted sequentially with ~0.3s delay for a cascade effect.

**Embed structure:**
- **Title**: `"{name} — {race} {class}"` (e.g. "Aldric — Humain Guerrier")
- **Color**: Class-based, reuse existing `CLASS_COLORS` from `character_embed.py`
- **Description**: `"Niveau {level} · {hp} PV · CA {ac}"`
- **Single field** (no inline): All 6 ability scores in compact format:
  ```
  FOR 16(+3)  DEX 12(+1)  CON 14(+2)
  INT 10(+0)  SAG 13(+1)  CHA  8(-1)
  ```
- **Footer**: Discord user display name (via `guild.get_member(user_id).display_name`)

**Localization**: Use i18n keys for ability abbreviations and "Niveau"/"PV"/"CA" labels.

### 3. Unicode Separator

A plain text message (not an embed):

```
━━━━━━━━━━ ✦ ━━━━━━━━━━
```

Visually light, clearly marks the boundary between reference cards and narrative flow.

### 4–5. Opening Crawl & Scene Awareness

Unchanged from current implementation. They follow the separator and begin the narrative flow.

## Files to Modify

### `bot/embeds/narrative_embed.py`

Add one new builder function:

- **`build_countdown_embed(step: int, campaign_name: str, language: str = "fr") -> discord.Embed`**
  - `step` is 3, 2, or 1
  - Returns embed with title, description, color, and footer per the table above
  - i18n for descriptions

### `bot/embeds/character_embed.py`

- Extract `CLASS_COLORS` dict to module level if not already (for reuse)
- Add **`build_party_card_embed(character: Character, member_name: str, language: str = "fr") -> discord.Embed`**
  - Builds the condensed character card
  - Uses `CLASS_COLORS`
  - i18n for stat labels

### `bot/campaign_launcher.py`

Modify `_launch_campaign()` method — replace lines 769-780 (current countdown) with:

```python
# --- Countdown (animated embed, then deleted) ---
countdown_embed = build_countdown_embed(3, campaign.name, lang)
countdown_msg = await self.channel.send(embed=countdown_embed)
for step in (2, 1):
    await asyncio.sleep(1.5)
    await countdown_msg.edit(embed=build_countdown_embed(step, campaign.name, lang))
await asyncio.sleep(1.5)
await countdown_msg.delete()

# --- Party cards (permanent) ---
for user_id, character in self.characters.items():
    member = self.channel.guild.get_member(user_id)
    member_name = member.display_name if member else "???"
    card_embed = build_party_card_embed(character, member_name, lang)
    await self.channel.send(embed=card_embed)
    await asyncio.sleep(0.3)

# --- Separator (permanent) ---
await self.channel.send("━━━━━━━━━━ ✦ ━━━━━━━━━━")

# --- Opening crawl (existing, unchanged) ---
...
```

### `bot/i18n.py`

Add i18n keys:
- `countdown_step_3`, `countdown_step_2`, `countdown_step_1` (descriptions)
- `party_card_level`, `party_card_hp`, `party_card_ac`
- Ability abbreviations: `ability_str`, `ability_dex`, `ability_con`, `ability_int`, `ability_wis`, `ability_cha`

### Tests

- **`tests/test_embeds.py`**: Add tests for `build_countdown_embed()` and `build_party_card_embed()`
- **`tests/test_campaign_launcher_recreation.py`**: Update launch sequence assertions to expect new message pattern (countdown embed → party cards → separator → crawl → scene)

## Verification

1. `uv run pytest tests/test_embeds.py` — new embed builders produce correct structure
2. `uv run pytest tests/test_campaign_launcher_recreation.py` — launch sequence sends messages in correct order
3. `uv run ruff check . && uv run mypy .` — no lint/type errors
4. Live Discord test: `/start_campaign` → onboard characters → force-launch → verify visual result in channel
