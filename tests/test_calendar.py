"""Calendar semantics, privacy, bounded range reads, and cache behavior."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.util import dt as dt_util

from custom_components.dibber.api import CommunicationError, parse_children
from custom_components.dibber.calendar import DibberCalendar
from custom_components.dibber.coordinator import DibberCoordinator, date_windows
from custom_components.dibber.events import event_start, overlaps, parse_source

from .conftest import activity, child_payload

START = datetime(2030, 4, 15, tzinfo=UTC)
END = START + timedelta(days=7)


def source_payload(source, *, personal_child="child-a"):
    """Return one occurrence in both ordinary and RSVP feeds plus daily focus."""
    item = activity()
    if source == "closures":
        return []
    if source == "activities":
        return [{"date": "2030-04-14", "activities": [item]}]
    if source == "focus":
        return [
            {
                "date": "2030-04-15",
                "focusForTheDay": "Fictional focus.",
                "learningFriends": ["PRIVATE_UNUSED"],
            }
        ]
    return [
        {
            "date": "2030-04-15",
            "pending": [
                activity(
                    visibility="Personal",
                    hasRsvp=True,
                    rsvpResponse={
                        "location": "Example room",
                        "caregivers": [
                            {
                                "id": "PRIVATE_CAREGIVER",
                                "photo": "PRIVATE_PHOTO",
                                "children": [{"id": personal_child}],
                            }
                        ],
                        "employees": ["PRIVATE_EMPLOYEE"],
                    },
                )
            ],
            "accepted": [],
        }
    ]


def make_coordinator(hass, entry, client):
    """Use the real coordinator with a mocked API boundary."""
    client.async_children = AsyncMock(
        return_value=parse_children([child_payload(), child_payload("child-b")])
    )
    client.async_source = AsyncMock(
        side_effect=lambda source, *_: source_payload(source)
    )
    return DibberCoordinator(hass, entry, client)


async def test_shared_requests_merge_rsvp_location_and_daily_focus(hass, entry, client):
    """Sibling calendars share source reads without sharing personal invitations."""
    coordinator = make_coordinator(hass, entry, client)
    a, b = await asyncio.gather(
        coordinator.async_events(START, END), coordinator.async_events(START, END)
    )
    assert client.async_source.call_count == 4
    assert client.async_children.call_count == 1
    assert a == b
    assert len(a["child-a", "activities"]) == 2
    assert a["child-a", "activities"][0].summary == "Daily focus"
    assert a["child-a", "activities"][1].location == "Example room"
    assert a["child-b", "activities"][1].location is None
    assert a["child-a", "activities"][1].start.date() == date(2030, 4, 15)
    assert a["child-a", "rsvp"] == [a["child-a", "activities"][1]]
    assert a["child-b", "rsvp"] == []
    assert "PRIVATE_" not in repr(coordinator._cache)
    assert all(event.uid for event in a["child-a", "activities"])


def test_personal_events_only_for_explicit_recipients_and_unknown_visibility(caplog):
    """Unfamiliar recipients or RSVP audiences are skipped, never broadened."""
    payload = source_payload("rsvp")
    valid = payload[0]["pending"][0]
    payload[0]["accepted"] = [
        activity(
            activity_id="accepted-a",
            **{key: valid[key] for key in ("visibility", "rsvpResponse")},
        )
    ]
    payload[0]["pending"].extend(
        [
            activity(visibility="Personal"),
            activity(visibility="Department"),
            activity(visibility="Unverified", description="PRIVATE_MALFORMED_CONTENT"),
        ]
    )
    parsed = parse_source(payload, "rsvp", "nursery-a", "", "Daily focus")
    assert len(parsed) == 2
    assert all(item.recipients == frozenset({"child-a"}) for item in parsed)
    assert "degraded" in caplog.text
    assert "PRIVATE_" not in caplog.text


@pytest.mark.parametrize(
    "bad",
    [
        {"appointment": {"date": "not-a-date"}},
        {
            "appointment": {
                "date": "2030-04-15",
                "start": "09:00",
                "stop": "09:30",
                "zone": "Not/AZone",
            }
        },
        {
            "appointment": {
                "date": "2030-04-15",
                "start": "23:30",
                "stop": "00:30",
                "zone": "Etc/UTC",
            }
        },
        {"fullDayEvent": None},
    ],
)
def test_invalid_items_do_not_break_valid_items(bad, caplog):
    """No overnight guesses or silent local-time fallbacks."""
    events = parse_source(
        [{"activities": [activity(**bad), activity(activity_id="valid")]}],
        "activities",
        "n",
        "d",
        "Daily focus",
    )
    assert len(events) == 1
    assert events[0].event.uid.endswith("valid:2030-04-15")
    assert "skipped 1" in caplog.text


def test_timezones_all_day_and_exclusive_boundaries():
    """HA dates use the HA zone; timed events preserve their source zone."""
    dt_util.set_default_time_zone(ZoneInfo("America/Los_Angeles"))
    events = parse_source(
        [
            {
                "activities": [
                    activity(fullDayEvent=True),
                    activity(
                        activity_id="oslo",
                        appointment={
                            "date": "2030-04-15",
                            "start": "09:00",
                            "stop": "09:30",
                            "zone": "Europe/Oslo",
                        },
                    ),
                ]
            }
        ],
        "activities",
        "n",
        "d",
        "Daily focus",
    )
    all_day, timed = (item.event for item in events)
    assert all_day.start == date(2030, 4, 15)
    assert all_day.end == date(2030, 4, 16)
    assert event_start(all_day) == datetime(2030, 4, 15, 7, tzinfo=UTC)
    assert event_start(timed) == datetime(2030, 4, 15, 7, tzinfo=UTC)
    assert not overlaps(timed, timed.end, timed.end + timedelta(hours=1))
    assert not overlaps(timed, timed.start - timedelta(hours=1), timed.start)
    assert overlaps(timed, timed.start, timed.end)


@pytest.mark.parametrize(
    ("day", "start", "stop", "valid"),
    [
        ("2030-03-31", "01:30", "03:30", True),
        ("2030-03-31", "02:15", "03:30", False),
        ("2030-10-27", "02:15", "03:30", False),
    ],
)
def test_dst_real_duration_and_ambiguous_times(day, start, stop, valid):
    """A spring-forward interval is one hour; nonexistent/ambiguous times skip."""
    events = parse_source(
        [
            {
                "activities": [
                    activity(
                        appointment={
                            "date": day,
                            "start": start,
                            "stop": stop,
                            "zone": "Europe/Oslo",
                        }
                    )
                ]
            }
        ],
        "activities",
        "n",
        "d",
        "Daily focus",
    )
    assert bool(events) is valid
    if valid:
        event = events[0].event
        assert event.end.astimezone(UTC) - event.start.astimezone(UTC) == timedelta(
            hours=1
        )


async def test_membership_routes_each_date_and_rediscovery(hass, entry, client):
    """Overlapping memberships and a transfer preserve child calendar identity."""
    coordinator = make_coordinator(hass, entry, client)
    original_id = DibberCalendar(coordinator, "child-a").unique_id
    child = child_payload()
    child["departments"][0]["to"] = "2030-04-15"
    second = child_payload(department="department-new")["departments"][0]
    second["from"] = "2030-04-16"
    child["departments"].append(second)
    client.async_children.return_value = parse_children([child])
    result = await coordinator.async_events(START, END)
    assert len(result["child-a", "activities"]) == 2
    assert result["child-b", "activities"] == []
    assert DibberCalendar(coordinator, "child-a").unique_id == original_id
    assert (
        len([c for c in client.async_source.call_args_list if c.args[0] == "rsvp"]) == 1
    )
    assert (
        len(
            [c for c in client.async_source.call_args_list if c.args[0] == "activities"]
        )
        == 2
    )


async def test_successful_empty_refetch_clears_and_failure_retains_cache(
    hass, entry, client
):
    """Keep stale data internal; a successful empty response clears it."""
    coordinator = make_coordinator(hass, entry, client)
    assert (await coordinator.async_events(START, END))["child-a", "activities"]
    old = dict(coordinator._cache)
    with patch(
        "custom_components.dibber.coordinator.time.monotonic", return_value=10**12
    ):
        client.async_source.side_effect = CommunicationError("cannot_connect")
        with pytest.raises(CommunicationError):
            await coordinator.async_events(START, END)
        assert dict(coordinator._cache) == old
        client.async_source.side_effect = lambda *_: []
        assert await coordinator.async_events(START, END) == {
            ("child-a", "activities"): [],
            ("child-b", "activities"): [],
            ("child-a", "closures"): [],
            ("child-b", "closures"): [],
            ("child-a", "rsvp"): [],
            ("child-b", "rsvp"): [],
        }


async def test_range_chunking_bounds_cache_and_deduplicates(hass, entry, client):
    """Long ranges are complete and have bounded memory, not one request per day."""
    coordinator = make_coordinator(hass, entry, client)
    result = await coordinator.async_events(START, START + timedelta(days=90))
    assert len(result["child-a", "activities"]) == 2
    assert len(result["child-a", "rsvp"]) == 1
    assert result["child-b", "rsvp"] == []
    assert (
        client.async_source.call_count == 16
    )  # Four padded date chunks, four sources.
    assert all(
        (call.args[-1] - call.args[-2]).days <= 30
        for call in client.async_source.call_args_list
    )
    for offset in range(70):
        start = START + timedelta(days=offset * 31)
        await coordinator.async_events(start, start + timedelta(days=7))
    assert len(coordinator._cache) <= 64


def test_adjacent_chunks_share_boundary_without_gaps():
    """Server inclusion is unverified, so neighboring chunks overlap one date."""
    chunks = list(date_windows(date(2030, 1, 1), date(2030, 4, 1)))
    assert chunks[0][0] == date(2030, 1, 1)
    assert chunks[-1][1] == date(2030, 4, 1)
    assert all(left[1] == right[0] for left, right in pairwise(chunks))


async def test_current_next_state_and_no_network_in_properties(
    hass, entry, client, freezer
):
    """State moves on without a network call when the ongoing event ends."""
    coordinator = make_coordinator(hass, entry, client)
    coordinator.data = await coordinator.async_events(START, END)
    coordinator.last_update_success = True
    entity = DibberCalendar(coordinator, "child-a")
    freezer.move_to("2030-04-15 09:10:00+00:00")
    assert entity.event.summary == "Daily focus"
    assert entity.supported_features == 0
    assert entity.available
    freezer.move_to("2030-04-17 00:00:00+00:00")
    assert entity.event is None
    assert client.async_source.call_count == 4


@pytest.mark.parametrize(
    "localization", [("en", "Invitations"), ("nb", "Invitasjoner")]
)
async def test_invitation_entity_preserves_details_and_recipient_scope(
    hass, entry, client, freezer, localization
):
    """Pending and accepted invitations share reads and expose only calendar data."""
    language, label = localization
    hass.config.language = language
    coordinator = make_coordinator(hass, entry, client)
    payload = source_payload("rsvp")
    pending = payload[0]["pending"][0]
    pending["appointment"] = {
        "date": "2030-04-15",
        "start": "18:00",
        "stop": "19:30",
        "zone": "Europe/Oslo",
    }
    accepted = {
        **pending,
        "id": "accepted-a",
        "appointment": {**pending["appointment"], "date": "2030-04-16"},
    }
    payload[0]["accepted"] = [accepted, pending]
    payload[0]["declined"] = [{**pending, "id": "declined-a"}]
    client.async_source.side_effect = lambda source, *_: (
        payload if source == "rsvp" else source_payload(source)
    )
    coordinator.data = await coordinator.async_events(START, END)
    coordinator.last_update_success = True
    entity = DibberCalendar(coordinator, "child-a", "rsvp")
    assert entity.name == f"Dibber Example {label}"
    assert entity.unique_id == "account-a:child-a:rsvp"
    assert entity.supported_features == 0
    events = await entity.async_get_events(hass, START, END)
    assert len(events) == 2
    assert events[0].start == datetime(2030, 4, 15, 18, tzinfo=ZoneInfo("Europe/Oslo"))
    assert events[0].end == datetime(
        2030, 4, 15, 19, 30, tzinfo=ZoneInfo("Europe/Oslo")
    )
    assert events[0].summary == pending["subject"]
    assert events[0].description == pending["description"]
    assert events[0].location == "Example room"
    assert events[1].uid == "activity:nursery-a:accepted-a:2030-04-16"
    assert "PRIVATE_" not in repr(events)
    sibling = DibberCalendar(coordinator, "child-b", "rsvp")
    assert await sibling.async_get_events(hass, START, END) == []
    assert sibling.event is None
    freezer.move_to(events[0].start)
    assert entity.event == events[0]
    freezer.move_to(events[0].end)
    assert entity.event == events[1]
    freezer.move_to(events[1].end)
    assert entity.event is None
    assert client.async_source.call_count == 4
    coordinator.children = {}
    assert not entity.available


async def test_short_month_uses_range_queries_and_boundary_events_survive(
    hass, entry, client
):
    """A padded short month fits one query per source; adjacent-day events survive."""
    coordinator = make_coordinator(hass, entry, client)
    start = datetime(2030, 2, 1, tzinfo=UTC)
    end = datetime(2030, 3, 1, tzinfo=UTC)
    client.async_source.side_effect = lambda source, *_: (
        [
            {
                "activities": [
                    activity(
                        day="2030-01-31",
                        appointment={
                            "date": "2030-01-31",
                            "start": "23:30",
                            "stop": "23:45",
                            "zone": "America/Los_Angeles",
                        },
                    ),
                    activity(day="2030-02-28", activity_id="last"),
                    activity(day="2030-03-01", activity_id="excluded"),
                ]
            }
        ]
        if source == "activities"
        else []
    )
    result = await coordinator.async_events(start, end)
    assert len(result["child-a", "activities"]) == 2
    assert client.async_source.call_count == 4
    assert all(
        call.args[-2:] == (date(2030, 1, 31), date(2030, 3, 2))
        for call in client.async_source.call_args_list
    )
