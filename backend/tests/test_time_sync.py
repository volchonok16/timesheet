from datetime import date

from app.time_sync import (
    ParsedTimeSlice,
    aggregate_slices_for_task,
    filter_updates_for_sync,
    parse_update_time_slices,
)


def test_parse_history_lines() -> None:
    update = {
        "rev": 3,
        "revisedDate": "2026-06-04T10:00:00Z",
        "fields": {
            "System.History": {
                "newValue": "2026-06-03: +2ч — встреча\n2026-06-04: -0.5ч — правка",
            },
            "Microsoft.VSTS.Scheduling.CompletedWork": {"oldValue": 1.5, "newValue": 3.5},
        },
    }
    slices = parse_update_time_slices(update, tracking_work_item_id=1001)
    assert len(slices) == 2
    assert slices[0].entry_date.isoformat() == "2026-06-03"
    assert slices[0].hours == 2.0
    assert slices[1].hours == -0.5


def test_parse_completed_work_delta_without_history() -> None:
    update = {
        "rev": 5,
        "revisedDate": "2026-06-04T12:00:00Z",
        "fields": {
            "Microsoft.VSTS.Scheduling.CompletedWork": {"oldValue": 2, "newValue": 5},
        },
    }
    slices = parse_update_time_slices(update, tracking_work_item_id=42)
    assert len(slices) == 1
    assert slices[0].hours == 3.0
    assert slices[0].entry_date.isoformat() == "2026-06-04"


def test_history_inside_period_even_if_revised_later() -> None:
    update = {
        "rev": 9,
        "revisedDate": "2026-06-10T08:00:00Z",
        "fields": {
            "System.History": {"newValue": "2026-06-02: +8ч — работа"},
        },
    }
    slices = aggregate_slices_for_task(
        [update],
        tracking_work_item_id=1248312,
        period_start=date(2026, 6, 1),
    )
    assert len(slices) == 1
    assert slices[0].entry_date.isoformat() == "2026-06-02"
    assert slices[0].hours == 8.0


def test_filter_updates_skips_old_revisions() -> None:
    updates = [
        {"revisedDate": "2020-01-01T00:00:00Z", "fields": {"System.History": {"newValue": "x"}}},
        {
            "revisedDate": "2026-06-04T00:00:00Z",
            "fields": {"Microsoft.VSTS.Scheduling.CompletedWork": {"oldValue": 0, "newValue": 1}},
        },
    ]
    filtered = filter_updates_for_sync(updates, period_start=date(2026, 6, 1))
    assert len(filtered) == 1
