"""Structured logging helpers for application operations."""
from datetime import datetime, timezone
import json
import logging


class StructuredJsonFormatter(logging.Formatter):
    """Format log records as one JSON object per line."""

    def format(self, record):
        event_name = getattr(record, "event_name", None)
        if record.name == "werkzeug" and not event_name:
            message = record.getMessage()
            if record.exc_info:
                message = f"{message}\n{self.formatException(record.exc_info)}"
            return message

        payload = {
            "timestamp": datetime.fromtimestamp(
                record.created, timezone.utc
            ).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if event_name:
            payload["event"] = event_name
        event_fields = getattr(record, "event_fields", None)
        if event_fields:
            payload["context"] = event_fields
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_application_logging():
    """Configure newline-delimited JSON output when the host has no handler."""
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return

    handler = logging.StreamHandler()
    handler.setFormatter(StructuredJsonFormatter())
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)


def log_operational_event(
        logger, level, event_name, message, exc_info=False, **fields):
    logger.log(
        level,
        message,
        extra={
            "event_name": event_name,
            "event_fields": fields,
        },
        exc_info=exc_info,
    )
