from abc import ABC, abstractmethod
from pathlib import Path
from uuid import uuid4
import os
import re


class StorageInterface(ABC):
    @abstractmethod
    def save(self, file_bytes, filename):
        """Persist bytes and return a storage URL/path."""

    @abstractmethod
    def delete(self, file_url):
        """Delete a persisted file if it exists."""

    def get(self, file_url):
        """Return file bytes for providers that support direct reads."""
        raise NotImplementedError


class LocalFileStorage(StorageInterface):
    def __init__(self, base_path=None, url_prefix=None):
        self.base_path = Path(base_path or os.getenv("LOCAL_STORAGE_PATH", "uploads/invoices"))
        self.url_prefix = (url_prefix or os.getenv("LOCAL_STORAGE_URL_PREFIX", "local://")).rstrip("/")
        self.base_path.mkdir(parents=True, exist_ok=True)

    def save(self, file_bytes, filename):
        safe_name = self._safe_filename(filename)
        stored_name = f"{uuid4().hex}_{safe_name}"
        target = self.base_path / stored_name
        target.write_bytes(file_bytes)
        return f"{self.url_prefix}/{stored_name}"

    def delete(self, file_url):
        try:
            path = self._path_from_url(file_url)
        except ValueError:
            return
        if path.exists() and path.is_file():
            path.unlink()

    def get(self, file_url):
        return self._path_from_url(file_url).read_bytes()

    def _path_from_url(self, file_url):
        if not file_url:
            raise ValueError("file_url is required")

        file_url = str(file_url)
        prefix = f"{self.url_prefix}/"
        if file_url.startswith(prefix):
            relative_name = file_url[len(prefix):]
            candidate = self.base_path / relative_name
        else:
            candidate = Path(file_url)
            if not candidate.is_absolute():
                candidate = self.base_path / candidate

        resolved_base = self.base_path.resolve()
        resolved_candidate = candidate.resolve()
        if resolved_base != resolved_candidate and resolved_base not in resolved_candidate.parents:
            raise ValueError("file_url resolves outside the local storage path")
        return resolved_candidate

    @staticmethod
    def _safe_filename(filename):
        stem = Path(filename or "file.pdf").name
        stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._")
        return stem or "file.pdf"


class S3Storage(StorageInterface):
    def __init__(self, *args, **kwargs):
        self.bucket = os.getenv("S3_BUCKET")
        self.region = os.getenv("S3_REGION")

    def save(self, file_bytes, filename):
        raise NotImplementedError

    def delete(self, file_url):
        raise NotImplementedError

    def get(self, file_url):
        raise NotImplementedError


def get_storage():
    storage_type = os.getenv("STORAGE_TYPE", "local").strip().lower()
    if storage_type == "local":
        return LocalFileStorage()
    if storage_type == "s3":
        return S3Storage()
    raise ValueError(f"Unsupported STORAGE_TYPE: {storage_type}")
