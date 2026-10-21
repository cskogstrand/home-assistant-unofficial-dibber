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
deploy_head=$(git rev-parse HEAD)
deploy_backup=$(mktemp)
deploy_expected=$(mktemp)
deploy_committing=false
cp "$deploy_file" "$deploy_backup"
cleanup() {
  deploy_status=$?
  trap - EXIT
  if [[ $deploy_status -ne 0 ]]; then
    if [[ $deploy_committing == false ]]; then
      # Restore our bump without overwriting a concurrent edit or commit.
      if [[ $(git rev-parse HEAD) == "$deploy_head" ]] && cmp -s "$deploy_file" "$deploy_expected"; then
        cp "$deploy_backup" "$deploy_file"
        echo "Version restored; nothing committed or pushed." >&2
      else
        echo "Nothing committed or pushed; concurrent changes were left untouched." >&2
      fi
    else
      echo "Inspect the local release commit and finish the push manually; do not bump again." >&2
    fi
  fi
  rm -f "$deploy_backup" "$deploy_expected"
  exit "$deploy_status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

deploy_version=$(.venv/bin/python - "$deploy_backup" "$deploy_expected" <<'PYTHON'
import json
from pathlib import Path
import re
import sys

original = Path(sys.argv[1]).read_text()
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
Path(sys.argv[2]).write_text(updated)
print(version)
PYTHON
)
cp "$deploy_expected" "$deploy_file"
echo "Testing release $deploy_version"

./scripts/lint
./scripts/test

[[ $(git branch --show-current) == main && $(git rev-parse HEAD) == "$deploy_head" ]] || fail "Branch or HEAD changed during tests."
cmp -s "$deploy_file" "$deploy_expected" || fail "Version changed during tests."
[[ $(git status --porcelain) == " M $deploy_file" ]] || fail "Only the version file may change during tests."
deploy_committing=true
git commit --only -m "chore: release $deploy_version" -- "$deploy_file"
deploy_commit=$(git rev-parse HEAD)
[[ $(git rev-parse HEAD^) == "$deploy_head" && -z $(git status --porcelain) ]] || fail "Checkout changed during commit; inspect before pushing."
[[ $(git diff --name-only HEAD^ HEAD) == "$deploy_file" ]] || fail "Commit must change only the version file."
cmp -s "$deploy_file" "$deploy_expected" || fail "Version changed during commit."
git -c push.followTags=false push origin "$deploy_commit:refs/heads/main"
echo "Release $deploy_version pushed. Watch its workflow in GitHub Actions."
