"""Shared discovery, polling, and bounded range cache for one Dibber account."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.components.calendar import CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    AuthenticationError,
    Child,
    DibberClient,
    DibberError,
    RateLimitError,
)
from .const import CONF_CHILDREN, DISCOVERY_INTERVAL, DOMAIN, LOGGER, POLL_INTERVAL
from .events import Occurrence, event_start, overlaps, parse_source

if TYPE_CHECKING:
    from collections.abc import Iterator

    from homeassistant.core import HomeAssistant

type DibberConfigEntry = ConfigEntry[DibberCoordinator]
type CacheKey = tuple[str, str, str, date, date]
MAX_CACHE_WINDOWS = 64


def date_windows(start: date, end: date) -> Iterator[tuple[date, date]]:
    """Use at most 31 dates per query, sharing boundary dates between chunks."""
    while start <= end:
        stop = min(start + timedelta(days=30), end)
        yield start, stop
        if stop == end:
            break
        start = stop


class DibberCoordinator(DataUpdateCoordinator[dict[str, list[CalendarEvent]]]):
    """Keep event properties local; calendars share all requests and refreshes."""

    def __init__(
        self, hass: HomeAssistant, entry: DibberConfigEntry, client: DibberClient
    ) -> None:
        """Initialize account-local caches and conservative polling."""
        super().__init__(
            hass,
            LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=POLL_INTERVAL,
            always_update=True,
        )
        self.client = client
        self.children: dict[str, Child] = {}
        self.selected = tuple(
            entry.options.get(CONF_CHILDREN, entry.data[CONF_CHILDREN])
        )
        self._discovered_at: float | None = None
        self._cache: OrderedDict[CacheKey, tuple[float, list[Occurrence]]] = (
            OrderedDict()
        )
        self._range_lock = asyncio.Lock()
        self._inflight: set[asyncio.Task] = set()
        self._closed = False
        self._focus_title = (
            "Dagens fokus" if hass.config.language in ("nb", "no") else "Daily focus"
        )

    async def async_discover(self, *, force: bool = False) -> None:
        """Swap discovery only after a successful response."""
        if (
            force
            or self._discovered_at is None
            or time.monotonic() - self._discovered_at
            >= DISCOVERY_INTERVAL.total_seconds()
        ):
            self.children = await self.client.async_children()
            self._discovered_at = time.monotonic()

    async def _source(self, key: CacheKey) -> list[Occurrence]:
        """Cache sanitized events; failures leave the previous entry untouched."""
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < POLL_INTERVAL.total_seconds():
            self._cache.move_to_end(key)
            return cached[1]
        source, nursery, department, start, end = key
        data = await self.client.async_source(source, nursery, department, start, end)
        events = parse_source(data, source, nursery, department, self._focus_title)
        self._cache[key] = (time.monotonic(), events)
        self._cache.move_to_end(key)
        while len(self._cache) > MAX_CACHE_WINDOWS:
            self._cache.popitem(last=False)
        return events

    async def async_events(  # noqa: PLR0912 Source and membership routing
        self, start: datetime, end: datetime
    ) -> dict[str, list[CalendarEvent]]:
        """Fetch complete ranges, coalescing concurrent sibling calendar reads."""
        if self._closed:
            raise DibberError("integration_unloaded")
        task = asyncio.current_task()
        self._inflight.add(task)
        try:
            async with self._range_lock:
                await self.async_discover()
                result: dict[str, dict[str, CalendarEvent]] = {
                    child: {} for child in self.selected
                }
                # Planner dates have source timezones, which can differ from HA.
                first = dt_util.as_utc(start).date() - timedelta(days=1)
                last = dt_util.as_utc(end).date() + timedelta(days=1)
                for window_start, window_end in date_windows(first, last):
                    scopes: dict[CacheKey, list[tuple[str, date, date]]] = {}
                    for child_id in self.selected:
                        child = self.children.get(child_id)
                        if child is None:
                            continue
                        for membership in child.memberships:
                            if (
                                membership.end < window_start
                                or membership.start > window_end
                            ):
                                continue
                            for source in ("activities", "focus", "rsvp"):
                                key = (
                                    source,
                                    membership.nursery,
                                    "" if source == "rsvp" else membership.department,
                                    window_start,
                                    window_end,
                                )
                                scopes.setdefault(key, []).append(
                                    (child_id, membership.start, membership.end)
                                )
                    # Sequential source reads also bound concurrency and cache pressure.
                    for key, routes in scopes.items():
                        for occurrence in await self._source(key):
                            if not overlaps(occurrence.event, start, end):
                                continue
                            for child_id, membership_start, membership_end in routes:
                                # shortcut: inclusive end; verify on transfer.
                                if (
                                    not membership_start
                                    <= occurrence.day
                                    <= membership_end
                                ):
                                    continue
                                if (
                                    occurrence.recipients is not None
                                    and child_id not in occurrence.recipients
                                ):
                                    continue
                                event = occurrence.event
                                previous = result[child_id].get(event.uid)
                                if previous is None or event.location:
                                    result[child_id][event.uid] = event
                return {
                    child: sorted(events.values(), key=event_start)
                    for child, events in result.items()
                }
        finally:
            self._inflight.discard(task)

    async def _async_update_data(self) -> dict[str, list[CalendarEvent]]:
        """Refresh only a small current/near-term window."""
        now = dt_util.now()
        try:
            return await self.async_events(
                now.replace(hour=0, minute=0, second=0, microsecond=0),
                now + timedelta(days=7),
            )
        except AuthenticationError as error:
            raise ConfigEntryAuthFailed(str(error)) from None
        except RateLimitError as error:
            raise UpdateFailed(str(error), retry_after=error.retry_after) from None
        except DibberError as error:
            raise UpdateFailed(str(error)) from None

    async def async_shutdown(self) -> None:
        """Cancel range requests on unload; HA owns the shared HTTP session."""
        self._closed = True
        tasks = self._inflight - {asyncio.current_task()}
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await super().async_shutdown()
