"""Read-only Dibber calendars."""

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import DibberClient, SchemaError, Tokens
from .const import CONF_UNCERTAIN
from .coordinator import DibberConfigEntry, DibberCoordinator

PLATFORMS = [Platform.CALENDAR]


@callback
def create_client(hass: HomeAssistant, entry: DibberConfigEntry) -> DibberClient:
    """Restore and atomically update both tokens without update/reload listeners."""

    @callback
    def persist(tokens: Tokens, uncertain: bool) -> None:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, **tokens.as_dict(), CONF_UNCERTAIN: uncertain}
        )

    return DibberClient(
        async_get_clientsession(hass),
        Tokens.parse(entry.data),
        persist,
        uncertain=entry.data.get(CONF_UNCERTAIN, False),
    )


async def async_setup_entry(hass: HomeAssistant, entry: DibberConfigEntry) -> bool:
    """Set up the account and discover children before loading calendars."""
    try:
        client = create_client(hass, entry)
    except SchemaError:
        raise ConfigEntryAuthFailed("invalid_tokens") from None
    entry.runtime_data = coordinator = DibberCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DibberConfigEntry) -> bool:
    """Unload listeners and in-flight work, without logging out."""
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_shutdown()
        return True
    return False
