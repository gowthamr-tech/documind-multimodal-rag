from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from app.storage import LocalStorage, ObjectNotFoundError, ObjectStorage, StorageError
from app.storage.s3 import S3Storage


@pytest.fixture(params=["local", "s3"])
def storage(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ObjectStorage]:
    if request.param == "local":
        yield LocalStorage(tmp_path / "root")
        return
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="test-bucket")
        yield S3Storage("test-bucket", region="us-east-1")


def test_round_trip_and_download(storage: ObjectStorage, tmp_path: Path) -> None:
    src = tmp_path / "src.bin"
    src.write_bytes(b"\x00\x01payload")
    storage.put_file("org/a/docs/1/original", src, "application/octet-stream")
    assert storage.exists("org/a/docs/1/original")
    assert storage.get_bytes("org/a/docs/1/original") == b"\x00\x01payload"
    out = storage.download_to("org/a/docs/1/original", tmp_path / "dl" / "copy.bin")
    assert out.read_bytes() == b"\x00\x01payload"


def test_missing_object(storage: ObjectStorage) -> None:
    assert not storage.exists("org/a/none")
    with pytest.raises(ObjectNotFoundError):
        storage.get_bytes("org/a/none")


def test_delete_prefix(storage: ObjectStorage) -> None:
    for i in range(3):
        storage.put_bytes(f"org/a/docs/1/p{i}", b"x", "text/plain")
    storage.put_bytes("org/a/docs/2/keep", b"x", "text/plain")
    assert storage.delete_prefix("org/a/docs/1/") == 3
    assert storage.exists("org/a/docs/2/keep")


@pytest.mark.parametrize(
    "key", ["../escape", "org/../../etc/passwd", "/abs/path", "org//double", "", "a b"]
)
def test_invalid_keys_rejected(storage: ObjectStorage, key: str) -> None:
    with pytest.raises(StorageError):
        storage.put_bytes(key, b"x", "text/plain")


def test_local_storage_healthcheck(tmp_path: Path) -> None:
    LocalStorage(tmp_path).healthcheck()
