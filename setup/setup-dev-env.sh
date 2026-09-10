#!/usr/bin/env bash
#
# setup-dev-env.sh
#
# Part 0 infrastructure for the Confidential ML Model Distribution PoC.
# Target host: Ubuntu 24.04 LTS (noble) running under WSL2 on Windows 11.
#
# Installs: Docker Engine, kubectl, kind, and Python build prerequisites.
# The rationale for every choice is documented in
# docs/00-development-environment.md
#
# The script is idempotent: it is safe to run more than once.
#
# Usage:
#   ./setup-dev-env.sh                  install tooling only
#   ./setup-dev-env.sh --with-cluster   also create the kind cluster
#
# Environment overrides:
#   CLUSTER_NAME   name of the kind cluster (default: confidential-ml)
#
set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-confidential-ml}"
CREATE_CLUSTER=0
[ "${1:-}" = "--with-cluster" ] && CREATE_CLUSTER=1

KEYRINGS=/etc/apt/keyrings
RESTART_REQUIRED=0

log()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1. Preflight
# ---------------------------------------------------------------------------
log "Preflight checks"

[ "$(id -u)" -ne 0 ] || die "Run as a normal user. The script calls sudo only where it needs to."
[ -r /etc/os-release ] || die "/etc/os-release is not readable; cannot identify the distribution."

# shellcheck disable=SC1091
. /etc/os-release
[ "${ID:-}" = "ubuntu" ] || die "This script targets Ubuntu. Found: ${ID:-unknown}."
if [ "${VERSION_ID:-}" != "24.04" ]; then
  warn "Expected Ubuntu 24.04 LTS, found ${VERSION_ID:-unknown}. Continuing, but this is untested."
fi
info "Distribution: ${PRETTY_NAME}"

grep -qi microsoft /proc/version || warn "WSL2 was not detected. The systemd handling below assumes WSL."

ARCH="$(dpkg --print-architecture)"
info "Architecture: ${ARCH}"

if [ -d /mnt/wsl/docker-desktop ]; then
  warn "Docker Desktop WSL integration is active for this distribution."
  warn "Disable it under Docker Desktop > Settings > Resources > WSL Integration,"
  warn "otherwise two docker CLIs will compete for the same PATH entry."
fi

# ---------------------------------------------------------------------------
# 2. systemd
#
# dockerd is managed as a systemd service. WSL only runs systemd as PID 1 when
# it is explicitly enabled in /etc/wsl.conf, so this has to be settled before
# anything else is installed.
# ---------------------------------------------------------------------------
log "Checking systemd"

if [ "$(ps -p 1 -o comm=)" != "systemd" ]; then
  info "systemd is not PID 1."
  if grep -qs '^systemd=true' /etc/wsl.conf; then
    info "/etc/wsl.conf already requests systemd; the distribution has not been restarted yet."
  else
    printf '[boot]\nsystemd=true\n' | sudo tee -a /etc/wsl.conf >/dev/null
    info "Enabled systemd in /etc/wsl.conf."
  fi
  echo
  warn "Restart this WSL distribution, then run the script again:"
  warn "  1. from Windows PowerShell:  wsl --shutdown"
  warn "  2. reopen Ubuntu and re-run: ./setup-dev-env.sh"
  exit 0
fi
info "systemd is running as PID 1."

# ---------------------------------------------------------------------------
# 3. Base packages
# ---------------------------------------------------------------------------
log "Installing base packages"

sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  ca-certificates \
  curl \
  gnupg \
  jq \
  git \
  make \
  unzip \
  python3 \
  python3-venv \
  python3-pip

sudo install -m 0755 -d "$KEYRINGS"
info "Python: $(python3 --version)"

# ---------------------------------------------------------------------------
# 4. Docker Engine
#
# Installed from Docker's own apt repository rather than the Ubuntu archive:
# the archive package (docker.io) lags upstream and does not ship the buildx
# plugin used for multi-stage builds.
# ---------------------------------------------------------------------------
log "Installing Docker Engine"

curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --yes --dearmor -o "${KEYRINGS}/docker.gpg"
sudo chmod a+r "${KEYRINGS}/docker.gpg"

echo "deb [arch=${ARCH} signed-by=${KEYRINGS}/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null

sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  docker-ce \
  docker-ce-cli \
  containerd.io \
  docker-buildx-plugin \
  docker-compose-plugin

sudo systemctl enable --now docker

if id -nG "$USER" | grep -qw docker; then
  info "User '${USER}' is already in the docker group."
else
  sudo usermod -aG docker "$USER"
  RESTART_REQUIRED=1
  info "Added '${USER}' to the docker group."
