# Litrev

Litrev is a local-first desktop workspace for organizing the papers, notes, evidence, summaries,
and relationships involved in a literature review.

The project currently targets one person's research workflow. The long-term goal is a dependable
research memory system in which every summary, claim, and connection remains traceable to its
source and, where possible, an exact passage.

## Product principles

- **Local by default:** documents and research data stay on the user's computer.
- **Traceable:** derived notes and claims should link back to inspectable evidence.
- **Interoperable:** standard identifiers and bibliography formats should remain importable and
  exportable.
- **Recoverable:** a personal research library must be safe to migrate, back up, and inspect.
- **AI optional:** future AI features should enhance a complete non-AI workflow, not replace it.

## Current status

Litrev is an early technical prototype, not yet a dependable reference manager. Its current
vertical slice can:

1. Start a React interface and local FastAPI service together.
2. Manually add and list books and papers in SQLite.
3. Select a local document, inspect its file details, and confirm or edit its source title.
4. Save the source and original in the managed local library before extracting structured Markdown
   with Anydoc's Rust engine.
5. Reopen the source later to review attachment status, retry a failed extraction, and view saved
   Markdown.

Original document files, converted Markdown, and useful conversion failures are persisted locally.
Visual PDF rendering, durable annotations, metadata import, search, citation graphs, distributable
desktop packaging, and AI assistance are planned work.

The next priority is safeguarded cleanup for failed attachments, followed by richer library
metadata. See the [implementation roadmap](docs/ROADMAP.md).

## Architecture

```text
Tauri 2 desktop shell
└── React + TypeScript + Vite interface
    └── FastAPI service on 127.0.0.1:8765
        ├── SQLite + SQLAlchemy library
        ├── Anydoc Rust document conversion
        └── NetworkX relationship prototype
```

The boundaries are intentional:

- `web/` owns presentation and interaction.
- `src/litrev/api.py` owns the local HTTP contract.
- `src/litrev/domain/` owns research concepts that should not depend on a framework.
- `src/litrev/services/` coordinates application behavior such as document conversion.
- `src/litrev/infrastructure/` owns SQLite, SQLAlchemy, files, and external adapters.
- `src-tauri/` owns desktop lifecycle and packaging.

The Python service currently runs as a separate development process. Packaging it as a managed
Tauri sidecar is required before Litrev can ship as a self-contained desktop application.

## Document processing

