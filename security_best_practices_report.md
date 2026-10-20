# Dibber security review

Reviewed on 2026-10-20. No real credentials or high-severity security issues were
identified in the reviewed repository. One preventive gap in Git ignore rules
was corrected. This is a review of the current files and locally available Git
history, not a guarantee about future commits or deployed systems.

## Credential scan

- Gitleaks 8.30.1 scanned the complete Git index (54 files), the commit-eligible
  working files, and all locally reachable history (one commit). All scans
  reported no leaks. The repository is not shallow.
- The scanner was downloaded from its official release and checked against the
  published SHA-256 checksum. Scans ran locally with redacted output; repository
  contents and credentials were not sent to a scanning service.
- A supplementary pattern scan checked private keys, provider tokens, JWTs,
  credential literals, and passwords embedded in URLs. Matches were fictional
  examples, synthetic test values, and interface labels.
- No credential files, raw traffic captures, private keys, runtime databases,
  or Home Assistant storage files were tracked. Test JWTs are generated from
  fictional payloads with a synthetic signature.

## Findings

### SEC-01 — Low — Runtime secrets outside config could be staged

**Status: corrected in the working tree.** The original rules protected
`config/`, environment files, and common captures, but allowed `.storage/`,
Home Assistant logs/databases, backup archives, and private-key files elsewhere
in the repository. Those files can contain renewable tokens, private keys, or
family data. There was no evidence that these files had been committed.

The added exclusions in [.gitignore](.gitignore#L20) cover these paths and
common key/archive formats. [CONTRIBUTING.md](CONTRIBUTING.md#L10) now documents
a redacted scan of staged changes and manual diff review before each commit.
Ignore rules and a documented command do not install an automatic commit hook;
force-adding ignored files or placing secrets in ordinary source files still
requires review and scanning.

## Security controls reviewed

- [api.py](custom_components/dibber/api.py#L207) uses a fixed HTTPS origin,
  ordinary certificate validation, disabled redirects, a request timeout,
  and sanitized HTTP failures.
- [config_flow.py](custom_components/dibber/config_flow.py#L98) clears transient
  login state when a flow ends. Passwords and verification codes are not added
  to config-entry storage. Reauthentication checks server-provided account
  identity before replacing an existing entry.
- [api.py](custom_components/dibber/api.py#L86) omits token values from object
  representations; renewal is serialized and replaces the pair together.
- [events.py](custom_components/dibber/events.py#L63) rejects personal events
  without valid recipients; routing filters them to matching selected children.
  The explicit parser log contains only an aggregate count.
- GitHub workflows pin actions to commit hashes, declare empty default
  permissions, and disable persisted checkout credentials.

## Validation and limits

All 66 existing tests passed, including credential-storage, error-redaction,
HTTP transport, wrong-account reauthentication, and private-event routing checks.
Ruff lint and formatting checks passed. Git ignore checks verified sensitive
paths are excluded while integration code, fixtures, and the example
configuration remain eligible for tracking.

Live Dibber sign-in, token revocation, deployed Home Assistant permissions,
remote GitHub security settings, and dependency vulnerability databases were
not tested. No Home Assistant-specific reference exists in the security skill;
this review used the implementation and its tests.

Home Assistant must persist the renewable token pair. Its private config-entry
storage and backups therefore remain sensitive, as documented in
[README.md](README.md#L91). Calendar names and event text are intentionally exposed
to Home Assistant and may appear in its recorder, dashboards, backups, or debug
logs. Protect that installation and review exports before sharing them.
