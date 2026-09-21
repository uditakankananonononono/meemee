import json
import logging

from meemee.observability import JSONFormatter, metrics_response


def test_json_logging_fields_and_exception():
    formatter = JSONFormatter()
    record = logging.LogRecord("meemee", logging.ERROR, __file__, 1, "failed %s", ("job",), None)
    record.request_id = "req-1"
    payload = json.loads(formatter.format(record))
    assert payload["message"] == "failed job"
    assert payload["request_id"] == "req-1"
    assert payload["level"] == "ERROR"


def test_metrics_are_prometheus_format():
    response = metrics_response()
    assert b"meemee_http_requests_total" in response.body
    assert "text/plain" in response.media_type