[Anydoc](https://github.com/firecrawl/anydoc) is Litrev's local document parser. The official
`firecrawl-anydoc` Python binding executes its Rust core and converts PDF, Word, PowerPoint, Excel,
OpenDocument, RTF, EPUB, and CSV files to GitHub-Flavored Markdown.

Anydoc provides structured extraction, not visual page rendering. Litrev will need a separate PDF
renderer for page-accurate reading, highlighting, and annotation geometry. Scanned or image-only
PDF pages are reported as needing OCR; Litrev does not silently send documents to hosted OCR.

## Repository map

```text
AGENTS.md                    Instructions for coding agents
docs/ROADMAP.md              Ordered product and implementation plan
web/                         React and TypeScript interface
src/litrev/                  Python service, domain, and persistence code
src-tauri/                   Tauri desktop project
tests/                       Python tests
assets/                      Editable source assets
package.json                 Frontend and desktop commands
pyproject.toml               Python package and tooling
mise.toml                    Rust toolchain declaration
```

Lockfiles are committed for each toolchain: `package-lock.json`, `uv.lock`, and
`src-tauri/Cargo.lock`.

## Requirements

- Python 3.14 or newer
- [uv](https://docs.astral.sh/uv/)
- Node.js and npm
- [mise](https://mise.jdx.dev/) or an equivalent Rust installation
- Tauri's operating-system prerequisites

The repository declares the Rust toolchain in `mise.toml` and the Python version in
`.python-version`.

## Setup

```bash
mise install
uv sync
npm install
```

Run the interface and Python service in a browser:

```bash
npm run dev
```

Open `http://127.0.0.1:1420`.

Run the same interface inside Tauri:

```bash
npm run tauri dev
```

Tauri starts the frontend and Python development service through its `beforeDevCommand`.

### Run the browser workflow in Docker

Docker Compose runs the Vite browser interface and FastAPI service; it does not run the native
Tauri window. Build and start the development container in the background:

```bash
docker volume create litrev-data
docker compose up --build -d
```

Open `http://127.0.0.1:1420`. Compose bind-mounts the Python and React source directories. Vite
updates the browser when frontend files change, and Uvicorn restarts the local API when Python or
migration files change; ordinary code edits do not require an image rebuild. Changes to
dependencies, lockfiles, the Dockerfile, or Compose configuration still require
`docker compose up --build -d`.

Follow the development logs or stop the container with:

```bash
docker compose logs -f litrev
docker compose down
```

The external `litrev-data` volume preserves the SQLite library and managed files across container
rebuilds and `docker compose down`; creating it again is harmless. Compose does not delete this
volume. Run `docker volume rm litrev-data` only when the library is backed up and deletion is
intentional.

## Local API

The current development API is intentionally small:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Report local stack health and versions |
| `GET` | `/api/sources` | List source records |
| `GET` | `/api/sources/{source_id}` | Read a source and its attachment states |
| `POST` | `/api/sources` | Create a manual book or paper record |
| `POST` | `/api/imports` | Save a confirmed source and original document |
| `POST` | `/api/attachments/{attachment_id}/convert` | Extract or retry Markdown with Anydoc |
| `GET` | `/api/attachments/{attachment_id}/extracted-text` | Read persisted Markdown |

The service binds to `127.0.0.1:8765`. Application data uses the operating system's standard
per-user data directory through `platformdirs`; on Linux the default library is normally
`~/.local/share/litrev/`. Litrev creates and manages this layout when the service starts:

```text
litrev/
├── litrev.sqlite3
├── attachments/
├── extracted/
├── thumbnails/
└── temporary-imports/
```

Set `LITREV_DATA_DIR` to use a different library root during isolated development or testing. Tests
also inject temporary library roots directly and must never read from or write to the real library.
The database schema is upgraded explicitly with Alembic when the local API starts.

## Private research data

Do not add copyrighted papers or a real research database to Git. When a development file must be
kept inside this repository, put it under `local-data/` (for example,
`local-data/papers/paper.pdf`). The entire directory is ignored. As a second safeguard, `.gitignore`
also excludes PDF files, SQLite database files, and SQLite journal files anywhere in the worktree.

The application database normally lives outside the repository in the operating system's per-user
data directory, as described above. Selecting a document alone does not copy it. Confirming an
import copies the original into the managed `attachments/` directory and writes extracted Markdown
under `extracted/`; neither location is inside this repository by default. Ignore rules prevent new
files from being added to the worktree, but they are not encryption and do not remove a file that
Git was already tracking.

Schema migrations are forward-only because automatically reversing a revision could destroy
research data. Developers create and review new revisions with `uv run alembic revision
--autogenerate -m "description"`; application startup is responsible for applying them. Never run
Alembic commands against the real library while developing a migration.

### Backup and recovery

Litrev does not yet have an in-app backup command. Until it does:

1. Fully close Litrev so SQLite has no active transaction.
2. Copy the entire library directory—not only `litrev.sqlite3`—to a separate backup location.
3. Keep dated backups before application upgrades or consequential imports.
4. To restore, close Litrev, move the current library aside, and copy the complete backup into its
   place. Start Litrev and verify the sources and notes before deleting the moved copy.

Never overwrite the only copy of a library during recovery. A restored older database will be
migrated forward on startup; migrations do not provide a way to recover documents or records that
were already deleted before the backup was made.

## Verification

Python:

```bash
uv run litrev --check
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

React and TypeScript:

```bash
npm run test:web
npm run lint:web
npm run build:web
```

Tauri and Rust:

```bash
mise exec -- cargo check --manifest-path src-tauri/Cargo.toml
```

## Planning and agent handoff

- [Roadmap](docs/ROADMAP.md) defines the milestone order, acceptance criteria, and recommended next
  task.
- [Agent instructions](AGENTS.md) define architectural boundaries, safety rules, checks, and the
  definition of done.

Future agents should begin with those documents, inspect the existing worktree, and work on the
first unchecked milestone unless the user explicitly changes the priority.
