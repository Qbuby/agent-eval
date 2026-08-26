from __future__ import annotations

import codecs
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_eval.config import settings
from agent_eval.db_models.tables import OmniAgentArtifactRow
from agent_eval.omniagent_runtime.events import append_event
from agent_eval.omniagent_runtime.policy import DEFAULT_POLICY

TEMPORARY_RETENTION = timedelta(days=7)
PINNED_RETENTION = timedelta(days=90)
CHUNK_SIZE = 1024 * 1024

_ALLOWED_MIMES: dict[str, set[str]] = {
    ".csv": {"text/csv", "text/plain", "application/vnd.ms-excel"},
    ".json": {"application/json", "text/json", "text/plain"},
    ".jsonl": {"application/json", "application/x-ndjson", "text/plain"},
    ".txt": {"text/plain"},
    ".md": {"text/markdown", "text/plain"},
    ".xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/zip",
        "application/octet-stream",
    },
    ".pdf": {"application/pdf"},
    ".png": {"image/png"},
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
}
_TEXT_EXTENSIONS = frozenset({".csv", ".json", ".jsonl", ".txt", ".md"})
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


class ArtifactError(ValueError):
    pass


class ArtifactTooLarge(ArtifactError):
    pass


class ArtifactQuarantined(ArtifactError):
    pass


class AsyncReadable(Protocol):
    async def read(self, size: int = -1) -> bytes: ...


@dataclass(frozen=True)
class StoredObject:
    object_key: str
    path: Path
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ScanResult:
    clean: bool
    engine: str
    details: dict[str, Any]


class ArtifactScanner(Protocol):
    async def scan(self, path: Path) -> ScanResult: ...


class DevelopmentScanner:
    """Explicit local-only scanner. It is never selected by default."""

    async def scan(self, path: Path) -> ScanResult:
        return ScanResult(True, "development", {"mode": "development-no-antivirus"})


class FailClosedScanner:
    def __init__(self, mode: str) -> None:
        self.mode = mode

    async def scan(self, path: Path) -> ScanResult:
        return ScanResult(False, self.mode, {"error": "scanner is not configured"})


