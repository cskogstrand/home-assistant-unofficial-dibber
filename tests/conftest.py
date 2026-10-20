"""Fictional fixtures and real Home Assistant test infrastructure."""

import base64
import json
import time
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dibber.api import DibberClient, Tokens
from custom_components.dibber.const import DOMAIN


def token(expiry=None, marker="access"):
    """Create an explicitly unsigned synthetic JWT used only by mocked HTTP."""
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "exp": expiry or time.time() + 21600,
                    "iat": 1,
                    "fixture": marker,
                }
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    return f"eyJhbGciOiJub25lIn0.{payload}.synthetic"


def token_pair(expiry=None, marker="first"):
    """Use different synthetic pairs, retaining the original iat."""
    return {
        "accessToken": token(expiry, marker),
        "refreshToken": f"synthetic-refresh-{marker}",
        "passwordExpired": False,
    }


def child_payload(child_id="child-a", department="department-a", nursery="nursery-a"):
    """Include private fields as sentinels to prove they are discarded."""
    return {
        "id": child_id,
        "firstName": "Example",
        "lastName": child_id,
        "healthInformation": "PRIVATE_HEALTH_SENTINEL",
        "identityNumber": "PRIVATE_IDENTITY_SENTINEL",
        "departments": [
            {
                "department": department,
                "departmentName": "Example department",
                "kindergarten": nursery,
                "kindergartenName": "Example nursery",
                "from": "2020-01-01",
                "to": "2040-12-31",
            }
        ],
    }


def activity(day="2030-04-15", activity_id="activity-a", **overrides):
    """Generate ordinary or personal events with fictional contents."""
    return {
        "id": activity_id,
        "subject": "Example activity",
        "description": "Fictional description.",
        "activityType": "Other",
        "visibility": "Department",
        "fullDayEvent": False,
        "appointment": {
            "date": day,
            "start": "09:00",
            "stop": "09:30",
            "zone": "Etc/UTC",
        },
        "hasRsvp": False,
        "rsvpResponse": None,
        **overrides,
    }


def closure(**overrides):
    """Generate fictional single-day or multi-day nursery closures."""
    return {
        "id": "closure-a",
        "type": "PLANNING_DAY",
        "title": "Example planning day",
        "from": "2030-04-15",
        "to": None,
        "holidayDays": 0,
        **overrides,
    }


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    """Permit loading the repository's custom integration."""


@pytest.fixture
def entry(hass):
    """Add a real config entry with only synthetic secrets."""
    result = MockConfigEntry(
        domain=DOMAIN,
        title="Dibber",
        unique_id="account-a",
        data={
            **token_pair(),
            "account_id": "account-a",
            "device_id": "synthetic-device",
            "children": ["child-a", "child-b"],
        },
    )
    result.add_to_hass(hass)
    return result


@pytest.fixture
async def client(hass, aioclient_mock):
    """Exercise the actual HTTP wrapper with HA's mock session."""
    return DibberClient(async_get_clientsession(hass), Tokens.parse(token_pair()))


@pytest.fixture
def mock_setup():
    """Keep config-flow tests separate from platform lifecycle tests."""
    with patch(
        "custom_components.dibber.async_setup_entry", new=AsyncMock(return_value=True)
    ) as mock:
        yield mock
