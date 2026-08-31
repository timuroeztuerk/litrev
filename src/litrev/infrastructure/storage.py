from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_path

DATA_DIRECTORY_ENV = "LITREV_DATA_DIR"
SHA256_HEX_LENGTH = 64


class ManagedFileConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class LibraryPaths:
    root: Path
    database: Path
    attachments: Path
    extracted: Path
    thumbnails: Path
    temporary_imports: Path

    @classmethod
    def from_root(cls, root: Path) -> LibraryPaths:
        normalized_root = root.expanduser().resolve()
        return cls(
            root=normalized_root,
            database=normalized_root / "litrev.sqlite3",
            attachments=normalized_root / "attachments",
            extracted=normalized_root / "extracted",
            thumbnails=normalized_root / "thumbnails",
            temporary_imports=normalized_root / "temporary-imports",
        )

    @classmethod
    def default(cls) -> LibraryPaths:
        configured_root = os.environ.get(DATA_DIRECTORY_ENV)
        if configured_root:
            return cls.from_root(Path(configured_root))
        return cls.from_root(user_data_path("litrev", "Litrev"))

    def ensure_exists(self) -> None:
        for directory in (
            self.root,
            self.attachments,
            self.extracted,
            self.thumbnails,
            self.temporary_imports,
        ):
            directory.mkdir(parents=True, exist_ok=True)


class ManagedAttachmentStore:
    def __init__(self, paths: LibraryPaths) -> None:
        self.paths = paths

    def relative_path_for(self, checksum: str) -> str:
        if len(checksum) != SHA256_HEX_LENGTH or any(
            character not in "0123456789abcdef" for character in checksum
        ):
            raise ValueError("A lowercase SHA-256 checksum is required")
        return (Path("attachments") / checksum[:2] / checksum).as_posix()

    def put(self, data: bytes, checksum: str) -> None:
        self.paths.ensure_exists()
        relative_path = self.relative_path_for(checksum)
        destination = self.paths.root / relative_path

        if destination.exists():
            _require_regular_file(destination, relative_path)
            with destination.open("rb") as existing_file:
                existing_checksum = hashlib.file_digest(existing_file, "sha256").hexdigest()
            if existing_checksum != checksum:
                raise ManagedFileConflictError(
                    f"Managed attachment {relative_path!r} does not match its checksum."
                )
            return

        _atomic_replace(self.paths, destination, data, prefix="attachment-")

    def read(self, checksum: str, managed_path: str) -> bytes:
        attachment_path = self.file_for(checksum, managed_path)
        data = attachment_path.read_bytes()
        if hashlib.sha256(data).hexdigest() != checksum:
            raise ManagedFileConflictError(
                f"Managed attachment {managed_path!r} does not match its checksum."
            )
        return data

    def file_for(self, checksum: str, managed_path: str) -> Path:
        expected_path = self.relative_path_for(checksum)
        if managed_path != expected_path:
            raise ManagedFileConflictError(
                f"Attachment path {managed_path!r} does not match checksum {checksum!r}."
            )

        attachment_path = self.paths.root / expected_path
        _require_regular_file(attachment_path, expected_path)
        return attachment_path


class ManagedExtractionStore:
    def __init__(self, paths: LibraryPaths) -> None:
        self.paths = paths

    def relative_path_for(self, checksum: str) -> str:
        if len(checksum) != SHA256_HEX_LENGTH or any(
            character not in "0123456789abcdef" for character in checksum
        ):
            raise ValueError("A lowercase SHA-256 checksum is required")
        return (Path("extracted") / checksum[:2] / f"{checksum}.md").as_posix()

    def put(self, markdown: str, checksum: str) -> str:
        relative_path = self.relative_path_for(checksum)
        destination = self.paths.root / relative_path
        _atomic_replace(
            self.paths,
            destination,
            markdown.encode("utf-8"),
            prefix="extracted-",
        )
        return relative_path

    def read(self, checksum: str, extracted_path: str) -> str:
        expected_path = self.relative_path_for(checksum)
        if extracted_path != expected_path:
            raise ManagedFileConflictError(
                f"Extracted path {extracted_path!r} does not match checksum {checksum!r}."
            )

        markdown_path = self.paths.root / expected_path
        _require_regular_file(markdown_path, expected_path)
        return markdown_path.read_text(encoding="utf-8")


def _require_regular_file(path: Path, relative_path: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise ManagedFileConflictError(
            f"Managed path {relative_path!r} is missing or is not a regular file."
        )


def _atomic_replace(
    paths: LibraryPaths,
    destination: Path,
    data: bytes,
    *,
    prefix: str,
) -> None:
    paths.ensure_exists()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staged_name = tempfile.mkstemp(
        dir=paths.temporary_imports,
        prefix=prefix,
        suffix=".tmp",
    )
    staged_path = Path(staged_name)
    try:
        with os.fdopen(descriptor, "wb") as staged_file:
            staged_file.write(data)
            staged_file.flush()
            os.fsync(staged_file.fileno())
        os.replace(staged_path, destination)
    finally:
        staged_path.unlink(missing_ok=True)
