from datetime import date

from app.tfs_auth import TfsAuth
from app.tfs_tsapi import (
    delta_user_matches_auth,
    parse_list_delta_payload,
    resolve_tsapi_base_url,
)

SAMPLE = {
    "ListDelta": [
        {
            "ID": 2509298,
            "PeriodDate": "2026-06-01T00:00:00",
            "CreationDate": "2026-06-04T16:38:03.473",
            "Duration": 480,
            "AD_UserID": "T2RU\\alexander.taraskin",
        },
        {
            "ID": 2509302,
            "PeriodDate": "2026-06-05T00:00:00",
            "CreationDate": "2026-06-04T16:38:33.6",
            "Duration": 480,
            "AD_UserID": "T2RU\\alexander.taraskin",
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


def test_delta_user_matches_auth() -> None:
    auth = TfsAuth(
        base_url="https://tfs.t2.ru/tfs/Main",
        project="Tele2",
        username="alexander.taraskin@t2.ru",
        tfs_unique_name="T2RU\\alexander.taraskin",
    )
    assert delta_user_matches_auth("T2RU\\alexander.taraskin", auth)
