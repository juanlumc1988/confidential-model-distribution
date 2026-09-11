# Confidential Model Distribution

A proof of concept for a confidential ML model delivery pipeline. A **producer** encrypts an open model and publishes the ciphertext to the Hugging Face Hub. A **consumer**, running as a pod in Kubernetes, downloads the artifact, decrypts it with a key mounted from a Kubernetes Secret, and loads the model.

> **Status: work in progress.** The development environment is complete and documented. Layer 1 is under construction. Sections marked _pending_ are filled in as the implementation lands.

---

## Scope

The assignment is structured in three progressive layers. Only the first is required.

| Layer | Description | Status |
| --- | --- | --- |
| **1** | Encrypted model distribution in Kubernetes | **In scope** — in progress |
| 2 | Model signing and verification | Optional — out of scope |
| 3 | Attested key release via Kata + Confidential Containers | Optional — out of scope |

Layers 2 and 3 are deliberately excluded rather than merely unfinished. A solid, well-understood Layer 1 was preferred over a partial attempt at all three; the reasoning is set out in the accompanying design document.

---

## Pipeline

```
  PRODUCER                                             CONSUMER (pod)
  ────────                                             ──────────────

  1. fetch open model from HF Hub
  2. encrypt                                           1. mount key from Secret
  3. push ciphertext  ──────► Hugging Face Hub ──────► 2. download ciphertext
  4. store key        ──────► Kubernetes Secret ─────► 3. decrypt
                                                       4. load model
```

The key never travels with the artifact. The Hub carries only ciphertext; the key reaches the consumer through the cluster.

---

## Prerequisites

Ubuntu 24.04 LTS — natively, or under WSL2 on Windows 11 — plus a Hugging Face account with a write-scoped access token, roughly 8 GB of free RAM and 20 GB of disk. Docker Engine, kubectl and kind are installed by the setup script.

No cloud account and no confidential hardware are required. The cluster is local.

---

## Quick start

```bash
git clone https://github.com/juanlumc1988/confidential-model-distribution.git ~/confidential-model-distribution
cd ~/confidential-model-distribution
./setup/setup-dev-env.sh
```

Step-by-step instructions, environment verification and troubleshooting are in **[`setup/README.md`](setup/README.md)**.

---

## Repository layout

```
.
├── setup/
│   ├── README.md                  Environment setup, verification, troubleshooting
│   └── setup-dev-env.sh           Idempotent installer for Ubuntu 24.04 / WSL2
├── producer/
│   ├── build_artifact.py          Fetch, re-serialize, pack and encrypt the model
│   ├── Dockerfile                 Base pinned by digest, unprivileged runtime user
│   ├── requirements.in            Direct dependencies
│   └── requirements.lock          Full tree pinned by digest (--require-hashes)
├── consumer/                      (pending) Dockerfile and fetch/decrypt/load logic
├── manifests/                     (pending) Kubernetes Pod, Secret and supporting resources
├── .gitattributes
├── .gitignore
├── LICENSE
└── README.md
```

---

## Build

_Pending._ Will cover building the producer and consumer images and loading them into the kind cluster with `kind load docker-image` — necessary because kind nodes maintain their own image store, and locally built images are otherwise invisible to the cluster.

## Deploy

_Pending._ Will cover creating the Secret, running the producer, and deploying the consumer pod.

## Verify the pipeline

_Pending._ Will cover how to confirm each step independently: that the published artifact is genuinely opaque, that the consumer fails as expected without the key, and that the model loads successfully with it.

---

## Design decisions

Every significant choice — host platform, container runtime, Kubernetes distribution, implementation language, artifact format, cipher and mode — is recorded with its rationale and the alternatives that were rejected, since the assignment asks for decisions to be defensible rather than merely present.

That record is delivered as a separate document alongside this repository.

---

## Known limitations

Stated up front, because they are properties of the design rather than oversights.

- **A Kubernetes Secret is not encrypted.** It is base64-encoded, stored in etcd, and readable by any principal with `get secrets` in the namespace. Layer 1 trusts the cluster and its administrator by construction. This is precisely the trust assumption that Layer 3 exists to remove, by releasing the key only after a successful attestation.
- **The producer holds the key.** Key generation and custody sit outside the pipeline in this PoC; a production design would source it from a KMS or an HSM.
- **No integrity guarantee on the transport.** An authenticated cipher detects tampering with the ciphertext, but nothing binds the artifact to its publisher. That binding is what Layer 2 adds.

---

## Licence

[Apache License 2.0](LICENSE) — the licence used across the stack this project builds on: Kubernetes, containerd, Kata Containers and Confidential Containers.
