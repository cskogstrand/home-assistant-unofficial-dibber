"""Token rotation, concurrency, trust boundaries, and sanitized errors."""

import asyncio
import time
from datetime import UTC, date, datetime, timedelta
from email.utils import format_datetime
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from custom_components.dibber import create_client
from custom_components.dibber.api import (
    AccessDeniedError,
    AuthenticationError,
    CommunicationError,
    DibberError,
    RateLimitError,
    SchemaError,
    Tokens,
    retry_delay,
    token_expiry,
)
from custom_components.dibber.const import BASE_URL

from .conftest import token_pair

REFRESH = BASE_URL + "/open/bff/user/dibberid/token/refresh"
CHILDREN = BASE_URL + "/api/caregivers/children"


async def test_refresh_persists_pair_before_get_and_survives_restart(
    hass, entry, aioclient_mock
):
    """Both rotated values persist before another request observes the pair."""
    old = token_pair(time.time() + 60)
    new = token_pair(marker="replacement")
    hass.config_entries.async_update_entry(entry, data={**entry.data, **old})
    aioclient_mock.post(REFRESH, json=new)
    aioclient_mock.get(CHILDREN, json=[])
    client = create_client(hass, entry)
    assert await client.async_children() == {}
    assert entry.data["accessToken"] == new["accessToken"]
    assert entry.data["refreshToken"] == new["refreshToken"]
    assert entry.data["device_id"] == "synthetic-device"
    assert entry.data["children"] == ["child-a", "child-b"]
    assert not entry.update_listeners
    assert create_client(hass, entry).tokens == client.tokens
    refresh = aioclient_mock.mock_calls[0]
    assert refresh[2] == {
        "accessToken": old["accessToken"],
        "refreshToken": old["refreshToken"],
    }
    assert "Authorization" not in refresh[3]
    assert (
        aioclient_mock.mock_calls[1][3]["Authorization"]
        == "Bearer " + new["accessToken"]
    )
    assert client.tokens.expires == token_expiry(new["accessToken"])
    assert "accessToken" not in repr(client.tokens)


async def test_concurrent_reads_refresh_once(hass, entry, aioclient_mock):
    """The lock rechecks expiry after another caller renews."""
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, **token_pair(time.time() + 1)}
    )
    client = create_client(hass, entry)
    aioclient_mock.post(REFRESH, json=token_pair(marker="next"))
    aioclient_mock.get(CHILDREN, json=[])
    await asyncio.gather(*(client.async_children() for _ in range(8)))
    assert sum(call[0] == "POST" for call in aioclient_mock.mock_calls) == 1


async def test_401_retry_and_parallel_rejected_pair(client):
    """All reads rejected with the old pair share one renewal."""
    old = client.tokens
    new = token_pair(marker="next")

    async def request(method, path, **kwargs):
        await asyncio.sleep(0)
        if method == "POST":
            return new
        if kwargs["token"] == old.access:
            raise AuthenticationError("invalid_auth")
        return []

    with patch.object(client, "_request", side_effect=request) as mock:
        await asyncio.gather(*(client.async_children() for _ in range(6)))
    assert sum(call.args[0] == "POST" for call in mock.call_args_list) == 1


async def test_repeated_401_stops_and_refresh_never_recurses(client):
    """Two GETs and one refresh are the absolute limit."""
    with patch.object(
        client,
        "_request",
        side_effect=[
            AuthenticationError("invalid_auth"),
            token_pair(marker="new"),
            AuthenticationError("invalid_auth"),
        ],
    ) as mock:
        with pytest.raises(AuthenticationError):
            await client.async_children()
        with pytest.raises(AuthenticationError):
            await client.async_children()
    assert mock.call_count == 3


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (403, AccessDeniedError),
        (500, CommunicationError),
        (429, RateLimitError),
        (302, DibberError),
    ],
)
async def test_get_errors_do_not_refresh(client, aioclient_mock, status, error, caplog):
    """Forbidden resources and transient failures are not invalid passwords."""
    aioclient_mock.get(
        CHILDREN,
        status=status,
        text="PRIVATE_REMOTE_DETAIL",
        headers={"Retry-After": "120"},
    )
    with pytest.raises(error) as caught:
        await client.async_children()
    assert len(aioclient_mock.mock_calls) == 1
    assert "PRIVATE_REMOTE_DETAIL" not in str(caught.value) + caplog.text


@pytest.mark.parametrize(
    "failure", [CommunicationError("cannot_connect"), SchemaError("invalid_response")]
)
async def test_uncertain_refresh_is_not_replayed_even_after_restart(
    hass, entry, failure
):
    """A possibly consumed pair is retained, with a persistent recovery marker."""
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, **token_pair(time.time() + 1)}
    )
    client = create_client(hass, entry)
    old = client.tokens
    with patch.object(client, "_request", side_effect=failure) as mock:
        with pytest.raises(DibberError):
            await client.async_children()
        with pytest.raises(CommunicationError, match="renewal_uncertain"):
            await client.async_children()
    assert mock.call_count == 1
    assert client.tokens == old
    assert entry.data["renewal_uncertain"] is True
    with pytest.raises(CommunicationError, match="renewal_uncertain"):
        await create_client(hass, entry).async_children()


