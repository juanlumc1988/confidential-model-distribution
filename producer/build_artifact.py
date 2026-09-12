"""Build the encrypted model artifact.

    Hugging Face model  ->  safetensors  ->  deterministic tar  ->  AES-256-GCM

A library module with no entry point of its own: :func:`build` returns the key
and the ciphertext in memory and writes nothing outside a temporary directory
it cleans up. Whether the key is ever put on a filesystem is the caller's
decision, and in the deployed path it is not -- see ``main.py``.

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

import io
import tarfile
import tempfile
from pathlib import Path

from artifact_format import associated_data, decrypt, encrypt

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
# Composition
# ---------------------------------------------------------------------------


def build(model_id: str, hf_repo_id: str, self_check: bool = True) -> tuple[bytes, bytes]:
    """Fetch, normalise, pack and encrypt ``model_id``.

    Returns ``(key, blob)``. Nothing touches the filesystem outside a temporary
    directory that is removed before returning, so the caller decides whether
    the key is ever written down.
    """
    aad = associated_data(hf_repo_id)

    with tempfile.TemporaryDirectory(prefix="producer-") as tmp:
        model_dir = fetch_and_normalise(model_id, Path(tmp) / "model")

        log("Packing into a deterministic tar")
        plaintext = build_tar(model_dir)

        log(f"Encrypting {len(plaintext):,} bytes with AES-256-GCM")
        key, blob = encrypt(plaintext, aad)

        if self_check:
            log("Verifying round trip")
            if decrypt(blob, key, aad) != plaintext:
                raise RuntimeError(
                    "round trip mismatch; refusing to emit the artifact"
                )

    return key, blob
