#!/usr/bin/env python3
"""Build the encrypted model artifact.

First stage of the producer:

    Hugging Face model  ->  safetensors  ->  deterministic tar  ->  AES-256-GCM

Publishing to the Hub and storing the key as a Kubernetes Secret are a later
stage. This module stops once the ciphertext and the key exist on disk, so the
encryption step can be exercised and verified on its own.

Three decisions are visible in the code and worth stating here:

  * The model is re-serialized to safetensors before packing. Upstream
    ``pytorch_model.bin`` checkpoints are Python pickles, and loading one is
    arbitrary code execution. Converting in the producer -- once, against a
    known model, on the trusted side of the pipeline -- means the consumer
    never deserializes a pickle at all.

  * One tar, one ciphertext. A single integrity boundary rather than many
    unlinked ones, and only the total size leaks rather than the file names,
    the file count and their individual sizes.

  * AES-GCM rather than an unauthenticated mode. With signing out of scope,
    the GCM tag is the *only* integrity control in the pipeline; a consumer
    given a tampered artifact must fail rather than deserialize it.
"""

from __future__ import annotations

import argparse
import base64
import io
import os
import sys
import tarfile
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Bound into the AES-GCM associated data. Neither value is secret; binding them
# means a ciphertext cannot be silently relocated to a different Hub repository,
# or reinterpreted under a future revision of this format, without the tag
# failing to verify.
FORMAT_VERSION = b"cmd/v1"

KEY_BYTES = 32  # AES-256
NONCE_BYTES = 12  # GCM's native nonce size; no derivation or truncation needed

# Google's smallest published BERT, ~17 MB. `prajjwal1/bert-tiny` is the more
# commonly cited tiny BERT and was the first choice, but its config.json
# predates the `model_type` key that transformers 5.x requires, so AutoModel
# cannot resolve it. This checkpoint is the same size and loads cleanly.
DEFAULT_MODEL_ID = "google/bert_uncased_L-2_H-128_A-2"


def log(message: str) -> None:
    print(f"==> {message}", flush=True)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


def fetch_and_normalise(model_id: str, dest: Path) -> Path:
    """Download ``model_id`` and re-save it as safetensors under ``dest``.

    ``save_pretrained(safe_serialization=True)`` writes ``model.safetensors``
    regardless of the format the upstream repository happens to ship, which is
    the point: the published artifact carries a format the consumer can load
    without executing anything.
    """
    from transformers import AutoModel, AutoTokenizer

    # AutoModel loads the base encoder, so any task head present in the
    # upstream checkpoint (an MLM head, here) is dropped. That is intended:
    # what gets distributed is the encoder, and transformers reports the
    # discarded tensors as UNEXPECTED on load.
    log(f"Fetching {model_id}")
    model = AutoModel.from_pretrained(model_id)
    tokenizer = AutoTokenizer.from_pretrained(model_id)

    dest.mkdir(parents=True, exist_ok=True)
    log(f"Re-serializing to safetensors in {dest}")
    model.save_pretrained(dest, safe_serialization=True)
    tokenizer.save_pretrained(dest)

    if not (dest / "model.safetensors").exists():
        raise RuntimeError(
            "save_pretrained did not produce model.safetensors; refusing to "
            "publish a checkpoint the consumer would have to unpickle"
        )
    return dest


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------


def _normalise(info: tarfile.TarInfo) -> tarfile.TarInfo:
    """Strip everything that would make two builds of the same model differ."""
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.mode = 0o755 if info.isdir() else 0o644
    return info


def build_tar(src: Path) -> bytes:
    """Pack ``src`` into an uncompressed, deterministic tar held in memory.

    Entries are sorted and their metadata normalised, so the same model always
    produces byte-identical output. That costs nothing here and is a
    precondition for any later signing or reproducibility claim.

    Uncompressed on purpose: model weights are dense float tensors with little
    redundancy, so compression returns a few percent for real CPU on both ends.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for path in sorted(src.rglob("*")):
            tar.add(
                path,
                arcname=str(path.relative_to(src)),
                filter=_normalise,
                recursive=False,
            )
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Encryption
# ---------------------------------------------------------------------------


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
    """Inverse of :func:`encrypt`. Used here only for the round-trip check."""
    nonce, ciphertext = blob[:NONCE_BYTES], blob[NONCE_BYTES:]
    return AESGCM(key).decrypt(nonce, ciphertext, aad)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Encrypt a Hugging Face model into a single artifact.",
    )
    parser.add_argument(
        "--model-id",
        default=os.environ.get("MODEL_ID", DEFAULT_MODEL_ID),
        help=f"source model on the Hub (default: {DEFAULT_MODEL_ID})",
    )
    parser.add_argument(
        "--hf-repo-id",
        default=os.environ.get("HF_REPO_ID"),
        required="HF_REPO_ID" not in os.environ,
        help="destination repository, bound into the artifact as associated data",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path(os.environ.get("OUT_DIR", "./out")),
        help="where to write artifact.enc and model.key (default: ./out)",
    )
    parser.add_argument(
        "--skip-self-check",
        action="store_true",
        help="skip the decrypt-and-compare round trip (not recommended)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    aad = associated_data(args.hf_repo_id)

    with tempfile.TemporaryDirectory(prefix="producer-") as tmp:
        model_dir = fetch_and_normalise(args.model_id, Path(tmp) / "model")

        log("Packing into a deterministic tar")
        plaintext = build_tar(model_dir)

        log(f"Encrypting {len(plaintext):,} bytes with AES-256-GCM")
        key, blob = encrypt(plaintext, aad)

    if not args.skip_self_check:
        log("Verifying round trip")
        if decrypt(blob, key, aad) != plaintext:
            raise RuntimeError("round trip mismatch; refusing to emit the artifact")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = args.out_dir / "artifact.enc"
    key_path = args.out_dir / "model.key"

    artifact_path.write_bytes(blob)

    # The key is written through a file descriptor opened 0600 rather than
    # written and then chmod-ed, so it is never briefly world-readable.
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(base64.b64encode(key))

    log(f"Artifact: {artifact_path} ({len(blob):,} bytes)")
    log(f"Key:      {key_path} (base64, mode 0600 -- do not commit)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
