from datetime import date

from app.tfs_auth import TfsAuth
from app.tfs_tsapi import (
    delta_user_matches_auth,
    parse_list_delta_payload,
    resolve_tsapi_base_url,
)

# Обезличенные фикстуры (не реальные сотрудники).
SAMPLE_USER_T2 = "T2RU\\sample.user"
SAMPLE_USER_TELE2 = "TELE2\\sample.user"
SAMPLE_EMAIL = "sample.user@t2.ru"
OTHER_USER_T2 = "T2RU\\other.user"
OTHER_EMAIL = "other.user@t2.ru"

SAMPLE = {
    "ListDelta": [
        {
            "ID": 2509298,
            "PeriodDate": "2026-06-01T00:00:00",
            "CreationDate": "2026-06-04T16:38:03.473",
            "Duration": 480,
            "AD_UserID": SAMPLE_USER_T2,
        },
        {
            "ID": 2509302,
            "PeriodDate": "2026-06-05T00:00:00",
            "CreationDate": "2026-06-04T16:38:33.6",
            "Duration": 480,
            "AD_UserID": SAMPLE_USER_T2,
        },
    ]
}


def test_resolve_tsapi_base_url() -> None:
    assert (
        resolve_tsapi_base_url("https://tfs.t2.ru/tfs/Main")
        == "https://tfs.t2.ru/tsapi"
    )


def test_parse_list_delta_uses_period_date_not_creation() -> None:
    rows = parse_list_delta_payload(SAMPLE, work_item_id=1248312)
    assert len(rows) == 2
    assert rows[0].period_date == date(2026, 6, 1)
    assert rows[0].hours == 8.0
    assert rows[1].period_date == date(2026, 6, 5)


def test_parse_period_date_dmY() -> None:
    rows = parse_list_delta_payload(
        {
            "ListDelta": [
                {
                    "ID": 1,
                    "PeriodDate": "5-6-2026",
                    "Duration": 480,
                    "AD_UserID": "T2RU\\user",
                }
            ]
        },
        work_item_id=1,
    )
    assert rows[0].period_date == date(2026, 6, 5)


def test_delta_user_matches_pat_with_tfs_unique_name() -> None:
    auth = TfsAuth(
        base_url="https://tfs.t2.ru/tfs/Main",
        project="Tele2",
        username=SAMPLE_EMAIL,
        tfs_unique_name=SAMPLE_USER_T2,
    )
    assert delta_user_matches_auth(SAMPLE_USER_T2, auth)


def test_delta_user_matches_login_domain_alias() -> None:
    """Вход TELE2\\user, в ListDelta — T2RU\\user (тот же человек)."""
    auth = TfsAuth(
        base_url="https://tfs.t2.ru/tfs/Main",
        project="Tele2",
        username=SAMPLE_USER_TELE2,
    )
    assert delta_user_matches_auth(SAMPLE_USER_T2, auth)


def test_delta_user_matches_email_expands_t2ru() -> None:
    auth = TfsAuth(
        base_url="https://tfs.t2.ru/tfs/Main",
        project="Tele2",
        username=SAMPLE_EMAIL,
    )
    tokens = auth.identity_match_tokens()
    assert "t2ru\\sample.user" in tokens
    assert delta_user_matches_auth(SAMPLE_USER_T2, auth)


def test_delta_user_rejects_other_user() -> None:
    auth = TfsAuth(
        base_url="https://tfs.t2.ru/tfs/Main",
        project="Tele2",
        username=OTHER_EMAIL,
        tfs_unique_name=OTHER_USER_T2,
    )
    assert not delta_user_matches_auth(SAMPLE_USER_T2, auth)
    assert delta_user_matches_auth(OTHER_USER_T2, auth)
