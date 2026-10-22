#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

fail() { echo "$*" >&2; exit 1; }
[[ $# -eq 0 ]] || fail "Usage: ./deploy.sh"
[[ $(git branch --show-current) == main ]] || fail "Switch to main first."
[[ -z $(git status --porcelain) ]] || fail "Commit or stash local changes first."
git fetch origin main
git merge-base --is-ancestor FETCH_HEAD HEAD || fail "Pull origin/main before deploying."

deploy_file=custom_components/dibber/manifest.json
deploy_files=("$deploy_file" CHANGELOG.md)
deploy_head=$(git rev-parse HEAD)
deploy_backup=$(mktemp -d)
deploy_committing=false
for i in "${!deploy_files[@]}"; do
  cp "${deploy_files[$i]}" "$deploy_backup/$i.original"
  cp "${deploy_files[$i]}" "$deploy_backup/$i.expected"
done
cleanup() {
  deploy_status=$?
  trap - EXIT
  if [[ $deploy_status -ne 0 ]]; then
    if [[ $deploy_committing == false ]]; then
      # Restore each generated file independently, preserving concurrent edits.
      if [[ $(git rev-parse HEAD) == "$deploy_head" ]]; then
        deploy_restored=true
        for i in "${!deploy_files[@]}"; do
          if cmp -s "${deploy_files[$i]}" "$deploy_backup/$i.expected"; then
            cp "$deploy_backup/$i.original" "${deploy_files[$i]}"
          else
            deploy_restored=false
          fi
        done
        if [[ $deploy_restored == true ]]; then
          echo "Version and changelog restored; nothing committed or pushed." >&2
        else
          echo "Generated files restored where unchanged; concurrent edits left untouched. Nothing committed or pushed." >&2
        fi
      else
        echo "Nothing committed or pushed; concurrent changes were left untouched." >&2
      fi
    else
      echo "Inspect the local release commit and finish the push manually; do not bump again." >&2
    fi
  fi
  rm -rf "$deploy_backup"
  exit "$deploy_status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

deploy_version=$(.venv/bin/python - "$deploy_backup" "$deploy_head" <<'PYTHON'
import json
from pathlib import Path
import re
import subprocess
import sys

backup = Path(sys.argv[1])
revision = sys.argv[2]
original = (backup / "0.original").read_text()
previous = json.loads(original)["version"]
if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", previous):
    raise ValueError("Automatic patch bumps require a stable version")
major, minor, patch = previous.split(".")
version = f"{major}.{minor}.{int(patch) + 1}"
updated, count = re.subn(
    r'("version"\s*:\s*")[^"]*(")',
    lambda match: f"{match[1]}{version}{match[2]}", original,
)
if count != 1:
    raise ValueError("Expected one manifest version field")

changelog = (backup / "1.original").read_text()
heading = "# Changelog\n\n"
if not changelog.startswith(heading):
    raise ValueError("Keep the # Changelog heading followed by a blank line")
sections = re.split(r"^## ", changelog, flags=re.MULTILINE)
if any(section.startswith(f"{version}\n") for section in sections):
    raise ValueError("The release already has a changelog entry; inspect it before deploying")
section = next((section for section in sections if section.startswith(f"{previous}\n")), "")
marker = re.search(r"^<!-- release-source: ([a-f0-9]{40}) -->$", section, re.MULTILINE)
if marker is None:
    raise ValueError(f"Missing release-source marker for version {previous}")
base = marker[1]
subprocess.run(["git", "merge-base", "--is-ancestor", base, revision], check=True)
subjects = subprocess.check_output(
    ["git", "log", "--no-merges", "--format=%s", f"{base}..{revision}"], text=True,
).splitlines()
notes = []
for subject in subjects:
    if not re.fullmatch(r"chore: release (?:Home Assistant )?\d+\.\d+\.\d+", subject):
        notes.append("- " + re.sub(r"[\\\x60*_\[\]<>]", lambda match: "\\" + match[0], subject))
entry = "\n".join(notes) or "- No changes beyond release metadata."
(backup / "0.expected").write_text(updated)
(backup / "1.expected").write_text(
    f"{heading}## {version}\n\n<!-- release-source: {revision} -->\n\n{entry}\n\n"
    + changelog[len(heading):]
)
print(version)
PYTHON
)
for i in "${!deploy_files[@]}"; do
  cp "$deploy_backup/$i.expected" "${deploy_files[$i]}"
done
echo "Testing release $deploy_version"

./scripts/lint
./scripts/test

[[ $(git branch --show-current) == main && $(git rev-parse HEAD) == "$deploy_head" ]] || fail "Branch or HEAD changed during tests."
for i in "${!deploy_files[@]}"; do
  cmp -s "${deploy_files[$i]}" "$deploy_backup/$i.expected" || fail "Release files changed during tests."
done
[[ $(git status --porcelain) == "$(printf ' M %s\n' CHANGELOG.md "$deploy_file")" ]] || fail "Only the manifest and changelog may change during tests."
deploy_committing=true
git commit --only -m "chore: release $deploy_version" -- "${deploy_files[@]}"
deploy_commit=$(git rev-parse HEAD)
[[ $(git rev-parse HEAD^) == "$deploy_head" && -z $(git status --porcelain) ]] || fail "Checkout changed during commit; inspect before pushing."
[[ $(git diff --name-only HEAD^ HEAD) == "$(printf '%s\n' CHANGELOG.md "$deploy_file")" ]] || fail "Commit must change only the manifest and changelog."
for i in "${!deploy_files[@]}"; do
  cmp -s "${deploy_files[$i]}" "$deploy_backup/$i.expected" || fail "Release files changed during commit."
done
git -c push.followTags=false push origin "$deploy_commit:refs/heads/main"
echo "Release $deploy_version pushed. Watch its workflow in GitHub Actions."
