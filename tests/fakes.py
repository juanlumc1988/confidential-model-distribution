"""Stand-ins for the third-party clients the producer imports lazily.

``huggingface_hub``, ``kubernetes`` and ``transformers`` are imported inside
the functions that use them, so installing these modules into ``sys.modules``
is enough to run those functions without the real packages or a network.
"""

import sys
import types


class FakeHTTPError(Exception):
    pass


class FakeHfApi:
    """Records calls; each step can be made to fail via class attributes."""

    fail_whoami = None
    fail_repo_info = None
    fail_upload = None
    calls = []

    def __init__(self, token=None):
        self.token = token

    def whoami(self):
        FakeHfApi.calls.append(("whoami",))
        if FakeHfApi.fail_whoami:
            raise FakeHfApi.fail_whoami
        return {"name": "tester"}

    def repo_info(self, repo_id, repo_type):
        FakeHfApi.calls.append(("repo_info", repo_id))
        if FakeHfApi.fail_repo_info:
            raise FakeHfApi.fail_repo_info

    def upload_file(
        self, path_or_fileobj, path_in_repo, repo_id, repo_type, commit_message
    ):
        data = path_or_fileobj
        if isinstance(data, str):  # a path: read it while it still exists
            with open(data, "rb") as handle:
                data = handle.read()
        FakeHfApi.calls.append(("upload_file", path_in_repo, repo_id, data))
        if FakeHfApi.fail_upload:
            raise FakeHfApi.fail_upload


def install_hf(monkeypatch):
    FakeHfApi.fail_whoami = FakeHfApi.fail_repo_info = FakeHfApi.fail_upload = None
    FakeHfApi.calls = []
    hub = types.ModuleType("huggingface_hub")
    hub.HfApi = FakeHfApi
    errors = types.ModuleType("huggingface_hub.errors")
    errors.HfHubHTTPError = FakeHTTPError
    hub.errors = errors
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    monkeypatch.setitem(sys.modules, "huggingface_hub.errors", errors)
    return FakeHfApi


class FakeApiException(Exception):
    def __init__(self, status):
        super().__init__(f"status {status}")
        self.status = status


class FakeConfigException(Exception):
    pass


class FakeCoreV1Api:
    create_status = None  # None: create succeeds; else raise with this status
    calls = []

    def create_namespaced_secret(self, namespace, body):
        FakeCoreV1Api.calls.append(("create", namespace, body))
        if FakeCoreV1Api.create_status is not None:
            raise FakeApiException(FakeCoreV1Api.create_status)

    def replace_namespaced_secret(self, name, namespace, body):
        FakeCoreV1Api.calls.append(("replace", namespace, body))


def install_kubernetes(monkeypatch, in_cluster=True):
    FakeCoreV1Api.create_status = None
    FakeCoreV1Api.calls = []

    def load_incluster_config():
        if not in_cluster:
            raise FakeConfigException()

    pkg = types.ModuleType("kubernetes")
    config = types.ModuleType("kubernetes.config")
    config.ConfigException = FakeConfigException
    config.load_incluster_config = load_incluster_config
    config.load_kube_config = lambda: None
    client = types.ModuleType("kubernetes.client")
    client.CoreV1Api = FakeCoreV1Api
    client.V1Secret = types.SimpleNamespace
    client.V1ObjectMeta = types.SimpleNamespace
    rest = types.ModuleType("kubernetes.client.rest")
    rest.ApiException = FakeApiException
    client.rest = rest
    pkg.config, pkg.client = config, client
    for name, mod in [
        ("kubernetes", pkg),
        ("kubernetes.config", config),
        ("kubernetes.client", client),
        ("kubernetes.client.rest", rest),
    ]:
        monkeypatch.setitem(sys.modules, name, mod)
    return FakeCoreV1Api


def install_transformers(monkeypatch, writes_safetensors=True):
    class FakePretrained:
        @classmethod
        def from_pretrained(cls, model_id):
            return cls()

        def save_pretrained(self, dest, **kwargs):
            if writes_safetensors and type(self).__name__ == "AutoModel":
                (dest / "model.safetensors").write_bytes(b"weights")

    mod = types.ModuleType("transformers")
    mod.AutoModel = type("AutoModel", (FakePretrained,), {})
    mod.AutoTokenizer = type("AutoTokenizer", (FakePretrained,), {})
    monkeypatch.setitem(sys.modules, "transformers", mod)
