"""Bounded, safe storage and extraction for local ZIP scan sources."""

from __future__ import annotations

import shutil
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO


DEFAULT_MAX_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
DEFAULT_MAX_ENTRIES = 20_000


class UploadRejected(ValueError):  # noqa: N818
    """The uploaded archive is invalid or exceeds a safety limit."""


class UploadNotFound(KeyError):  # noqa: N818
    """No upload exists for the requested identifier."""


@dataclass(frozen=True)
class UploadRecord:
    upload_id: str
    filename: str
    size: int
    path: Path


class UploadStore:
    def __init__(
        self,
        root: Path,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        self.root = root
        self.uploads_dir = root / "uploads"
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.max_uncompressed_bytes = max_uncompressed_bytes
        self.max_entries = max_entries

    def save(self, stream: BinaryIO, content_length: int, filename: str) -> UploadRecord:
        if not isinstance(filename, str) or not filename.lower().endswith(".zip"):
            raise UploadRejected("只支持 ZIP 文件")
        if Path(filename).name != filename or not filename.strip():
            raise UploadRejected("文件名无效")
        if content_length <= 0 or content_length > self.max_bytes:
            raise UploadRejected("ZIP 文件过大")

        upload_id = str(uuid.uuid4())
        temp_path = self.uploads_dir / f".{upload_id}.zip.part"
        final_path = self.uploads_dir / f"{upload_id}.zip"
        written = 0
        try:
            with temp_path.open("wb") as output:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > self.max_bytes:
                        raise UploadRejected("ZIP 文件过大")
                    output.write(chunk)
            if written != content_length:
                raise UploadRejected("上传内容不完整")
            self._validate_archive(temp_path)
            temp_path.replace(final_path)
            return UploadRecord(upload_id, filename, written, final_path)
        except (OSError, zipfile.BadZipFile) as exc:
            if isinstance(exc, UploadRejected):
                raise
            raise UploadRejected("ZIP 文件无效") from exc
        finally:
            temp_path.unlink(missing_ok=True)

    def prepare(self, upload_id: str, destination: Path) -> Path:
        archive_path = self._path_for(upload_id)
        if not archive_path.is_file():
            raise UploadNotFound(upload_id)
        destination = destination.resolve()
        destination.mkdir(parents=True, exist_ok=False)
        try:
            with zipfile.ZipFile(archive_path) as archive:
                infos = self._validate_infos(archive.infolist())
                for info in infos:
                    relative = self._safe_relative(info.filename)
                    target = (destination / relative).resolve()
                    if target != destination and destination not in target.parents:
                        raise UploadRejected("ZIP 包含越界路径")
                    if info.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
        except (OSError, zipfile.BadZipFile) as exc:
            shutil.rmtree(destination, ignore_errors=True)
            if isinstance(exc, UploadRejected):
                raise
            raise UploadRejected("ZIP 解压失败") from exc
        return destination

    def discard(self, upload_id: str) -> None:
        self._path_for(upload_id).unlink(missing_ok=True)

    def _path_for(self, upload_id: str) -> Path:
        try:
            parsed = uuid.UUID(upload_id)
        except (ValueError, AttributeError) as exc:
            raise UploadNotFound(upload_id) from exc
        return self.uploads_dir / f"{parsed}.zip"

    def _validate_archive(self, path: Path) -> None:
        try:
            with zipfile.ZipFile(path) as archive:
                self._validate_infos(archive.infolist())
        except zipfile.BadZipFile as exc:
            raise UploadRejected("ZIP 文件无效") from exc

    def _validate_infos(self, infos: list[zipfile.ZipInfo]) -> list[zipfile.ZipInfo]:
        if not infos:
            raise UploadRejected("ZIP 文件为空")
        if len(infos) > self.max_entries:
            raise UploadRejected("ZIP 文件条目过多")
        total_size = 0
        for info in infos:
            self._safe_relative(info.filename)
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise UploadRejected("ZIP 不允许包含符号链接")
            total_size += max(0, info.file_size)
            if total_size > self.max_uncompressed_bytes:
                raise UploadRejected("ZIP 解压后过大")
        return infos

    @staticmethod
    def _safe_relative(name: str) -> PurePosixPath:
        normalized = name.replace("\\", "/")
        path = PurePosixPath(normalized)
        if not normalized or path.is_absolute() or ".." in path.parts:
            raise UploadRejected("ZIP 包含不安全路径")
        return path