fi

# ---------------------------------------------------------------------------
# 5. kubectl
#
# pkgs.k8s.io is versioned per minor release, so the channel is derived from
# whatever upstream currently marks as stable. This keeps the script correct
# over time instead of pinning a minor that goes stale.
# ---------------------------------------------------------------------------
log "Installing kubectl"

K8S_STABLE="$(curl -fsSL https://dl.k8s.io/release/stable.txt)"
[ -n "$K8S_STABLE" ] || die "Could not resolve the upstream stable Kubernetes version."
K8S_MINOR="$(echo "$K8S_STABLE" | cut -d. -f1,2)"
info "Upstream stable: ${K8S_STABLE}  ->  repository channel: ${K8S_MINOR}"

curl -fsSL "https://pkgs.k8s.io/core:/stable:/${K8S_MINOR}/deb/Release.key" \
  | sudo gpg --yes --dearmor -o "${KEYRINGS}/kubernetes-apt-keyring.gpg"
sudo chmod a+r "${KEYRINGS}/kubernetes-apt-keyring.gpg"

echo "deb [signed-by=${KEYRINGS}/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/${K8S_MINOR}/deb/ /" \
  | sudo tee /etc/apt/sources.list.d/kubernetes.list >/dev/null

sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq kubectl

# ---------------------------------------------------------------------------
# 6. kind
#
# Distributed only as a static binary, so it goes to /usr/local/bin rather
# than through apt.
# ---------------------------------------------------------------------------
log "Installing kind"

KIND_VERSION="$(curl -fsSL https://api.github.com/repos/kubernetes-sigs/kind/releases/latest | jq -r '.tag_name // empty')"
[ -n "$KIND_VERSION" ] || die "Could not resolve the latest kind release from the GitHub API."
info "Latest release: ${KIND_VERSION}"

TMP_KIND="$(mktemp)"
curl -fsSL -o "$TMP_KIND" "https://kind.sigs.k8s.io/dl/${KIND_VERSION}/kind-linux-${ARCH}"
sudo install -m 0755 "$TMP_KIND" /usr/local/bin/kind
rm -f "$TMP_KIND"

# ---------------------------------------------------------------------------
# 7. Optional cluster
# ---------------------------------------------------------------------------
if [ "$CREATE_CLUSTER" -eq 1 ]; then
  # Test the socket directly rather than inferring from group membership:
  # the group can be granted in /etc/group yet still be absent from the
  # credentials of the running session, which is the usual case under WSL.
  if ! docker info >/dev/null 2>&1; then
    RESTART_REQUIRED=1
    warn "Skipping cluster creation: the Docker socket is not reachable from this shell."
    warn "The 'docker' group is granted but not active in the current session."
    warn "Run 'wsl --shutdown' from Windows, reopen Ubuntu, then run:"
    warn "  ./setup/setup-dev-env.sh --with-cluster"
  else
    log "Creating kind cluster '${CLUSTER_NAME}'"
    if kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
      info "Cluster already exists."
    else
      kind create cluster --name "$CLUSTER_NAME"
    fi
    kubectl cluster-info --context "kind-${CLUSTER_NAME}"
  fi
fi

# ---------------------------------------------------------------------------
# 8. Report
# ---------------------------------------------------------------------------
log "Installed versions"
printf '    %-10s %s\n' "docker"  "$(docker --version 2>/dev/null || echo 'not reachable from this shell yet')"
printf '    %-10s %s\n' "kubectl" "$(kubectl version --client 2>/dev/null | head -n1)"
printf '    %-10s %s\n' "kind"    "$(kind --version 2>/dev/null)"
printf '    %-10s %s\n' "python3" "$(python3 --version)"
printf '    %-10s %s\n' "git"     "$(git --version)"

log "Done"

if [ "$RESTART_REQUIRED" -eq 1 ]; then
  echo
  warn "One step remains: this shell does not have the new docker group yet."
  warn "From Windows PowerShell run 'wsl --shutdown', then reopen Ubuntu."
fi

echo
echo "Next steps:"
echo
echo "  1. Keep the repository on the Linux filesystem, not under /mnt/c."
echo "         mkdir -p ~/projects && cd ~/projects"
echo
echo "  2. Create the cluster if it was not created above:"
echo "         kind create cluster --name ${CLUSTER_NAME}"
echo
echo "  3. Create the Python virtual environment for local iteration."
echo "     Ubuntu 24.04 marks the system interpreter as externally managed"
echo "     (PEP 668), so pip refuses to install outside a venv. That is intended."
echo "         python3 -m venv .venv && source .venv/bin/activate"
echo
