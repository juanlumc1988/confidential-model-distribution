"""Publish the encrypted artifact and hand the key to the cluster.

Two destinations, deliberately different:

  * The **artifact** goes to a public Hugging Face repository. It is ciphertext,
    so publishing it openly is the point rather than a compromise -- it makes
    the claim testable: the bytes are available to anyone and disclose nothing.

  * The **key** goes straight into a Kubernetes Secret through the API. It is
    never written to a file, never echoed, and never passed through a shell,
    where it would land in history and scrollback.
"""

from __future__ import annotations

import base64
from pathlib import Path

ARTIFACT_NAME = "artifact.enc"
SECRET_KEY_FIELD = "model.key"

# Where the kubelet projects the pod's own namespace. Reading it means the Job
# does not have to be told which namespace it is running in.
NAMESPACE_FILE = Path(
    "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
)


def log(message: str) -> None:
    print(f"==> {message}", flush=True)


# ---------------------------------------------------------------------------
# Hugging Face Hub
# ---------------------------------------------------------------------------


class PreflightError(RuntimeError):
    """The Hub is not ready to receive the artifact. Raised before any work."""


def preflight(repo_id: str, token: str, model_card: str) -> None:
    """Prove the token can write to ``repo_id`` before anything expensive runs.

    Fetching, re-serializing, packing and encrypting take about a minute. A
    token that turns out to lack write permission wastes all of it and fails at
    the last step, so the model card -- a small file that does not go through
    LFS -- is uploaded first and used as the canary. If that write is accepted,
    the artifact upload will be too.
    """
    from huggingface_hub import HfApi
    from huggingface_hub.errors import HfHubHTTPError

    api = HfApi(token=token)

    try:
        who = api.whoami()
    except Exception as exc:  # noqa: BLE001 -- surface anything as one message
        raise PreflightError(f"the Hugging Face token was rejected: {exc}") from exc
    log(f"Authenticated to the Hub as {who.get('name', '<unknown>')}")

    try:
        api.repo_info(repo_id=repo_id, repo_type="model")
    except HfHubHTTPError as exc:
        raise PreflightError(
            f"cannot reach the repository {repo_id}. Create it on the Hub first, "
            f"and check the token is scoped to it. ({exc})"
        ) from exc

    try:
        api.upload_file(
            path_or_fileobj=model_card.encode("utf-8"),
            path_in_repo="README.md",
            repo_id=repo_id,
            repo_type="model",
            commit_message="Update model card",
        )
    except HfHubHTTPError as exc:
        raise PreflightError(
            f"the token cannot write to {repo_id}. On the Hub, under Settings > "
            f"Access Tokens, give the token write access to the contents of this "
            f"repository -- read access is not enough. ({exc})"
        ) from exc
    log(f"Model card published, write access confirmed on {repo_id}")


def upload_artifact(repo_id: str, blob: bytes, token: str) -> str:
    """Upload ``blob`` as ``artifact.enc`` to ``repo_id``.

    The ciphertext is staged through a temporary file rather than an in-memory
    buffer. The Hub's Xet storage backend refuses binary IO buffers and falls
    back to plain HTTP, and there is nothing to protect by keeping it in
    memory: this is the artifact everyone is allowed to download. The *key* is
    what never touches a filesystem.

    The repository is expected to exist already. Creating it here would mean
    the token needed permission to create repositories, and it is deliberately
    scoped to write to this one and nothing else.
    """
    import tempfile

    from huggingface_hub import HfApi

    api = HfApi(token=token)

    log(f"Uploading {ARTIFACT_NAME} to {repo_id} ({len(blob):,} bytes)")
    with tempfile.TemporaryDirectory(prefix="upload-") as tmp:
        staged = Path(tmp) / ARTIFACT_NAME
        staged.write_bytes(blob)
        api.upload_file(
            path_or_fileobj=str(staged),
            path_in_repo=ARTIFACT_NAME,
            repo_id=repo_id,
            repo_type="model",
            commit_message="Publish encrypted model artifact",
        )

    return f"https://huggingface.co/{repo_id}/blob/main/{ARTIFACT_NAME}"


def render_model_card(template: Path, repo_id: str, model_id: str) -> str:
    """Fill in the model card that explains why this repository looks empty.

    Without it a visitor finds an undocumented binary blob, which is a poor
    showing for a repository whose whole purpose is to be inspected.
    """
    return template.read_text(encoding="utf-8").format(
        repo_id=repo_id,
        model_id=model_id,
        artifact_name=ARTIFACT_NAME,
    )


# ---------------------------------------------------------------------------
# Kubernetes
# ---------------------------------------------------------------------------


def current_namespace(default: str = "default") -> str:
    try:
        return NAMESPACE_FILE.read_text(encoding="utf-8").strip() or default
    except OSError:
        return default


def _load_kube_config() -> None:
    """Use the pod's ServiceAccount in-cluster, the kubeconfig outside it."""
    from kubernetes import config

    try:
        config.load_incluster_config()
        log("Using in-cluster Kubernetes credentials")
    except config.ConfigException:
        config.load_kube_config()
        log("Using local kubeconfig")


def store_key_in_secret(key: bytes, name: str, namespace: str) -> None:
    """Write ``key`` into an Opaque Secret, creating or replacing it.

    Existence is probed by attempting the create and catching the conflict
    rather than by reading first. That keeps the Role down to ``create`` and
    ``update``: the producer never needs permission to read secrets, which is
    the permission worth withholding.
    """
    from kubernetes import client
    from kubernetes.client.rest import ApiException

    _load_kube_config()
    api = client.CoreV1Api()

    body = client.V1Secret(
        metadata=client.V1ObjectMeta(name=name, namespace=namespace),
        type="Opaque",
        # The Kubernetes API takes Secret values base64-encoded. This is
        # transport encoding, not protection: a Secret is not encrypted at
        # rest, and anyone with `get secrets` in this namespace can read the
        # key back. Layer 1 trusts the cluster by construction; Layer 3 is
        # what removes that trust.
        data={SECRET_KEY_FIELD: base64.b64encode(key).decode("ascii")},
    )

    try:
        api.create_namespaced_secret(namespace=namespace, body=body)
        log(f"Created Secret {namespace}/{name}")
    except ApiException as exc:
        if exc.status != 409:
            raise
        api.replace_namespaced_secret(name=name, namespace=namespace, body=body)
        log(f"Replaced existing Secret {namespace}/{name}")
