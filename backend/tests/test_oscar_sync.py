import json
from datetime import date

from app.oscar_sync import parse_oscar_stream_payload

HAR_SAMPLE = """
[[{"id":1248307,"status":"New","title":"[analyst] Test","childs":[
{"id":1248312,"status":"Closed","title":"Аналитик - Прочие активности","delta":[8,8,0,0,8,0,0]},
{"id":1248310,"status":"Closed","title":"Аналитик - Написание брифа","delta":[0,0,8,0,0,0,0]}
],"delta":[8,8,8,0,8,0,0]}],
[{"date":"Mon, 01.06","value":8}]]
"""


def test_parse_oscar_stream_delta() -> None:
    payload = json.loads(HAR_SAMPLE)
    rows = parse_oscar_stream_payload(
        payload, period_start=date(2026, 6, 1), period_end=date(2026, 6, 7)
    )
    assert len(rows) == 2
    assert rows[0].task_id == 1248312
    assert rows[0].daily_hours[date(2026, 6, 1)] == 8.0
    assert rows[1].daily_hours[date(2026, 6, 3)] == 8.0
