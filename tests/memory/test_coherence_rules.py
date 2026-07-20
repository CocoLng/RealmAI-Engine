"""Unit tests for the shared coherence-rule core (hard rules)."""

from memory.coherence_rules import (
    CoherenceSnapshot,
    LockedFactSnapshot,
    check_hp_mismatch,
    check_item_use_without_owning,
    check_location_mismatch,
    check_locked_fact_violation,
    check_npc_status,
    check_phantom_npc,
    check_zone_violation,
)


class TestCheckNpcStatus:
    def test_dead_npc_acting_is_flagged(self) -> None:
        snap = CoherenceSnapshot(dead_npcs=["Aldric"])
        violations = check_npc_status("Aldric sourit et vous tend la main.", snap)
        assert len(violations) == 1
        assert violations[0].rule == "R1.npc_status"
        assert violations[0].severity == "hard"
        assert "Aldric" in violations[0].expected

    def test_mentioning_corpse_without_active_verb_is_fine(self) -> None:
        snap = CoherenceSnapshot(dead_npcs=["Aldric"])
        assert check_npc_status("Le cadavre d'Aldric gît près de l'autel.", snap) == []

    def test_short_form_of_multiword_name_is_caught(self) -> None:
        snap = CoherenceSnapshot(dead_npcs=["Père Aldric"])
        assert len(check_npc_status("Aldric murmure une prière.", snap)) == 1

    def test_self_reported_mention_flags_without_verb(self) -> None:
        snap = CoherenceSnapshot(dead_npcs=["Aldric"], npcs_mentioned=["Aldric"])
        assert len(check_npc_status("Une silhouette familière attend.", snap)) == 1

    def test_no_dead_npcs_means_no_violation(self) -> None:
        snap = CoherenceSnapshot(known_npc_names=["Aldric"])
        assert check_npc_status("Aldric sourit.", snap) == []


class TestCheckPhantomNpc:
    def test_unknown_proper_noun_is_flagged(self) -> None:
        snap = CoherenceSnapshot(
            known_npc_names=["Elara, la Gardienne"], player_names=["Kael"],
            known_locations=["Salle des échos"],
        )
        violations = check_phantom_npc("Soudain, Baldur surgit de l'ombre.", snap)
        assert len(violations) == 1
        assert violations[0].rule == "R1.phantom_npc"

    def test_known_short_form_and_location_words_pass(self) -> None:
        snap = CoherenceSnapshot(
            known_npc_names=["Elara, la Gardienne"], player_names=["Kael"],
            known_locations=["Salle des échos"],
        )
        assert check_phantom_npc("Elara guide Kael vers la Salle.", snap) == []

    def test_whitelist_words_pass(self) -> None:
        snap = CoherenceSnapshot()
        assert check_phantom_npc("Mais Vous hésitez. Alors Tout bascule.", snap) == []


class TestCheckItemUse:
    def test_using_unowned_item_is_flagged(self) -> None:
        snap = CoherenceSnapshot(actor_inventory=["Épée courte"])
        violations = check_item_use_without_owning(
            "Tu brandis la torche enflammée.", snap,
        )
        assert len(violations) == 1
        assert violations[0].rule == "R1.item_use_without_owning"

    def test_using_owned_item_passes(self) -> None:
        snap = CoherenceSnapshot(actor_inventory=["Épée courte"])
        assert check_item_use_without_owning("Tu dégaines l'épée courte.", snap) == []


class TestCheckHpMismatch:
    def test_wounded_prose_with_full_hp_is_flagged(self) -> None:
        snap = CoherenceSnapshot(player_hp_ratio=1.0)
        violations = check_hp_mismatch("Tu chancelles, grièvement blessé.", snap)
        assert len(violations) == 1
        assert violations[0].rule == "R1.hp_mismatch"

    def test_wounded_prose_with_low_hp_passes(self) -> None:
        snap = CoherenceSnapshot(player_hp_ratio=0.3)
        assert check_hp_mismatch("Tu chancelles, grièvement blessé.", snap) == []


class TestCheckLocationMismatch:
    def test_other_known_location_without_move_is_flagged(self) -> None:
        snap = CoherenceSnapshot(
            current_location="Crypte", known_locations=["Crypte", "Taverne du Sanglier"],
            moved_this_turn=False,
        )
        violations = check_location_mismatch(
            "La Taverne du Sanglier bruisse autour de vous.", snap,
        )
        assert len(violations) == 1

    def test_move_turn_passes(self) -> None:
        snap = CoherenceSnapshot(
            current_location="Crypte", known_locations=["Crypte", "Taverne du Sanglier"],
            moved_this_turn=True,
        )
        assert check_location_mismatch("Vous rejoignez la Taverne du Sanglier.", snap) == []


class TestCheckZoneViolation:
    def test_unknown_zone_in_combat_is_flagged(self) -> None:
        snap = CoherenceSnapshot(combat_active=True, combat_zones=["autel", "nef"])
        violations = check_zone_violation("Tu recules vers la zone balcon.", snap)
        assert len(violations) == 1
        assert violations[0].rule == "R1.zone_violation"

    def test_out_of_combat_passes(self) -> None:
        snap = CoherenceSnapshot(combat_active=False)
        assert check_zone_violation("Tu recules vers la zone balcon.", snap) == []


class TestCheckLockedFactViolation:
    def test_negating_a_locked_fact_is_flagged(self) -> None:
        snap = CoherenceSnapshot(locked_facts=[
            LockedFactSnapshot(id="beat:3:hint", text="Le pont de pierre est effondré."),
        ])
        violations = check_locked_fact_violation(
            "Le pont de pierre n'est plus effondré, la voie est libre.", snap,
        )
        assert len(violations) == 1
        assert violations[0].rule == "R1.locked_fact_violation"

    def test_fact_subject_absent_passes(self) -> None:
        snap = CoherenceSnapshot(locked_facts=[
            LockedFactSnapshot(id="beat:3:hint", text="Le pont de pierre est effondré."),
        ])
        assert check_locked_fact_violation("La forêt s'étend devant vous.", snap) == []
