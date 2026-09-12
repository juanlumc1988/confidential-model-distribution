---
license: apache-2.0
tags:
  - encrypted
  - confidential-computing
  - proof-of-concept
---

# Encrypted model artifact

**This repository does not contain a loadable model.** It contains one
encrypted file, `{artifact_name}`, published by a confidential model
distribution pipeline. Downloading it without the decryption key gets you
bytes indistinguishable from random.

That is the point. The artifact is public and still confidential, because the
only secret in the system is the key — which never travels with it.

## What is inside

`{artifact_name}` is an AES-256-GCM ciphertext of an uncompressed tar archive
containing [`{model_id}`](https://huggingface.co/{model_id}), re-serialized to
safetensors.

```
{artifact_name} := nonce (12 bytes) || ciphertext || GCM tag (16 bytes)
```

The repository id `{repo_id}` is bound into the ciphertext as associated data,
so the artifact cannot be relocated to another repository without failing
authentication.

## How it is consumed

A workload running in Kubernetes mounts the decryption key from a Secret,
downloads this artifact, verifies and decrypts it in memory, and loads the
model into a RAM-backed volume — the plaintext never reaches disk.

## Source

Pipeline, design decisions and reproduction steps:
<https://github.com/juanlumc1988/confidential-model-distribution>

## Licence

Apache 2.0, following the upstream model.
