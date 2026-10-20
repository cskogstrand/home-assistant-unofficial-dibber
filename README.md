# Dibber calendars for Home Assistant

An **unofficial**, read-only integration for caregiver accounts, installable as a
HACS custom repository. Each selected child gets an ordinary Home Assistant
calendar combining department activities, personal invitations (pending and
accepted), and daily pedagogical focus.

Dibber's observed mobile API is not a supported public API and can change without
notice. Development targets **Home Assistant 2026.10.2 / Python 3.14.2+**. Synthetic
tests run on HA 2026.10.2 / Python 3.14.3; **no live account has been tested**.
Month-long queries were verified separately by the user who supplied the API brief.

## Installation

1. In HACS, open **Custom repositories** and add
   `https://github.com/cskogstrand/home-assistant-unofficial-dibber` as **Integration**.
2. Download Dibber and restart Home Assistant.
3. Open **Settings → Devices & services → Add integration → Dibber**.
4. Enter your caregiver email and password. **Submitting may initiate an email
   verification step.** Enter your Dibber access code on the next screen.
5. Select the children whose calendars you want.

HACS installation becomes available once this implementation is pushed to the
repository. Implementation does not publish it or create a release. For local
installation, copy `custom_components/dibber` into
`<HA config>/custom_components/dibber` and restart HA.

Only provisioned caregiver `DibberMFA` sign-in is supported; passkeys and educator
accounts are unsupported. Code validity depends on Dibber and your account; the
integration assumes neither single-use nor permanent validity. It creates its
own device UUID and never borrows a phone's token pair.

Use **Configure** options to change child selection. Discovery runs during setup,
sign-in, options, and approximately daily. Department transfers preserve calendar
entity IDs because IDs use account and child identity. Inaccessible children become
unavailable; newly discovered children must be selected in options.

## Calendar use

Use these calendars in HA's Calendar panel, calendar card, automations, or
`calendar.get_events`. Family dashboards can consume standard `summary`,
`description`, `location`, `start`, `end`, and `uid` fields. No custom frontend,
ICS server, or dashboard credentials are needed. Replace this example entity:

```yaml
type: calendar
entities:
  - calendar.dibber_example_child
initial_view: dayGridMonth
```

Calendars advertise no create, edit, delete, or RSVP actions. Personal invitations
route only to children explicitly listed in recipient IDs. Attendee names, photos,
and counts are discarded. Descriptions remain text: the integration neither
interprets markup nor downloads images or follows links.

## Refresh and recovery

The account polls a seven-day near-term window every 15 minutes. Historical and
future ranges are fetched on demand. Source requests and caches are shared across
children. There is one request per distinct source/nursery/department/date window;
RSVP is shared per nursery, with no department query parameter. Up to 64 parsed
source windows are cached for 15 minutes. Range reads are serialized; account HTTP
concurrency is limited to three.

Dates are padded by one day on either end in UTC because source timezones can
differ from HA. Windows longer than 31 dates, including some padded full-month
views, split into overlapping month-sized chunks. This may require two queries per
source for a full month, preventing timezone-boundary omissions without assuming
larger API spans. A week normally needs three source queries per department, with
RSVP shared per nursery. Results are locally filtered, sorted, and deduplicated.

Every poll writes state, which also refreshes HA 2026.10.2 calendar subscriptions
when the current event is unchanged. Successful empty responses clear their window.
Failed reads raise errors and make account calendars unavailable; old caches remain
internal. Skipped malformed/unsupported items produce aggregate sanitized warnings.

Access-token `exp` schedules renewal five minutes early; `iat` is never used for
remaining lifetime. One lock serializes renewal. HA persists both new tokens before
requests use them, without triggering reloads. A rejected GET gets one renewal and
one retry. Authentication rejection starts HA reauthentication. A data GET's 403
reports access denial instead of a password failure.

Rate limits honor `Retry-After` seconds or HTTP dates, bounded to 1–3600 seconds.
Transport/service failures retry. A lost, malformed, cancelled, or failed renewal
response may mean Dibber already rotated the pair. The integration persists an
uncertainty marker and **does not replay that renewal**, including after restart.
Use the entry's **Reconfigure** menu to explicitly sign in again. Polling never
initiates login/email or resets passwords. Unload, restart, and removal never log out.

