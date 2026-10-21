<img src="https://raw.githubusercontent.com/cskogstrand/home-assistant-unofficial-dibber/main/custom_components/dibber/brand/logo.png" alt="Dibber" width="240">

# Dibber for Home Assistant

Unofficial, read-only calendars for each child: **activities, meetings, daily focus,
closures, invitations and opening hours**.

Requires **Home Assistant 2026.10.2+**, **HACS 2.0.5+** and a Dibber caregiver account.
Passkeys and educator accounts are not supported.

## Installation

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=cskogstrand&repository=home-assistant-unofficial-dibber&category=integration)

1. Open the link above, or add this repository in HACS as a custom **Integration**.
2. Download **Dibber** and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → Dibber**.
4. Sign in with your caregiver email, password and Dibber access code. Login may trigger email verification.
5. Select your children.

Use the calendars in Home Assistant's Calendar panel, dashboard cards or automations.
Change children under **Configure**. If sign-in breaks, use **Reconfigure**.

## Know this

- Dibber's unofficial API can change. Tests use mock data; live-account compatibility is unverified.
- Invitations also appear in Activities or Meetings. Showing both can produce duplicates.
- Opening hours repeat the regular schedule, including closure days. Set Home Assistant to the nursery's timezone.
- Notes are not included. Closure end dates are assumed inclusive and need live verification.
- Tokens are stored in Home Assistant and may appear in backups. Passwords and access codes are not saved.

[Report a bug](https://github.com/cskogstrand/home-assistant-unofficial-dibber/issues) — keep credentials and children's data out of reports.

Not affiliated with Dibber. Dibber owns its branding; it is excluded from this project's [MIT license](LICENSE).
