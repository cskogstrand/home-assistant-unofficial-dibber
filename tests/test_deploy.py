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
    )


def assert_unchanged(deployment):
    """Failures before committing restore the exact original manifest."""
    assert (deployment.repo / MANIFEST).read_text() == ORIGINAL
    assert deployment.git("rev-parse", "HEAD") == deployment.head
    assert deployment.git("rev-parse", "main", cwd=deployment.remote) == deployment.head
    assert "push" not in deployment.log.read_text().splitlines()


def test_deploy_checks_before_pushing_only_manifest(deployment):
    """Patch bump without reformatting or requiring another checkout."""
    result = deployment.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert (deployment.repo / MANIFEST).read_text() == ORIGINAL.replace(
        "1.2.9", "1.2.10"
    )
    assert deployment.log.read_text().splitlines() == [*CHECKS, "push"]
    assert deployment.git("diff", "--name-only", "HEAD^", "HEAD") == MANIFEST
    assert deployment.git("status", "--porcelain") == ""
    assert deployment.git("rev-parse", "HEAD") == deployment.git(
        "rev-parse", "main", cwd=deployment.remote
    )


@pytest.mark.parametrize("failed_check", CHECKS)
def test_failed_check_restores_version(deployment, failed_check):
    """Every failed local check must revert the bump without committing or pushing."""
    deployment.env["DEPLOY_TEST_FAIL"] = failed_check
    result = deployment.run()
    assert result.returncode != 0
    assert "Version restored" in result.stderr
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
