# Dibber calendar HACS integration implementation prompt

Build a Home Assistant custom integration named **Dibber**, installable through HACS as a custom repository. Its purpose is to show a caregiver's Dibber calendar and plans in existing Home Assistant calendars and a family dashboard.

Work in the new integration project, not the dashboard repository. Implement the smallest complete version: UI sign-in, child and department discovery, renewable authentication, and read-only calendar entities. Follow the target project's instructions and use Ponytail for edits. Complete implementation and focused tests; distinguish mocked checks from live verification.

This is an unofficial integration with an observed mobile-app API, not a supported public API. The contracts below come from successful captured requests. Do not invent undocumented endpoints or treat unverified behavior as established.

## Scope and delivery

- Use domain `dibber`, subject to checking for a collision in the target project and current HA integrations.
- Create one calendar entity per selected child, combining department activities, that child's RSVP events, and daily pedagogical focus where available.
- Discover the user's children and nursery/department identifiers automatically. Support selection of multiple children without duplicating shared department requests.
- Implement Home Assistant setup, unload, reload, options for child selection, and reauthentication.
- Keep authentication and polling in Home Assistant. The family dashboard already consumes ordinary HA calendar events with `summary`, `description`, `location`, `start`, `end`, and optional `uid`. It needs no Dibber credentials or custom frontend.
- Exclude messaging, attendance changes, RSVP submission, photos, push notifications, Firebase registration, password resets, passkeys, and event editing. Login and token renewal POSTs are the necessary exceptions to read-only data access.
- Record the closure-day API, but leave closure events disabled until its multi-day end-date convention is verified. Empty planner-note responses do not establish a note schema; do not fabricate one.
- Deliver the component, synthetic tests, README, installation instructions, manifests, and CI configuration. Do not publish, create a release, send communications, or use captured credentials against the live service without explicit user approval.

## Evidence and remaining limits

The combined export inspected on 2026-10-20 contains 152 request records, including repeated calls, across 22 method/path/query shapes. Every recorded host is `api.dibber.com`. No raw capture, real identifiers, event text, personal information, password, code, or token belongs in this project.

Confirmed:

- Successful password plus code login issues access and refresh tokens.
- Four captured refresh calls returned replacement token pairs. Access expiry was six hours after the response; refresh expiry was fourteen days after the response.
- Refresh preserves the original JWT `iat` while moving `exp`. Never calculate the remaining validity as `exp - iat`.
- Child discovery provides the identifiers needed by the planner. Captured identifier relationships were compared directly.
- Department activities and RSVP events are separate sources. The captured RSVP meeting did not occur in the ordinary activity list.
- The captured activity, RSVP, and note queries used `from == to`. The user subsequently tested queries spanning one month successfully, confirming extended date-range support beyond those single-day captures.
- Closure responses contained entries outside the requested date range. Local range filtering is required.

Not established:

- Whether old refresh tokens are invalidated; whether replacement tokens form an indefinitely renewable chain; behavior after password changes, concurrent devices, or a long offline period.
- Exact start/end boundary semantics, pagination, the maximum supported date span, rate limits, and whether mobile-only headers are required. Month-long query support is confirmed by the user's test.
- Multi-child and multi-department behavior beyond the observed schema.
- All-day activity payloads, overnight activities, DST edge cases, declined RSVP buckets, and non-empty notes.
- Whether closure `to` is inclusive or exclusive. `holidayDays` does not consistently equal calendar duration and must not determine event length.

The user reports that their email code stays unchanged until a password reset. Treat this as account-specific observed behavior, not a universal API guarantee. Accept a user-entered code during setup/reauthentication; do not store it or automatically request replacement codes.

The month-span verification was performed by the user, not by this agent. It is sufficient to choose range-based fetching; do not repeat the basic range-support investigation. Other live checks still require explicit approval after a concrete implementation is ready.

## API conventions

Base URL: `https://api.dibber.com`

