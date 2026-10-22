"""Exercise deploy.sh against local Git repositories, without publishing."""

# Commands use synthetic repositories and fake checks.
# ruff: noqa: S603, S607

import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "custom_components/dibber/manifest.json"
CHECKS = ["scripts/lint", "scripts/test"]
if "eva" in MANIFEST:
    CHECKS.append("docker compose config --quiet")
ORIGINAL = '{\n  "domain": "example",\n  "version": "1.2.9",\n  "requirements": []\n}\n'


@pytest.fixture
def deployment(tmp_path):
    """Use real version editing and Git, with fake lint/test commands."""
    repo = tmp_path / "repo"
    remote = tmp_path / "origin.git"
    log = tmp_path / "calls.log"
    env = os.environ | {
        "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "DEPLOY_TEST_LOG": str(log),
        "GIT_AUTHOR_NAME": "Release Test",
        "GIT_COMMITTER_NAME": "Release Test",
        "GIT_AUTHOR_EMAIL": "release@example.invalid",
        "GIT_COMMITTER_EMAIL": "release@example.invalid",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "commit.gpgsign",
        "GIT_CONFIG_VALUE_0": "false",
    }

    def write(path, content, executable=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        if executable:
            path.chmod(0o755)

    def git(*args, cwd=repo):
        return subprocess.check_output(
            ["git", *args], cwd=cwd, env=env, text=True, stderr=subprocess.PIPE
        ).strip()

    def recorder(command):
        return (
            f"#!{sys.executable}\n"
            "import os, pathlib, sys\n"
            f"call = {command!r} + "
            "(' ' + ' '.join(sys.argv[1:]) if len(sys.argv) > 1 else '')\n"
            "with pathlib.Path(os.environ['DEPLOY_TEST_LOG']).open('a') as output:\n"
            "    output.write(call + '\\n')\n"
            "if os.environ.get('DEPLOY_TEST_DIRTY') == call:\n"
            "    pathlib.Path('unrelated.txt').write_text('concurrent work\\n')\n"
            "if os.environ.get('DEPLOY_TEST_EDIT') and call == 'scripts/test':\n"
            "    path = pathlib.Path(os.environ['DEPLOY_TEST_EDIT'])\n"
            "    path.write_text(path.read_text() + 'Concurrent edit\\n')\n"
            "if os.environ.get('DEPLOY_TEST_FAIL') == call: sys.exit(1)\n"
        )

    write(repo / MANIFEST, ORIGINAL)
    write(repo / ".gitignore", ".venv/\n")
    write(log, "")
    shutil.copy2(ROOT / "deploy.sh", repo / "deploy.sh")
    for command in ("lint", "test"):
        write(repo / "scripts" / command, recorder(f"scripts/{command}"), True)
    write(tmp_path / "bin/docker", recorder("docker"), True)
    (repo / ".venv/bin").mkdir(parents=True)
    (repo / ".venv/bin/python").symlink_to(sys.executable)
    git("init", "--bare", "--initial-branch=main", str(remote), cwd=tmp_path)
    git("init", "--initial-branch=main")
    git("add", ".")
    git("commit", "-m", "test: initial files")
    base = git("rev-parse", "HEAD")
    changelog = (
        "# Changelog\n\n## 1.2.9\n\n"
        f"<!-- release-source: {base} -->\n\n- Earlier changes.\n"
    )
    write(repo / "CHANGELOG.md", changelog)
    git("add", "CHANGELOG.md")
    git("commit", "-m", "chore: release 1.2.9")
    git("remote", "add", "origin", str(remote))
    git("push", "-u", "origin", "main")
    write(remote / "hooks/pre-receive", recorder("push"), True)

    def run():
        return subprocess.run(
            [str(repo / "deploy.sh")],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    return SimpleNamespace(
        repo=repo,
        remote=remote,
        log=log,
        env=env,
        git=git,
        run=run,
        head=git("rev-parse", "HEAD"),
        changelog=changelog,
    )


def assert_unchanged(deployment):
    """Failures before committing restore the exact original release files."""
    assert (deployment.repo / MANIFEST).read_text() == ORIGINAL
    assert (deployment.repo / "CHANGELOG.md").read_text() == deployment.changelog
    assert deployment.git("rev-parse", "HEAD") == deployment.head
    assert deployment.git("rev-parse", "main", cwd=deployment.remote) == deployment.head
    assert "push" not in deployment.log.read_text().splitlines()


def test_deploy_checks_before_pushing_manifest_and_changelog(deployment):
    """Publish notes for actual changes, preserving history and version formatting."""
    deployment.git("checkout", "-b", "feature")
    (deployment.repo / "state.txt").write_text("state\n")
    deployment.git("add", "state.txt")
    deployment.git("commit", "-m", "fix: show [state] <safely>")
    deployment.git("checkout", "main")
    (deployment.repo / "controls.txt").write_text("controls\n")
    deployment.git("add", "controls.txt")
    deployment.git("commit", "-m", "feat: improve controls")
    deployment.git("merge", "--no-ff", "feature", "-m", "Merge feature")
    source = deployment.git("rev-parse", "HEAD")
    result = deployment.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert (deployment.repo / MANIFEST).read_text() == ORIGINAL.replace(
        "1.2.9", "1.2.10"
    )
    assert deployment.log.read_text().splitlines() == [*CHECKS, "push"]
    assert deployment.git("diff", "--name-only", "HEAD^", "HEAD") == (
        f"CHANGELOG.md\n{MANIFEST}"
    )
    changelog = (deployment.repo / "CHANGELOG.md").read_text()
    assert changelog.startswith(
        f"# Changelog\n\n## 1.2.10\n\n<!-- release-source: {source} -->\n"
    )
    assert "- feat: improve controls\n" in changelog
    assert r"- fix: show \[state\] \<safely\>" in changelog
    assert "Merge feature" not in changelog
    assert "chore: release" not in changelog
    assert "test: initial files" not in changelog
    assert changelog.endswith(deployment.changelog.removeprefix("# Changelog\n\n"))
    assert deployment.git("status", "--porcelain") == ""
    assert deployment.git("rev-parse", "HEAD") == deployment.git(
        "rev-parse", "main", cwd=deployment.remote
    )

    result = deployment.run()
    assert result.returncode == 0, result.stdout + result.stderr
    following = (deployment.repo / "CHANGELOG.md").read_text()
    new_entry = following.split("## 1.2.10\n")[0]
    assert "## 1.2.11\n" in new_entry
    assert "- No changes beyond release metadata." in new_entry
    assert "feat: improve controls" not in new_entry
    assert following.endswith(changelog.removeprefix("# Changelog\n\n"))


@pytest.mark.parametrize("failed_check", CHECKS)
def test_failed_check_restores_version_and_changelog(deployment, failed_check):
    """Failed checks restore both files without committing or pushing."""
    deployment.env["DEPLOY_TEST_FAIL"] = failed_check
    result = deployment.run()
    assert result.returncode != 0
    assert "Version and changelog restored" in result.stderr
    assert_unchanged(deployment)


def test_dirty_checkout_is_untouched(deployment):
    """Refuse to stage unrelated local work."""
    (deployment.repo / "unrelated.txt").write_text("local work\n")
    assert deployment.run().returncode != 0
    assert_unchanged(deployment)
    assert (deployment.repo / "unrelated.txt").read_text() == "local work\n"


def test_require_main(deployment):
    """Do not edit a development or feature branch."""
    deployment.git("checkout", "-b", "develop")
    assert deployment.run().returncode != 0
    assert_unchanged(deployment)


def test_remote_ahead_stops_before_bump(deployment):
    """Require pulling newer main instead of overwriting it."""
    (deployment.repo / "remote.txt").write_text("remote work\n")
    deployment.git("add", "remote.txt")
    deployment.git("commit", "-m", "test: remote change")
    deployment.git("push", "origin", "main")
    deployment.git("reset", "--hard", deployment.head)
    deployment.log.write_text("")
    assert deployment.run().returncode != 0
    assert deployment.log.read_text() == ""
    assert (deployment.repo / MANIFEST).read_text() == ORIGINAL


def test_concurrent_edits_are_preserved(deployment):
    """Restore the version while keeping edits made during tests."""
    deployment.env["DEPLOY_TEST_DIRTY"] = "scripts/test"
    assert deployment.run().returncode != 0
    assert_unchanged(deployment)
    assert (deployment.repo / "unrelated.txt").read_text() == "concurrent work\n"


@pytest.mark.parametrize("edited_file", [MANIFEST, "CHANGELOG.md"])
def test_concurrent_release_edit_preserves_edit_and_restores_other_file(
    deployment, edited_file
):
    """Rollback each generated file without overwriting someone's concurrent work."""
    deployment.env["DEPLOY_TEST_EDIT"] = edited_file
    result = deployment.run()
    assert result.returncode != 0
    assert "concurrent edits left untouched" in result.stderr
    assert (deployment.repo / edited_file).read_text().endswith("Concurrent edit\n")
    if edited_file == MANIFEST:
        assert (deployment.repo / "CHANGELOG.md").read_text() == deployment.changelog
    else:
        assert (deployment.repo / MANIFEST).read_text() == ORIGINAL
    assert deployment.git("rev-parse", "HEAD") == deployment.head
    assert deployment.git("rev-parse", "main", cwd=deployment.remote) == deployment.head
    assert "push" not in deployment.log.read_text().splitlines()


@pytest.mark.parametrize("invalid", ["missing marker", "unknown commit", "duplicate"])
def test_invalid_changelog_stops_before_testing(deployment, invalid):
    """Never invent a release boundary or overwrite existing release notes."""
    changelog = deployment.changelog
    if invalid == "missing marker":
        changelog = "# Changelog\n\n## 1.2.9\n\n- Earlier changes.\n"
    elif invalid == "unknown commit":
        base = deployment.git("rev-parse", "HEAD^")
        changelog = changelog.replace(base, "a" * 40)
    else:
        changelog = changelog.replace(
            "# Changelog\n\n", "# Changelog\n\n## 1.2.10\n\n- Handwritten notes.\n\n"
        )
    (deployment.repo / "CHANGELOG.md").write_text(changelog)
    deployment.git("add", "CHANGELOG.md")
    deployment.git("commit", "-m", "test: invalid changelog")
    head = deployment.git("rev-parse", "HEAD")
    assert deployment.run().returncode != 0
    assert deployment.log.read_text() == ""
    assert deployment.git("rev-parse", "HEAD") == head
    assert (deployment.repo / MANIFEST).read_text() == ORIGINAL
    assert (deployment.repo / "CHANGELOG.md").read_text() == changelog


def test_failed_push_keeps_release_commit(deployment):
    """Leave the tested commit available for a manual push retry."""
    deployment.env["DEPLOY_TEST_FAIL"] = "push"
    result = deployment.run()
    assert result.returncode != 0
    assert "finish the push manually; do not bump again" in result.stderr
    assert deployment.git("rev-parse", "main", cwd=deployment.remote) == deployment.head
    assert deployment.git("rev-parse", "HEAD") != deployment.head
    assert (deployment.repo / MANIFEST).read_text() == ORIGINAL.replace(
        "1.2.9", "1.2.10"
    )
    assert "## 1.2.10\n" in (deployment.repo / "CHANGELOG.md").read_text()
    assert deployment.git("status", "--porcelain") == ""
