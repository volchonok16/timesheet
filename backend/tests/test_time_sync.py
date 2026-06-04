from datetime import date

from app.time_sync import (
    aggregate_slices_for_task,
    filter_updates_for_sync,
    parse_update_time_slices,
    update_revised_by_current_user,
)


def _update_with_author(
    *,
    rev: int,
    revised_date: str,
    author_display: str,
    author_unique: str,
    fields: dict,
) -> dict:
    return {
        "rev": rev,
        "revisedDate": revised_date,
        "revisedBy": {
            "displayName": author_display,
            "uniqueName": author_unique,
        },
        "fields": fields,
    }


def test_parse_history_lines() -> None:
    update = _update_with_author(
        rev=3,
        revised_date="2026-06-04T10:00:00Z",
        author_display="Иванов Иван",
        author_unique="CORP\\ivanov",
        fields={
            "System.History": {
                "newValue": "2026-06-03: +2ч — встреча\n2026-06-04: -0.5ч — правка",
            },
        },
    )
    slices = parse_update_time_slices(update, tracking_work_item_id=1001)
    assert len(slices) == 2
    assert slices[0].hours == 2.0


def test_only_current_user_revisions_imported() -> None:
    mine = _update_with_author(
        rev=1,
        revised_date="2026-06-03T10:00:00Z",
        author_display="Петров Пётр",
        author_unique="CORP\\petrov",
        fields={"System.History": {"newValue": "2026-06-03: +4ч — моё"}},
    )
    other = _update_with_author(
        rev=2,
        revised_date="2026-06-03T11:00:00Z",
        author_display="Сидоров Сидор",
        author_unique="CORP\\sidorov",
        fields={"System.History": {"newValue": "2026-06-03: +100ч — чужое"}},
    )
    slices = aggregate_slices_for_task(
        [mine, other],
        tracking_work_item_id=1,
        period_start=date(2026, 6, 1),
        display_name="Петров Пётр",
        login="petrov",
    )
    assert len(slices) == 1
    assert slices[0].hours == 4.0


def test_update_revised_by_match_login() -> None:
    update = _update_with_author(
        rev=1,
        revised_date="2026-06-04T00:00:00Z",
        author_display="Петров Пётр",
        author_unique="MAIN\\petrov",
        fields={},
    )
    assert update_revised_by_current_user(
        update, display_name="Петров Пётр", login="petrov"
    )
    assert not update_revised_by_current_user(
        update, display_name="Сидоров", login="sidorov"
    )


def test_filter_updates_skips_old_revisions() -> None:
    updates = [
        {"revisedDate": "2020-01-01T00:00:00Z", "revisedBy": {"displayName": "A"}, "fields": {}},
        _update_with_author(
            rev=2,
            revised_date="2026-06-04T00:00:00Z",
            author_display="Петров",
            author_unique="CORP\\petrov",
            fields={
                "Microsoft.VSTS.Scheduling.CompletedWork": {"oldValue": 0, "newValue": 1},
            },
        ),
    ]
    filtered = filter_updates_for_sync(
        updates,
        period_start=date(2026, 6, 1),
        display_name="Петров",
        login="petrov",
    )
    assert len(filtered) == 1
