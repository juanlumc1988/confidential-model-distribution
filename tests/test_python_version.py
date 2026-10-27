"""The CI helper that reads the images' Python version from the Dockerfiles."""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / ".github" / "scripts" / "python-version.sh"
DIGEST = "sha256:" + "ab" * 32


def run(root=None):
    args = ["bash", str(SCRIPT)] + ([str(root)] if root else [])
    return subprocess.run(args, capture_output=True, text=True)


def make_repo(tmp_path, producer, consumer=None):
    for name, from_line in (("producer", producer), ("consumer", consumer or producer)):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "Dockerfile").write_text(f"# comment\n{from_line}\nWORKDIR /app\n")
    return tmp_path


GOOD = f"FROM python:3.12.15-slim-trixie@{DIGEST}"


def test_reads_the_version(tmp_path):
    result = run(make_repo(tmp_path, GOOD))
    assert (result.returncode, result.stdout) == (0, "3.12.15\n")


def test_real_dockerfiles_are_readable():
    # Guards the repository itself: if a Dockerfile drifts out of the pattern,
    # this fails here, before CI's lint and test jobs do.
    result = run()
    assert result.returncode == 0, result.stderr
    assert re.fullmatch(r"\d+\.\d+\.\d+\n", result.stdout)


def test_producer_and_consumer_must_agree(tmp_path):
    other = f"FROM python:3.12.14-slim-trixie@{DIGEST}"
    result = run(make_repo(tmp_path, GOOD, other))
    assert result.returncode == 1
    assert "different Python versions" in result.stderr


@pytest.mark.parametrize(
    "from_line",
    [
        f"FROM python@{DIGEST}",  # bare digest: Dependabot follows "latest"
        "FROM python:3.12.15-slim-trixie",  # tag without a digest
        f"FROM python:3.15.0-trixie@{DIGEST}",  # not the slim variant
        f"FROM python:3.12-slim-trixie@{DIGEST}",  # no patch version
        "FROM python:3.12.15-slim-trixie@sha256:abc",  # truncated digest
        f"FROM python:3.12.15-slim-trixie@{DIGEST} AS base",  # unexpected tail
    ],
    ids=["bare-digest", "no-digest", "not-slim", "no-patch", "short-digest", "stage"],
)
def test_rejects_a_from_line_that_lost_its_shape(tmp_path, from_line):
    result = run(make_repo(tmp_path, from_line))
    assert result.returncode == 1
    assert "expected 'FROM python:X.Y.Z-slim" in result.stderr


def test_rejects_several_from_lines(tmp_path):
    root = make_repo(tmp_path, GOOD)
    with (root / "producer" / "Dockerfile").open("a") as handle:
        handle.write(f"FROM python:3.12.15-slim-trixie@{DIGEST}\n")
    result = run(root)
    assert result.returncode == 1
    assert "more than one FROM" in result.stderr


def test_rejects_a_missing_dockerfile(tmp_path):
    root = make_repo(tmp_path, GOOD)
    (root / "consumer" / "Dockerfile").unlink()
    result = run(root)
    assert result.returncode == 1
    assert "cannot read" in result.stderr