async def test_refresh_rate_limit_allows_later_recovery(hass, entry, aioclient_mock):
    """429 backs off without poisoning the pair or requiring sign-in."""
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, **token_pair(time.time() + 1)}
    )
    client = create_client(hass, entry)
    aioclient_mock.post(REFRESH, status=429, headers={"Retry-After": "120"})
    for _ in range(2):
        with pytest.raises(RateLimitError) as caught:
            await client.async_children()
        assert 1 <= caught.value.retry_after <= 120
    assert len(aioclient_mock.mock_calls) == 1
    assert not entry.data.get("renewal_uncertain")


@pytest.mark.parametrize("status", [401, 403])
async def test_refresh_rejection_is_auth_failure(client, aioclient_mock, status):
    """Refresh rejection is final, unlike a data GET's 403."""
    client.tokens = Tokens.parse(token_pair(time.time() + 1))
    aioclient_mock.post(REFRESH, status=status)
    with pytest.raises(AuthenticationError):
        await client.async_children()


@pytest.mark.parametrize(
    "data", [{}, {"accessToken": "bad", "refreshToken": "synthetic"}, [], None]
)
def test_malformed_tokens(data):
    """Reject malformed values without retaining a half-valid pair."""
    with pytest.raises(SchemaError):
        Tokens.parse(data)


def test_expired_password_and_retry_after():
    """Password expiry is explicit; numeric backoffs are finite and bounded."""
    with pytest.raises(AuthenticationError, match="password_expired"):
        Tokens.parse({**token_pair(), "passwordExpired": True})
    assert retry_delay("9999999") == 3600
    assert retry_delay("NaN") == 60
    assert retry_delay("invalid") == 60


async def test_http_options_and_exception_redaction(client):
    """No redirects, normal TLS, explicit timeout, and no raw HTTP error leaks."""
    request = AsyncMock(side_effect=aiohttp.ClientError("PRIVATE_URL_AND_TOKEN"))
    # The context-manager mock raises on enter, just as a failed aiohttp request does.
    context = MagicMock()
    context.__aenter__ = request
    with (
        patch.object(client._session, "request", return_value=context) as mock,
        pytest.raises(CommunicationError) as caught,
    ):
        await client.async_children()
    assert mock.call_args.kwargs["allow_redirects"] is False
    assert mock.call_args.kwargs["timeout"].total == 30
    assert "ssl" not in mock.call_args.kwargs
    assert "PRIVATE" not in str(caught.value)
    assert caught.value.__suppress_context__


async def test_source_paths_queries_and_opaque_segments(client):
    """Only observed GET endpoints are used, with encoded path/query identifiers."""
    start, end = date(2030, 4, 1), date(2030, 4, 30)
    with patch.object(client, "async_get", return_value=[]) as get:
        for source in ("activities", "rsvp", "focus", "closures"):
            await client.async_source(
                source, "nursery/a?b", "department/x&y", start, end
            )
    activity_call, rsvp_call, focus_call, closure_call = get.call_args_list
    assert activity_call.args[0] == "/api/ecdc/nursery%2Fa%3Fb/planner/activities"
    assert activity_call.args[1] == {
        "from": "2030-04-01",
        "to": "2030-04-30",
        "department": "department/x&y",
    }
    assert rsvp_call.args[0].endswith("/activities/rsvp")
    assert "department" not in rsvp_call.args[1]
    assert focus_call.args[0].endswith("/pedagogy/department%2Fx%26y/schedule")
    assert closure_call.args == (
        "/api/ecdc/nursery%2Fa%3Fb/schedule/closure-days",
        {"from": "2030-04-01", "to": "2030-04-30"},
    )
    with patch.object(client, "async_get", return_value=[]) as get:
        await client.async_source("focus", "..", "..", start, end)
    assert "/%2E%2E/" in get.call_args.args[0]
    with pytest.raises(ValueError, match="Unsupported source"):
        await client.async_source("unknown", "n", "d", start, end)


def test_http_date_retry_after():
    """HTTP-date backoff works in addition to numeric seconds."""
    later = datetime.now(UTC) + timedelta(minutes=5)
    assert 298 <= retry_delay(format_datetime(later)) <= 300


async def test_malformed_json_and_profile_fail_safely(client, aioclient_mock):
    """A successful HTTP status does not make an unknown body trustworthy."""
    aioclient_mock.get(
        CHILDREN, text="PRIVATE_NON_JSON", headers={"Content-Type": "application/json"}
    )
    with pytest.raises(SchemaError, match="invalid_response"):
        await client.async_children()
    with (
        patch.object(
            client, "async_get", return_value={"responsePayload": {"userId": "a"}}
        ),
        pytest.raises(SchemaError),
    ):
        await client.async_account_id()


async def test_timeout_does_not_expose_request_details(client):
    """Translate timeout exceptions without their private messages or context."""
    context = MagicMock()
    context.__aenter__ = AsyncMock(side_effect=TimeoutError("PRIVATE_REQUEST"))
    with (
        patch.object(client._session, "request", return_value=context),
        pytest.raises(CommunicationError, match="cannot_connect") as caught,
    ):
        await client.async_children()
    assert "PRIVATE" not in str(caught.value)
