"""The artifact format, defined once for both sides of the pipeline.

    artifact.enc := nonce (12 bytes) || ciphertext || GCM tag (16 bytes)

The producer and the consumer must agree on every constant here — the nonce
length, the version string, how the associated data is built. A disagreement
does not produce a clear error: it produces `InvalidTag`, which is
indistinguishable from tampering. Defining the format in one module makes that
class of bug impossible rather than merely unlikely, which is why both images
are built from the repository root and copy this directory in.
"""

from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Bound into the associated data. Neither value is secret; binding them means a
# ciphertext cannot be silently relocated to a different Hub repository, or
# reinterpreted under a future revision of this format, without the tag failing.
FORMAT_VERSION = b"cmd/v1"

KEY_BYTES = 32  # AES-256
NONCE_BYTES = 12  # GCM's native nonce size; no derivation or truncation needed
TAG_BYTES = 16


def associated_data(hf_repo_id: str) -> bytes:
    return FORMAT_VERSION + b"|" + hf_repo_id.encode("utf-8")


def encrypt(plaintext: bytes, aad: bytes) -> tuple[bytes, bytes]:
    """Encrypt ``plaintext`` under a freshly generated key.

    Returns ``(key, blob)`` where ``blob`` is ``nonce || ciphertext || tag``.
    The nonce travels with the ciphertext because it is not secret; the key
    travels through the cluster because it is.

    One key per artifact, so a nonce is never reused under the same key.

    Limitation, deliberately not hidden: ``AESGCM.encrypt`` is a one-shot API
    holding plaintext and ciphertext in memory at once. Fine for a model of
    this size; a multi-gigabyte model would need chunked framing, with a chunk
    index in the associated data to prevent reordering and truncation.
    """
    key = os.urandom(KEY_BYTES)
    nonce = os.urandom(NONCE_BYTES)
    return key, nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def decrypt(blob: bytes, key: bytes, aad: bytes) -> bytes:
    """Authenticate and decrypt ``blob``.

    Raises ``cryptography.exceptions.InvalidTag`` if the ciphertext, the
    associated data or the key do not match what was used to encrypt. The
    caller must let that propagate: with signing out of scope for Layer 1, this
    tag is the only integrity control in the pipeline, and a consumer that
    continued past a failure here would be deserializing attacker-controlled
    input.
    """
    if len(blob) < NONCE_BYTES + TAG_BYTES:
        raise ValueError("artifact is too short to be a valid ciphertext")
    nonce, ciphertext = blob[:NONCE_BYTES], blob[NONCE_BYTES:]
    return AESGCM(key).decrypt(nonce, ciphertext, aad)
