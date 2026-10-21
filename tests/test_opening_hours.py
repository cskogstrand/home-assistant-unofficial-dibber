"""Weekly opening hours, local dates, membership routing, and daily refresh."""

import asyncio
from datetime import UTC, date, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.util import dt as dt_util

from custom_components.dibber.api import CommunicationError, SchemaError, parse_children
from custom_components.dibber.calendar import DibberCalendar
from custom_components.dibber.events import opening_events, parse_opening_hours

from .conftest import child_payload, opening_hours
from .test_calendar import END, START, make_coordinator


@pytest.mark.parametrize(
    "localization", [("en", "Opening hours"), ("nb", "Åpningstider")]
)
async def test_weekly_calendar_shares_reads_and_uses_local_times(
    hass, entry, client, freezer, localization
):
    """Weekdays recur without extra requests across siblings or date windows."""
    language, label = localization
    hass.config.language = language
    dt_util.set_default_time_zone(ZoneInfo("Europe/Oslo"))
    coordinator = make_coordinator(hass, entry, client)
    client.async_opening_hours.return_value = opening_hours()
    first, second = await asyncio.gather(
        coordinator.async_events(START, END), coordinator.async_events(START, END)
    )
    assert first == second
    coordinator.data = first
    coordinator.last_update_success = True
    entity = DibberCalendar(coordinator, "child-a", "opening_hours")
    assert entity.name == f"Dibber Example {label}"
    assert entity.unique_id == "account-a:child-a:opening_hours"
    assert entity.supported_features == 0
    events = await entity.async_get_events(hass, START, END)
    assert len(events) == 5
    assert events == first["child-b", "opening_hours"]
    assert [event.start.weekday() for event in events] == list(range(5))
    assert events[0].start.astimezone(UTC) == START + timedelta(hours=6)
    assert events[0].end.astimezone(UTC) == START + timedelta(hours=14, minutes=30)
    assert events[0].summary == label
    assert events[0].uid == "opening:nursery-a:2030-04-15"
    assert events[0] not in first["child-a", "activities"]
    assert (
        await entity.async_get_events(
            hass, events[0].start - timedelta(hours=1), events[0].start
        )
        == []
    )
    assert (
        await entity.async_get_events(
            hass, events[0].end, events[0].end + timedelta(hours=1)
        )
        == []
    )
    longer = await entity.async_get_events(hass, START, START + timedelta(days=90))
    assert len(longer) == len({event.uid for event in longer}) == 65
    freezer.move_to(events[0].start)
    assert entity.event == events[0]
    freezer.move_to(events[0].end)
    assert entity.event == events[1]
    freezer.move_to(events[-1].end)
    assert entity.event is None
    client.async_opening_hours.assert_awaited_once_with("nursery-a")


async def test_opening_hours_follow_each_childs_nursery_memberships(
    hass, entry, client
):
    """Transfers use each nursery's hours only on dates with a membership."""
    coordinator = make_coordinator(hass, entry, client)
    child = child_payload()
    child["departments"][0]["to"] = "2030-04-15"
    new = child_payload(nursery="nursery-b")["departments"][0]
    new["from"] = "2030-04-16"
    child["departments"].append(new)
    client.async_children.return_value = parse_children(
        [child, child_payload("child-b")]
    )
    client.async_opening_hours.side_effect = lambda nursery: opening_hours(
        opens="09:00" if nursery == "nursery-b" else "08:00"
    )
    result = await coordinator.async_events(START, END)
    events = result["child-a", "opening_hours"]
    assert len(events) == 5
    assert events[0].uid == "opening:nursery-a:2030-04-15"
    assert events[0].start.hour == 8
    assert all(event.start.hour == 9 for event in events[1:])
    assert all(event.uid.startswith("opening:nursery-b:") for event in events[1:])
    assert all(event.start.hour == 8 for event in result["child-b", "opening_hours"])
    assert client.async_opening_hours.await_count == 2


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {},
        {
            "dayOfWeek": "PRIVATE_UNKNOWN",
            "openingHours": {"opens": "08:00", "closes": "16:00"},
        },
        {"dayOfWeek": "MONDAY", "openingHours": None},
        *[
            opening_hours(days=("MONDAY",), opens=opens, closes=closes)[0]
            for opens, closes in (
                ("25:00", "16:00"),
                ("08:00", "PRIVATE_TIME"),
                (8, "16:00"),
                ("08:00:00", "16:00"),
                ("16:00", "08:00"),
                ("08:00", "08:00"),
            )
        ],
    ],
)
def test_malformed_hours_are_skipped_without_leaking_contents(bad, caplog):
    """Only the known weekday/time schema is retained, without guessing closures."""
    result = parse_opening_hours([bad, *opening_hours(days=("FRIDAY",))])
    assert result == {4: ("08:00", "16:30")}
    assert "skipped 1" in caplog.text
    assert "PRIVATE_" not in caplog.text


