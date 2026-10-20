"""Read-only activity, closure, and invitation calendars for each selected child."""

from typing import TYPE_CHECKING

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import AccessDeniedError, AuthenticationError, DibberError
from .const import CONF_ACCOUNT_ID
from .coordinator import DibberConfigEntry, DibberCoordinator
from .events import identity

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001 HA platform signature
    entry: DibberConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Retain selected identities even if a child temporarily loses access."""
    coordinator = entry.runtime_data
    async_add_entities(
        DibberCalendar(coordinator, child, calendar_type)
        for child in coordinator.selected
        for calendar_type in ("activities", "closures", "rsvp")
    )


class DibberCalendar(CoordinatorEntity[DibberCoordinator], CalendarEntity):
    """Network-free state and on-demand range reads using the shared coordinator."""

    _attr_supported_features = 0
    _attr_icon = "mdi:calendar"

    def __init__(
        self,
        coordinator: DibberCoordinator,
        child_id: str,
        calendar_type: str = "activities",
    ) -> None:
        """Names may change; account and child IDs remain the identity."""
        super().__init__(coordinator)
        self.child_id = child_id
        self._calendar_key = (child_id, calendar_type)
        self._suffix = ""
        self._attr_unique_id = identity(
            coordinator.config_entry.data[CONF_ACCOUNT_ID], child_id
        )
        if calendar_type == "closures":
            self._attr_unique_id += ":closures"
            self._attr_icon = "mdi:calendar-remove"
            self._suffix = (
                " Stengedager"
                if coordinator.hass.config.language in ("nb", "no")
                else " Closure days"
            )
        elif calendar_type == "rsvp":
            self._attr_unique_id += ":rsvp"
            self._attr_icon = "mdi:calendar-account"
            self._suffix = (
                " Invitasjoner"
                if coordinator.hass.config.language in ("nb", "no")
                else " Invitations"
            )

    @property
    def name(self) -> str:
        """Use only a minimum display label."""
        child = self.coordinator.children.get(self.child_id)
        return (f"Dibber {child.name}" if child else "Dibber") + self._suffix

    @property
    def available(self) -> bool:
        """Make a child unavailable when rediscovery confirms missing access."""
        return super().available and self.child_id in self.coordinator.children

    @property
    def event(self) -> CalendarEvent | None:
        """Return the first ongoing or upcoming event, including all-day events."""
        if not self.available:
            return None
        now = dt_util.utcnow()
        return next(
            (
                event
                for event in (self.coordinator.data or {}).get(self._calendar_key, [])
                if dt_util.as_utc(event.end_datetime_local) > now
            ),
            None,
        )

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Never turn a failed fetch into an empty or stale successful response."""
        if end_date <= start_date:
            return []
        try:
            events = await self.coordinator.async_events(start_date, end_date)
            if self.child_id not in self.coordinator.children:
                raise AccessDeniedError("child_unavailable")
        except DibberError as error:
            if isinstance(error, AuthenticationError):
                self.coordinator.config_entry.async_start_reauth(hass)
            if self.coordinator.last_update_success:
                self.coordinator.async_set_update_error(HomeAssistantError(str(error)))
            raise HomeAssistantError(str(error)) from None
        return events.get(self._calendar_key, [])
