# Dibber calendars for Home Assistant

[![Version](https://img.shields.io/github/manifest-json/v/cskogstrand/home-assistant-unofficial-dibber?filename=custom_components%2Fdibber%2Fmanifest.json)](https://github.com/cskogstrand/home-assistant-unofficial-dibber/releases)
[![Downloads](https://img.shields.io/github/downloads/cskogstrand/home-assistant-unofficial-dibber/total)](https://github.com/cskogstrand/home-assistant-unofficial-dibber/releases)
[![HACS custom repository](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/cskogstrand/home-assistant-unofficial-dibber#installation)
[![Home Assistant minimum version](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2Fcskogstrand%2Fhome-assistant-unofficial-dibber%2Fmain%2Fhacs.json&query=%24.homeassistant&prefix=%E2%89%A5%20&label=Home%20Assistant&color=18bcf2)](https://github.com/cskogstrand/home-assistant-unofficial-dibber/blob/main/hacs.json)

[![Tests](https://github.com/cskogstrand/home-assistant-unofficial-dibber/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/cskogstrand/home-assistant-unofficial-dibber/actions/workflows/test.yml)
[![Lint](https://github.com/cskogstrand/home-assistant-unofficial-dibber/actions/workflows/lint.yml/badge.svg?branch=main)](https://github.com/cskogstrand/home-assistant-unofficial-dibber/actions/workflows/lint.yml)
[![HACS and Hassfest validation](https://github.com/cskogstrand/home-assistant-unofficial-dibber/actions/workflows/validate.yml/badge.svg?branch=main)](https://github.com/cskogstrand/home-assistant-unofficial-dibber/actions/workflows/validate.yml)
[![License](https://img.shields.io/github/license/cskogstrand/home-assistant-unofficial-dibber)](LICENSE)

An **unofficial**, read-only integration for caregiver accounts, installable as a
HACS custom repository. Each selected child gets ordinary Home Assistant calendars:

| Calendar | Norwegian name | Contents |
|---|---|---|
| Activities | Aktiviteter | Department activities and personal invitations whose `activityType` is not `Meeting` |
| Meetings | Møter | `Meeting` events from department activities and personal invitations |
| Pedagogy | Pedagogikk | Daily pedagogical focus as all-day events |
| Closure days | Stengedager | Planning days, nursery closures, and public holidays |
| Invitations | Invitasjoner | Pending and accepted personal invitations from the RSVP endpoint |
| Opening hours | Åpningstider | Regular weekly nursery opening intervals |

Dibber's observed mobile API is not a supported public API and can change without
notice. Development targets **Home Assistant 2026.10.2 / Python 3.14.2+**. Synthetic
tests run on HA 2026.10.2 / Python 3.14.3; **no live account has been tested**.
Month-long queries were verified separately by the user who supplied the API brief.

## Installation

[![Open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=cskogstrand&repository=home-assistant-unofficial-dibber&category=integration)

HACS downloads require a published release. Before the first release, use the
manual source installation below.

With HACS installed, use the button above or add the repository manually:

1. In HACS, open **Custom repositories** and add
   `https://github.com/cskogstrand/home-assistant-unofficial-dibber` as **Integration**.
2. Download Dibber and restart Home Assistant.
3. Open **Settings → Devices & services → Add integration → Dibber**.
4. Enter your caregiver email and password. **Submitting may initiate an email
   verification step.** Enter your Dibber access code on the next screen.
5. Select the children whose calendars you want.

For manual installation, download `dibber.zip` from the
[latest release](https://github.com/cskogstrand/home-assistant-unofficial-dibber/releases/latest)
and extract its contents into `<HA config>/custom_components/dibber`, then restart
Home Assistant. For local development or before the first release, copy the
repository's `custom_components/dibber` directory to the same location instead.

Only provisioned caregiver `DibberMFA` sign-in is supported; passkeys and educator
accounts are unsupported. Code validity depends on Dibber and your account; the
integration assumes neither single-use nor permanent validity. It creates its
own device UUID and never borrows a phone's token pair.

Use **Configure** options to change child selection. Discovery runs during setup,
sign-in, options, and approximately daily. Department transfers preserve calendar
entity IDs because registry identities use account and child IDs. Inaccessible
children become unavailable; newly discovered children must be selected in options.

## Branding

The integration bundles Dibber's official icon and wordmark from
[dibber.app](https://www.dibber.app/) in `custom_components/dibber/brand/`.
Home Assistant loads these automatically after installation and restart using its
[local brand image support](https://contributors.home-assistant.io/core/integrations/brands/).
If an old placeholder remains, refresh the browser or companion app.

Sources: [icon](https://static.wixstatic.com/media/fc9d19_6f99730adbc14c46b6cd682d986a99ec~mv2.png)
and [wordmark](https://static.wixstatic.com/media/fc9d19_2c67357a7d7b4471a016afc6fd09885e~mv2.png),
retrieved on 2026-10-20. The website's image CDN trims transparent margins and
provides standard and `@2x` PNG sizes. The artwork retains its original colors;
high-resolution versions are upscaled from the available source images.
The Dibber artwork and trademarks belong to their respective owners and are not
covered by this repository's MIT license. This integration remains unofficial.

## Calendar use

Use these calendars in HA's Calendar panel, calendar card, automations, or
`calendar.get_events`. Family dashboards can consume standard `summary`,
`description`, `location`, `start`, `end`, and `uid` fields. No custom frontend,
ICS server, or dashboard credentials are needed. Replace these example entities:

```yaml
type: calendar
entities:
  - calendar.dibber_example_activities
  - calendar.dibber_example_meetings
  - calendar.dibber_example_pedagogy
  - calendar.dibber_example_closure_days
  - calendar.dibber_example_opening_hours
initial_view: dayGridMonth
```

Restart Home Assistant after updating the integration to add the new calendars
for your selected children. Closure calendars use each child's nursery memberships;
no nursery ID or fixed date range needs to be configured. Closure titles become
event summaries and the API's closure type becomes the description.

Calendar names and default entity IDs use the child's first name, such as
**Dibber Example Activities** (`calendar.dibber_example_activities`), with
**Aktiviteter** in Norwegian. Existing default IDs are renamed on restart;
manually chosen names and IDs are preserved. Update dashboards and automations
that reference the old IDs. Children with the same first name receive numbered
IDs from Home Assistant.

Use `calendar.dibber_example_pedagogy` for daily focus and
`calendar.dibber_example_meetings` for meetings. These events have moved out of
Activities; add the new calendars to dashboards and automations that previously
used Activities for everything. Existing entity and event IDs are preserved.
The new calendars reuse the existing source requests and cache.

Use `calendar.dibber_example_invitations` to view invitations separately.
Invitations also appear in Meetings when their `activityType` is exactly `Meeting`,
or in Activities otherwise, so displaying Invitations alongside those calendars
may show duplicates. Pending and accepted invitations are both included; their
response status is not displayed. Invitations retain their title, description,
location, and timezone-aware start/end times.

Use `calendar.dibber_example_opening_hours` for the regular weekly schedule.
Each supplied weekday becomes a timed event, from `opens` to `closes`, on the
requested dates. Missing weekdays produce no events. The API supplies no timezone,
so **Home Assistant's configured timezone must match the nursery's timezone**.
The current schedule is repeated into past and future calendar views; the endpoint
does not supply historical schedules or effective dates. Closure days are shown
on their own calendar and do not remove regular opening-hours events.

Calendars advertise no create, edit, delete, or RSVP actions. Personal invitations
route only to children explicitly listed in recipient IDs. Attendee names, photos,
and counts are discarded. Descriptions remain text: the integration neither
interprets markup nor downloads images or follows links.

## Refresh and recovery

The account polls a seven-day near-term window every 15 minutes. Historical and
future ranges are fetched on demand. Source requests and caches are shared across
children. There is one request per distinct source/nursery/department/date window;
RSVP and closures are shared per nursery, with no department query parameter.
Up to 64 parsed source windows are cached for 15 minutes. Range reads are
serialized; account HTTP concurrency is limited to three.
Opening hours use one additional GET per nursery, shared across children and date
windows and cached for 24 hours. Up to 64 validated weekly schedules are retained.
Recurring events are generated locally as needed, without one request per day.

Dates are padded by one day on either end in UTC because source timezones can
differ from HA. Windows longer than 31 dates, including some padded full-month
views, split into overlapping month-sized chunks. This may require two queries per
source for a full month, preventing timezone-boundary omissions without assuming
larger API spans. A week normally needs two source queries per department plus
two per nursery (RSVP and closures). Results are locally filtered, sorted, and
deduplicated, including closure responses that exceed the requested date range.

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
- Opening hours use the supplied `dayOfWeek` / `openingHours.opens` /
  `openingHours.closes` schema. Same-day `HH:MM` intervals are supported.
  Missing/null intervals, unknown weekday names, overnight/equal clock ranges,
  and nonexistent/ambiguous DST times are skipped with sanitized warnings.
  Conflicting intervals for one weekday are rejected. Nursery timezone and
  holiday exceptions are not supplied by this endpoint.
- Closure calendars use the observed endpoint:
  `GET /api/ecdc/{ecdc_id}/schedule/closure-days?from=YYYY-MM-DD&to=YYYY-MM-DD`.
  A null `to` is assumed to mean one day; a dated `to` is assumed **inclusive**
  and converted to HA's exclusive next-day end. These assumptions need live
  comparison with the app. `holidayDays` never determines the event duration.
  A closure overlapping a child's membership appears as its full date range.
- Minimal headers, an independent device UUID, multi-child operation, long-term
  renewal, and password-change/revocation behavior need live verification. Requests
  use normal TLS, a fixed Dibber origin, no redirects, and no copied device
  fingerprints, app tracing, Firebase registration, or app-state calls.

Messaging, attendance changes, RSVP submission, photos, push registration, password
resets, passkeys, and event editing are outside scope.

## Development and verification

Run these commands from the repository root. Docker Compose runs a separate
Home Assistant instance; Python 3.14.2+ in the 3.14 series runs local checks.

```sh
./scripts/setup
./scripts/lint
./scripts/test
docker compose config --quiet
./scripts/develop
```

Open [Home Assistant on localhost:8125](http://localhost:8125) and complete HA
onboarding. Adding Dibber and signing in is the separate live-verification step
described below. The container uses HA 2026.10.2, stores its configuration in this
project's ignored `config/` files, and mounts the integration read-only. Port 8125
allows the Onics project's instance on port 8124 to run alongside it.

After editing Python code or translations, run
`docker compose restart homeassistant`. Close any old setup dialog before starting
**Add integration** again. Stop with Ctrl-C; `docker compose down` removes the
container and preserves the configuration. Use a development configuration, never
a production HA folder. Run Docker commands on the host with Docker Compose
available; the VS Code devcontainer provides Python tooling only.

`scripts/lint` checks without editing files. To format, run
`.venv/bin/ruff format .`. `scripts/test` forwards pytest arguments, for example
`./scripts/test tests/test_auth.py`.

Tests use synthetic payloads and blocked network access. They exercise HA's real
config-flow manager, config-entry persistence, setup/unload/reload, entity registry,
and calendar subscriptions, plus mocked HTTP, parser, and range-cache behavior.
They do not establish live Dibber compatibility. CI includes pytest, Ruff,
hassfest, and HACS repository validation. Brand images ship with the integration;
HACS default-store inclusion remains a separate publishing task.

See [DIBBER_HACS_PROMPT.md](DIBBER_HACS_PROMPT.md) for the evidence and scope,
[HA's calendar contract](https://contributors.home-assistant.io/core/entities/calendar/),
and [HA reauthentication](https://contributors.home-assistant.io/core/integrations/reauth-reconfigure/).

## Releases

The version in `custom_components/dibber/manifest.json` is the source of truth.
Use stable `MAJOR.MINOR.PATCH` versions and matching `vMAJOR.MINOR.PATCH` Git tags.
The README version badge follows the manifest on `main`; the download badge counts
release-asset downloads and becomes useful after the first published release.

To prepare a release:

1. Update the manifest version and commit all changes for the release. The first
   release can use the current version, `0.1.3`.
2. Push the commit and its matching tag, for example:

   ```sh
   git push origin main
   git tag -a v0.1.3 -m "Dibber v0.1.3"
   git push origin v0.1.3
   ```

3. The [Release workflow](https://github.com/cskogstrand/home-assistant-unofficial-dibber/actions/workflows/release.yml)
   runs tests, lint, Hassfest, and HACS validation for the tag. It builds `dibber.zip`
   from the committed integration directory, verifies its version and contents,
   and creates a **draft** GitHub release with generated release notes.
4. Review the notes and attached ZIP in
   [GitHub Releases](https://github.com/cskogstrand/home-assistant-unofficial-dibber/releases),
   then explicitly publish the draft. Only published releases are offered to HACS
   users; pushing a tag alone is insufficient.

The ZIP contains the integration files at its root, including translations and
branding. HACS installs it using `zip_release` and `filename` in `hacs.json`.
The default branch is hidden in HACS because it has no release ZIP; install
development snapshots manually from source.
Untracked local files, Home Assistant configuration, tests, and development tools
are excluded. No workflow commits version changes or publishes a release on its own.
HACS default-store inclusion remains a separate process.

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
5. Compare one single-day and one multi-day closure with the app to verify the
   documented null and inclusive end-date assumptions.
6. Compare opening hours with the app and confirm HA's timezone matches the nursery.
