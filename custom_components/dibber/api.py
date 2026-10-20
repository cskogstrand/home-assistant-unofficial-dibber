"""Small client for the observed Dibber API; errors never contain remote data."""

from __future__ import annotations

import asyncio
import base64
import json
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import aiohttp
from yarl import URL

from .const import BASE_URL, USER_AGENT


class DibberError(Exception):
    """A sanitized API failure."""


class AuthenticationError(DibberError):
    """The server rejected authentication."""


class CommunicationError(DibberError):
    """A retryable service failure."""


class AccessDeniedError(DibberError):
    """Access to a resource is forbidden, independently of authentication."""


class SchemaError(DibberError):
    """An unsupported or malformed response."""


class UnsupportedAuthError(DibberError):
    """The account requires an unsupported sign-in method."""


class RateLimitError(CommunicationError):
    """A bounded server backoff."""

    def __init__(self, retry_after: int) -> None:
        """Remember only the delay, never headers or response bodies."""
        super().__init__("rate_limited")
        self.retry_after = retry_after


def required_string(data: Any, key: str) -> str:
    """Validate a required, nonempty string without exposing its value."""
    if not isinstance(data, Mapping) or not isinstance(data.get(key), str):
        raise SchemaError("invalid_response")
    if not data[key].strip():
        raise SchemaError("invalid_response")
    return data[key]


def token_expiry(token: str) -> float:
    """Read exp for scheduling only; this does not authenticate a JWT."""
    try:
        payload = token.split(".")[1]
        value = json.loads(
            base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        )
        expiry = value["exp"]
        if (
            isinstance(expiry, bool)
            or not isinstance(expiry, (float, int))
            or not math.isfinite(expiry)
            or expiry <= 0
        ):
            raise ValueError
        return float(expiry)
    except ValueError, KeyError, IndexError, TypeError:
        raise SchemaError("invalid_tokens") from None


@dataclass(frozen=True, repr=False)
class Tokens:
    """A complete token pair, deliberately excluded from repr."""

    access: str
    refresh: str
    expires: float

    @classmethod
    def parse(cls, data: Any) -> Tokens:
        """Validate both tokens before replacing either."""
        if not isinstance(data, Mapping):
            raise SchemaError("invalid_tokens")
        if data.get("passwordExpired") is True:
            raise AuthenticationError("password_expired")
        access = required_string(data, "accessToken")
        refresh = required_string(data, "refreshToken")
        return cls(access, refresh, token_expiry(access))

    def as_dict(self) -> dict[str, str]:
        """Return the pair for HA's private config-entry storage."""
        return {"accessToken": self.access, "refreshToken": self.refresh}


@dataclass(frozen=True)
class Membership:
    """The identifiers and dates needed to route planner queries."""

    nursery: str
    department: str
    start: date
    end: date


@dataclass(frozen=True)
class Child:
    """Minimum discovery data; no full upstream object is retained."""

    id: str
    name: str
    first_name: str
    memberships: tuple[Membership, ...] = field(default_factory=tuple)


def parse_children(data: Any) -> dict[str, Child]:
    """Reject invalid discovery instead of mistaking it for removed access."""
    if not isinstance(data, list):
        raise SchemaError("invalid_children")
    children = {}
    try:
        for item in data:
            child_id = required_string(item, "id")
            name = required_string(item, "firstName")
            last = item.get("lastName", "")
            if not isinstance(last, str) or not isinstance(
                item.get("departments"), list
            ):
                raise SchemaError("invalid_children")
            memberships = []
            for department in item["departments"]:
                start = date.fromisoformat(required_string(department, "from"))
                end = date.fromisoformat(required_string(department, "to"))
                if end < start:
                    raise SchemaError("invalid_children")
                memberships.append(
                    Membership(
                        required_string(department, "kindergarten"),
                        required_string(department, "department"),
                        start,
                        end,
                    )
                )
            children[child_id] = Child(
                child_id, f"{name} {last}".strip(), name.strip(), tuple(memberships)
            )
    except ValueError:
        raise SchemaError("invalid_children") from None
    return children


def retry_delay(value: str | None) -> int:
    """Accept Retry-After seconds or HTTP dates, bounded to one hour."""
    try:
        seconds = float(value)
    except TypeError, ValueError:
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except TypeError, ValueError, OverflowError:
            seconds = 60
    return min(3600, max(1, math.ceil(seconds))) if math.isfinite(seconds) else 60


