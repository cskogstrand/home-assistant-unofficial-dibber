"""Select a release only when the manifest version increases across a push."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path


def version_parts(version: str) -> tuple[int, ...]:
    """Parse a canonical stable release version for numeric comparison."""
    match = re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version)
    if match is None:
        message = f"Unsupported manifest version: {version}"
        raise ValueError(message)
    return tuple(int(part) for part in match.groups())


def release_tag(previous: str, current: str) -> str:
    """Return no tag for ordinary commits and reject version rollbacks."""
    if current == previous:
        return ""
    if version_parts(current) <= version_parts(previous):
        raise ValueError("The manifest version must increase for a release")
    return f"v{current}"


def main() -> None:
    """Compare the push's starting revision with the checked-out commit."""
    manifest = Path(sys.argv[1])
    before = os.environ["BEFORE_SHA"]
    previous = json.loads(
        subprocess.check_output(  # noqa: S603 - GitHub supplies the revision; no shell.
            ["git", "show", f"{before}:{manifest.as_posix()}"],  # noqa: S607 - Runner Git.
        )
    )["version"]
    current = json.loads(manifest.read_text())["version"]
    tag = release_tag(previous, current)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write(f"tag={tag}\n")
    print(  # noqa: T201 - CLI output.
        f"Release requested: {tag}" if tag else "No version change; no release"
    )


if __name__ == "__main__":
    main()
