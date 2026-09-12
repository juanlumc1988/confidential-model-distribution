#!/usr/bin/env python3
"""Consumer: fetch the encrypted artifact, decrypt it, load the model.

    Hub -> artifact.enc -> authenticate + decrypt -> tar -> safetensors -> model

Everything about this program is arranged so that it **fails closed**. The key
is read before anything is downloaded; the ciphertext is authenticated before
anything is unpacked; the archive is extracted through a filter that rejects
paths escaping the destination. At no point does an error become a warning.

The decrypted model is written only into a RAM-backed volume. Nothing here
prevents someone with access to the cluster from reading it out of the pod --
that is Layer 3's problem, not this one -- but the node's disk never sees it.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import tarfile
from pathlib import Path

from artifact_format import KEY_BYTES, associated_data, decrypt

DEFAULT_KEY_PATH = Path("/etc/model-key/model.key")
DEFAULT_WORK_DIR = Path("/scratch")
ARTIFACT_NAME = "artifact.enc"


def log(message: str) -> None:
    print(f"==> {message}", flush=True)


def fail(message: str) -> int:
    print(f"[x] {message}", file=sys.stderr, flush=True)
    return 1


# ---------------------------------------------------------------------------
# Key
# ---------------------------------------------------------------------------


def read_key(path: Path) -> bytes:
    """Read the decryption key from the mounted Secret.

    The file holds the **raw** 32 bytes. Secret values are base64 in the API
    object because that is how the API transports binary, but the kubelet
    decodes them when projecting the volume, so what lands on disk is the key
    itself.

    Read first, before the download, so a missing or malformed key costs
    nothing. The key is mounted as a file rather than injected as an
    environment variable: environment variables are inherited by every child
    process and routinely appear in crash dumps and debug output, while a
    volume mount is scoped to a path and a mode.

    Note the absence of ``.strip()``. Stripping whitespace off a text
    credential is habit; doing it to raw key material is a bug, because a
    random 32-byte key can legitimately begin or end with a byte whose value
    happens to be 0x20 or 0x0a. It would corrupt the key intermittently and
    unreproducibly, and the only symptom would be `InvalidTag` -- the same
    symptom as tampering.
    """
    key = path.read_bytes()
    if len(key) != KEY_BYTES:
        raise ValueError(
            f"expected {KEY_BYTES} raw bytes of AES-256 key, got {len(key)}"
        )
    return key


# ---------------------------------------------------------------------------
# Artifact
# ---------------------------------------------------------------------------


def download_artifact(repo_id: str, dest_dir: Path) -> Path:
    """Fetch ``artifact.enc`` from a public repository.

    No token is passed and none is needed: the repository is public. That is
    the whole claim being demonstrated -- the artifact is available to anyone
    and discloses nothing, because the only secret in the system is the key.
    """
    from huggingface_hub import hf_hub_download

    log(f"Downloading {ARTIFACT_NAME} from {repo_id}")
    return Path(
        hf_hub_download(
            repo_id=repo_id,
            filename=ARTIFACT_NAME,
            repo_type="model",
            local_dir=dest_dir,
        )
    )


def extract(tarball: bytes, dest: Path) -> Path:
    """Unpack the decrypted archive into ``dest``.

    ``filter="data"`` is the point of this function. It refuses absolute paths,
    paths escaping the destination via `..`, symlinks pointing outside it, and
    device or special files. This archive was produced by our own producer, so
    in principle it is trusted -- but a consumer that skips the check because
    of where the input came from is precisely the failure this pipeline exists
    to argue against. The cost is one keyword argument.

    The archive is read straight from memory. An earlier version staged it
    through a temporary file and failed: with ``readOnlyRootFilesystem``, the
    only writable path in this container is the tmpfs at /scratch, and there is
    no usable /tmp. Reading from a buffer removes the need for one, and means
    the decrypted tar never reaches a filesystem at all -- only the extracted
    model files do, in RAM.
    """
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:") as tar:
        tar.extractall(path=dest, filter="data")
    return dest


def load_model(model_dir: Path) -> None:
    """Load the decrypted model and prove it is usable.

    ``use_safetensors=True`` is not decoration: it makes the consumer refuse a
    pickle rather than silently accept one. The producer re-serializes to
    safetensors precisely so this can be demanded here.
    """
    from transformers import AutoModel, AutoTokenizer

    log("Loading the decrypted model")
    model = AutoModel.from_pretrained(model_dir, use_safetensors=True)
    tokenizer = AutoTokenizer.from_pretrained(model_dir)

    params = sum(p.numel() for p in model.parameters())
    log(f"Loaded {model.config.model_type} with {params:,} parameters")

    # A forward pass, so "loaded" means the weights actually work rather than
    # that a directory happened to parse.
    inputs = tokenizer("confidential model distribution", return_tensors="pt")
    outputs = model(**inputs)
    log(f"Forward pass produced {tuple(outputs.last_hidden_state.shape)}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch, decrypt and load an encrypted model artifact.",
    )
    parser.add_argument(
        "--hf-repo-id",
        default=os.environ.get("HF_REPO_ID"),
        required="HF_REPO_ID" not in os.environ,
        help="repository holding the artifact; also bound into its associated data",
    )
    parser.add_argument(
        "--key-path",
        type=Path,
        default=Path(os.environ.get("KEY_PATH", DEFAULT_KEY_PATH)),
        help=f"mounted decryption key (default: {DEFAULT_KEY_PATH})",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path(os.environ.get("WORK_DIR", DEFAULT_WORK_DIR)),
        help=f"RAM-backed scratch space (default: {DEFAULT_WORK_DIR})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        key = read_key(args.key_path)
    except FileNotFoundError:
        return fail(
            f"no decryption key at {args.key_path}. The Secret is not mounted, "
            "so there is nothing to decrypt with. Refusing to continue."
        )
    except (ValueError, OSError) as exc:
        return fail(f"the decryption key at {args.key_path} is unusable: {exc}")
    log(f"Read a {len(key) * 8}-bit key from {args.key_path}")

    args.work_dir.mkdir(parents=True, exist_ok=True)

    # TMPDIR points into the tmpfs (see the Dockerfile), but the directory
    # itself does not exist until something creates it: the volume is mounted
    # empty at every start. torch would otherwise fail at import time.
    tmpdir = os.environ.get("TMPDIR")
    if tmpdir:
        Path(tmpdir).mkdir(parents=True, exist_ok=True)

    # Deliberately broad: a missing repository, a renamed file, a DNS failure
    # and a proxy rejection are all equally fatal here, and all of them should
    # reach the operator as one line rather than as a traceback. Every other
    # failure in this program is reported that way; an unhandled exception on
    # the one step that crosses the network would be the odd one out.
    try:
        artifact_path = download_artifact(args.hf_repo_id, args.work_dir / "download")
        blob = artifact_path.read_bytes()
    except Exception as exc:  # noqa: BLE001
        return fail(
            f"could not fetch {ARTIFACT_NAME} from {args.hf_repo_id}: "
            f"{type(exc).__name__}: {exc}"
        )
    log(f"Artifact is {len(blob):,} bytes")

    from cryptography.exceptions import InvalidTag

    try:
        tarball = decrypt(blob, key, associated_data(args.hf_repo_id))
    except InvalidTag:
        return fail(
            "authentication failed. The artifact, the key or the repository "
            "binding do not match. Nothing has been unpacked."
        )
    except ValueError as exc:
        return fail(f"the artifact is malformed: {exc}")
    log(f"Authenticated and decrypted to {len(tarball):,} bytes")

    model_dir = extract(tarball, args.work_dir / "model")
    log(f"Extracted to {model_dir} (tmpfs -- the node's disk never sees it)")

    load_model(model_dir)
    log("Consumer finished successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