## Privacy

HA config-entry storage contains tokens, device UUID, account ID, selected child
IDs, and a recovery marker when needed. Backups can contain these secrets; no
encryption claim is made. Passwords, codes, and challenges remain transient and
are cleared when flows finish or are cancelled.

Only child labels, memberships, routing IDs, and calendar fields remain in memory.
Raw profiles, medical data, identity numbers, attendance, photos, and unrelated
recipient information are discarded. No diagnostics endpoint is provided.
Integration logs contain fixed error categories and aggregate counts. HA itself
may log entity labels/events at debug level; review logs before sharing them.

Never put real credentials, captures, HAR files, exports, event text, identifiers,
or screenshots in issues, fixtures, or commits. Ignore patterns are a second
protection, not permission to copy personal data into the repository.

## Known limitations and assumptions

- Membership end dates are assumed **inclusive**. All matching memberships are
  fetched and duplicates merged. Verify this at a department transfer.
- The profile `role` must be a nonempty string; role enum values are not guessed.
  The caregiver children endpoint authorizes calendar access.
- Activities use `appointment.date` and its IANA timezone, independently of the
  grouping date and HA timezone. All activity types render.
- Unobserved `fullDayEvent: true` items are assumed to last one date with an
  exclusive next-day end. Overnight/equal clock ranges and nonexistent/ambiguous
  DST clock times are skipped rather than guessed.
- Accepted RSVP objects use pending's schema, tested synthetically. Unknown RSVP
  audiences and missing personal recipients are skipped. Declined/cancelled buckets,
  pagination, exact range boundaries, and rate limits remain unverified.
- Daily focus is all-day and labeled **Daily focus** or **Dagens fokus** for Norwegian
  Bokmål. `learningFriends` is omitted because its meaning is unverified.
- Notes are omitted because only empty responses were observed.
- **Closures are disabled.** Observed endpoint:
  `GET /api/ecdc/{ecdc_id}/schedule/closure-days?from=YYYY-MM-DD&to=YYYY-MM-DD`.
  Fields: `id`, `title`, `type`, `from`, `to`, `holidayDays`. Responses can exceed
  requested bounds. Verify whether `to` is inclusive before enabling; never derive
  duration from `holidayDays`. Null `to` could mean one day, but is not enabled.
- Minimal headers, an independent device UUID, multi-child operation, long-term
  renewal, and password-change/revocation behavior need live verification. Requests
  use normal TLS, a fixed Dibber origin, no redirects, and no copied device
  fingerprints, app tracing, Firebase registration, or app-state calls.

Messaging, attendance changes, RSVP submission, photos, push registration, password
resets, passkeys, and event editing are outside scope.

## Development and verification

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements_dev.txt
.venv/bin/python -m pytest
.venv/bin/ruff check .
.venv/bin/ruff format . --check
```

Tests use synthetic payloads and blocked network access. They exercise HA's real
config-flow manager, config-entry persistence, setup/unload/reload, entity registry,
and calendar subscriptions, plus mocked HTTP, parser, and range-cache behavior.
They do not establish live Dibber compatibility. CI includes pytest, Ruff,
hassfest, and HACS repository validation. Brand submission and default-store
inclusion remain separate publishing tasks.

See [DIBBER_HACS_PROMPT.md](DIBBER_HACS_PROMPT.md) for the evidence and scope,
[HA's calendar contract](https://contributors.home-assistant.io/core/entities/calendar/),
and [HA reauthentication](https://contributors.home-assistant.io/core/integrations/reauth-reconfigure/).

## Controlled live verification — explicit approval required

Only with account-owner approval, outside this repository:

1. Sign in with the integration's own device UUID. Give advance notice that login
   can initiate email verification.
2. Compare child/department discovery and a known day's activities, personal RSVP
   meeting, and daily focus against the app.
3. Compare first/last-day and adjacent-window boundaries, including timezone
   differences. Confirm minimal headers and independence from Firebase/state calls.
   Basic month-range support was already verified by the user.
4. Allow one renewal and restart HA. Verify persisted tokens work without printing
   them. Do not revoke/reset credentials or call logout.
5. Compare one single-day and one multi-day closure before proposing a separate
   change to enable closure events.
