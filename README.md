# Confidential Model Distribution

A proof of concept for a confidential ML model delivery pipeline. A **producer** encrypts an open model and publishes the ciphertext to the Hugging Face Hub. A **consumer**, running as a pod in Kubernetes, downloads the artifact, decrypts it with a key mounted from a Kubernetes Secret, and loads the model.

> **Status: Layer 1 complete and verified end to end.** The producer publishes, the consumer decrypts and loads, and the pipeline demonstrably fails closed without the key, with the wrong key, and against a tampered artifact. Layers 2 and 3 are out of scope by choice — see below.

---

## Scope

The assignment is structured in three progressive layers. Only the first is required.

| Layer | Description | Status |
| --- | --- | --- |
| **1** | Encrypted model distribution in Kubernetes | **In scope** — complete |
| 2 | Model signing and verification | Optional — out of scope |
| 3 | Attested key release via Kata + Confidential Containers | Optional — out of scope |

Layers 2 and 3 are deliberately excluded rather than merely unfinished. A solid, well-understood Layer 1 was preferred over a partial attempt at all three. [Known limitations](#known-limitations) states what that leaves open, and which layer would close each gap.

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
├── shared/
│   └── artifact_format.py         The wire format, defined once for both sides
├── producer/
│   ├── build_artifact.py          Fetch, re-serialize, pack and encrypt
│   ├── publish.py                 Upload to the Hub, write the key to a Secret
│   ├── main.py                    Entry point
│   ├── model_card.md              README published to the Hub repository
│   ├── Dockerfile                 Base pinned by digest, unprivileged runtime user
│   ├── requirements.in            Direct dependencies
│   └── requirements.lock          Full tree pinned by digest (--require-hashes)
├── consumer/
│   ├── consume.py                 Download, authenticate, decrypt, load
│   ├── Dockerfile
│   ├── requirements.in
│   └── requirements.lock
├── manifests/
│   ├── producer-rbac.yaml         ServiceAccount, Role, RoleBinding
│   ├── producer-job.yaml          The producer, run once
│   ├── consumer-pod.yaml          The consumer, key mounted from the Secret
│   └── verify/
│       ├── consumer-no-key.yaml       Must fail before downloading
│       ├── consumer-wrong-key.yaml    Must fail at authentication
│       └── consumer-tamper-check.yaml Intact decrypts, every mutation rejected
├── .dockerignore
├── .gitattributes
├── .gitignore
├── LICENSE
└── README.md
```

---

## Build

Both images are built **from the repository root**, with `-f`, so that they can
copy `shared/` in. The wire format lives there and both sides must agree on it
byte for byte; a disagreement would surface as `InvalidTag`, indistinguishable
from tampering.

```bash
docker build -t producer:dev -f producer/Dockerfile .
docker build -t consumer:dev -f consumer/Dockerfile .
```

```bash
kind load docker-image producer:dev --name confidential-ml
kind load docker-image consumer:dev --name confidential-ml
```

`kind load` is not optional. A kind node is a Docker container with its own
image store, separate from the daemon that built the image; without it the
kubelet cannot see the image and tries to pull it from a registry that does not
have it. This is also why the manifests set `imagePullPolicy: IfNotPresent`.

## Deploy

The Hugging Face token is a personal credential, so the pipeline does not create
it — you do. The leading space keeps it out of shell history:

```bash
 kubectl create secret generic hf-token --from-literal=token=hf_...
```

Then the producer, RBAC first so the ServiceAccount exists before the pod needs
it:

```bash
kubectl apply -f manifests/producer-rbac.yaml
kubectl apply -f manifests/producer-job.yaml
kubectl logs -f job/producer
```

The Job publishes `artifact.enc` to the Hub and leaves the decryption key in a
Secret named `model-decryption-key`. To run it again, delete the Job first: a
completed Job is not re-executed by `apply`.

Then the consumer:

```bash
kubectl apply -f manifests/consumer-pod.yaml
kubectl logs -f pod/consumer
```

It runs to completion in about twenty seconds and ends in `Completed`. To run it
again, delete the pod first — a Pod's spec is immutable.

## Verify the pipeline

**The key reached the cluster at the right length.** Prints a length, not a key:

```bash
kubectl get secret model-decryption-key -o jsonpath='{.data.model\.key}' | base64 -d | wc -c
```

Expect `32` — an AES-256 key.

**The producer is actually constrained.** `kubectl auth can-i` evaluates the
real authorisation chain rather than trusting the manifest:

```bash
kubectl auth can-i create secrets --as=system:serviceaccount:default:producer   # yes
kubectl auth can-i update secrets --as=system:serviceaccount:default:producer   # yes
kubectl auth can-i get    secrets --as=system:serviceaccount:default:producer   # no
kubectl auth can-i list   secrets --as=system:serviceaccount:default:producer   # no
```

Those two `yes` answers are its only capabilities in the cluster. It cannot read
secrets back, which is the permission worth withholding.

**The artifact is opaque.** It is public, so anyone can check:

```bash
curl -sL https://huggingface.co/juanlumc1988/bert-tiny-encrypted/resolve/main/artifact.enc | head -c 32 | xxd
```

**The pipeline works end to end.** The consumer's own output is the evidence:

```
==> Read a 256-bit key from /etc/model-key/model.key
==> Downloading artifact.enc from juanlumc1988/bert-tiny-encrypted
Warning: You are sending unauthenticated requests to the HF Hub.
==> Artifact is 18,268,188 bytes
==> Authenticated and decrypted to 18,268,160 bytes
==> Extracted to /scratch/model (tmpfs -- the node's disk never sees it)
==> Loaded bert with 4,385,920 parameters
==> Forward pass produced (1, 5, 128)
```

Two of those lines carry the argument. The **warning** is not a defect: the
consumer downloads with no Hugging Face credential at all, because the artifact
is public and the only secret in the system is the key. The **forward pass**
means the weights actually work, rather than that a directory happened to parse.

### Failing closed

Working is half the claim. The other half is that it fails when it should, and
the three manifests under `manifests/verify/` demonstrate it.

Each check **waits for the pod to finish before reading its log**. A pod that
downloads the artifact takes a few seconds, and `kubectl logs` on its own prints
whatever exists at that instant — which can stop short of the verdict and look
like a pass. The wait is also the assertion: waiting for `Failed` times out if
the pod succeeds instead, so the `&&` never runs.

**Without the key**, the consumer exits before it downloads anything:

```bash
kubectl apply -f manifests/verify/consumer-no-key.yaml
kubectl wait --for=jsonpath='{.status.phase}'=Failed pod/consumer-no-key --timeout=180s && kubectl logs pod/consumer-no-key
```

```
[x] no decryption key at /etc/model-key/model.key. The Secret is not mounted,
    so there is nothing to decrypt with. Refusing to continue.
```

`Failed`, exit 1, and no download line — the key is read first precisely so this
case is cheap.

**With a valid but wrong key**, it fails at authentication, before unpacking:

```bash
head -c 32 /dev/urandom > /tmp/wrong.key
kubectl create secret generic wrong-model-key --from-file=model.key=/tmp/wrong.key
shred -u /tmp/wrong.key
kubectl apply -f manifests/verify/consumer-wrong-key.yaml
kubectl wait --for=jsonpath='{.status.phase}'=Failed pod/consumer-wrong-key --timeout=180s && kubectl logs pod/consumer-wrong-key
```

```
[x] authentication failed. The artifact, the key or the repository binding
    do not match. Nothing has been unpacked.
```

**With the correct key and a tampered artifact**, every mutation is rejected.
This one runs inside the cluster so the key is never read out of the Secret.
It is the one check expected to succeed, so it waits for `Succeeded`:

```bash
kubectl apply -f manifests/verify/consumer-tamper-check.yaml
kubectl wait --for=jsonpath='{.status.phase}'=Succeeded pod/consumer-tamper-check --timeout=180s && kubectl logs pod/consumer-tamper-check
```

```
  PASS  intact                   -> decrypted 18,268,160 bytes
  PASS  byte flipped mid-file    -> REJECTED (InvalidTag)
  PASS  nonce altered            -> REJECTED (InvalidTag)
  PASS  tag altered              -> REJECTED (InvalidTag)
  PASS  truncated by one byte    -> REJECTED (InvalidTag)
  PASS  one byte appended        -> REJECTED (InvalidTag)
  PASS  relocated to another repo -> REJECTED (InvalidTag)
```

The last case is the associated-data binding: the same key and the same bytes
still fail under a different repository id, so a published artifact cannot be
silently relocated.

Clean up afterwards:

```bash
kubectl delete -f manifests/verify/ && kubectl delete secret wrong-model-key
```

---

## Design decisions

The choices that shape the pipeline, each with the reason it won and the
alternative it beat. Comments in the code and the manifests carry the
finer-grained reasoning at the point where it applies.

| Decision | Why | Rejected |
| --- | --- | --- |
| AES-256-GCM | With signing out of scope, the authentication tag is the only integrity control | ChaCha20-Poly1305, Fernet, AES-CBC with HMAC |
| One deterministic tar, encrypted once | One integrity boundary; only the total size leaks | Per-file encryption |
| Re-serialise the model to safetensors | Loading a pickle checkpoint executes code | Publishing the upstream `.bin` |
| Public Hub repository | Shows confidentiality comes from the key, not from access control | Private repository |
| Producer as a Job, Role limited to `create` and `update` | Least privilege, checkable with `kubectl auth can-i` | Local script piping the key into `kubectl` |
| Plaintext only in a tmpfs `emptyDir` | The node's disk never sees the decrypted model | Default disk-backed `emptyDir` |
| Dependencies locked by hash | A re-uploaded or tampered package fails the build | Version pins only |
| Python | Reference client for the Hub, and the library that loads the model | Go, Rust |

---

## Known limitations

Stated up front, because they are properties of the design rather than oversights.

- **A Kubernetes Secret is not encrypted.** It is base64-encoded, stored in etcd, and readable by any principal with `get secrets` in the namespace. Layer 1 trusts the cluster and its administrator by construction. This is precisely the trust assumption that Layer 3 exists to remove, by releasing the key only after a successful attestation.
- **The producer holds the key.** Key generation and custody sit outside the pipeline in this PoC; a production design would source it from a KMS or an HSM.
- **No integrity guarantee on the transport.** An authenticated cipher detects tampering with the ciphertext, but nothing binds the artifact to its publisher. That binding is what Layer 2 adds.

---

## Licence

[Apache License 2.0](LICENSE) — the licence used across the stack this project builds on: Kubernetes, containerd, Kata Containers and Confidential Containers.
