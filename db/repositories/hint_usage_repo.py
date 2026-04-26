"""Persistence operations for /hint usage tracking."""

from sqlalchemy.orm import Session

from db.models import HintUsageRow


class HintUsageRepository:
    """CRUD for per-campaign per-beat /hint usage tracking."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_or_create(self, *, campaign_id: str, beat_number: int) -> HintUsageRow:
        """Fetch the row for (campaign, beat); create with defaults if missing."""
        row = self._session.get(HintUsageRow, (campaign_id, beat_number))
        if row is None:
            row = HintUsageRow(
                campaign_id=campaign_id, beat_number=beat_number,
            )
            self._session.add(row)
            self._session.flush()
        return row

    def increment_level1(self, *, campaign_id: str, beat_number: int) -> None:
        """Increment the level-1 use counter for a given campaign+beat."""
        row = self.get_or_create(campaign_id=campaign_id, beat_number=beat_number)
        row.level1_uses += 1
        self._session.commit()

    def set_level2_used(self, *, campaign_id: str, beat_number: int) -> None:
        """Mark level-2 as used for a given campaign+beat."""
        row = self.get_or_create(campaign_id=campaign_id, beat_number=beat_number)
        row.level2_used = True
        self._session.commit()

    def set_level3_last_used_turn(
        self, *, campaign_id: str, beat_number: int, turn: int,
    ) -> None:
        """Record the turn number at which level-3 was last used."""
        row = self.get_or_create(campaign_id=campaign_id, beat_number=beat_number)
        row.level3_last_used_turn = turn
        self._session.commit()

    def clear_for_beat(self, *, campaign_id: str, beat_number: int) -> None:
        """Delete the usage row — called when the beat advances."""
        row = self._session.get(HintUsageRow, (campaign_id, beat_number))
        if row is not None:
            self._session.delete(row)
            self._session.commit()
