from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_path

DATA_DIRECTORY_ENV = "LITREV_DATA_DIR"


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
