import io
import os
import tarfile

import pytest

import build_artifact
from artifact_format import associated_data, decrypt
from fakes import install_transformers

REPO = "owner/repo"


def make_model(root):
    (root / "sub").mkdir(parents=True)
    (root / "config.json").write_bytes(b"{}")
    (root / "sub" / "vocab.txt").write_bytes(b"a\nb\n")
    return root


def members(tarball):
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:") as tar:
        return tar.getmembers()


def test_normalise_strips_identity_and_time():
    info = tarfile.TarInfo("f")
    info.uid, info.gid, info.uname, info.gname = 1000, 1000, "me", "us"
    info.mtime, info.mode = 123456, 0o777
    out = build_artifact._normalise(info)
    assert (out.uid, out.gid, out.uname, out.gname, out.mtime) == (0, 0, "", "", 0)
    assert out.mode == 0o644


def test_normalise_directory_mode():
    info = tarfile.TarInfo("d")
    info.type = tarfile.DIRTYPE
    info.mode = 0o700
    assert build_artifact._normalise(info).mode == 0o755


def test_build_tar_is_sorted_and_relative(tmp_path):
    src = make_model(tmp_path / "model")
    names = [m.name for m in members(build_artifact.build_tar(src))]
    assert names == ["config.json", "sub", "sub/vocab.txt"]


def test_build_tar_is_deterministic(tmp_path):
    src = make_model(tmp_path / "model")
    first = build_artifact.build_tar(src)
    os.utime(src / "config.json", (1, 1))
    os.chmod(src / "config.json", 0o600)
    assert build_artifact.build_tar(src) == first


@pytest.fixture
def fake_fetch(monkeypatch):
    seen = {}

    def fetch(model_id, dest):
        seen["dest"] = dest
        return make_model(dest)

    monkeypatch.setattr(build_artifact, "fetch_and_normalise", fetch)
    return seen


def test_build_round_trips_and_cleans_up(fake_fetch):
    key, blob = build_artifact.build("some/model", REPO)
    tarball = decrypt(blob, key, associated_data(REPO))
    assert [m.name for m in members(tarball)][0] == "config.json"
    assert not fake_fetch["dest"].exists()


def test_build_refuses_on_round_trip_mismatch(fake_fetch, monkeypatch):
    monkeypatch.setattr(build_artifact, "decrypt", lambda blob, key, aad: b"other")
    with pytest.raises(RuntimeError, match="round trip"):
        build_artifact.build("some/model", REPO)


def test_fetch_requires_safetensors(tmp_path, monkeypatch):
    install_transformers(monkeypatch, writes_safetensors=False)
    with pytest.raises(RuntimeError, match="safetensors"):
        build_artifact.fetch_and_normalise("some/model", tmp_path / "m")


def test_fetch_returns_dest(tmp_path, monkeypatch):
    install_transformers(monkeypatch)
    dest = tmp_path / "m"
    assert build_artifact.fetch_and_normalise("some/model", dest) == dest
    assert (dest / "model.safetensors").exists()
