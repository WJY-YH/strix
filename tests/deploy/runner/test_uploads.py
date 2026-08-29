from __future__ import annotations

import io
import zipfile
from typing import TYPE_CHECKING

import pytest

from deploy.runner.uploads import UploadRejected, UploadStore

if TYPE_CHECKING:
    from pathlib import Path


def zip_bytes(name: str = "app.py", content: bytes = b"print('ok')") -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(name, content)
    return stream.getvalue()


def test_save_and_prepare_upload(tmp_path: Path) -> None:
    store = UploadStore(tmp_path)

    record = store.save(io.BytesIO(zip_bytes()), len(zip_bytes()), "project.zip")
    destination = tmp_path / "runs" / "source"

    source = store.prepare(record.upload_id, destination)

    assert source == destination
    assert (source / "app.py").read_bytes() == b"print('ok')"
    store.discard(record.upload_id)
    assert not record.path.exists()


def test_rejects_zip_path_traversal(tmp_path: Path) -> None:
    store = UploadStore(tmp_path)
    payload = zip_bytes("../escape.py")

    with pytest.raises(UploadRejected, match="路径"):
        store.save(io.BytesIO(payload), len(payload), "project.zip")


def test_rejects_symbolic_links(tmp_path: Path) -> None:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        info = zipfile.ZipInfo("link")
        info.external_attr = (0o120777 << 16) | 0xA000
        archive.writestr(info, "target")
    payload = stream.getvalue()
    store = UploadStore(tmp_path)

    with pytest.raises(UploadRejected, match="符号链接"):
        store.save(io.BytesIO(payload), len(payload), "project.zip")


def test_rejects_compressed_size_limit(tmp_path: Path) -> None:
    store = UploadStore(tmp_path, max_bytes=4)

    with pytest.raises(UploadRejected, match="过大"):
        store.save(io.BytesIO(zip_bytes()), len(zip_bytes()), "project.zip")


def test_rejects_uncompressed_size_limit(tmp_path: Path) -> None:
    payload = zip_bytes(content=b"x" * 20)
    store = UploadStore(tmp_path, max_uncompressed_bytes=10)

    with pytest.raises(UploadRejected, match="解压"):
        store.save(io.BytesIO(payload), len(payload), "project.zip")
