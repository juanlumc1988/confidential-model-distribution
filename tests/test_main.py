import os
import stat

import pytest

import main
import publish

REPO = "owner/repo"
KEY = bytes(range(32))


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (
        "HF_TOKEN",
        "HF_REPO_ID",
        "MODEL_ID",
        "SECRET_NAME",
        "NAMESPACE",
        "OUT_DIR",
    ):
        monkeypatch.delenv(name, raising=False)


# --- parse_args -------------------------------------------------------------


def test_parse_args_defaults():
    args = main.parse_args(["--hf-repo-id", REPO])
    assert args.model_id == main.artifact.DEFAULT_MODEL_ID
    assert args.secret_name == main.DEFAULT_SECRET_NAME
    assert (args.namespace, args.out_dir) == (None, None)
    assert not args.skip_upload and not args.skip_secret


def test_parse_args_requires_repo():
    with pytest.raises(SystemExit):
        main.parse_args([])


def test_parse_args_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_REPO_ID", REPO)
    monkeypatch.setenv("OUT_DIR", str(tmp_path))
    args = main.parse_args([])
    assert (args.hf_repo_id, args.out_dir) == (REPO, tmp_path)


# --- write_local ------------------------------------------------------------


def test_write_local_raw_key_owner_only(tmp_path):
    old = os.umask(0)  # prove 0600 comes from os.open, not from the umask
    try:
        main.write_local(tmp_path / "out", b"cipher", KEY)
    finally:
        os.umask(old)
    out = tmp_path / "out"
    assert (out / "artifact.enc").read_bytes() == b"cipher"
    assert (out / "model.key").read_bytes() == KEY
    assert stat.S_IMODE((out / "model.key").stat().st_mode) == 0o600


def test_write_local_tightens_existing_key_file(tmp_path):
    # O_CREAT's mode does not apply to a file that already exists.
    key_path = tmp_path / "model.key"
    key_path.write_bytes(b"stale")
    key_path.chmod(0o644)
    main.write_local(tmp_path, b"cipher", KEY)
    assert key_path.read_bytes() == KEY
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600


# --- main -------------------------------------------------------------------


@pytest.fixture
def stubs(monkeypatch):
    calls = []
    monkeypatch.setattr(
        main.artifact,
        "build",
        lambda model_id, repo: calls.append("build") or (KEY, b"cipher"),
    )
    monkeypatch.setattr(publish, "preflight", lambda **kw: calls.append("preflight"))
    monkeypatch.setattr(
        publish, "upload_artifact", lambda **kw: calls.append("upload") or "url"
    )
    monkeypatch.setattr(
        publish,
        "store_key_in_secret",
        lambda key, name, ns: calls.append(("secret", key, name, ns)),
    )
    monkeypatch.setattr(publish, "current_namespace", lambda: "pod-ns")
    return calls


def test_main_without_token_exits_2(stubs):
    assert main.main(["--hf-repo-id", REPO]) == 2
    assert stubs == []


def test_main_preflight_failure_exits_3(stubs, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "tok")

    def fail(**kw):
        raise publish.PreflightError("nope")

    monkeypatch.setattr(publish, "preflight", fail)
    assert main.main(["--hf-repo-id", REPO]) == 3
    assert stubs == []


def test_main_full_run(stubs, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "tok")
    assert main.main(["--hf-repo-id", REPO]) == 0
    assert stubs == [
        "preflight",
        "build",
        "upload",
        ("secret", KEY, main.DEFAULT_SECRET_NAME, "pod-ns"),
    ]


def test_main_offline_writes_local_only(stubs, tmp_path):
    argv = [
        "--hf-repo-id",
        REPO,
        "--skip-upload",
        "--skip-secret",
        "--out-dir",
        str(tmp_path),
    ]
    assert main.main(argv) == 0
    assert stubs == ["build"]
    assert (tmp_path / "model.key").read_bytes() == KEY


def test_main_explicit_namespace(stubs, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "tok")
    assert main.main(["--hf-repo-id", REPO, "--namespace", "ns"]) == 0
    assert stubs[-1][-1] == "ns"
