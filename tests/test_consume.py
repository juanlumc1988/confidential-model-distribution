import io
import tarfile

import pytest

import consume
from artifact_format import KEY_BYTES, associated_data, encrypt

REPO = "owner/repo"


# --- read_key ---------------------------------------------------------------


@pytest.mark.parametrize("edge", [b"\n", b" "])
def test_read_key_keeps_whitespace_bytes(tmp_path, edge):
    raw = edge + bytes(range(1, KEY_BYTES - 1)) + edge
    path = tmp_path / "model.key"
    path.write_bytes(raw)
    assert consume.read_key(path) == raw


@pytest.mark.parametrize("length", [0, KEY_BYTES - 1, KEY_BYTES + 1])
def test_read_key_rejects_wrong_length(tmp_path, length):
    path = tmp_path / "model.key"
    path.write_bytes(bytes(length))
    with pytest.raises(ValueError):
        consume.read_key(path)


def test_read_key_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        consume.read_key(tmp_path / "absent")


# --- extract ----------------------------------------------------------------


def make_tar(add) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        add(tar)
    return buf.getvalue()


def add_file(tar, name, data=b"x"):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def add_symlink(tar, name, target):
    info = tarfile.TarInfo(name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    tar.addfile(info)


def test_extract_valid_archive(tmp_path):
    tarball = make_tar(lambda t: add_file(t, "config.json", b"{}"))
    out = consume.extract(tarball, tmp_path / "model")
    assert (out / "config.json").read_bytes() == b"{}"


@pytest.mark.parametrize(
    "add",
    [
        lambda t: add_file(t, "../escape"),
        lambda t: add_symlink(t, "link", "../../etc/passwd"),
    ],
    ids=["dotdot", "symlink-out"],
)
def test_extract_refuses_unsafe_entries(tmp_path, add):
    with pytest.raises(tarfile.FilterError):
        consume.extract(make_tar(add), tmp_path / "model")
    assert not (tmp_path / "escape").exists()


def test_extract_confines_absolute_paths(tmp_path):
    # filter="data" strips the leading "/" rather than refusing the entry.
    out = consume.extract(make_tar(lambda t: add_file(t, "/abs/f")), tmp_path / "model")
    assert (out / "abs" / "f").exists()


# --- main: fail closed ------------------------------------------------------


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A valid key and artifact, with the network and the model load stubbed."""
    tarball = make_tar(lambda t: add_file(t, "config.json", b"{}"))
    key, blob = encrypt(tarball, associated_data(REPO))

    key_path = tmp_path / "model.key"
    key_path.write_bytes(key)
    artifact = tmp_path / "artifact.enc"
    artifact.write_bytes(blob)

    loaded = []
    monkeypatch.delenv("TMPDIR", raising=False)
    monkeypatch.setattr(consume, "download_artifact", lambda repo, dest: artifact)
    monkeypatch.setattr(consume, "load_model", loaded.append)

    work = tmp_path / "work"
    argv = ["--hf-repo-id", REPO, "--key-path", str(key_path), "--work-dir", str(work)]
    return {
        "argv": argv,
        "key_path": key_path,
        "artifact": artifact,
        "work": work,
        "loaded": loaded,
    }


def test_main_succeeds(env):
    assert consume.main(env["argv"]) == 0
    assert (env["work"] / "model" / "config.json").exists()
    assert env["loaded"] == [env["work"] / "model"]


def assert_failed_closed(env):
    assert consume.main(env["argv"]) == 1
    assert not (env["work"] / "model").exists()
    assert env["loaded"] == []


def test_main_no_key(env):
    env["key_path"].unlink()
    assert_failed_closed(env)


def test_main_wrong_key_length(env):
    env["key_path"].write_bytes(bytes(KEY_BYTES - 1))
    assert_failed_closed(env)


def test_main_wrong_key(env):
    env["key_path"].write_bytes(bytes(KEY_BYTES))
    assert_failed_closed(env)


def test_main_tampered_artifact(env):
    blob = bytearray(env["artifact"].read_bytes())
    blob[-1] ^= 0x01
    env["artifact"].write_bytes(bytes(blob))
    assert_failed_closed(env)


def test_main_malformed_artifact(env):
    env["artifact"].write_bytes(b"short")
    assert_failed_closed(env)


def test_main_other_repository(env):
    env["argv"][1] = "owner/other"
    assert_failed_closed(env)


def test_main_download_failure(env, monkeypatch):
    def boom(repo, dest):
        raise OSError("network down")

    monkeypatch.setattr(consume, "download_artifact", boom)
    assert_failed_closed(env)
