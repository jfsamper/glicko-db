import json
import logging

import routes.admin as admin_routes
from services.operational_logging import (
    StructuredJsonFormatter,
    log_operational_event,
)


def test_structured_json_formatter_serializes_event_context():
    record = logging.LogRecord(
        "services.rating_service",
        logging.INFO,
        __file__,
        1,
        "Incremental replay completed.",
        (),
        None,
    )
    record.event_name = "ratings.replay.completed"
    record.event_fields = {"earliest_dirty_date": "2026-10-01"}

    payload = json.loads(StructuredJsonFormatter().format(record))

    assert payload["event"] == "ratings.replay.completed"
    assert payload["context"] == {"earliest_dirty_date": "2026-10-01"}
    assert payload["message"] == "Incremental replay completed."
    assert payload["level"] == "INFO"


def test_werkzeug_console_messages_are_not_wrapped_as_json():
    message = (
        "\u001b[33mWARNING: development server\u001b[0m\n"
        " * Running on http://127.0.0.1:5000"
    )
    record = logging.LogRecord(
        "werkzeug",
        logging.INFO,
        __file__,
        1,
        message,
        (),
        None,
    )

    formatted = StructuredJsonFormatter().format(record)

    assert formatted == message
    assert '"message"' not in formatted


def test_log_operational_event_attaches_structured_record_fields(caplog):
    logger = logging.getLogger("test.operational_logging")

    with caplog.at_level(logging.INFO, logger=logger.name):
        log_operational_event(
            logger,
            logging.INFO,
            "ratings.replay.started",
            "Incremental rating replay started.",
            earliest_dirty_date="2026-10-01",
        )

    record = next(
        record for record in caplog.records
        if record.name == logger.name
    )
    assert record.event_name == "ratings.replay.started"
    assert record.event_fields == {"earliest_dirty_date": "2026-10-01"}


def test_deferred_import_replay_emits_a_structured_event(monkeypatch, caplog):
    refreshed = []
    monkeypatch.setattr(
        admin_routes, "defer_import_replay_enabled", lambda: True
    )
    monkeypatch.setattr(
        admin_routes, "refresh_stats", lambda: refreshed.append(True)
    )

    with caplog.at_level(logging.INFO, logger="routes.admin"):
        replayed = admin_routes.run_post_import_replay()

    record = next(
        record for record in caplog.records
        if getattr(record, "event_name", None) == "ratings.replay.deferred"
    )
    assert replayed is False
    assert refreshed == [True]
    assert record.event_fields == {
        "environment_variable": "DEFER_RATING_REPLAY_ON_IMPORT",
    }
