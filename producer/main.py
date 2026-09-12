#!/usr/bin/env python3
"""Producer entry point.

Runs the whole producer side of Layer 1 in one pass:

    fetch model -> safetensors -> deterministic tar -> AES-256-GCM
                -> upload ciphertext to the Hub
                -> write the key into a Kubernetes Secret

The default path never puts the key on a filesystem: it is generated in
memory and handed to the Kubernetes API directly. ``--out-dir`` exists for
local development and for demonstrating the artifact outside a cluster; it
writes the key to disk, and says so.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path

import build_artifact as artifact
import publish

DEFAULT_SECRET_NAME = "model-decryption-key"
MODEL_CARD_TEMPLATE = Path(__file__).with_name("model_card.md")


def log(message: str) -> None:
    print(f"==> {message}", flush=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Encrypt a Hugging Face model, publish it, and store the key.",
    )
    parser.add_argument(
        "--model-id",
        default=os.environ.get("MODEL_ID", artifact.DEFAULT_MODEL_ID),
        help=f"source model on the Hub (default: {artifact.DEFAULT_MODEL_ID})",
    )
    parser.add_argument(
        "--hf-repo-id",
        default=os.environ.get("HF_REPO_ID"),
        required="HF_REPO_ID" not in os.environ,
        help="destination repository, also bound into the artifact as associated data",
    )
    parser.add_argument(
        "--secret-name",
        default=os.environ.get("SECRET_NAME", DEFAULT_SECRET_NAME),
        help=f"Secret to hold the decryption key (default: {DEFAULT_SECRET_NAME})",
    )
    parser.add_argument(
        "--namespace",
        default=os.environ.get("NAMESPACE"),
        help="namespace for the Secret (default: the pod's own, else 'default')",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path(os.environ["OUT_DIR"]) if "OUT_DIR" in os.environ else None,
        help="also write artifact.enc and model.key here (development only)",
    )
    parser.add_argument(
        "--skip-upload",
        action="store_true",
        help="build the artifact but do not publish it",
    )
    parser.add_argument(
        "--skip-secret",
        action="store_true",
        help="build the artifact but do not touch the cluster",
    )
    return parser.parse_args(argv)


def write_local(out_dir: Path, blob: bytes, key: bytes) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "artifact.enc").write_bytes(blob)

    # Opened 0600 rather than written and then chmod-ed: between those two
    # calls the file would briefly exist with default permissions.
    key_path = out_dir / "model.key"
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(base64.b64encode(key))

    log(f"Wrote {out_dir}/artifact.enc")
    log(f"Wrote {key_path} -- key material on disk, delete it when done")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    token = os.environ.get("HF_TOKEN")
    if not args.skip_upload and not token:
        print(
            "HF_TOKEN is not set. Provide a write-scoped Hugging Face token, "
            "or pass --skip-upload to build the artifact without publishing.",
            file=sys.stderr,
        )
        return 2

    # Check the Hub will accept a write before spending a minute building the
    # artifact. A permissions problem should cost a second, not the whole run.
    if not args.skip_upload:
        try:
            publish.preflight(
                repo_id=args.hf_repo_id,
                token=token,
                model_card=publish.render_model_card(
                    MODEL_CARD_TEMPLATE, args.hf_repo_id, args.model_id
                ),
            )
        except publish.PreflightError as exc:
            print(f"Preflight failed: {exc}", file=sys.stderr)
            return 3

    key, blob = artifact.build(args.model_id, args.hf_repo_id)

    if args.out_dir is not None:
        write_local(args.out_dir, blob, key)

    if args.skip_upload:
        log("Skipping upload")
    else:
        url = publish.upload_artifact(
            repo_id=args.hf_repo_id, blob=blob, token=token
        )
        log(f"Published: {url}")

    if args.skip_secret:
        log("Skipping Secret creation")
    else:
        namespace = args.namespace or publish.current_namespace()
        publish.store_key_in_secret(key, args.secret_name, namespace)

    return 0


if __name__ == "__main__":
    sys.exit(main())
