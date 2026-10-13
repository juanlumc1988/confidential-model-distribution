import base64

import pytest

import publish
from fakes import FakeApiException, FakeHTTPError, install_hf, install_kubernetes

REPO = "owner/repo"
KEY = bytes(range(32))


# --- model card -------------------------------------------------------------


def test_render_model_card(tmp_path):
    template = tmp_path / "card.md"
    template.write_text("{repo_id} {model_id} {artifact_name}", encoding="utf-8")
    assert (
        publish.render_model_card(template, REPO, "m/x") == f"{REPO} m/x artifact.enc"
    )


def test_shipped_model_card_renders():
    # The real template must use only these placeholders, or str.format
    # raises at publish time -- after the expensive build.
    template = (
        publish.Path(__file__).resolve().parent.parent / "producer" / "model_card.md"
    )
    card = publish.render_model_card(template, REPO, "m/x")
    assert REPO in card and "m/x" in card
    assert "{repo_id}" not in card and "{model_id}" not in card


# --- namespace --------------------------------------------------------------


@pytest.mark.parametrize(
    "content, expected",
    [("team-a\n", "team-a"), ("", "default"), (None, "default")],
    ids=["present", "empty", "absent"],
)
def test_current_namespace(tmp_path, monkeypatch, content, expected):
    path = tmp_path / "namespace"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(publish, "NAMESPACE_FILE", path)
    assert publish.current_namespace() == expected


# --- preflight --------------------------------------------------------------


def test_preflight_uploads_model_card(monkeypatch):
    api = install_hf(monkeypatch)
    publish.preflight(REPO, "tok", "card")
    assert api.calls[-1] == ("upload_file", "README.md", REPO, b"card")


@pytest.mark.parametrize(
    "step, exc, match",
    [
        ("fail_whoami", RuntimeError("401"), "token was rejected"),
        ("fail_repo_info", FakeHTTPError("404"), "cannot reach"),
        ("fail_upload", FakeHTTPError("403"), "cannot write"),
    ],
)
def test_preflight_failures(monkeypatch, step, exc, match):
    api = install_hf(monkeypatch)
    setattr(api, step, exc)
    with pytest.raises(publish.PreflightError, match=match):
        publish.preflight(REPO, "tok", "card")


# --- upload -----------------------------------------------------------------


def test_upload_artifact(monkeypatch):
    api = install_hf(monkeypatch)
    url = publish.upload_artifact(REPO, b"cipher", "tok")
    assert api.calls == [("upload_file", "artifact.enc", REPO, b"cipher")]
    assert url == f"https://huggingface.co/{REPO}/blob/main/artifact.enc"


# --- Secret -----------------------------------------------------------------


@pytest.mark.parametrize("in_cluster", [True, False])
def test_store_key_creates_secret(monkeypatch, in_cluster):
    api = install_kubernetes(monkeypatch, in_cluster=in_cluster)
    publish.store_key_in_secret(KEY, "s", "ns")
    ((op, ns, body),) = api.calls
    assert (op, ns, body.type) == ("create", "ns", "Opaque")
    assert (body.metadata.name, body.metadata.namespace) == ("s", "ns")
    assert base64.b64decode(body.data["model.key"]) == KEY


def test_store_key_replaces_on_conflict(monkeypatch):
    api = install_kubernetes(monkeypatch)
    api.create_status = 409
    publish.store_key_in_secret(KEY, "s", "ns")
    assert [c[0] for c in api.calls] == ["create", "replace"]


def test_store_key_propagates_other_errors(monkeypatch):
    api = install_kubernetes(monkeypatch)
    api.create_status = 403
    with pytest.raises(FakeApiException):
        publish.store_key_in_secret(KEY, "s", "ns")
    assert [c[0] for c in api.calls] == ["create"]
