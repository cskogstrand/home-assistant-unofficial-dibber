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

## Deploying from main

After `./scripts/setup`, commit your code and changelog changes, then run
`./deploy.sh` from a clean `main` checkout. The script operates only on this
repository and patch-bumps `custom_components/dibber/manifest.json` without reformatting it. It fetches
`origin/main` and refuses behind or diverged branches. Committed local changes
ahead of `origin/main` are included in the push.

It runs `./scripts/lint` and `./scripts/test` before committing only the manifest and pushing `main`.
**If any check fails, the original version is restored and nothing is committed
or pushed.** Concurrent edits are preserved. If a commit or push fails after
testing, inspect the local release commit and finish the push manually; do not
run the script again and bump twice. It never force-pushes or resets your work.

Automatic patch bumps require stable `MAJOR.MINOR.PATCH` versions. Use the manual
release process for minor, major or prerelease changes. GitHub's release workflow
still performs Hassfest and HACS validation before publishing.