class ClamAVScanner:
    """Scan a local staging file with the deployment-owned ClamAV executable."""

    async def scan(self, path: Path) -> ScanResult:
        command = settings.omniagent.clamav_command.strip()
        if not command or shutil.which(command) is None:
            return ScanResult(False, "clamav", {"error": "ClamAV is not installed"})
        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                [command, "--no-summary", "--infected", "--", str(path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return ScanResult(False, "clamav", {"error": type(exc).__name__})
        output = (completed.stdout + completed.stderr).strip()[:2000]
        if completed.returncode == 0:
            return ScanResult(True, "clamav", {"result": "clean"})
        if completed.returncode == 1:
            return ScanResult(False, "clamav", {"result": "infected", "detail": output})
        return ScanResult(False, "clamav", {"error": "scanner failure", "detail": output})


def scanner_from_settings() -> ArtifactScanner:
    if settings.omniagent.artifact_scanner == "development":
        return DevelopmentScanner()
    if settings.omniagent.artifact_scanner == "clamav":
        return ClamAVScanner()
    return FailClosedScanner(settings.omniagent.artifact_scanner)


def sanitize_filename(filename: str) -> tuple[str, str]:
    if not isinstance(filename, str):
        raise ArtifactError("filename must be text")
    name = filename.strip()
    if (
        not name
        or name in {".", ".."}
        or "\x00" in name
        or "/" in name
        or "\\" in name
        or _WINDOWS_DRIVE.match(name)
        or len(name.encode("utf-8")) > 512
    ):
        raise ArtifactError("invalid filename")
    extension = Path(name).suffix.lower()
    if extension not in _ALLOWED_MIMES:
        raise ArtifactError(f"unsupported file extension: {extension or '<none>'}")
    return name, extension


def _normalized_mime(value: str | None) -> str:
    return (value or "application/octet-stream").split(";", 1)[0].strip().lower()


def _validate_text(path: Path, extension: str) -> str:
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            if b"\x00" in chunk:
                raise ArtifactError("text file contains NUL bytes")
            try:
                decoder.decode(chunk)
            except UnicodeDecodeError as exc:
                raise ArtifactError("text file must be UTF-8") from exc
        try:
            decoder.decode(b"", final=True)
        except UnicodeDecodeError as exc:
            raise ArtifactError("text file must be UTF-8") from exc
    if extension == ".json" and path.stat().st_size <= 10 * 1024 * 1024:
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ArtifactError("invalid JSON document") from exc
    return {
        ".csv": "text/csv",
        ".json": "application/json",
        ".jsonl": "application/x-ndjson",
        ".md": "text/markdown",
    }.get(extension, "text/plain")


def validate_file_type(path: Path, extension: str, declared_mime: str | None) -> str:
    mime = _normalized_mime(declared_mime)
    if mime not in _ALLOWED_MIMES[extension]:
        raise ArtifactError("declared MIME type does not match the extension")
    with path.open("rb") as handle:
        header = handle.read(16)
    if extension in _TEXT_EXTENSIONS:
        return _validate_text(path, extension)
    if extension == ".pdf" and not header.startswith(b"%PDF-"):
        raise ArtifactError("PDF magic bytes do not match")
    if extension == ".png" and not header.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ArtifactError("PNG magic bytes do not match")
    if extension in {".jpg", ".jpeg"} and not header.startswith(b"\xff\xd8\xff"):
        raise ArtifactError("JPEG magic bytes do not match")
    if extension == ".xlsx":
        if not header.startswith(b"PK\x03\x04"):
            raise ArtifactError("XLSX container magic bytes do not match")
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                if "[Content_Types].xml" not in names or not any(
                    name.startswith("xl/") for name in names
                ):
                    raise ArtifactError("ZIP file is not an XLSX workbook")
        except zipfile.BadZipFile as exc:
            raise ArtifactError("invalid XLSX container") from exc
    return {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }[extension]


class FilesystemArtifactStore:
    def __init__(self, root: str | Path | None = None) -> None:
        raw_root = Path(root or settings.omniagent.artifact_root)
        if raw_root.exists() and raw_root.is_symlink():
            raise ArtifactError("artifact root cannot be a symlink")
        raw_root.mkdir(parents=True, exist_ok=True)
        self.root = raw_root.resolve(strict=True)

    def _path(self, object_key: str, *, must_exist: bool = False) -> Path:
        if not object_key or "\\" in object_key:
            raise ArtifactError("invalid object key")
        pure = PurePosixPath(object_key)
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
            raise ArtifactError("invalid object key")
        candidate = self.root.joinpath(*pure.parts)
        current = candidate.parent
        while current != self.root:
            if current.exists() and current.is_symlink():
                raise ArtifactError("artifact path crosses a symlink")
            current = current.parent
        if must_exist:
            if not candidate.exists() or candidate.is_symlink():
                raise ArtifactError("artifact object is unavailable")
            resolved = candidate.resolve(strict=True)
        else:
            resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(self.root):
            raise ArtifactError("artifact path escapes the storage root")
        return candidate

    async def write_stream(
        self,
        object_key: str,
        source: AsyncReadable,
        *,
        max_bytes: int = DEFAULT_POLICY.artifact_file_bytes,
    ) -> StoredObject:
        target = self._path(object_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging_dir = self.root / ".staging"
        staging_dir.mkdir(exist_ok=True)
        staging = staging_dir / f"{uuid.uuid4().hex}.part"
        digest = hashlib.sha256()
        size = 0
        try:
            with staging.open("xb") as handle:
                while True:
                    chunk = await source.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise ArtifactTooLarge("artifact exceeds the per-file limit")
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(staging, target)
        except Exception:
            staging.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            raise
        return StoredObject(object_key, target, size, digest.hexdigest())

    def path_for_read(self, object_key: str) -> Path:
        return self._path(object_key, must_exist=True).resolve(strict=True)

    def delete(self, object_key: str) -> None:
        path = self._path(object_key)
        if path.exists() and not path.is_symlink():
            path.unlink()

    def cleanup_read(self, path: Path) -> None:
        """Filesystem reads return the authoritative object, so no cleanup is needed."""

    def copy_from(
        self,
        source: Path,
        object_key: str,
        *,
        max_bytes: int = DEFAULT_POLICY.artifact_job_output_bytes,
    ) -> StoredObject:
        if source.is_symlink():
            raise ArtifactError("output cannot be a symbolic link")
        source = source.resolve(strict=True)
        if not source.is_file():
            raise ArtifactError("output is not a regular file")
        target = self._path(object_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        try:
            with source.open("rb") as src, target.open("xb") as dst:
                while chunk := src.read(CHUNK_SIZE):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ArtifactTooLarge("job outputs exceed the configured limit")
                    digest.update(chunk)
                    dst.write(chunk)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return StoredObject(object_key, target, size, digest.hexdigest())


class MinioArtifactStore:
    """Server-side MinIO adapter; credentials never leave Agent Eval."""

    def __init__(self) -> None:
        try:
            from minio import Minio
        except ImportError as exc:
            raise ArtifactError("MinIO adapter dependency is unavailable") from exc
        cfg = settings.omniagent
        if not cfg.minio_endpoint or not cfg.minio_access_key or not cfg.minio_secret_key:
            raise ArtifactError("MinIO storage is not configured")
        self.bucket = cfg.minio_bucket
        self._client = Minio(
            cfg.minio_endpoint,
            access_key=cfg.minio_access_key,
            secret_key=cfg.minio_secret_key,
            secure=cfg.minio_secure,
        )
        if not self._client.bucket_exists(self.bucket):
            self._client.make_bucket(self.bucket)

    async def write_stream(
        self,
        object_key: str,
        source: AsyncReadable,
        *,
        max_bytes: int = DEFAULT_POLICY.artifact_file_bytes,
    ) -> StoredObject:
        _validate_object_key(object_key)
        staging = Path(tempfile.mkstemp(prefix="omniagent-", suffix=".part")[1])
        digest = hashlib.sha256()
        size = 0
        try:
            with staging.open("wb") as handle:
                while chunk := await source.read(CHUNK_SIZE):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ArtifactTooLarge("artifact exceeds the per-file limit")
                    digest.update(chunk)
                    handle.write(chunk)
            await asyncio.to_thread(
                self._client.fput_object, self.bucket, object_key, str(staging)
            )
            return StoredObject(object_key, staging, size, digest.hexdigest())
        except Exception:
            staging.unlink(missing_ok=True)
            raise

    def path_for_read(self, object_key: str) -> Path:
        _validate_object_key(object_key)
        staging = Path(tempfile.mkstemp(prefix="omniagent-read-", suffix=".part")[1])
        try:
            self._client.fget_object(self.bucket, object_key, str(staging))
        except Exception:
            staging.unlink(missing_ok=True)
            raise ArtifactError("artifact object is unavailable")
        return staging

    def delete(self, object_key: str) -> None:
        _validate_object_key(object_key)
        self._client.remove_object(self.bucket, object_key)

    def cleanup_read(self, path: Path) -> None:
        path.unlink(missing_ok=True)

    def copy_from(
        self,
        source: Path,
        object_key: str,
        *,
        max_bytes: int = DEFAULT_POLICY.artifact_job_output_bytes,
    ) -> StoredObject:
        if source.is_symlink() or not source.is_file():
            raise ArtifactError("output is not a regular file")
        size = source.stat().st_size
        if size > max_bytes:
            raise ArtifactTooLarge("job outputs exceed the configured limit")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        self._client.fput_object(self.bucket, object_key, str(source))
        return StoredObject(object_key, source, size, digest)


def _validate_object_key(object_key: str) -> None:
    pure = PurePosixPath(object_key)
    if (
        not object_key
        or "\\" in object_key
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise ArtifactError("invalid object key")


def store_from_settings() -> FilesystemArtifactStore | MinioArtifactStore:
    if settings.omniagent.artifact_storage == "filesystem":
        return FilesystemArtifactStore()
    if settings.omniagent.artifact_storage == "minio":
        return MinioArtifactStore()
    raise ArtifactError("configured artifact storage adapter is unavailable")


def artifact_dict(row: OmniAgentArtifactRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "session_id": str(row.session_id) if row.session_id else None,
        "job_id": str(row.job_id) if row.job_id else None,
        "state": row.state,
        "filename": row.filename,
        "mime_type": row.mime_type,
        "extension": row.extension,
        "size_bytes": row.size_bytes,
        "sha256": row.sha256,
        "retention": row.retention,
        "expires_at": row.expires_at,
        "created_at": row.created_at,
    }


def owner_filter(column: Any, owner_id: uuid.UUID | None) -> Any:
    return column.is_(None) if owner_id is None else column == owner_id


async def ingest_artifact(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    owner_id: uuid.UUID | None,
    filename: str,
    declared_mime: str | None,
    source: AsyncReadable,
    session_id: uuid.UUID | None = None,
    store: FilesystemArtifactStore | MinioArtifactStore | None = None,
    scanner: ArtifactScanner | None = None,
) -> OmniAgentArtifactRow:
    name, extension = sanitize_filename(filename)
    artifact_id = uuid.uuid4()
    object_key = f"{tenant_id}/{owner_id or 'system'}/{artifact_id}/content{extension}"
    row = OmniAgentArtifactRow(
        id=artifact_id,
        tenant_id=tenant_id,
        owner_id=owner_id,
        session_id=session_id,
        state="uploading",
        filename=name,
        mime_type=_normalized_mime(declared_mime),
        extension=extension,
        size_bytes=0,
        object_key=object_key,
        retention="temporary",
        expires_at=datetime.now(timezone.utc) + TEMPORARY_RETENTION,
    )
    db.add(row)
    await db.flush()
    storage = store or store_from_settings()
    stored = await storage.write_stream(object_key, source)
    row.size_bytes = stored.size_bytes
    row.sha256 = stored.sha256
    try:
        row.mime_type = validate_file_type(stored.path, extension, declared_mime)
    except ArtifactError as exc:
        row.state = "quarantined"
        row.scan_result = {"clean": False, "engine": "type-validator", "error": str(exc)}
        await append_event(
            db,
            tenant_id=tenant_id,
            user_id=owner_id,
            session_id=session_id,
            event_type="artifact.quarantined",
            entity_type="artifact",
            entity_id=str(row.id),
            payload={"state": row.state, "filename": row.filename, "reason": "type_mismatch"},
        )
        if isinstance(storage, MinioArtifactStore):
            stored.path.unlink(missing_ok=True)
        return row

    used = int(
        (
            await db.execute(
                select(func.coalesce(func.sum(OmniAgentArtifactRow.size_bytes), 0)).where(
                    OmniAgentArtifactRow.tenant_id == tenant_id,
                    owner_filter(OmniAgentArtifactRow.owner_id, owner_id),
                    OmniAgentArtifactRow.state.in_(["available", "scanning", "uploading"]),
                    OmniAgentArtifactRow.id != row.id,
                )
            )
        ).scalar_one()
    )
    if used + stored.size_bytes > DEFAULT_POLICY.artifact_storage_user_bytes:
        row.state = "quarantined"
        row.scan_result = {"clean": False, "engine": "quota", "error": "QUOTA_EXCEEDED"}
        if isinstance(storage, MinioArtifactStore):
            stored.path.unlink(missing_ok=True)
        return row

    row.state = "scanning"
    result = await (scanner or scanner_from_settings()).scan(stored.path)
    row.scan_result = {"clean": result.clean, "engine": result.engine, **result.details}
    row.state = "available" if result.clean else "quarantined"
    if isinstance(storage, MinioArtifactStore):
        stored.path.unlink(missing_ok=True)
    await append_event(
        db,
        tenant_id=tenant_id,
        user_id=owner_id,
        session_id=session_id,
        event_type=f"artifact.{row.state}",
        entity_type="artifact",
        entity_id=str(row.id),
        payload={
            "state": row.state,
            "filename": row.filename,
            "size_bytes": row.size_bytes,
            "sha256": row.sha256,
        },
    )
    return row


async def get_owned_artifact(
    db: AsyncSession,
    *,
    artifact_id: uuid.UUID,
    tenant_id: uuid.UUID,
    owner_id: uuid.UUID | None,
    require_available: bool = False,
) -> OmniAgentArtifactRow | None:
    stmt = select(OmniAgentArtifactRow).where(
        OmniAgentArtifactRow.id == artifact_id,
        OmniAgentArtifactRow.tenant_id == tenant_id,
        owner_filter(OmniAgentArtifactRow.owner_id, owner_id),
    )
    if require_available:
        stmt = stmt.where(OmniAgentArtifactRow.state == "available")
    return (await db.execute(stmt)).scalar_one_or_none()


async def pin_artifact(row: OmniAgentArtifactRow) -> None:
    if row.state != "available":
        raise ArtifactQuarantined("only available artifacts can be pinned")
    row.retention = "pinned"
    row.pinned_at = datetime.now(timezone.utc)
    row.expires_at = row.pinned_at + PINNED_RETENTION


async def expire_artifacts(db: AsyncSession, *, limit: int = 200) -> int:
    """Mark due objects expired and delete their bytes through the configured adapter."""
    now = datetime.now(timezone.utc)
    rows = list(
        (
            await db.execute(
                select(OmniAgentArtifactRow)
                .where(
                    OmniAgentArtifactRow.state.in_(["available", "quarantined"]),
                    OmniAgentArtifactRow.expires_at <= now,
                )
                .order_by(OmniAgentArtifactRow.expires_at)
                .with_for_update(skip_locked=True)
                .limit(max(1, min(limit, 1000)))
            )
        ).scalars()
    )
    if not rows:
        return 0
    store = store_from_settings()
    for row in rows:
        try:
            store.delete(row.object_key)
        except Exception as exc:
            row.scan_result = {**(row.scan_result or {}), "delete_error": type(exc).__name__}
            continue
        row.state = "expired"
        await append_event(
            db,
            tenant_id=row.tenant_id,
            user_id=row.owner_id,
            session_id=row.session_id,
            event_type="artifact.expired",
            entity_type="artifact",
            entity_id=str(row.id),
            payload={"state": "expired", "filename": row.filename},
        )
    return sum(row.state == "expired" for row in rows)


class ArtifactLifecycleWorker:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if not settings.omniagent.product_plane_enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="omniagent-artifact-lifecycle")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _loop(self) -> None:
        from agent_eval.db import async_session_factory

        while not self._stopping.is_set():
            try:
                async with async_session_factory() as db:
                    changed = await expire_artifacts(db)
                    await db.commit()
            except asyncio.CancelledError:
                raise
            except Exception:
                changed = 0
            await asyncio.sleep(5 if changed else 300)
