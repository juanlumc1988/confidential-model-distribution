# Development Environment Setup

Everything needed to build and run this project locally. Target host is **Ubuntu 24.04 LTS**, either natively or under WSL2 on Windows 11.

No cloud account and no confidential hardware are required. The Kubernetes cluster runs locally.

---

## What gets installed

| Tool | Source | Purpose |
| --- | --- | --- |
| Docker Engine | Docker's official apt repository | Build the producer and consumer images |
| kubectl | `pkgs.k8s.io` apt repository | Talk to the cluster |
| kind | GitHub release binary | Run a single-node Kubernetes cluster in Docker |
| Python 3.12 + venv | Ubuntu archive | Local iteration before containerising |
| `jq`, `git`, `make`, `curl` | Ubuntu archive | Supporting tooling |

---

## Before you start

- **Hugging Face account** with a write-scoped access token, for publishing the encrypted artifact.
- **~8 GB free RAM** and **~20 GB free disk**.
- On Windows: WSL2 enabled, and Docker Desktop's WSL integration **disabled** for this distribution if Docker Desktop is installed. Two Docker CLIs on the same PATH will conflict.

---

## 1. Install Ubuntu 24.04 (Windows only)

From PowerShell. This installs alongside any existing distribution rather than replacing it:

```powershell
wsl --install -d Ubuntu-24.04
```

## 2. Clone on the Linux filesystem

Keep the repository under your Linux home, **not** under `/mnt/c`. Docker build contexts read across the Windows/Linux boundary are roughly an order of magnitude slower.

```bash
git clone https://github.com/<user>/confidential-model-distribution.git ~/confidential-model-distribution
cd ~/confidential-model-distribution
```

## 3. Run the setup script

```bash
./setup/setup-dev-env.sh
```

The script is idempotent — safe to run again at any point.

## 4. Restart for systemd

The script **stops once on purpose**, after enabling systemd in `/etc/wsl.conf`. Docker runs as a systemd unit, and WSL does not start systemd as PID 1 unless asked.

From Windows PowerShell:

```powershell
wsl --shutdown
```

Reopen Ubuntu, then continue.

## 5. Create the cluster

```bash
./setup/setup-dev-env.sh --with-cluster
```

This creates a single-node kind cluster named `confidential-ml`. To use a different name:

```bash
CLUSTER_NAME=my-cluster ./setup/setup-dev-env.sh --with-cluster
```

## 6. Python environment (optional, for local iteration)

Ubuntu 24.04 marks the system interpreter as externally managed (PEP 668), so pip refuses to install outside a virtual environment. That is intended behaviour, not an error.

```bash
python3 -m venv .venv && source .venv/bin/activate
```

---

## Verify

```bash
docker run --rm hello-world
kubectl get nodes
kind get clusters
```

A `Ready` node and a cluster named `confidential-ml` mean the environment is complete.

## Next

The environment is ready. Return to the main README and continue with
**[Build](../README.md#build)**, then Deploy, then Verify the pipeline.

---

## Troubleshooting

**`permission denied` on the Docker socket.** Your shell does not have the `docker` group yet. Run `wsl --shutdown` from Windows and reopen Ubuntu — logging out is not enough under WSL.

**The script exits immediately after mentioning systemd.** That is step 4 above, working as designed. Restart the distribution and run it again.

**Builds are unexpectedly slow.** Check `pwd`. If the repository sits under `/mnt/c`, move it to your Linux home.

**`docker` resolves to Docker Desktop.** Disable WSL integration for this distribution under Docker Desktop → Settings → Resources → WSL Integration.

---

## Removing everything

```bash
kind delete cluster --name confidential-ml
```

Docker, kubectl and kind remain installed. Remove them with `apt remove` and by deleting `/usr/local/bin/kind` if you want the machine back as it was.