class DibberClient:
    """One shared session/refresh lock per account; no runtime password login."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        tokens: Tokens | None = None,
        persist: Callable[[Tokens, bool], None] | None = None,
        *,
        uncertain: bool = False,
    ) -> None:
        """Use HA's session without owning or closing it."""
        self._session = session
        self.tokens = tokens
        self._persist = persist
        self._renewal_lock = asyncio.Lock()
        self._requests = asyncio.Semaphore(3)
        self._retry_at = 0.0
        self._uncertain = uncertain
        self._auth_failed = False

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        params: dict | None = None,
        token: str | None = None,
    ) -> Any:
        """Use a fixed TLS origin, no redirects, and sanitized exceptions."""
        async with self._requests:
            if self._retry_at > time.monotonic():
                raise RateLimitError(math.ceil(self._retry_at - time.monotonic()))
            try:
                async with self._session.request(
                    method,
                    URL(BASE_URL + path, encoded=True),
                    json=body,
                    params=params,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Accept": "application/json",
                        **({"Authorization": f"Bearer {token}"} if token else {}),
                    },
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status == HTTPStatus.UNAUTHORIZED:
                        raise AuthenticationError("invalid_auth")
                    if response.status == HTTPStatus.FORBIDDEN:
                        raise AccessDeniedError("access_denied")
                    if response.status == HTTPStatus.TOO_MANY_REQUESTS:
                        delay = retry_delay(response.headers.get("Retry-After"))
                        self._retry_at = time.monotonic() + delay
                        raise RateLimitError(delay)
                    if response.status >= HTTPStatus.INTERNAL_SERVER_ERROR:
                        raise CommunicationError("service_unavailable")
                    if response.status != HTTPStatus.OK:
                        raise DibberError("request_rejected")
                    try:
                        return await response.json()
                    except ValueError, aiohttp.ContentTypeError:
                        raise SchemaError("invalid_response") from None
            except TimeoutError, aiohttp.ClientError:
                raise CommunicationError("cannot_connect") from None

    async def async_start_login(self, email: str, password: str, device_id: str) -> str:
        """Run only after an explicit setup/reauth form submission."""
        methods = await self._request(
            "POST", "/open/bff/user/dibberid/validate", body={"email": email}
        )
        if not isinstance(methods, dict) or not isinstance(
            methods.get("validAuthMethods"), list
        ):
            raise SchemaError("invalid_response")
        if not any(
            isinstance(method, dict)
            and method.get("authType") == "DibberMFA"
            and method.get("isProvisioned") is True
            and method.get("isEducator") is False
            for method in methods["validAuthMethods"]
        ):
            raise UnsupportedAuthError("unsupported_auth")
        response = await self._request(
            "POST",
            "/open/bff/user/dibberid/login/mfa",
            body={
                "loginId": email,
                "password": password,
                "isSharedDevice": False,
                "deviceId": device_id,
            },
        )
        return required_string(response, "validationHash")

    async def async_validate_code(self, email: str, code: str, challenge: str) -> None:
        """Keep the code/challenge out of persistent client state."""
        response = await self._request(
            "POST",
            "/open/bff/user/dibberid/login/mfa/validate",
            body={"loginId": email, "accessCode": code, "validationHash": challenge},
        )
        self.tokens = Tokens.parse(response)

    async def _refresh(self, rejected: Tokens | None = None) -> None:
        """Serialize renewal and recheck the pair after waiting for the lock."""
        async with self._renewal_lock:
            if self._auth_failed:
                raise AuthenticationError("invalid_auth")
            current = self.tokens
            if current is None:
                raise AuthenticationError("invalid_auth")
            if rejected is not None and current is not rejected:
                return
            if rejected is None and current.expires > time.time() + 300:
                return
            if self._uncertain:
                raise CommunicationError("renewal_uncertain")
            try:
                response = await self._request(
                    "POST",
                    "/open/bff/user/dibberid/token/refresh",
                    body=current.as_dict(),
                )
                replacement = Tokens.parse(response)
            except AccessDeniedError:
                self._auth_failed = True
                raise AuthenticationError("invalid_auth") from None
            except AuthenticationError:
                self._auth_failed = True
                raise
            except RateLimitError:
                raise
            except DibberError, asyncio.CancelledError:
                # A lost response may have rotated the pair: never replay it blindly.
                self._uncertain = True
                if self._persist:
                    self._persist(current, True)
                raise
            if self._persist:
                self._persist(replacement, False)
            self.tokens = replacement

    async def async_get(self, path: str, params: dict | None = None) -> Any:
        """Retry a rejected authenticated GET once, without refresh recursion."""
        await self._refresh()
        current = self.tokens
        if current is None:
            raise AuthenticationError("invalid_auth")
        try:
            return await self._request("GET", path, params=params, token=current.access)
        except AuthenticationError:
            await self._refresh(rejected=current)
        try:
            return await self._request(
                "GET", path, params=params, token=self.tokens.access
            )
        except AuthenticationError:
            self._auth_failed = True
            raise

    async def async_account_id(self) -> str:
        """Use server identity, never JWT claims, for duplicate/same-account checks."""
        response = await self.async_get("/bff/user/fetch-summary-profile/v1")
        if not isinstance(response, dict) or "status" not in response:
            raise SchemaError("invalid_profile")
        profile = response.get("responsePayload")
        required_string(profile, "role")
        # The caregiver endpoint authorizes calendar access; role enums are unverified.
        return required_string(profile, "userId")

    async def async_children(self) -> dict[str, Child]:
        """Discard all unrelated child data immediately."""
        return parse_children(await self.async_get("/api/caregivers/children"))

    async def async_opening_hours(self, nursery: str) -> Any:
        """Fetch the weekly schedule without undocumented date/department queries."""
        segment = quote(nursery, safe="").replace(".", "%2E")
        return await self.async_get(f"/api/ecdc/{segment}/schedule/opening-hours")

    async def async_source(
        self, source: str, nursery: str, department: str, start: date, end: date
    ) -> Any:
        """Fetch only the observed calendar sources."""
        nursery_segment = quote(nursery, safe="").replace(".", "%2E")
        department_segment = quote(department, safe="").replace(".", "%2E")
        prefix = f"/api/ecdc/{nursery_segment}/planner"
        params = {"from": start.isoformat(), "to": end.isoformat()}
        if source == "activities":
            path = f"{prefix}/activities"
            params["department"] = department
        elif source == "rsvp":
            path = f"{prefix}/activities/rsvp"
        elif source == "focus":
            path = f"{prefix}/pedagogy/{department_segment}/schedule"
        elif source == "closures":
            path = f"/api/ecdc/{nursery_segment}/schedule/closure-days"
        else:
            raise ValueError("Unsupported source")
        return await self.async_get(path, params)
