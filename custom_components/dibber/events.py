"""Parse only calendar fields and the recipient IDs required for routing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from homeassistant.components.calendar import CalendarEvent
from homeassistant.util import dt as dt_util

from .api import SchemaError, required_string
from .const import LOGGER


@dataclass(frozen=True)
class Occurrence:
    """An event and its minimum routing metadata."""

    event: CalendarEvent
    day: date
    recipients: frozenset[str] | None = None


def identity(*parts: str) -> str:
    """Keep opaque identifiers unambiguous, even when they contain delimiters."""
    return ":".join(quote(part, safe="") for part in parts)


def _text(value: Any) -> str | None:
    """Keep text as text without interpreting HTML or fetching URLs."""
    if value is None or isinstance(value, str):
        return value
    raise SchemaError("invalid_text")


def _local_time(day: date, value: Any, zone: ZoneInfo) -> datetime:
    """Reject nonexistent or ambiguous clock times rather than inventing a fold."""
    if not isinstance(value, str) or len(value) != len("HH:MM"):
        raise SchemaError("invalid_time")
    clock = time.fromisoformat(value)
    naive = datetime.combine(day, clock)
    result = naive.replace(tzinfo=zone)
    if (
        result.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != naive
        or result.utcoffset() != result.replace(fold=1).utcoffset()
    ):
        raise SchemaError("ambiguous_time")
    return result


def _activity(item: Any, nursery: str, *, rsvp: bool) -> Occurrence:
    """Use appointment date, and restrict personal events to named children."""
    activity_id = required_string(item, "id")
    summary = required_string(item, "subject")
    appointment = item.get("appointment")
    day = date.fromisoformat(required_string(appointment, "date"))
    recipients = None
    response = item.get("rsvpResponse")
    visibility = item.get("visibility")
    if visibility == "Personal":
        if not isinstance(response, dict) or not isinstance(
            response.get("caregivers"), list
        ):
            raise SchemaError("invalid_recipients")
        ids = set()
        for caregiver in response["caregivers"]:
            if not isinstance(caregiver, dict) or not isinstance(
                caregiver.get("children"), list
            ):
                raise SchemaError("invalid_recipients")
            ids.update(required_string(child, "id") for child in caregiver["children"])
        if not ids:
            raise SchemaError("invalid_recipients")
        recipients = frozenset(ids)
    elif rsvp or visibility != "Department":
        raise SchemaError("unsupported_visibility")
    if item.get("fullDayEvent") is True:
        # shortcut: assume one day; revisit when real all-day payloads are verified.
        start, end = day, day + timedelta(days=1)
    elif item.get("fullDayEvent") is False:
        zone = ZoneInfo(required_string(appointment, "zone"))
        start = _local_time(day, appointment.get("start"), zone)
        end = _local_time(day, appointment.get("stop"), zone)
        if end.astimezone(UTC) <= start.astimezone(UTC):
            raise SchemaError("unsupported_duration")
    else:
        raise SchemaError("invalid_full_day")
    return Occurrence(
        CalendarEvent(
            start=start,
            end=end,
            summary=summary,
            description=_text(item.get("description")),
            location=_text(response.get("location"))
            if isinstance(response, dict)
            else None,
            uid=identity("activity", nursery, activity_id, day.isoformat()),
        ),
        day,
        recipients,
    )


def _closure(item: Any, nursery: str) -> Occurrence:
    """Convert closure dates to an all-day event, ignoring holiday day counts."""
    closure_id = required_string(item, "id")
    day = date.fromisoformat(required_string(item, "from"))
    last = (
        day if item["to"] is None else date.fromisoformat(required_string(item, "to"))
    )
    if last < day:
        raise SchemaError("invalid_closure")
    # shortcut: assume inclusive to; verify against the app's date range.
    return Occurrence(
        CalendarEvent(
            start=day,
            end=last + timedelta(days=1),
            summary=required_string(item, "title"),
            description=required_string(item, "type"),
            uid=identity("closure", nursery, closure_id),
        ),
        day,
    )


def parse_source(
    data: Any, source: str, nursery: str, department: str, focus_title: str
) -> list[Occurrence]:
    """Skip malformed items with aggregate, content-free warnings."""
    if not isinstance(data, list):
        raise SchemaError("invalid_calendar")
    result = []
    skipped = 0
    for group in data:
        try:
            if not isinstance(group, dict):
                raise SchemaError("invalid_calendar")
            if source == "closures":
                result.append(_closure(group, nursery))
                continue
            if source == "focus":
                text = _text(group.get("focusForTheDay"))
                if text and text.strip():
                    day = date.fromisoformat(required_string(group, "date"))
                    result.append(
                        Occurrence(
                            CalendarEvent(
                                start=day,
                                end=day + timedelta(days=1),
                                summary=focus_title,
                                description=text,
                                uid=identity(
                                    "focus", nursery, department, day.isoformat()
                                ),
                            ),
                            day,
                        )
                    )
                continue
            buckets = ("pending", "accepted") if source == "rsvp" else ("activities",)
            for bucket in buckets:
                items = group.get(bucket)
                if not isinstance(items, list):
                    raise SchemaError("invalid_calendar")
                for item in items:
                    try:
                        result.append(_activity(item, nursery, rsvp=source == "rsvp"))
                    except SchemaError, ValueError, TypeError, ZoneInfoNotFoundError:
                        skipped += 1
        except SchemaError, ValueError, TypeError, KeyError, OverflowError:
            skipped += 1
    if skipped:
        LOGGER.warning(
            "Dibber calendar results degraded: skipped %d malformed/unsupported items",
            skipped,
        )
    return result


def event_start(event: CalendarEvent) -> datetime:
    """Sort all-day events at midnight in HA's configured timezone."""
    return dt_util.as_utc(event.start_datetime_local)


def overlaps(event: CalendarEvent, start: datetime, end: datetime) -> bool:
    """Apply HA's exclusive interval boundaries in absolute time."""
    return event_start(event) < dt_util.as_utc(end) and dt_util.as_utc(
        event.end_datetime_local
    ) > dt_util.as_utc(start)
