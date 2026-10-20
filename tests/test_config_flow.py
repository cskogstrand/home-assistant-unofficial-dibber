"""Exercise the real config-flow manager with synthetic HTTP responses."""

from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType

from custom_components.dibber.api import (
    AuthenticationError,
    CommunicationError,
    SchemaError,
    parse_children,
)
from custom_components.dibber.const import BASE_URL, DOMAIN

from .conftest import child_payload, token_pair


def login_responses(mock, *, account="account-a", children=None, method="DibberMFA"):
    """Mock exactly the observed routes and minimal schemas."""
    mock.post(
        BASE_URL + "/open/bff/user/dibberid/validate",
        json={
            "defaultAuthMethod": {"authType": method},
            "validAuthMethods": [
                {"authType": method, "isProvisioned": True, "isEducator": False}
            ],
        },
    )
    mock.post(
        BASE_URL + "/open/bff/user/dibberid/login/mfa",
        json={"validationHash": "synthetic-challenge"},
    )
    mock.post(
        BASE_URL + "/open/bff/user/dibberid/login/mfa/validate", json=token_pair()
    )
    mock.get(
        BASE_URL + "/bff/user/fetch-summary-profile/v1",
        json={
            "status": 200,
            "responsePayload": {
                "userId": account,
                "role": "Caregiver",
                "address": "PRIVATE_ADDRESS_SENTINEL",
            },
        },
    )
    mock.get(
        BASE_URL + "/api/caregivers/children",
        json=[child_payload(), child_payload("child-b")]
        if children is None
        else children,
    )


async def sign_in(hass, *, context=None):
    """Submit credentials and code explicitly, as a user would."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context=context or {"source": SOURCE_USER}
    )
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "email": "example@example.invalid",
            "password": "synthetic-password",
        },
    )
    assert result["step_id"] == "code"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"code": "synthetic-code"}
    )


async def test_full_login_and_selection(hass, aioclient_mock, mock_setup, caplog):
    """Challenge propagates, but credentials/challenges never reach config storage."""
    login_responses(aioclient_mock)
    result = await sign_in(hass)
    assert result["step_id"] == "children"
    flow = hass.config_entries.flow._progress[result["flow_id"]]
    assert not flow._challenge
    assert not flow._email
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"children": ["child-b"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "account-a"
    data = result["data"]
    assert set(data) == {
        "accessToken",
        "refreshToken",
        "account_id",
        "device_id",
        "children",
    }
    assert data["children"] == ["child-b"]
    assert len(data["device_id"]) == 36
    calls = aioclient_mock.mock_calls
    assert calls[1][2]["deviceId"] == data["device_id"]
    assert calls[2][2] == {
        "loginId": "example@example.invalid",
        "accessCode": "synthetic-code",
        "validationHash": "synthetic-challenge",
    }
    assert all("Authorization" not in call[3] for call in calls[:3])
    assert flow._client is None
    assert all(
        secret not in caplog.text
        for secret in (
            "synthetic-password",
            "synthetic-code",
            "PRIVATE_",
            data["accessToken"],
        )
    )


async def test_cancel_clears_transient_challenge(hass, aioclient_mock):
    """HA calls async_remove for user cancellation as well as completion."""
    login_responses(aioclient_mock)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            "email": "example@example.invalid",
            "password": "synthetic-password",
        },
    )
    flow = hass.config_entries.flow._progress[result["flow_id"]]
    assert flow._challenge
    hass.config_entries.flow.async_abort(result["flow_id"])
    assert flow._client is None
    assert not flow._challenge
    assert not flow._email


async def test_unsupported_auth_never_starts_login(hass, aioclient_mock):
    """A passkey-only account does not fall through to MFA."""
    login_responses(aioclient_mock, method="Fido2")
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            "email": "example@example.invalid",
            "password": "synthetic-password",
        },
    )
    assert result["errors"] == {"base": "unsupported_auth"}
    assert len(aioclient_mock.mock_calls) == 1


async def test_duplicate_account(hass, entry, aioclient_mock):
    """Duplicate prevention uses server account identity, not an email label."""
    login_responses(aioclient_mock)
    result = await sign_in(hass)
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize("source", [SOURCE_REAUTH, SOURCE_RECONFIGURE])
async def test_same_account_reauth_preserves_selection(
    hass, entry, aioclient_mock, source
):
    """Reauthentication and manual recovery update the existing entry."""
    login_responses(aioclient_mock)
    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        result = await sign_in(
            hass,
            context={
                "source": source,
                "entry_id": entry.entry_id,
                "unique_id": entry.unique_id,
            },
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] in ("reauth_successful", "reconfigure_successful")
    assert entry.unique_id == "account-a"
    assert entry.data["children"] == ["child-a", "child-b"]
    assert entry.data["device_id"] == "synthetic-device"
    reload.assert_called_once_with(entry.entry_id)


async def test_reauth_wrong_account(hass, entry, aioclient_mock):
    """Another account cannot overwrite the existing child's entity identity."""
    login_responses(aioclient_mock, account="different-account")
    original = dict(entry.data)
    result = await sign_in(
        hass, context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id}
    )
    assert result["reason"] == "wrong_account"
    assert dict(entry.data) == original


