"""ODD's alert, as the one line of text a chat webhook accepts.

The shape is ODD 0.29's `AlertNotificationMessage`, serialised snake_case by
its JSONSerDeUtils -- the body its generic webhook POSTs.
"""
from __future__ import annotations

from api.odd_alerts import compose

ODD = "http://odd.local:8080"

MESSAGE = {
    "alert_type": "FAILED_DQ_TEST",
    "event_type": "CREATED",
    "data_entity": {"id": 239, "name": "sbr_firma", "data_source_name": "siber_erp_mssql",
                    "namespace_name": "master_data", "type": "TABLE", "owners": []},
    "alert_chunks": [{"description": "email format failed on 146 rows"}],
    "downstream": [{"id": 5, "name": "vw_firma"}, {"id": 6, "name": "mart.revenue"}],
}


def test_the_line_says_what_happened_to_what_and_where():
    text = compose(MESSAGE, ODD)
    first = text.splitlines()[0]
    assert "opened" in first and "failed dq test" in first
    assert "*sbr_firma*" in first and "(master_data / siber_erp_mssql)" in first


def test_what_odd_said_and_what_is_downstream_are_kept():
    text = compose(MESSAGE, ODD)
    assert "- email format failed on 146 rows" in text
    assert "2 downstream: vw_firma, mart.revenue" in text


def test_it_links_back_to_the_entity_in_odd():
    assert f"<{ODD}/dataentities/239|Open in ODD>" in compose(MESSAGE, ODD)


def test_a_sparse_message_still_reads():
    text = compose({"event_type": "RESOLVED"}, ODD)
    assert text.startswith("*ODD alert resolved:* alert on *entity ?*")
    assert "Open in ODD" not in text
