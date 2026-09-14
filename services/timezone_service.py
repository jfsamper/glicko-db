"""Owns configured-timezone resolution and all current-time/date helpers."""
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import session

from config import DEFAULT_TIMEZONE
from services.db import get_db


def get_configured_timezone(user_id=None):
    if user_id is None:
        try:
            user_id = session.get("user_id")
        except RuntimeError:
            user_id = None
    if user_id is None:
        return DEFAULT_TIMEZONE

    conn = get_db()
    try:
        try:
            row = conn.execute(
                "SELECT timezone FROM users WHERE id = ? AND is_active = 1",
                (user_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return DEFAULT_TIMEZONE
    finally:
        conn.close()

    timezone_name = row["timezone"] if row is not None else None
    if not timezone_name:
        return DEFAULT_TIMEZONE
    try:
        return ZoneInfo(timezone_name)
    except (TypeError, ZoneInfoNotFoundError):
        return DEFAULT_TIMEZONE


def validate_timezone(timezone_name):
    if timezone_name in (None, ""):
        return None
    if not isinstance(timezone_name, str):
        raise ValueError("unsupported timezone")
    try:
        ZoneInfo(timezone_name)
    except (TypeError, ZoneInfoNotFoundError) as exc:
        raise ValueError("unsupported timezone") from exc
    return timezone_name


def format_timezone_label(timezone_name):
    """Return an IANA timezone name with its current UTC adjustment."""
    if timezone_name == "UTC":
        return "UTC"
    try:
        zone = ZoneInfo(timezone_name)
        offset = datetime.now(zone).utcoffset()
    except (TypeError, ZoneInfoNotFoundError):
        return timezone_name
    if offset is None:
        return timezone_name

    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    adjustment = f"UTC{sign}{hours}"
    if minutes:
        adjustment += f":{minutes:02d}"
    return f"{timezone_name} ({adjustment})"


def get_timezone_choices():
    """Return common timezone choices once per current UTC offset, ascending."""
    from config import TIMEZONE_CHOICES

    choices_by_offset = {}
    for timezone_name in TIMEZONE_CHOICES:
        try:
            offset = datetime.now(ZoneInfo(timezone_name)).utcoffset()
        except (TypeError, ZoneInfoNotFoundError):
            continue
        if offset is not None:
            choices_by_offset.setdefault(int(offset.total_seconds()), timezone_name)
    return tuple(
        timezone_name
        for _, timezone_name in sorted(choices_by_offset.items())
    )


def current_datetime(user_id=None):
    return datetime.now(get_configured_timezone(user_id))


def current_date(user_id=None):
    return current_datetime(user_id).date()


def server_date():
    """Return the calendar date used for period calculations and data defaults."""
    return datetime.now(DEFAULT_TIMEZONE).date()


def current_timestamp(user_id=None):
    return current_datetime(user_id).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def timestamp_days_ago(days, user_id=None):
    return (current_datetime(user_id) - timedelta(days=days)).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
