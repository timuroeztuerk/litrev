from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_path

DATA_DIRECTORY_ENV = "LITREV_DATA_DIR"
SHA256_HEX_LENGTH = 64


class ManagedFileConflictError(RuntimeError):
    pass


class ManagedFileRecoveryError(RuntimeError):
    pass


class ManagedFileCleanupError(RuntimeError):
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

    def verified_file_for(self, checksum: str, managed_path: str) -> Path:
        attachment_path = self.file_for(checksum, managed_path)
        with attachment_path.open("rb") as attachment_file:
            actual_checksum = hashlib.file_digest(attachment_file, "sha256").hexdigest()
        if actual_checksum != checksum:
            raise ManagedFileConflictError(
                f"Managed attachment {managed_path!r} does not match its checksum."
            )
        return attachment_path

    def file_for(self, checksum: str, managed_path: str) -> Path:
        expected_path = self.relative_path_for(checksum)
        if managed_path != expected_path:
            raise ManagedFileConflictError(
                f"Attachment path {managed_path!r} does not match checksum {checksum!r}."
            )

        attachment_path = self.paths.root / expected_path
        _require_safe_managed_path(self.paths.root, attachment_path, "attachment")
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


@dataclass(frozen=True)
class StagedManagedArtifactRemoval:
    root: Path
    directory: Path | None
    files: tuple[tuple[Path, Path], ...]

    def restore(self) -> None:
        try:
            for original, staged in reversed(self.files):
                _require_safe_managed_path(self.root, original, "restore destination")
                if original.exists():
                    raise ManagedFileConflictError(
                        f"Cannot restore managed artifact {original.name!r}; its path is occupied."
                    )
                original.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged, original)
            if self.directory is not None:
                self.directory.rmdir()
        except Exception as error:
            raise ManagedFileRecoveryError(
                "A failed managed artifact removal could not restore every staged file."
            ) from error

    def discard(self) -> None:
        try:
            for _original, staged in self.files:
                staged.unlink(missing_ok=True)
            if self.directory is not None:
                self.directory.rmdir()
        except Exception as error:
            raise ManagedFileCleanupError(
                "The database record was removed, but staged artifact cleanup did not finish."
            ) from error


def stage_managed_attachment_removal(
    paths: LibraryPaths,
    *,
    checksum: str,
    managed_path: str,
    extracted_path: str | None,
) -> StagedManagedArtifactRemoval:
    return stage_managed_attachment_removals(
        paths,
        attachments=[(checksum, managed_path, extracted_path)],
    )


def stage_managed_attachment_removals(
    paths: LibraryPaths,
    *,
    attachments: Iterable[tuple[str, str, str | None]],
) -> StagedManagedArtifactRemoval:
    attachment_store = ManagedAttachmentStore(paths)
    extraction_store = ManagedExtractionStore(paths)
    attachment_values = tuple(attachments)
    candidates: list[tuple[Path, str, str]] = []
    for index, (checksum, managed_path, extracted_path) in enumerate(attachment_values):
        expected_attachment = attachment_store.relative_path_for(checksum)
        if managed_path != expected_attachment:
            raise ManagedFileConflictError(
                f"Attachment path {managed_path!r} does not match checksum {checksum!r}."
            )

        name_prefix = f"{index}-" if len(attachment_values) > 1 else ""
        candidates.append((paths.root / expected_attachment, "original", f"{name_prefix}original"))
        if extracted_path is not None:
            expected_extraction = extraction_store.relative_path_for(checksum)
            if extracted_path != expected_extraction:
                raise ManagedFileConflictError(
                    f"Extracted path {extracted_path!r} does not match checksum {checksum!r}."
                )
            candidates.append(
                (paths.root / expected_extraction, "extracted", f"{name_prefix}extracted")
            )

    existing: list[tuple[Path, str]] = []
    for artifact, label, staged_name in candidates:
        _require_safe_managed_path(paths.root, artifact, label)
        if artifact.exists():
            existing.append((artifact, staged_name))

    if not existing:
        return StagedManagedArtifactRemoval(root=paths.root, directory=None, files=())

    paths.ensure_exists()
    staging_directory = Path(tempfile.mkdtemp(dir=paths.temporary_imports, prefix="removal-"))
    staged_files: list[tuple[Path, Path]] = []
    try:
        for artifact, label in existing:
            staged = staging_directory / label
            os.replace(artifact, staged)
            staged_files.append((artifact, staged))
    except Exception:
        staged_removal = StagedManagedArtifactRemoval(
            root=paths.root,
            directory=staging_directory,
            files=tuple(staged_files),
        )
        staged_removal.restore()
        raise

    return StagedManagedArtifactRemoval(
        root=paths.root,
        directory=staging_directory,
        files=tuple(staged_files),
    )


def _require_safe_managed_path(root: Path, artifact: Path, label: str) -> None:
    try:
        artifact.relative_to(root)
    except ValueError as error:
        raise ManagedFileConflictError(
            f"Managed {label} path is outside the library root."
        ) from error

    current = artifact
    while current != root:
        if current.is_symlink():
            raise ManagedFileConflictError(f"Managed {label} path contains a symbolic link.")
        current = current.parent
    if artifact.exists() and not artifact.is_file():
        raise ManagedFileConflictError(f"Managed {label} path is not a regular file.")


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