def test_invalid_schedule_and_conflicting_weekdays_fail():
    """An invalid body or conflicting intervals must not look like closed days."""
    with pytest.raises(SchemaError, match="invalid_opening_hours"):
        parse_opening_hours({})
    monday = opening_hours(days=("MONDAY",))
    assert parse_opening_hours(monday + monday) == {0: ("08:00", "16:30")}
    with pytest.raises(SchemaError, match="conflicting_opening_hours"):
        parse_opening_hours(monday + opening_hours(days=("MONDAY",), opens="09:00"))


def test_weekly_hours_expand_through_dst_without_guessing_invalid_times(caplog):
    """Local hours remain local across DST; nonexistent times are skipped."""
    dt_util.set_default_time_zone(ZoneInfo("Europe/Oslo"))
    schedule = parse_opening_hours(
        opening_hours(days=("SUNDAY",), opens="01:30", closes="03:30")
    )
    events = opening_events(
        schedule, "nursery-a", date(2030, 3, 24), date(2030, 4, 7), "Opening hours"
    )
    assert len(events) == 3
    assert [
        item.event.end.astimezone(UTC) - item.event.start.astimezone(UTC)
        for item in events
    ] == [timedelta(hours=2), timedelta(hours=1), timedelta(hours=2)]
    assert not opening_events(
        {6: ("02:15", "03:30")},
        "nursery-a",
        date(2030, 3, 31),
        date(2030, 3, 31),
        "Opening hours",
    )
    assert "skipped 1 invalid local dates" in caplog.text


@patch("custom_components.dibber.coordinator.time")
async def test_daily_refresh_preserves_failed_cache_and_clears_successful_empty(
    clock, hass, entry, client
):
    """Hours refresh once daily; failed responses never become an empty schedule."""
    clock.monotonic.return_value = 0.0
    coordinator = make_coordinator(hass, entry, client)
    client.async_opening_hours.return_value = opening_hours()
    first = (await coordinator.async_events(START, END))["child-a", "opening_hours"]
    client.async_opening_hours.return_value = opening_hours(opens="09:00")
    clock.monotonic.return_value = 86399.0
    assert (await coordinator.async_events(START, END))[
        "child-a", "opening_hours"
    ] == first
    client.async_opening_hours.assert_awaited_once_with("nursery-a")
    clock.monotonic.return_value = 86400.0
    updated = (await coordinator.async_events(START, END))["child-a", "opening_hours"]
    assert [event.uid for event in updated] == [event.uid for event in first]
    assert all(event.start.hour == 9 for event in updated)
    previous = dict(coordinator._opening_hours)
    clock.monotonic.return_value = 172800.0
    client.async_opening_hours.side_effect = CommunicationError("cannot_connect")
    with pytest.raises(CommunicationError, match="cannot_connect"):
        await coordinator.async_events(START, END)
    assert dict(coordinator._opening_hours) == previous
    client.async_opening_hours.side_effect = None
    client.async_opening_hours.return_value = []
    result = await coordinator.async_events(START, END)
    assert result["child-a", "opening_hours"] == []
    assert result["child-b", "opening_hours"] == []