async def test_empty_discovery_is_useful_abort(hass, aioclient_mock):
    """An authenticated empty list is a real no-children result."""
    login_responses(aioclient_mock, children=[])
    result = await sign_in(hass)
    assert result["reason"] == "no_children"


async def test_options_child_selection(hass, entry, aioclient_mock):
    """Options use current discovery and reject empty selections."""
    aioclient_mock.get(
        BASE_URL + "/api/caregivers/children",
        json=[child_payload(), child_payload("child-b")],
    )
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"children": []}
    )
    assert result["errors"] == {"base": "select_children"}
    with patch.object(hass.config_entries, "async_reload", return_value=True):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"children": ["child-b"]}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["children"] == ["child-b"]


@pytest.mark.parametrize(
    "payload", [{}, [None], [{"id": "a"}], [child_payload() | {"departments": None}]]
)
def test_invalid_discovery_not_empty(payload):
    """Malformed discovery cannot erase current access."""
    with pytest.raises(SchemaError):
        parse_children(payload)


def test_discovery_mapping_and_privacy():
    """Only names, routing IDs and membership dates survive parsing."""
    children = parse_children([child_payload()])
    assert children["child-a"].name == "Example child-a"
    assert children["child-a"].first_name == "Example"
    membership = children["child-a"].memberships[0]
    assert membership.nursery == "nursery-a"
    assert membership.department == "department-a"
    assert "PRIVATE_" not in repr(children)


async def test_login_failure_is_sanitized_form_error(hass, aioclient_mock):
    """No raw challenge, error body, or password is reflected into the form."""
    aioclient_mock.post(
        BASE_URL + "/open/bff/user/dibberid/validate", status=500, text="PRIVATE_ERROR"
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            "email": "example@example.invalid",
            "password": "synthetic-password",
        },
    )
    assert result["errors"] == {"base": "cannot_connect"}
    assert "PRIVATE_ERROR" not in str(result)


async def test_discovery_retry_does_not_replay_code(hass, aioclient_mock, mock_setup):
    """A failed discovery retries reads using the issued pair, not another login."""
    login_responses(aioclient_mock)
    with patch(
        "custom_components.dibber.api.DibberClient.async_children",
        side_effect=CommunicationError("cannot_connect"),
    ):
        result = await sign_in(hass)
    assert result["step_id"] == "children"
    assert result["errors"] == {"base": "cannot_connect"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert not result["errors"]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"children": ["child-a"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len([call for call in aioclient_mock.mock_calls if call[0] == "POST"]) == 3


async def test_wrong_code_keeps_challenge_for_retry(hass, aioclient_mock):
    """A rejected code can be re-entered without restarting login or sending email."""
    login_responses(aioclient_mock)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            "email": "example@example.invalid",
            "password": "synthetic-password",
        },
    )
    with patch(
        "custom_components.dibber.api.DibberClient.async_validate_code",
        side_effect=AuthenticationError("invalid_auth"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "wrong-synthetic-code"}
        )
    assert result["errors"] == {"base": "invalid_auth"}
    assert (
        hass.config_entries.flow._progress[result["flow_id"]]._challenge
        == "synthetic-challenge"
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"code": "synthetic-code"}
    )
    assert result["step_id"] == "children"
