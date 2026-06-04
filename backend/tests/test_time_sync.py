from datetime import date

from app.tfs_auth import TfsAuth, TfsIdentity, attach_tfs_identity
from app.time_sync import (
    _history_increment_text,
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


def _pat_user_tokens() -> set[str]:
    auth = attach_tfs_identity(
        TfsAuth(
            base_url="https://tfs.example/tfs/Main",
            project="Tele2",
            pat="secret-token",
            username=None,
            password=None,
        ),
        TfsIdentity(
            display_name="Петров Пётр",
            unique_name="MAIN\\petrov",
            descriptor="aad.U-petrov",
        ),
    )
    return auth.identity_match_tokens()


def test_parse_history_lines() -> None:
    update = _update_with_author(
        rev=3,
        revised_date="2026-06-04T10:00:00Z",
        author_display="Петров Пётр",
        author_unique="MAIN\\petrov",
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
    tokens = _pat_user_tokens()
    mine = _update_with_author(
        rev=1,
        revised_date="2026-06-03T10:00:00Z",
        author_display="Петров Пётр",
        author_unique="MAIN\\petrov",
        fields={"System.History": {"newValue": "2026-06-03: +4ч — моё"}},
    )
    other = _update_with_author(
        rev=2,
        revised_date="2026-06-03T11:00:00Z",
        author_display="Сидоров Сидор",
        author_unique="MAIN\\sidorov",
        fields={"System.History": {"newValue": "2026-06-03: +100ч — чужое"}},
    )
    slices = aggregate_slices_for_task(
        [mine, other],
        tracking_work_item_id=1,
        period_start=date(2026, 6, 1),
        current_user_tokens=tokens,
    )
    assert len(slices) == 1
    assert slices[0].hours == 4.0


def test_update_revised_by_match_unique_name_from_pat() -> None:
    tokens = _pat_user_tokens()
    update = _update_with_author(
        rev=1,
        revised_date="2026-06-04T00:00:00Z",
        author_display="Петров Пётр",
        author_unique="MAIN\\petrov",
        fields={},
    )
    assert update_revised_by_current_user(update, current_user_tokens=tokens)
    assert not update_revised_by_current_user(
        update, current_user_tokens={"main\\sidorov"}
    )


def test_history_increment_skips_previous_lines() -> None:
    fields = {
        "System.History": {
            "oldValue": "2026-06-01: +1ч — старое\n2026-06-02: +2ч — старое",
            "newValue": (
                "2026-06-01: +1ч — старое\n"
                "2026-06-02: +2ч — старое\n"
                "2026-06-03: +3ч — новое"
            ),
        },
    }
    assert _history_increment_text(fields) == "2026-06-03: +3ч — новое"


def test_tokens_do_not_match_by_substring() -> None:
    tokens = _pat_user_tokens()
    update = _update_with_author(
        rev=1,
        revised_date="2026-06-04T00:00:00Z",
        author_display="Петровский Иван",
        author_unique="MAIN\\petrovski",
        fields={},
    )
    assert not update_revised_by_current_user(update, current_user_tokens=tokens)


def test_filter_updates_skips_old_revisions() -> None:
    tokens = _pat_user_tokens()
    updates = [
        {"revisedDate": "2020-01-01T00:00:00Z", "revisedBy": {"displayName": "A"}, "fields": {}},
        _update_with_author(
            rev=2,
            revised_date="2026-06-04T00:00:00Z",
            author_display="Петров",
            author_unique="MAIN\\petrov",
            fields={
                "Microsoft.VSTS.Scheduling.CompletedWork": {"oldValue": 0, "newValue": 1},
            },
        ),
    ]
    filtered = filter_updates_for_sync(
        updates,
        period_start=date(2026, 6, 1),
        current_user_tokens=tokens,
    )
    assert len(filtered) == 1
