from app.time_sync import parse_update_time_slices


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
