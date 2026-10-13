import pytest
from cryptography.exceptions import InvalidTag

from artifact_format import (
    FORMAT_VERSION,
    KEY_BYTES,
    NONCE_BYTES,
    TAG_BYTES,
    associated_data,
    decrypt,
    encrypt,
)

PLAINTEXT = b"model weights"
AAD = associated_data("owner/repo")


def flip(blob: bytes, index: int) -> bytes:
    return blob[:index] + bytes([blob[index] ^ 0x01]) + blob[index + 1 :]


def test_associated_data_binds_version_and_repo():
    assert associated_data("owner/repo") == b"cmd/v1|owner/repo"


def test_round_trip():
    key, blob = encrypt(PLAINTEXT, AAD)
    assert len(key) == KEY_BYTES
    assert len(blob) == NONCE_BYTES + len(PLAINTEXT) + TAG_BYTES
    assert decrypt(blob, key, AAD) == PLAINTEXT


def test_fresh_key_and_nonce_per_call():
    key1, blob1 = encrypt(PLAINTEXT, AAD)
    key2, blob2 = encrypt(PLAINTEXT, AAD)
    assert key1 != key2
    assert blob1[:NONCE_BYTES] != blob2[:NONCE_BYTES]


def test_wrong_key_fails():
    _, blob = encrypt(PLAINTEXT, AAD)
    with pytest.raises(InvalidTag):
        decrypt(blob, bytes(KEY_BYTES), AAD)


def test_other_repository_fails():
    key, blob = encrypt(PLAINTEXT, AAD)
    with pytest.raises(InvalidTag):
        decrypt(blob, key, associated_data("owner/other"))


def test_other_format_version_fails():
    key, blob = encrypt(PLAINTEXT, AAD)
    other = AAD.replace(FORMAT_VERSION, b"cmd/v2")
    with pytest.raises(InvalidTag):
        decrypt(blob, key, other)


@pytest.mark.parametrize(
    "index",
    [0, NONCE_BYTES, -1],
    ids=["nonce", "ciphertext", "tag"],
)
def test_tampered_byte_fails(index):
    key, blob = encrypt(PLAINTEXT, AAD)
    with pytest.raises(InvalidTag):
        decrypt(flip(blob, index % len(blob)), key, AAD)


def test_truncated_blob_fails():
    key, blob = encrypt(PLAINTEXT, AAD)
    with pytest.raises(InvalidTag):
        decrypt(blob[:-1], key, AAD)


@pytest.mark.parametrize("length", [0, NONCE_BYTES + TAG_BYTES - 1])
def test_too_short_blob_is_malformed(length):
    with pytest.raises(ValueError, match="too short"):
        decrypt(bytes(length), bytes(KEY_BYTES), AAD)
