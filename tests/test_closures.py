"""Closure dates, separate calendars, and shared nursery routing."""

from datetime import date, timedelta

import pytest
from homeassistant.util import dt as dt_util

from custom_components.dibber.api import SchemaError, parse_children
from custom_components.dibber.calendar import DibberCalendar
from custom_components.dibber.events import overlaps, parse_source

from .conftest import child_payload, closure
from .test_calendar import END, START, make_coordinator, source_payload


@pytest.mark.parametrize("kind", ["CLOSED", "PLANNING_DAY", "PUBLIC_HOLIDAY"])
@pytest.mark.parametrize(
    ("last", "expected_end"),
    [
        (None, date(2030, 4, 16)),
        ("2030-04-15", date(2030, 4, 16)),
        ("2030-04-17", date(2030, 4, 18)),
    ],
)
def test_closure_dates_are_all_day_and_end_exclusively(kind, last, expected_end):
    """Null is one day; dated ends are inclusive regardless of holidayDays."""
    events = parse_source(
        [closure(type=kind, to=last, holidayDays=99)],
        "closures",
        "nursery-a",
        "",
        "Daily focus",
    )
    event = events[0].event
    assert event.start == date(2030, 4, 15)
    assert event.end == expected_end
    assert event.summary == "Example planning day"
    assert event.description == kind
    assert event.uid == "closure:nursery-a:closure-a"
    assert events[0].recipients is None
    assert not overlaps(event, event.end_datetime_local, END)
    assert overlaps(
        event,
        event.end_datetime_local - timedelta(hours=1),
        event.end_datetime_local,
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"id": ""},
        {"title": None},
        {"type": []},
        {"from": "PRIVATE_BAD_DATE"},
        {"to": 42},
        {"to": ""},
        {"to": "2030-04-14"},
        {"to": "9999-12-31"},
    ],
)
def test_malformed_closures_skip_without_leaking(changes, caplog):
    """A broken item does not hide valid closures or expose remote contents."""
    result = parse_source(
        [closure(**changes), closure(id="valid", holidayDays=None)],
        "closures",
        "nursery-a",
        "",
        "Daily focus",
    )
    assert len(result) == 1
    assert result[0].event.uid.endswith(":valid")
    assert "skipped 1" in caplog.text
    assert "PRIVATE_" not in caplog.text


def test_missing_closure_end_is_not_silently_treated_as_null(caplog):
    """A missing required date field is a malformed record."""
    missing = closure()
    del missing["to"]
    assert not parse_source([missing, None], "closures", "nursery-a", "", "Daily focus")
    assert "skipped 2" in caplog.text
    with pytest.raises(SchemaError, match="invalid_calendar"):
        parse_source({}, "closures", "nursery-a", "", "Daily focus")


async def test_shared_closures_separate_from_activities_and_filtered_locally(
    hass, entry, client
):
    """Sibling departments share closures; out-of-range results never leak in."""
    coordinator = make_coordinator(hass, entry, client)
    client.async_children.return_value = parse_children(
        [child_payload(), child_payload("child-b", department="department-b")]
    )
    payload = [
        closure(id="future", **{"from": "2030-08-01"}),
        closure(id="past", **{"from": "2029-12-01"}),
        closure(id="boundary", **{"from": "2030-04-22"}),
        closure(id="spanning", **{"from": "2030-04-10", "to": "2030-04-16"}),
        closure(),
        closure(),
    ]
    client.async_source.side_effect = lambda source, *_: (
        payload if source == "closures" else source_payload(source)
    )
    result = await coordinator.async_events(START, END)
    events = result["child-a", "closures"]
    assert [event.uid for event in events] == [
        "closure:nursery-a:spanning",
        "closure:nursery-a:closure-a",
    ]
    assert result["child-b", "closures"] == events
    assert len(result["child-a", "activities"]) == 2
    calls = [
        call
        for call in client.async_source.call_args_list
        if call.args[0] == "closures"
    ]
    assert len(calls) == 1
    assert calls[0].args[1:3] == ("nursery-a", "")
    again = await coordinator.async_events(START, END)
    assert again == result
    assert client.async_source.call_count == 6


async def test_closures_follow_nursery_memberships_and_span_transfers(
    hass, entry, client
):
    """An ongoing closure can start before membership, but not route outside it."""
    coordinator = make_coordinator(hass, entry, client)
    child = child_payload()
    child["departments"][0]["to"] = "2030-04-15"
    second = child_payload(nursery="nursery-b")["departments"][0]
    second["from"] = "2030-04-16"
    child["departments"].append(second)
    client.async_children.return_value = parse_children(
        [child, child_payload("child-b", nursery="nursery-c")]
    )
    client.async_source.side_effect = lambda source, *_: (
        [closure(**{"from": "2030-04-14", "to": "2030-04-17"})]
        if source == "closures"
        else []
    )
    result = await coordinator.async_events(START, END)
    assert {event.uid for event in result["child-a", "closures"]} == {
        "closure:nursery-a:closure-a",
        "closure:nursery-b:closure-a",
    }
    assert [event.uid for event in result["child-b", "closures"]] == [
        "closure:nursery-c:closure-a"
    ]
    after_transfer = await coordinator.async_events(
        dt_util.start_of_local_day(date(2030, 4, 16)), END
    )
    assert [event.uid for event in after_transfer["child-a", "closures"]] == [
        "closure:nursery-b:closure-a"
    ]


@pytest.mark.parametrize(
    "localization", [("en", "Closure days"), ("nb", "Stengedager")]
)
async def test_closure_entity_state_range_reads_and_empty_refresh(
    hass, entry, client, freezer, localization
):
    """Expose current/next closures locally and clear removed events on refresh."""
    language, label = localization
    hass.config.language = language
    freezer.move_to(START)
    coordinator = make_coordinator(hass, entry, client)
    client.async_source.side_effect = lambda source, *_: (
        [
            closure(),
            closure(id="next", **{"from": "2030-04-18", "to": "2030-04-19"}),
        ]
        if source == "closures"
        else source_payload(source)
    )
    await coordinator.async_refresh()
    entity = DibberCalendar(coordinator, "child-a", "closures")
    assert entity.name == f"Dibber Example child-a {label}"
    assert entity.unique_id == "account-a:child-a:closures"
    assert DibberCalendar(coordinator, "child-a").unique_id == "account-a:child-a"
    assert entity.supported_features == 0
    assert entity.available
    assert entity.event.uid == "closure:nursery-a:closure-a"
    calls = client.async_source.call_count
    freezer.move_to(entity.event.end_datetime_local)
    assert entity.event.uid == "closure:nursery-a:next"
    freezer.move_to(entity.event.end_datetime_local)
    assert entity.event is None
    assert client.async_source.call_count == calls
    assert len(await entity.async_get_events(hass, START, END)) == 2
    assert await entity.async_get_events(hass, END, START) == []
    client.async_source.side_effect = lambda *_: []
    coordinator._cache.clear()
    await coordinator.async_refresh()
    assert entity.event is None
    assert await entity.async_get_events(hass, START, END) == []
    coordinator.children = {}
    assert not entity.available


async def test_closures_deduplicate_across_month_windows(hass, entry, client):
    """The API may repeat the entire closure list for every requested window."""
    coordinator = make_coordinator(hass, entry, client)
    client.async_source.side_effect = lambda source, *_: (
        [closure(to="2030-06-01")] if source == "closures" else []
    )
    result = await coordinator.async_events(START, START + timedelta(days=90))
    assert len(result["child-a", "closures"]) == 1
    assert result["child-a", "closures"][0].end == date(2030, 6, 2)
