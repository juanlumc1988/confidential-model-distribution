#!/usr/bin/env bash
#
# python-version.sh [REPO_ROOT]
#
# Prints the Python version of the images (for example 3.12.15), read from the
# FROM line of producer/Dockerfile and consumer/Dockerfile. CI uses it so that
# lint, tests and lock-check run on the same interpreter as the images, with
# nothing to update by hand when Dependabot bumps the base image.
#
# It fails, with the reason on stderr, when:
#   * a Dockerfile does not have exactly one FROM line;
#   * that line is not "FROM python:X.Y.Z-slim...@sha256:<64 hex>", i.e. it lost
#     its tag (Dependabot then follows "latest"), its digest, or the slim variant;
#   * producer and consumer disagree.
#
# REPO_ROOT defaults to the repository containing this script.
#
set -euo pipefail

ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
PATTERN='^FROM python:([0-9]+\.[0-9]+\.[0-9]+)-slim[-a-z]*@sha256:[0-9a-f]{64}$'

die() { printf 'python-version.sh: %s\n' "$*" >&2; exit 1; }

version_of() {
  local file="$1" lines
  [ -r "$file" ] || die "cannot read ${file}"
  lines="$(grep -E '^FROM ' "$file" || true)"
  [ -n "$lines" ] || die "${file} has no FROM line"
  [ "$(printf '%s\n' "$lines" | wc -l)" -eq 1 ] || die "${file} has more than one FROM line"
  [[ "$lines" =~ $PATTERN ]] \
    || die "${file}: expected 'FROM python:X.Y.Z-slim-<release>@sha256:<digest>' (tag and digest), found: ${lines}"
  printf '%s\n' "${BASH_REMATCH[1]}"
}

producer="$(version_of "${ROOT}/producer/Dockerfile")"
consumer="$(version_of "${ROOT}/consumer/Dockerfile")"
[ "$producer" = "$consumer" ] \
  || die "producer (${producer}) and consumer (${consumer}) use different Python versions"

printf '%s\n' "$producer"
