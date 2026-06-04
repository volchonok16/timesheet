from datetime import date

from app.tfs_auth import TfsAuth, TfsIdentity, attach_tfs_identity
from app.time_sync import (
    _history_increment_text,
    aggregate_slices_for_task,
    filter_updates_for_sync,
    merge_slices_by_day,
    parse_update_time_slices,
    update_revised_by_current_user,
    work_item_assigned_to_current_user,
    work_item_created_by_current_user,
    work_item_owned_by_current_user,
)


def _update_with_author(
    *,
    rev: int,
    revised_date: str,
    author_display: str,
    author_unique: str,
    author_id: str = "user-guid-petrov",
    descriptor: str = "aad.U-petrov",
    fields: dict,
) -> dict:
    return {
        "rev": rev,
        "revisedDate": revised_date,
        "revisedBy": {
            "id": author_id,
            "displayName": author_display,
            "uniqueName": author_unique,
            "descriptor": descriptor,
        },
        "fields": fields,
    }


def _pat_user_token_sets() -> tuple[set[str], set[str]]:
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
            identity_id="user-guid-petrov",
        ),
    )
    return auth.identity_match_tokens(), auth.identity_strong_tokens()


def test_merge_slices_by_day() -> None:
    slices = parse_update_time_slices(
        _update_with_author(
            rev=1,
            revised_date="2026-06-04T10:00:00Z",
            author_display="Петров",
            author_unique="MAIN\\petrov",
            fields={"System.History": {"newValue": "2026-06-01: +8ч\n2026-06-03: +2ч"}},
        ),
        tracking_work_item_id=1,
    )
    daily = merge_slices_by_day(
        slices, period_start=date(2026, 6, 1), period_end=date(2026, 6, 7)
    )
    assert daily[date(2026, 6, 1)] == 8.0
    assert daily[date(2026, 6, 3)] == 2.0


def test_parse_history_without_plus_sign() -> None:
    update = _update_with_author(
        rev=2,
        revised_date="2026-06-04T10:00:00Z",
        author_display="Петров",
        author_unique="MAIN\\petrov",
        fields={"System.History": {"newValue": "2026-06-04: 8ч"}},
    )
    slices = parse_update_time_slices(update, tracking_work_item_id=1)
    assert len(slices) == 1
    assert slices[0].hours == 8.0


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


def test_completed_work_delta_imported_by_revision_date() -> None:
    tokens, strong = _pat_user_token_sets()
    update = _update_with_author(
        rev=1,
        revised_date="2026-06-04T10:30:00Z",
        author_display="Петров Пётр",
        author_unique="MAIN\\petrov",
        fields={
            "Microsoft.VSTS.Scheduling.CompletedWork": {"oldValue": 2, "newValue": 8},
        },
    )
    slices = aggregate_slices_for_task(
        [update],
        tracking_work_item_id=1,
        period_start=date(2026, 6, 1),
        current_user_tokens=tokens,
        current_user_strong_tokens=strong,
    )
    assert len(slices) == 1
    assert slices[0].entry_date == date(2026, 6, 4)
    assert slices[0].hours == 6.0


def test_only_current_user_revisions_imported() -> None:
    tokens, strong = _pat_user_token_sets()
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
        author_id="user-guid-sidorov",
        descriptor="aad.U-sidorov",
        fields={"System.History": {"newValue": "2026-06-03: +100ч — чужое"}},
    )
    slices = aggregate_slices_for_task(
        [mine, other],
        tracking_work_item_id=1,
        period_start=date(2026, 6, 1),
        current_user_tokens=tokens,
        current_user_strong_tokens=strong,
    )
    assert len(slices) == 1
    assert slices[0].hours == 4.0


def test_update_revised_by_match_unique_name_from_pat() -> None:
    tokens, strong = _pat_user_token_sets()
    update = _update_with_author(
        rev=1,
        revised_date="2026-06-04T00:00:00Z",
        author_display="Петров Пётр",
        author_unique="MAIN\\petrov",
        fields={},
    )
    assert update_revised_by_current_user(
        update,
        current_user_tokens=tokens,
        current_user_strong_tokens=strong,
    )
    assert not update_revised_by_current_user(
        update,
        current_user_tokens={"main\\sidorov"},
        current_user_strong_tokens={"main\\sidorov"},
    )


def test_assigned_to_other_user_skipped() -> None:
    tokens, strong = _pat_user_token_sets()
    fields = {
        "System.AssignedTo": {
            "uniqueName": "MAIN\\sidorov",
            "displayName": "Сидоров",
        },
    }
    assert not work_item_assigned_to_current_user(
        fields,
        current_user_tokens=tokens,
        current_user_strong_tokens=strong,
    )


def test_created_by_current_user() -> None:
    tokens, strong = _pat_user_token_sets()
    fields = {
        "System.CreatedBy": {
            "uniqueName": "MAIN\\petrov",
            "displayName": "Петров",
        },
    }
    assert work_item_created_by_current_user(
        fields,
        current_user_tokens=tokens,
        current_user_strong_tokens=strong,
    )


def test_owned_by_rejects_foreign_assignee() -> None:
    tokens, strong = _pat_user_token_sets()
    fields = {
        "System.AssignedTo": {
            "uniqueName": "MAIN\\sidorov",
            "displayName": "Сидоров",
        },
        "System.CreatedBy": {
            "uniqueName": "MAIN\\petrov",
            "displayName": "Петров",
        },
    }
    assert not work_item_owned_by_current_user(
        fields,
        current_user_tokens=tokens,
        current_user_strong_tokens=strong,
    )


def test_unassigned_task_allowed_for_sync() -> None:
    tokens, strong = _pat_user_token_sets()
    assert work_item_assigned_to_current_user(
        {},
        current_user_tokens=tokens,
        current_user_strong_tokens=strong,
    )


def test_history_increment_no_fallback_to_full_thread() -> None:
    fields = {
        "System.History": {
            "oldValue": "старый блок",
            "newValue": "совсем другой текст без diff",
        },
    }
    assert _history_increment_text(fields) == ""


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


def test_history_increment_strips_html() -> None:
    update = _update_with_author(
        rev=4,
        revised_date="2026-06-04T10:00:00Z",
        author_display="Петров Пётр",
        author_unique="MAIN\\petrov",
        fields={
            "System.History": {
                "newValue": "<div>2026-06-04: +8ч &mdash; работа</div>",
            },
        },
    )
    slices = parse_update_time_slices(update, tracking_work_item_id=1001)
    assert len(slices) == 1
    assert slices[0].hours == 8.0


def test_filter_updates_skips_old_revisions() -> None:
    tokens, strong = _pat_user_token_sets()
    updates = [
        {"revisedDate": "2020-01-01T00:00:00Z", "revisedBy": {"displayName": "A"}, "fields": {}},
        _update_with_author(
            rev=2,
            revised_date="2026-06-04T00:00:00Z",
            author_display="Петров",
            author_unique="MAIN\\petrov",
            fields={"System.History": {"newValue": "2026-06-04: +1ч"}},
        ),
    ]
    filtered = filter_updates_for_sync(
        updates,
        period_start=date(2026, 6, 1),
        current_user_tokens=tokens,
        current_user_strong_tokens=strong,
    )
    assert len(filtered) == 1