- Use JSON requests/responses. Authenticated data GETs carry `Authorization: Bearer <access_token>`.
- The captured login and refresh calls did not carry an Authorization header. Refresh receives both tokens in its JSON body.
- Use HA's shared asynchronous HTTP client, normal TLS verification, explicit timeouts, and fixed trusted origin. Do not send credentials across redirects.
- Supply a descriptive integration User-Agent. Do not copy device fingerprints, `x-dynatrace`, Apple-specific headers, cookies, or tracing identifiers from the app. Minimal-header compatibility still needs live validation.
- Treat identifiers as opaque strings. Encode URL path segments and query parameters safely.
- Validate response shapes at the boundary. Never dump raw request/response bodies or HTTP exceptions containing personal URLs.
- Examples below are fictional or placeholders. They illustrate structure, not usable credentials.

Captured 401 responses had a problem-details object with `type`, `title`, `status`, `detail`, `instance`, and `id`. Classify failures by HTTP status and safe fields, not an assumed English error string. Do not expose `detail`, `instance`, or correlation IDs in logs.

## Authentication contract

All paths in this section use POST unless stated otherwise.

### Discover authentication methods

`/open/bff/user/dibberid/validate`

```json
{"email": "caregiver@example.invalid"}
```

The response contains `defaultAuthMethod` and `validAuthMethods`. Each method has `isProvisioned`, `isDefault`, `authType`, and `isEducator`. The observed default was `DibberMFA`; `Fido2` was also listed. Implement the caregiver `DibberMFA` path only and provide an understandable unsupported-method error for other required flows.

### Start login

`/open/bff/user/dibberid/login/mfa`

```json
{
  "loginId": "caregiver@example.invalid",
  "password": "<user_entered_password>",
  "isSharedDevice": false,
  "deviceId": "<integration_owned_uuid>"
}
```

Response:

```json
{"validationHash": "<opaque_login_challenge>"}
```

The captured `deviceId` was UUID-shaped. Generate a stable UUID for this integration session; do not reuse the phone's identifier. Acceptance of an independently generated device ID needs live verification.

The setup form must explain that submitting login can initiate an email verification step. Runtime polling must never initiate login or send/request email automatically.

### Validate the code

`/open/bff/user/dibberid/login/mfa/validate`

```json
{
  "loginId": "caregiver@example.invalid",
  "accessCode": "<user_entered_code>",
  "validationHash": "<hash_from_start_login>"
}
```

Successful response shape:

```json
{
  "accessToken": "<access_jwt>",
  "refreshToken": "<refresh_jwt>",
  "passwordExpired": false,
  "daysUntilPasswordExpiration": 30,
  "showPwdChangeAdvice": false
}
```

The example expiry-advice values are fictional. The hash from the login response was confirmed to match the subsequent validation request. The issued access token was confirmed to match the authenticated profile request.

Keep password, code, and challenge in transient flow memory only; clear them after completion or abort. Do not assume the code is a one-time OTP or that it is permanently reusable.

### Renew the session

`/open/bff/user/dibberid/token/refresh`

```json
{
  "accessToken": "<current_access_jwt>",
  "refreshToken": "<current_refresh_jwt>"
}
```

Response: the same token/advice fields as successful MFA validation.

Implementation requirements:

