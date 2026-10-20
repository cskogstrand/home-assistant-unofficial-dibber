"""Real HA setup, unload, reauth, and calendar subscriber behavior."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.dibber.api import (
    AuthenticationError,
    CommunicationError,
    RateLimitError,
    parse_children,
)
from custom_components.dibber.calendar import DibberCalendar
from custom_components.dibber.coordinator import DibberCoordinator

from .conftest import activity, child_payload, closure


@pytest.fixture
def runtime_api():
    """Only replace the external API; run HA setup and platform loading for real."""
    with (
        patch(
            "custom_components.dibber.api.DibberClient.async_children",
            new=AsyncMock(
                return_value=parse_children([child_payload(), child_payload("child-b")])
            ),
        ) as children,
        patch(
            "custom_components.dibber.api.DibberClient.async_source",
            new=AsyncMock(return_value=[]),
        ) as source,
    ):
        yield children, source


async def test_setup_unload_and_no_logout(hass, entry, runtime_api):
    """Calendar platform lifecycle needs only discovery and read-only sources."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert len(entities) == 6
    assert {item.unique_id for item in entities} == {
        "account-a:child-a",
        "account-a:child-a:closures",
        "account-a:child-a:rsvp",
        "account-a:child-b",
        "account-a:child-b:closures",
        "account-a:child-b:rsvp",
    }
    assert all(item.domain == "calendar" for item in entities)
    assert not entry.update_listeners
    coordinator = entry.runtime_data
    assert len(coordinator._listeners) == 6
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert coordinator._closed
    assert not coordinator._listeners
    assert not coordinator._inflight


