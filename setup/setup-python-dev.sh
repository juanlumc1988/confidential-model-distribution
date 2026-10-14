#!/usr/bin/env bash
#
# setup-python-dev.sh
#
# Python development environment for this repository: a virtual environment
# at .venv with the test and lint tooling from requirements-dev.lock, which is
# also what CI installs. VSCode picks it up through .vscode/settings.json.
#
# Run it after setup-dev-env.sh, from anywhere inside the clone. It only
# calls sudo if python3-venv is missing. It is idempotent: it is safe to run
# again, for example after requirements-dev.lock changes.
#
# Usage:
#   ./setup/setup-python-dev.sh            create or update .venv, then verify
#   ./setup/setup-python-dev.sh --recreate delete .venv and build it again
#
set -euo pipefail

RECREATE=0
case "${1:-}" in
  "") ;;
  --recreate) RECREATE=1 ;;
  *) printf 'usage: %s [--recreate]\n' "$0" >&2; exit 2 ;;
esac

# CI tests on the base image's Python (3.12.14) and the dev lock is compiled
# for 3.12, so any 3.12.x matches. Ubuntu 24.04 ships 3.12.
EXPECTED_MINOR="3.12"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${REPO_ROOT}/.venv"
LOCK="${REPO_ROOT}/requirements-dev.lock"

log()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1. Preflight
# ---------------------------------------------------------------------------
log "Preflight checks"

[ "$(id -u)" -ne 0 ] || die "Run as a normal user, so .venv is not owned by root."
[ -f "$LOCK" ] || die "Not found: ${LOCK}. Run this from a clone of the repository."

case "$REPO_ROOT" in
  /mnt/*)
    warn "The repository is under ${REPO_ROOT}, on the Windows filesystem."
    warn "File access across that boundary is very slow and file modes are not"
    warn "kept (a test checks the key file's 0600). Clone it under your Linux home:"
    warn "  git clone <url> ~/confidential-model-distribution"
    die "Refusing to continue under /mnt."
    ;;
esac
info "Repository: ${REPO_ROOT}"

command -v python3 >/dev/null || die "python3 not found. Run ./setup/setup-dev-env.sh first."
PY_MINOR="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
info "System Python: $(python3 --version)"
if [ "$PY_MINOR" != "$EXPECTED_MINOR" ]; then
  warn "Expected Python ${EXPECTED_MINOR}.x (the images run 3.12.14), found ${PY_MINOR}."
  warn "Continuing; the lock may not install, or tests may differ from CI."
fi

# python3 -m venv needs ensurepip, which Ubuntu ships separately.
if ! python3 -c 'import ensurepip' 2>/dev/null; then
  info "python3-venv is missing; installing it (needs sudo)."
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv
fi

# ---------------------------------------------------------------------------
# 2. Virtual environment
# ---------------------------------------------------------------------------
log "Virtual environment"

if [ -d "$VENV" ] && [ "$RECREATE" -eq 0 ]; then
  if [ ! -x "${VENV}/bin/python" ]; then
    warn "${VENV} exists but has no interpreter; recreating it."
    RECREATE=1
  else
    VENV_MINOR="$("${VENV}/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    if [ "$VENV_MINOR" != "$PY_MINOR" ]; then
      warn "${VENV} was built with Python ${VENV_MINOR}, the system has ${PY_MINOR}; recreating it."
      RECREATE=1
    fi
  fi
fi

if [ "$RECREATE" -eq 1 ] && [ -d "$VENV" ]; then
  rm -rf "$VENV"
  info "Removed the old ${VENV}."
fi

if [ -d "$VENV" ]; then
  info "Reusing ${VENV}."
else
  python3 -m venv "$VENV"
  info "Created ${VENV}."
fi

# ---------------------------------------------------------------------------
# 3. Dependencies
#
# --require-hashes, exactly as CI and the images install: every package,
# transitive ones included, must match a hash in the lock. pip itself is not
# upgraded, because that would be an unhashed install.
# ---------------------------------------------------------------------------
log "Installing requirements-dev.lock"

"${VENV}/bin/python" -m pip install --quiet --disable-pip-version-check \
  --require-hashes -r "$LOCK"
info "Installed: $("${VENV}/bin/python" -m pip list --format=freeze 2>/dev/null \
  | grep -iE '^(pytest|ruff|cryptography)==' | tr '\n' ' ')"

# ---------------------------------------------------------------------------
# 4. Verify: the same lint and test commands CI runs
# ---------------------------------------------------------------------------
log "Verifying"

cd "$REPO_ROOT"
"${VENV}/bin/ruff" check --quiet . && info "ruff check: OK"
"${VENV}/bin/ruff" format --check --quiet . && info "ruff format: OK"
"${VENV}/bin/python" -m pytest -q -p no:cacheprovider tests

# ---------------------------------------------------------------------------
# 5. Next steps
# ---------------------------------------------------------------------------
log "Done"

echo
echo "Open the repository in VSCode on Windows, through WSL:"
echo
echo "  1. In VSCode on Windows, install the 'WSL' extension (ms-vscode-remote.remote-wsl)."
echo "  2. From this Ubuntu shell:"
echo "         cd ${REPO_ROOT} && code ."
echo "     The first run installs the VSCode server inside WSL."
echo "  3. Accept the recommended extensions. The interpreter (.venv) and"
echo "     pytest discovery are already configured in .vscode/settings.json."
echo
echo "From a terminal:"
echo "     source .venv/bin/activate"
echo "     pytest                      # unit tests, as in CI"
echo "     ruff check . && ruff format --check ."
