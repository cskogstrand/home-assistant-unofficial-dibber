"""Exercise version selection and publication without contacting GitHub."""

# Commands use synthetic repositories and a fake GitHub CLI.
# ruff: noqa: S603, S607

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts.release_version import release_tag

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("previous", "current", "expected"),
    [
        ("0.1.4", "0.1.4", ""),
        ("0.1.4", "0.1.5", "v0.1.5"),
        ("0.1.9", "0.1.10", "v0.1.10"),
        ("0.9.9", "0.10.0", "v0.10.0"),
        ("0.9.9", "1.0.0", "v1.0.0"),
    ],
)
def test_release_tag(previous, current, expected):
    """Compare numeric versions and skip unchanged manifests."""
    assert release_tag(previous, current) == expected


@pytest.mark.parametrize(
    "current", ["0.1.3", "0.0.9", "v0.1.5", "0.01.5", "0.1", "1.0.0\nbad"]
)
def test_reject_invalid_or_decreased_version(current):
    """Reject malformed versions and attempts to release an older version."""
    with pytest.raises(ValueError, match=r"Unsupported manifest version|must increase"):
        release_tag("0.1.4", current)


def test_version_bump_before_last_commit_in_push(tmp_path):
    """A follow-up commit must not hide a release request earlier in the push."""

    def git(*args):
        return subprocess.check_output(
            [
                "git",
                "-c",
                "user.name=Release Test",
                "-c",
                "user.email=test@example.invalid",
                "-c",
                "commit.gpgsign=false",
                *args,
            ],
            cwd=tmp_path,
            text=True,
        ).strip()

    git("init", "-q")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"version": "0.1.4"}))
    git("add", ".")
    git("commit", "-qm", "initial")
    before = git("rev-parse", "HEAD")
    manifest.write_text(json.dumps({"version": "0.1.5"}))
    git("add", ".")
    git("commit", "-qm", "version bump")
    (tmp_path / "README.md").write_text("Follow-up documentation")
    git("add", ".")
    git("commit", "-qm", "follow-up")
    output = tmp_path / "output"
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/release_version.py"), "manifest.json"],
        cwd=tmp_path,
        env=os.environ | {"BEFORE_SHA": before, "GITHUB_OUTPUT": str(output)},
        check=True,
    )
    assert output.read_text() == "tag=v0.1.5\n"


@pytest.mark.parametrize(("fail_at", "expected_calls"), [(0, 3), (1, 1), (2, 2)])
def test_publish_stops_on_existing_tag_or_failed_upload(
    tmp_path, fail_at, expected_calls
):
    """Run the workflow's actual publish commands with a fake GitHub CLI."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    publish = next(
        step
        for step in workflow["jobs"]["release"]["steps"]
        if step.get("id") == "publish"
    )
    fake_gh = tmp_path / "gh"
    fake_gh.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "log = pathlib.Path(os.environ['GH_TEST_LOG'])\n"
        "with log.open('a') as output: output.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "failed = len(log.read_text().splitlines()) == int(os.environ['FAIL_AT'])\n"
        "sys.exit(1 if failed else 0)\n"
    )
    fake_gh.chmod(0o755)
    log = tmp_path / "calls.jsonl"
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", publish["run"]],
        cwd=tmp_path,
        env=os.environ
        | {
            "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
            "GH_TEST_LOG": str(log),
            "FAIL_AT": str(fail_at),
            "GH_TOKEN": "test-token",
            "GH_REPO": "example/integration",
            "GITHUB_SHA": "a" * 40,
            "RELEASE_TAG": "v0.1.5",
            "RUNNER_TEMP": str(tmp_path),
            "PRERELEASE": "false",
        },
        check=False,
        capture_output=True,
        text=True,
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(calls) == expected_calls
    assert (result.returncode == 0) == (fail_at == 0)
    assert calls[0][:4] == [
        "api",
        "--method",
        "POST",
        "repos/example/integration/git/refs",
    ]
    assert "ref=refs/tags/v0.1.5" in calls[0]
    assert f"sha={'a' * 40}" in calls[0]
    if len(calls) > 1:
        assert calls[1][:3] == ["release", "create", "v0.1.5"]
        assert "--draft" in calls[1]
        assert "--verify-tag" in calls[1]
        assert calls[1][calls[1].index("--target") + 1] == "a" * 40
    if fail_at == 0:
        assert calls[2] == ["release", "edit", "v0.1.5", "--draft=false"]