- Decode JWT `exp` only to schedule renewal; unsigned decoding is not verification of identity or permissions. The authenticated server response remains authoritative.
- Renew shortly before access expiry, for example five minutes early, when requests are needed. Captured refresh succeeded before expiry.
- Serialize renewal with one lock per account and recheck the current token after acquiring it. Parallel calendar requests must not start competing refreshes.
- Replace and persist **both tokens together** before subsequent requests use them. Use `hass.config_entries.async_update_entry`; do not mutate config-entry data directly. Ensure token persistence does not trigger an integration reload loop. [HA config-entry updates](https://developers.home-assistant.io/blog/2024/02/12/async_update_entry/).
- Restore the latest persisted pair after restart. Preserve necessary device/account configuration when updating tokens.
- On an authenticated GET's 401, allow at most one coordinated refresh and one GET retry. Refresh authentication rejection or repeated 401 should start reauthentication, not a password/code loop.
- A timeout, 5xx, or rate limit is not proof of invalid credentials. Avoid blindly retrying a timed-out refresh: the server may already have replaced its tokens. Preserve state and handle this as an uncertain renewal failure with bounded recovery.
- Do not assume any 403 is a bad password; it may mean the account no longer has access to a child or department.
- An explicitly expired password requires user action. Do not reset it automatically.

Do not call logout on normal unload, reload, restart, or removal. The observed logout is `POST /bff/user/auth/logout/v1`, body `{}`, with both `Authorization` and `refresh-token` headers; it is not needed for calendar operation.

### Establish account identity

`GET /bff/user/fetch-summary-profile/v1`

The response is an object with `status` and `responsePayload`. The latter contains `userId`, `role`, `rolePrivileges`, and profile objects. Extract only the stable account identifier and the minimum role information required to support caregiver accounts. Discard addresses, identity numbers, contact details, photos, and the rest of the profile.

Use the stable account ID for duplicate-entry prevention and same-account reauthentication. Reauth must update the existing entry and retain its entity IDs and selections. Follow HA's [config-flow](https://developers.home-assistant.io/docs/config_entries_config_flow_handler/) and [reauthentication](https://contributors.home-assistant.io/core/integrations/reauth-reconfigure/) patterns.

## Child and department discovery

`GET /api/caregivers/children`

The response is an array. Retain only the fields needed for selection and routing:

```json
[
  {
    "id": "<child_id>",
    "firstName": "Example",
    "lastName": "Child",
    "departments": [
      {
        "department": "<department_id>",
        "departmentName": "Example department",
        "kindergarten": "<ecdc_id>",
        "kindergartenName": "Example nursery",
        "from": "2030-01-01",
        "to": "2030-12-31"
      }
    ]
  }
]
```

Important field mapping, verified across the capture:

- `departments[].kindergarten` is the `ecdc_id` in planner paths.
- `departments[].department` is the `department` query parameter.
- The same department ID appears in the pedagogy path.
- Membership `from` and `to` are date strings; they are not activity dates.

Do not retain the endpoint's health information, consents, identity numbers, birth dates, photos, or other unrelated fields. Do not expose full child objects as entity attributes or diagnostics.

Allow the user to select children. Handle multiple memberships explicitly rather than selecting array index zero. Route a requested date through matching memberships; membership end-day inclusivity is not verified, so document and test the chosen inclusive-date assumption. Rediscover on setup, reauth, and periodically at a low frequency so department changes do not require deleting the integration.

A successful empty children list means no accessible children; show a useful setup message. A transport failure must not be interpreted as all children being removed.

## Calendar sources

### Department activities

`GET /api/ecdc/{ecdc_id}/planner/activities?from=YYYY-MM-DD&to=YYYY-MM-DD&department={department_id}`

Response shape:

```json
[
  {
    "date": "2030-04-15",
    "activities": [
      {
        "id": "<activity_id>",
        "subject": "Example activity",
        "description": "Fictional description for a test.",
        "activityType": "Other",
        "visibility": "Department",
        "fullDayEvent": false,
        "appointment": {
          "date": "2030-04-15",
          "start": "09:00",
          "stop": "09:30",
          "zone": "Etc/UTC"
        },
        "hasRsvp": false,
        "rsvpResponse": null
      }
    ]
  }
]
```

Observed times are `HH:MM`, dates are `YYYY-MM-DD`, and zones are valid IANA timezone names. All observed ordinary activities were timed. The grouping date matched the appointment date in these samples.

Use `appointment.date` as the event date. Combine it with `start`, `stop`, and `zone`; never interpret these clock times as UTC merely because transport uses HTTPS/JSON. Map `subject` to summary and `description` to plain-text description.

Do not filter to a single activity type. Observed types included `Meal`, `Other`, `Playtime`, and `Sleep`; future values should still render.

### RSVP events

`GET /api/ecdc/{ecdc_id}/planner/activities/rsvp?from=YYYY-MM-DD&to=YYYY-MM-DD`

There is **no department query parameter** in the captured RSVP request.

Response:

```json
[
  {
    "date": "2030-04-15",
    "pending": [
      {
        "id": "<rsvp_activity_id>",
        "subject": "Example meeting",
        "description": "Fictional meeting description.",
        "activityType": "Meeting",
        "visibility": "Personal",
        "fullDayEvent": false,
        "appointment": {
          "date": "2030-04-15",
          "start": "10:00",
          "stop": "10:30",
          "zone": "Etc/UTC"
        },
        "hasRsvp": true,
        "rsvpResponse": {
          "location": "Example meeting room",
          "caregivers": [
            {
              "id": "<caregiver_id>",
              "children": [{"id": "<child_id>"}]
            }
          ]
        }
      }
    ],
    "accepted": []
  }
]
```

This example shows the minimal fields to retain, not every response field. `pending` contains activity objects with the same event fields shown above. `accepted` was empty in the capture; parsing its objects with the same event model is a reasonable implementation assumption requiring a synthetic test.

For observed personal events, `rsvpResponse` contains `location`, response counts, and `caregivers[]`; each caregiver has `children[]`, whose `id` matches the child-discovery ID. It also contains names/photos and an `employees` array that the integration must discard.

- Include both pending and accepted events without submitting or changing RSVP.
- For `visibility == "Personal"`, route an event only to children explicitly named in its nested recipient IDs. Do not put every personal invitation on every sibling's calendar. If recipient metadata is missing or unfamiliar, skip routing it and report a sanitized schema warning.
- The observed visibility was `Personal`, and the observed activity type was `Meeting`. Other RSVP visibility rules are unverified; do not guess a department audience from an endpoint that lacks a department selector.
- Use `rsvpResponse.location` when present, without copying attendee lists or counts into descriptions.
- Deduplicate by source activity identity plus occurrence date across ordinary activities, RSVP buckets, and overlapping membership fetches. Prefer the richer RSVP location when the same occurrence appears twice.
- Do not assume an event is cancelled because it is not accepted. Declined/cancelled payloads have not been captured.

### Daily pedagogical focus

`GET /api/ecdc/{ecdc_id}/planner/pedagogy/{department_id}/schedule?from=YYYY-MM-DD&to=YYYY-MM-DD`

Response:

```json
[
  {
    "date": "2030-04-15",
    "focusForTheDay": "Fictional learning focus.",
    "learningFriends": ["<string_value>"]
  }
]
```

Create an all-day event titled with a translated neutral label such as `Daily focus` when `focusForTheDay` is non-empty. Use the focus text as its description and a stable synthetic UID based on nursery, department, and date. The meaning/display mapping of `learningFriends` is unverified; omit it from the first version.

### Closure days

`GET /api/ecdc/{ecdc_id}/schedule/closure-days?from=YYYY-MM-DD&to=YYYY-MM-DD`

Response item fields: `id`, `title`, `type`, `from`, `to`, and `holidayDays`. `from` is a date; `to` can be null or a date. Observed types: `CLOSED`, `PLANNING_DAY`, `PUBLIC_HOLIDAY`.

The app requested a calendar-year window, but responses included records outside that window. Filter locally. Neither `holidayDays` nor the request bounds prove whether `to` is inclusive. Before enabling this source, compare one single-day closure and one multi-day closure with the app. A null `to` can be modeled as one day as an explicitly documented assumption. Do not silently convert every range to `to + 1 day` without verification.

### Notes and other endpoints

`GET /api/ecdc/{ecdc_id}/planner/notes/schedule?from=YYYY-MM-DD&to=YYYY-MM-DD&department={department_id}` returned only `[]`. Its non-empty schema remains unknown; leave this source out of v1.

The remaining observed routes do not need implementation for this calendar:

| Method and path | Reason to omit |
|---|---|
| `GET /api/state` with or without `childId` and `ecdcId` | Returns domain/state strings; no demonstrated need for calendar reads |
| `GET /api/ecdc/{ecdc_id}/schedule/opening-hours` | Weekly opening hours, not individual plans |
| `GET /api/banners/user?kindergartenId={ecdc_id}` | App UI content |
| `GET /api/daily-summary/feedback/emoji/list` | Feedback UI |
| `GET /api/ecdc/{ecdc_id}/department/{department_id}/daily-summary/detail?date=YYYY-MM-DD` | Daily report, outside calendar scope |
| `POST /bff/kgoperations/attendance/daily-summaries/v1` | Attendance/reporting, outside scope |
| `POST /bff/kindergartens/departments/employees/list/photo/v1` | Employee photos |
| `POST /open/bff/user/dibberid/firebase-token` | Firebase credentials |
| `POST /bff/notifications/push/device/subscribe/v1` | Changes push registration |

No replay has proved that omitting the app's state/Firebase calls is universally safe. Start with the explicitly authenticated calendar endpoints; add a dependency only if a controlled live check demonstrates one.

## Home Assistant behavior and implementation choices

Use native `CalendarEntity` and `CalendarEvent`, implement `async_get_events`, and expose the current/next event through the entity's `event` property. Advertise no create/update/delete features. HA expects exclusive ends; all-day events use dates and timed events use timezone-aware datetimes. Return chronologically sorted events overlapping the requested interval. [HA calendar contract](https://developers.home-assistant.io/docs/core/entity/calendar/).

Specific behavior:

- One stable calendar identity per account and selected child; names are labels, never identifiers. Department transfers must not recreate the calendar.
- Give activities stable occurrence UIDs. Do not derive IDs from array positions or event descriptions.
- For a future `fullDayEvent: true` item with a valid appointment date, use that date and the following date as exclusive end. Mark this single-day interpretation as unverified by real captures.
- Do not silently guess overnight duration from `stop <= start`. Such activities were not observed. Reject that item with a sanitized warning until the API's day-spanning representation is known.
- Missing/invalid dates or timezone should not crash the whole calendar or silently become server-local time. Skip malformed items, mark/report degraded results appropriately, and never log their contents.
- Test DST and HA timezone differences with fictional data. Do not hardcode a household timezone.
- Treat text as text. Do not download images, follow event hyperlinks, execute markup, or render raw HTML from descriptions.
- A successful empty result clears events for that fetched window. A failed request is not an empty calendar. Preserve any last successful cache internally, but surface failure/unavailability rather than advertising stale events as a fresh successful result.
- Keep entity properties free of network I/O. Ensure changed fetched windows reach calendar subscribers even when the current event is unchanged; verify the target HA version's event-listener behavior.

Use a small account client, a shared update coordinator, and a focused event parser. Reuse HA's session and `zoneinfo`; avoid a separate web service, database, frontend, or unnecessary third-party packages. Store runtime objects in typed `ConfigEntry.runtime_data`. [HA runtime data](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/runtime-data/).

Use a fixed conservative initial polling interval, such as 15 minutes. This is an implementation choice, not a known Dibber quota. Share authenticated clients and cached requests across selected children.

Fetch normal week/month views with one date-range request per endpoint and distinct nursery/department scope, using `from` and `to`. Do not issue one request per day. For longer requested periods, split into month-sized windows rather than assuming an unlimited server range, then merge and deduplicate occurrences. Fetch historical/future windows on demand, not the entire year every poll. Refresh a small current/near-term window for the entity state. Use bounded concurrency and a bounded in-memory range cache scoped to the account and source; coalesce duplicate requests. Never truncate the user's requested range silently.

Exact API boundary inclusion still needs verification. Keep local filtering to HA's requested interval regardless of server behavior, and test first/last-day inclusion, timezone boundaries, and deduplication across adjacent chunks. Month-range support does not establish closure `to` semantics or a maximum allowed span.

Use `DataUpdateCoordinator` error semantics: temporary network/service failures become retryable failures; confirmed authentication failure starts reauth. Handle 429 with `Retry-After` when available and bounded backoff. On setup, temporary failure should retry rather than destroy configuration. [HA fetching data](https://developers.home-assistant.io/docs/integration_fetching_data/) and [setup failures](https://developers.home-assistant.io/docs/integration_setup_failures/).

## Privacy and credential handling

- Generate synthetic fixtures from the schemas. Never commit a raw export, HAR, `.proxymanlogv2`, screenshot, recording, or real request/response JSON.
- Credentials and required private IDs belong only in the user's HA config-entry storage outside Git. HA storage/backups contain secrets; do not describe them as encrypted unless verified for that installation.
- Persist the latest token pair, integration device ID, stable account identity, and selection data only as needed. Password, access code, and validation hash are transient.
- A separate integration login/session is preferred to borrowing the phone's rotating token pair.
- Use an allowlist for any diagnostics: version, sanitized error category, and aggregate operational counts. Omit tokens, headers, identifiers, names, contact information, URLs containing IDs, event contents, and original bodies.
- Expose only event fields and minimum labels needed for the selected calendars. Do not retain profile, medical, attendance, or unrelated recipient data.
- Add ignore patterns for local credentials and captures. Ignoring files is a second defense, not permission to copy raw data into the working tree.
- If any future YAML example needs real values, keep them in external `secrets.yaml` with generic repository placeholders. UI config flows do not resolve YAML `!secret`.

## Packaging

Use the standard layout, keeping files small and merging trivial helpers when that reduces complexity:

```text
custom_components/dibber/
  __init__.py
  manifest.json
  config_flow.py
  api.py
  coordinator.py
  calendar.py
  const.py
  strings.json
  translations/en.json
  translations/nb.json
tests/
  test_config_flow.py
  test_auth.py
  test_calendar.py
  test_lifecycle.py
README.md
hacs.json
```

Include a versioned manifest with `config_flow: true`, `iot_class: cloud_polling`, and an appropriate integration type such as `service`. Use real target-repository documentation, issue-tracker, and maintainer metadata when available; never invent ownership or publish placeholder URLs. Check the currently supported HA/Python versions before choosing a minimum. [HA manifest](https://contributors.home-assistant.io/core/integrations/manifest/).

Root `hacs.json` should identify Dibber and render the README. Support installation as a HACS custom repository; default-store inclusion and upstream brand submission are separate publishing tasks. Validate the repository with HACS validation and hassfest using current supported action versions. [HACS integration layout](https://www.hacs.dev/docs/publish/integration/) and [HACS metadata](https://www.hacs.dev/docs/publish/start/).

README: purpose, unofficial API status, installation, login/code flow, child selection, calendar use, reauth, privacy, tested HA versions, and explicit limitations for notes, closures, and unverified API behavior. Include ordinary calendar usage without adding an ICS service or custom dashboard.

## Focused tests and acceptance

Use synthetic payloads and mocked HTTP by default. Cover:

1. Full multi-step login, challenge propagation, unsupported auth method, malformed/error responses, and no persistence of password/code/challenge.
2. Profile/account identity validation, duplicate setup, same-account reauth, child selection, and preserving entity identities through reload/department changes.
3. Refresh sends both tokens in JSON, replaces/persists both, survives restart, uses `exp`, and handles unchanged `iat`. Verify a single refresh under concurrent reads, one retry on GET 401, no refresh recursion, transient failures, and rate limits.
4. Discovery field mapping and per-date membership selection with fictional multiple children/departments, missing access, and an empty successful response.
5. Activities, RSVP merging, personal-event recipient filtering, duplicate occurrences, location, daily focus, empty windows, and removal after a successful refetch.
6. Timezone conversion, DST, all-day exclusive ends, overlap at range boundaries, malformed items, cache sharing, and calendar state selection. Assert range-based request counts for week/month views, chunking of longer windows, and no duplicate occurrences at chunk boundaries.
7. Setup retry, reauth-required state, unload cancellation, listener cleanup, and no logout/Firebase/push/RSVP-write calls.
8. Credentials and unrelated personal fields never appear in logs, diagnostics, entity attributes, snapshots, or committed fixtures. Intended calendar text, labels, and stable event/entity IDs are the explicit runtime exceptions; test them using fictional values only.

Completion means the component loads/unloads, its config flow and calendar reads pass meaningful tests, packaging validators pass where runnable, and the user has concrete installation and live-verification steps. Do not claim a live integration works based only on mocks.

Prepare the smallest controlled live checklist for approval:

- Sign in through the integration's own device ID and user-entered credentials/code, with advance notice that login may initiate email verification.
- Verify children/department mapping and a known date's plans, RSVP meeting, and daily focus against the app.
- Verify exact first/last-day inclusion and adjacent-window boundaries; confirm minimal headers and independence from Firebase/state calls. Basic month-range support has already been tested by the user.
- Exercise one approved renewal and HA restart to verify token persistence without logging tokens. Do not deliberately revoke/reset credentials or replay logout.
- Verify closure end dates before enabling closure events.

Do all offline implementation and tests first. If a live check needs approval or a missing payload, leave that limitation explicit and complete the independent work.