@pytest.mark.parametrize(
    "localization",
    [
        (
            "en",
            ("Activities", "Closure days", "Invitations"),
            ("activities", "closure_days", "invitations"),
        ),
        (
            "nb",
            ("Aktiviteter", "Stengedager", "Invitasjoner"),
            ("aktiviteter", "stengedager", "invitasjoner"),
        ),
        (
            "no",
            ("Aktiviteter", "Stengedager", "Invitasjoner"),
            ("aktiviteter", "stengedager", "invitasjoner"),
        ),
    ],
)
async def test_calendar_names_and_ids(hass, entry, runtime_api, localization):
    """Use the complete first name and distinguish children with identical names."""
    language, labels, suffixes = localization
    hass.config.language = language
    runtime_api[0].return_value = parse_children(
        [
            child_payload(child_id) | {"firstName": "Anne Marie"}
            for child_id in ("child-a", "child-b")
        ]
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert {item.entity_id for item in entities} == {
        f"calendar.dibber_anne_marie_{suffix}{number}"
        for suffix in suffixes
        for number in ("", "_2")
    }
    assert {item.original_name for item in entities} == {
        f"Dibber Anne Marie {label}" for label in labels
    }


@pytest.mark.parametrize("custom_id", [False, True])
@pytest.mark.parametrize(
    "localization",
    [
        (
            "en",
            ("", " Closure days", " Invitations"),
            ("activities", "closure_days", "invitations"),
        ),
        (
            "nb",
            ("", " Stengedager", " Invitasjoner"),
            ("aktiviteter", "stengedager", "invitasjoner"),
        ),
    ],
)
async def test_legacy_calendar_ids_migrate_once(
    hass, entry, runtime_api, custom_id, localization
):
    """Keep registry identity and overrides while migrating old generated IDs."""
    language, old_suffixes, new_suffixes = localization
    hass.config.language = language
    registry = er.async_get(hass)
    previous = []
    for unique_suffix, old_suffix in zip(
        ("", ":closures", ":rsvp"), old_suffixes, strict=True
    ):
        old_name = f"Dibber Example child-a{old_suffix}"
        registered = registry.async_get_or_create(
            "calendar",
            "dibber",
            f"account-a:child-a{unique_suffix}",
            config_entry=entry,
            original_name=old_name,
            object_id_base=old_name,
        )
        registered = registry.async_update_entity(
            registered.entity_id,
            new_entity_id=(
                f"calendar.family{unique_suffix.replace(':', '_')}"
                if custom_id
                else f"{registered.entity_id}_2"
            ),
            name="My calendar" if unique_suffix == ":closures" else None,
        )
        previous.append(registered)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    for old, suffix in zip(previous, new_suffixes, strict=True):
        entity_id = registry.async_get_entity_id("calendar", "dibber", old.unique_id)
        assert entity_id == (
            old.entity_id if custom_id else f"calendar.dibber_example_{suffix}"
        )
        registered = registry.async_get(entity_id)
        assert registered.id == old.id
        assert registered.name == old.name
        assert hass.states.get(entity_id) is not None
        if not custom_id:
            assert hass.states.get(old.entity_id) is None
    before = {
        item.unique_id: item.entity_id
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert {
        item.unique_id: item.entity_id
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
    } == before


@pytest.mark.parametrize(
    ("failure", "state"),
    [
        (CommunicationError("cannot_connect"), ConfigEntryState.SETUP_RETRY),
        (RateLimitError(60), ConfigEntryState.SETUP_RETRY),
        (AuthenticationError("invalid_auth"), ConfigEntryState.SETUP_ERROR),
    ],
)
async def test_setup_failure_semantics(hass, entry, runtime_api, failure, state):
    """Temporary failures retry; explicit rejection starts reauthentication."""
    runtime_api[0].side_effect = failure
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is state
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == "reauth" for flow in flows) == isinstance(
        failure, AuthenticationError
    )


async def test_reload_transfer_keeps_entity_ids(hass, entry, runtime_api):
    """Department membership and names are labels/routing, never entity IDs."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    before = {
        entity.unique_id: entity.entity_id
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    payload = child_payload(department="new-department", nursery="new-nursery")
    payload["firstName"] = "Renamed"
    runtime_api[0].return_value = parse_children([payload, child_payload("child-b")])
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    after = {
        entity.unique_id: entity.entity_id
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert after == before


@pytest.mark.parametrize("calendar_type", ["activities", "closures", "rsvp"])
async def test_subscriber_receives_changed_window_with_unchanged_current_event(
    hass, entry, runtime_api, calendar_type
):
    """HA 2026.10 writes notify subscribers even if event/state remains None."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    unique_id = "account-a:child-a"
    if calendar_type != "activities":
        unique_id += f":{calendar_type}"
    entity_id = er.async_get(hass).async_get_entity_id("calendar", "dibber", unique_id)
    entity = hass.data["calendar"].get_entity(entity_id)
    now = dt_util.now()
    future = now + timedelta(days=40)
    results = []
    unsubscribe = entity.async_subscribe_events(
        future, future + timedelta(days=7), results.append
    )
    entity.async_update_event_listeners()
    await hass.async_block_till_done()
    assert results[-1] == []
    assert entity.event is None
    day = (future + timedelta(days=1)).date().isoformat()
    payload = (
        [closure(**{"from": day})]
        if calendar_type == "closures"
        else [{"activities": [activity(day=day)]}]
    )
    if calendar_type == "rsvp":
        payload = [
            {
                "pending": [
                    activity(
                        day=day,
                        visibility="Personal",
                        rsvpResponse={
                            "caregivers": [{"children": [{"id": "child-a"}]}]
                        },
                    )
                ],
                "accepted": [],
            }
        ]
    runtime_api[1].side_effect = lambda source, *_: (
        payload if source == calendar_type else []
    )
    entry.runtime_data._cache.clear()
    entity.async_write_ha_state()
    await hass.async_block_till_done()
    assert len(results[-1]) == 1
    assert entity.event is None
    unsubscribe()
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not entity._event_listeners
    assert entity._event_listener_debouncer is None


async def test_unload_cancels_inflight_reads(hass, entry, client):
    """An unfinished future-window fetch cannot outlive its entry."""
    coordinator = DibberCoordinator(hass, entry, client)
    client.async_children = AsyncMock(return_value=parse_children([child_payload()]))
    started = asyncio.Event()

    async def pending(*_):
        started.set()
        await asyncio.Event().wait()

    client.async_source = pending
    task = asyncio.create_task(
        coordinator.async_events(dt_util.now(), dt_util.now() + timedelta(days=7))
    )
    await started.wait()
    await coordinator.async_shutdown()
    assert task.cancelled()
    assert not coordinator._inflight


@pytest.mark.parametrize("calendar_type", ["activities", "closures", "rsvp"])
async def test_on_demand_failure_unavailable_then_poll_recovers(
    hass, entry, runtime_api, calendar_type
):
    """Failed range reads raise an HA error and never return stale events."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entity = DibberCalendar(entry.runtime_data, "child-a", calendar_type)

    def fail_source(source, *_):
        if source == calendar_type:
            raise CommunicationError("cannot_connect")
        return []

    runtime_api[1].side_effect = fail_source
    start = dt_util.now() + timedelta(days=60)
    with pytest.raises(HomeAssistantError, match="cannot_connect"):
        await entity.async_get_events(hass, start, start + timedelta(days=7))
    assert not entity.available
    runtime_api[1].side_effect = None
    await entry.runtime_data.async_refresh()
    assert entity.available


async def test_discovery_failure_preserves_children_but_empty_removes_access(
    hass, entry, runtime_api
):
    """Failures and successful removals have different meanings."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    old = coordinator.children
    runtime_api[0].side_effect = CommunicationError("cannot_connect")
    with pytest.raises(CommunicationError):
        await coordinator.async_discover(force=True)
    assert coordinator.children == old
    runtime_api[0].side_effect = None
    runtime_api[0].return_value = {}
    await coordinator.async_discover(force=True)
    assert not DibberCalendar(coordinator, "child-a").available


async def test_options_reload_removes_unselected_active_calendar(
    hass, entry, runtime_api
):
    """Child selection removes active calendars while preserving stable registry IDs."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    before = {
        entity.unique_id: entity.entity_id
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"children": ["child-b"]}
    )
    await hass.async_block_till_done()
    assert entry.runtime_data.selected == ("child-b",)
    component = hass.data["calendar"]
    assert component.get_entity(before["account-a:child-a"]) is None
    assert component.get_entity(before["account-a:child-b"]) is not None
    assert component.get_entity(before["account-a:child-a:closures"]) is None
    assert component.get_entity(before["account-a:child-b:closures"]) is not None
    assert component.get_entity(before["account-a:child-a:rsvp"]) is None
    assert component.get_entity(before["account-a:child-b:rsvp"]) is not None
