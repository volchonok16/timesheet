from datetime import date

from app.db import TimeEntry
from app.time_service import dedupe_time_entries, sum_deduped_hours


def _entry(
    *,
    entry_id: int,
    tracking_id: int,
    entry_date: date,
    hours: float,
    sync_key: str | None,
    role: str = "—",
) -> TimeEntry:
    return TimeEntry(
        id=entry_id,
        account_key="acc",
        parent_work_item_id=100,
        tracking_work_item_id=tracking_id,
        role=role,
        activity="Work",
        entry_date=entry_date,
        hours=hours,
        tfs_sync_key=sync_key,
    )


def test_dedupe_prefers_tsapi_over_grid_same_day() -> None:
    day = date(2026, 6, 3)
    entries = [
        _entry(entry_id=1, tracking_id=42, entry_date=day, hours=8.0, sync_key="grid:42:2026-06-03"),
        _entry(
            entry_id=2,
            tracking_id=42,
            entry_date=day,
            hours=8.0,
            sync_key="tsapi:99",
            role="analyst",
        ),
    ]
    deduped = dedupe_time_entries(entries)
    assert len(deduped) == 1
    assert deduped[0].tfs_sync_key == "tsapi:99"
    assert sum_deduped_hours(entries, on_date=day) == 8.0


def test_dedupe_keeps_multiple_tsapi_deltas_same_day() -> None:
    day = date(2026, 6, 3)
    entries = [
        _entry(entry_id=1, tracking_id=42, entry_date=day, hours=4.0, sync_key="tsapi:1"),
        _entry(entry_id=2, tracking_id=42, entry_date=day, hours=4.0, sync_key="tsapi:2"),
    ]
    assert sum_deduped_hours(entries, on_date=day) == 8.0


def test_dedupe_merges_role_split_into_one_tracking_row_key() -> None:
    day = date(2026, 6, 1)
    entries = [
        _entry(entry_id=1, tracking_id=7, entry_date=day, hours=8.0, sync_key="tsapi:10", role="—"),
        _entry(
            entry_id=2,
            tracking_id=7,
            entry_date=day,
            hours=8.0,
            sync_key="grid:7:2026-06-01",
            role="analyst",
        ),
    ]
    assert sum_deduped_hours(entries, on_date=day) == 8.0
