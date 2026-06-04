import json
from datetime import date

from app.tracking_stream_sync import parse_track_stream_payload

SAMPLE = """
[[{"id":1248307,"title":"Req","childs":[
{"id":1248312,"title":"Аналитик - Прочие","delta":[8,8,0,0,8,0,0]}
]}],
[]]
"""


def test_parse_track_stream_delta() -> None:
    rows = parse_track_stream_payload(
        json.loads(SAMPLE),
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 7),
    )
    assert len(rows) == 1
    assert rows[0].daily_hours[date(2026, 6, 1)] == 8.0
    assert rows[0].daily_hours[date(2026, 6, 5)] == 8.0
