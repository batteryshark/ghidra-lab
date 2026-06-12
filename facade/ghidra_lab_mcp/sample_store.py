from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Iterable
from uuid import uuid4


SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9_.-]+")


class StoreError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_segment(value: str | None, default: str) -> str:
    raw = (value or default).strip()
    cleaned = SAFE_SEGMENT.sub("_", raw).strip("._")
    if cleaned in {"", ".", ".."}:
        raise StoreError(f"Invalid path segment: {value!r}")
    return cleaned[:128]


def clean_filename(value: str) -> str:
    name = Path(value).name.strip()
    if not name:
        raise StoreError("filename is required")
    cleaned = SAFE_SEGMENT.sub("_", name).strip("._")
    if cleaned in {"", ".", ".."}:
        raise StoreError(f"Invalid filename: {value!r}")
    return cleaned[:180]


class SampleStore:
    def __init__(self, root: Path, public_base_url: str, max_upload_bytes: int) -> None:
        self.root = root
        self.public_base_url = public_base_url.rstrip("/")
        self.max_upload_bytes = max_upload_bytes
        self.index_dir = self.root / "index"
        self.tmp_dir = self.root / "tmp"
        self.samples_dir = self.root / "samples"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        for path in (self.root, self.index_dir, self.tmp_dir, self.samples_dir):
            path.mkdir(parents=True, exist_ok=True)

    def _manifest_path(self, sample_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", sample_id):
            raise StoreError("Invalid sample_id", status_code=404)
        return self.index_dir / f"{sample_id}.json"

    def _read_manifest(self, sample_id: str) -> dict:
        path = self._manifest_path(sample_id)
        if not path.exists():
            raise StoreError("sample_id not found", status_code=404)
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_manifest(self, manifest: dict) -> dict:
        path = self._manifest_path(manifest["sample_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_name, path)
        return manifest

    def _with_urls(self, manifest: dict) -> dict:
        # The per-sample token is never echoed in normal records; callers that
        # need a working URL build it from peek_token().
        record = dict(manifest)
        record.pop("token", None)
        sample_id = record["sample_id"]
        if record.get("status") == "pending":
            record["upload_url"] = f"{self.public_base_url}/uploads/{sample_id}"
        if record.get("status") == "complete":
            record["download_url"] = f"{self.public_base_url}/downloads/{sample_id}"
        return record

    def peek_token(self, sample_id: str) -> str | None:
        try:
            return self._read_manifest(sample_id).get("token")
        except StoreError:
            return None

    def verify_token(self, sample_id: str, token: str | None) -> bool:
        if not token:
            return False
        actual = self.peek_token(sample_id)
        return bool(actual) and secrets.compare_digest(actual, token)

    def create_upload(
        self,
        filename: str,
        project: str = "default",
        collection: str = "default",
        size: int | None = None,
        sha256: str | None = None,
    ) -> dict:
        if size is not None and size < 0:
            raise StoreError("size must be positive")
        if size is not None and size > self.max_upload_bytes:
            raise StoreError(f"declared size exceeds limit of {self.max_upload_bytes} bytes")
        if sha256 is not None and not re.fullmatch(r"[A-Fa-f0-9]{64}", sha256):
            raise StoreError("sha256 must be a 64 character hex digest")

        sample_id = uuid4().hex
        manifest = {
            "sample_id": sample_id,
            "status": "pending",
            "token": secrets.token_urlsafe(24),
            "project": clean_segment(project, "default"),
            "collection": clean_segment(collection, "default"),
            "filename": clean_filename(filename),
            "declared_size": size,
            "declared_sha256": sha256.lower() if sha256 else None,
            "created_at": utc_now(),
            "imports": [],
        }
        return self._with_urls(self._write_manifest(manifest))

    async def save_upload(self, sample_id: str, chunks: AsyncIterator[bytes]) -> dict:
        manifest = self._read_manifest(sample_id)
        if manifest.get("status") != "pending":
            raise StoreError("sample upload is not pending")

        digest = hashlib.sha256()
        total = 0
        tmp_path = self.tmp_dir / f"{sample_id}.part"
        try:
            with tmp_path.open("wb") as handle:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > self.max_upload_bytes:
                        raise StoreError(f"upload exceeds limit of {self.max_upload_bytes} bytes", status_code=413)
                    digest.update(chunk)
                    handle.write(chunk)

            actual_sha = digest.hexdigest()
            declared_size = manifest.get("declared_size")
            declared_sha = manifest.get("declared_sha256")
            if declared_size is not None and total != declared_size:
                raise StoreError(f"size mismatch: expected {declared_size}, got {total}")
            if declared_sha is not None and actual_sha != declared_sha:
                raise StoreError(f"sha256 mismatch: expected {declared_sha}, got {actual_sha}")

            project = clean_segment(manifest.get("project"), "default")
            collection = clean_segment(manifest.get("collection"), "default")
            filename = clean_filename(manifest.get("filename", "sample.bin"))
            target_dir = (
                self.samples_dir
                / project
                / collection
                / actual_sha
            )
            target_dir.mkdir(parents=True, exist_ok=True)
            target_path = target_dir / filename
            shutil.move(str(tmp_path), target_path)

            manifest.update(
                {
                    "status": "complete",
                    "project": project,
                    "collection": collection,
                    "filename": filename,
                    "size": total,
                    "sha256": actual_sha,
                    "container_path": str(target_path),
                    "completed_at": utc_now(),
                }
            )
            return self._with_urls(self._write_manifest(manifest))
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def list_samples(
        self,
        project: str | None = None,
        collection: str | None = None,
        include_pending: bool = False,
        limit: int = 50,
    ) -> dict:
        clean_project = clean_segment(project, "default") if project else None
        clean_collection = clean_segment(collection, "default") if collection else None
        records = []
        for manifest in self._iter_manifests():
            if not include_pending and manifest.get("status") == "pending":
                continue
            if clean_project and manifest.get("project") != clean_project:
                continue
            if clean_collection and manifest.get("collection") != clean_collection:
                continue
            records.append(self._with_urls(manifest))

        records.sort(
            key=lambda item: item.get("completed_at") or item.get("created_at") or "",
            reverse=True,
        )
        bounded_limit = max(0, min(limit, 500))
        samples = records[:bounded_limit]
        return {"samples": samples, "count": len(samples), "total": len(records)}

    def get_sample(self, sample_id: str) -> dict:
        return self._with_urls(self._read_manifest(sample_id))

    def get_download_path(self, sample_id: str) -> tuple[Path, dict]:
        manifest = self._read_manifest(sample_id)
        if manifest.get("status") != "complete":
            raise StoreError("sample upload is not complete", status_code=404)
        path = Path(manifest["container_path"])
        if not path.exists():
            raise StoreError("sample file is missing on disk", status_code=404)
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise StoreError("sample path escaped sample root", status_code=500)
        return path, manifest

    def delete_sample(self, sample_id: str, delete_file: bool = False) -> dict:
        manifest_path = self._manifest_path(sample_id)
        manifest = self._read_manifest(sample_id)
        target = Path(manifest["container_path"]) if manifest.get("container_path") else None
        if target and not target.resolve().is_relative_to(self.root.resolve()):
            raise StoreError("sample path escaped sample root", status_code=500)
        manifest_path.unlink()

        file_deleted = False
        if delete_file and target and target.exists() and not self._path_is_referenced(target):
            target.unlink()
            file_deleted = True
        return {"deleted": sample_id, "file_deleted": file_deleted}

    def append_import(self, sample_id: str, result: dict) -> dict:
        manifest = self._read_manifest(sample_id)
        imports = list(manifest.get("imports", []))
        imports.append({"created_at": utc_now(), "result": result})
        manifest["imports"] = imports
        manifest["last_import_at"] = imports[-1]["created_at"]
        return self._with_urls(self._write_manifest(manifest))

    def _iter_manifests(self) -> Iterable[dict]:
        self._ensure_dirs()
        for path in self.index_dir.glob("*.json"):
            try:
                yield json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue

    def _path_is_referenced(self, target: Path) -> bool:
        target_str = str(target)
        for manifest in self._iter_manifests():
            if manifest.get("container_path") == target_str:
                return True
        return False
