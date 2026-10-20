# Contributing

Use Python 3.14.2+ and the commands in [README.md](README.md). Keep changes small,
use synthetic fixtures, and run pytest, Ruff lint, and Ruff format before a PR.

Do not share captures, tokens, credentials, names, child identifiers, event text,
or unreviewed logs. Describe API changes with fictional examples. Require evidence
for new endpoints and assumptions, especially writes, personal invitation audiences,
closure ends, and unknown note payloads.

Before every commit, stage the intended changes and scan them with
[Gitleaks](https://github.com/gitleaks/gitleaks):

```sh
gitleaks git --staged --redact --no-banner
```

Review `git diff --cached` as well: scanners cannot recognize every credential or
personal detail. Ignore rules do not protect files already tracked by Git.

Report bugs through this repository's GitHub issue templates, including HA and
integration versions, a sanitized error category, and reproduction steps.
Contributions use the repository's MIT license.
