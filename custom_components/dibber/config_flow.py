"""Explicit, transient MFA sign-in and child selection."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import probatio as vol
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import create_client
from .api import (
    AccessDeniedError,
    AuthenticationError,
    Child,
    CommunicationError,
    DibberClient,
    DibberError,
    RateLimitError,
    SchemaError,
    UnsupportedAuthError,
)
from .const import (
    CONF_ACCOUNT_ID,
    CONF_CHILDREN,
    CONF_DEVICE_ID,
    CONF_UNCERTAIN,
    DOMAIN,
)


def flow_error(error: DibberError) -> str:  # noqa: PLR0911 Error translation table
    """Map errors to fixed translations without remote content."""
    if isinstance(error, AuthenticationError):
        return (
            "password_expired" if str(error) == "password_expired" else "invalid_auth"
        )
    if isinstance(error, UnsupportedAuthError):
        return "unsupported_auth"
    if isinstance(error, AccessDeniedError):
        return "access_denied"
    if isinstance(error, RateLimitError):
        return "rate_limited"
    if isinstance(error, CommunicationError):
        return (
            "renewal_uncertain"
            if str(error) == "renewal_uncertain"
            else "cannot_connect"
        )
    if isinstance(error, SchemaError):
        return "invalid_response"
    return "request_rejected"


def children_schema(children: dict[str, Child], selected: list[str]) -> vol.Schema:
    """Offer only discovered children, never raw upstream dictionaries."""
    return vol.Schema(
        {
            vol.Required(
                CONF_CHILDREN, default=[item for item in selected if item in children]
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"value": child.id, "label": child.name}
                        for child in children.values()
                    ],
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                )
            )
        }
    )


class DibberConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Support initial setup and same-account sign-in recovery."""

    VERSION = 1

    def __init__(self) -> None:
        """Keep all challenges and credentials transient."""
        self._client: DibberClient | None = None
        self._email = ""
        self._challenge = ""
        self._device_id = str(uuid4())
        self._children: dict[str, Child] = {}
        self._account_id = ""

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,  # noqa: ARG004 HA flow factory
    ) -> DibberOptionsFlow:
        """Let HA reload only when child-selection options change."""
        return DibberOptionsFlow()

    @callback
    def async_remove(self) -> None:
        """Clear transient state on completion, cancellation, and abort."""
        self._client = None
        self._email = self._challenge = ""
        self._children = {}
        super().async_remove()

    async def async_step_reauth(
        self,
        entry_data: dict[str, Any],  # noqa: ARG002 HA reauth signature
    ) -> config_entries.ConfigFlowResult:
        """Show the explicit login form; never initiate login automatically."""
        self._device_id = self._get_reauth_entry().data[CONF_DEVICE_ID]
        return await self.async_step_user()

    async def async_step_reconfigure(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """Allow user-initiated recovery after an uncertain token renewal."""
        self._device_id = self._get_reconfigure_entry().data[CONF_DEVICE_ID]
        return await self.async_step_user(user_input)

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """Start login only after the user submits the email/password form."""
        errors = {}
        if user_input is not None:
            self._email = user_input[CONF_EMAIL].strip()
            self._client = DibberClient(async_get_clientsession(self.hass))
            try:
                self._challenge = await self._client.async_start_login(
                    self._email, user_input[CONF_PASSWORD], self._device_id
                )
            except DibberError as error:
                errors["base"] = flow_error(error)
            else:
                return await self.async_step_code()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.EMAIL
                        )
                    ),
                    vol.Required(CONF_PASSWORD): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_code(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """Accept a user-entered code without retaining it."""
        errors = {}
        if user_input is not None:
            try:
                await self._client.async_validate_code(
                    self._email, user_input["code"], self._challenge
                )
            except DibberError as error:
                errors["base"] = flow_error(error)
            else:
                self._email = self._challenge = ""
                return await self.async_step_children()
        return self.async_show_form(
            step_id="code",
            data_schema=vol.Schema(
                {
                    vol.Required("code"): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_children(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """Validate server identity, discover routing, and select calendars."""
        errors = {}
        if not self._children:
            try:
                self._account_id = await self._client.async_account_id()
                await self.async_set_unique_id(self._account_id)
                if self.source in (
                    config_entries.SOURCE_REAUTH,
                    config_entries.SOURCE_RECONFIGURE,
                ):
                    entry = (
                        self._get_reauth_entry()
                        if self.source == config_entries.SOURCE_REAUTH
                        else self._get_reconfigure_entry()
                    )
                    if entry.unique_id != self._account_id:
                        return self.async_abort(reason="wrong_account")
                else:
                    self._abort_if_unique_id_configured()
                self._children = await self._client.async_children()
            except DibberError as error:
                errors["base"] = flow_error(error)
            else:
                if self.source in (
                    config_entries.SOURCE_REAUTH,
                    config_entries.SOURCE_RECONFIGURE,
                ):
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={
                            **self._client.tokens.as_dict(),
                            CONF_UNCERTAIN: False,
                            CONF_DEVICE_ID: self._device_id,
                        },
                    )
                if not self._children:
                    return self.async_abort(reason="no_children")
        if user_input is not None and CONF_CHILDREN in user_input and not errors:
            selected = user_input.get(CONF_CHILDREN, [])
            if not selected or not set(selected) <= self._children.keys():
                errors["base"] = "select_children"
            else:
                return self.async_create_entry(
                    title="Dibber",
                    data={
                        **self._client.tokens.as_dict(),
                        CONF_ACCOUNT_ID: self._account_id,
                        CONF_DEVICE_ID: self._device_id,
                        CONF_CHILDREN: list(dict.fromkeys(selected)),
                    },
                )
        return self.async_show_form(
            step_id="children",
            data_schema=(
                children_schema(self._children, list(self._children))
                if self._children
                else vol.Schema({})
            ),
            errors=errors,
        )


class DibberOptionsFlow(config_entries.OptionsFlowWithReload):
    """Rediscover children and change the calendar selection."""

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """Keep errors separate from a successful empty discovery."""
        errors = {}
        entry = self.config_entry
        try:
            client = (
                entry.runtime_data.client
                if hasattr(entry, "runtime_data")
                else create_client(self.hass, entry)
            )
            children = await client.async_children()
        except DibberError as error:
            if isinstance(error, AuthenticationError):
                entry.async_start_reauth(self.hass)
            errors["base"] = flow_error(error)
            children = {}
        else:
            if not children:
                return self.async_abort(reason="no_children")
            if user_input is not None and CONF_CHILDREN in user_input:
                selected = user_input.get(CONF_CHILDREN, [])
                if not selected or not set(selected) <= children.keys():
                    errors["base"] = "select_children"
                else:
                    return self.async_create_entry(
                        title="", data={CONF_CHILDREN: list(dict.fromkeys(selected))}
                    )
        return self.async_show_form(
            step_id="init",
            data_schema=(
                children_schema(
                    children,
                    entry.options.get(CONF_CHILDREN, entry.data[CONF_CHILDREN]),
                )
                if children
                else vol.Schema({})
            ),
            errors=errors,
        )
