# Litrev

Litrev is a local-first desktop workspace for organizing the sources, notes, evidence, and
relationships involved in a literature review. It currently targets one researcher and is still
an early prototype, not a dependable reference manager.

The long-term goal is a research memory system in which every summary, claim, and connection can
be traced to its source and, where possible, an exact passage.

## Principles

- **Local by default:** documents and research data stay on the user's computer.
- **Traceable:** derived work should link back to inspectable evidence.
- **Interoperable:** use standard identifiers and bibliography formats.
- **Recoverable:** libraries must be safe to migrate, back up, and inspect.
- **AI optional:** core library workflows must remain useful without AI.

## Product status

### Available now

- **Source capture:** create books and papers manually, from a reviewed Crossref DOI record, or
  from a reviewed Open Library ISBN catalog record.
- **Metadata enrichment:** edit saved metadata directly or explicitly look up a saved DOI or book
  ISBN, compare current and proposed values, select fields, and retain applied provenance.
- **Library management:** search, sort, and filter saved sources; organize them with reusable tags
  and collections; and remove sources through a confirmed cleanup workflow.
- **Bibliography interoperability:** import BibTeX, RIS, and CSL JSON, and export the complete
  library in the same formats while preserving supported identifiers and stable record keys.
- **Local documents:** import distinct files durably, detect duplicate content, extract structured
  Markdown, inspect explicit conversion failures, retry extraction, and safely remove failed
  attachments.
- **PDF reading and notes:** open a managed PDF in the dedicated Reader, navigate one page at a
  time, save or delete persistent page highlights, and create or edit manual page-aware notes.
  Reading, highlighting, and notes do not require extraction or a network connection.
- **Recovery foundations:** use forward-only database migrations and guarded database/file
  transactions for imports, attachment cleanup, metadata application, and source deletion.

### Current roadmap focus

The Reader's manual reading workflow is complete through page-aware notes and saved-locator reopen.
The next item is proving annotations across their remaining real boundaries.
See [Reader, locators, and annotations](docs/ROADMAP.md#3-reader-locators-and-annotations).

### Not implemented yet

The synthesis workbench, research maps, full-text passage search, distributable desktop packaging,
in-app backup and restore, and optional AI assistance remain planned. Litrev is still an early
prototype rather than a dependable reference manager.

## Settings

Open **Settings** at the bottom of the left panel to manage user preferences. Appearance is
available there now, and further user-facing settings will be added in that location as Litrev
grows.

## Architecture

```text
Tauri desktop shell (`src-tauri/`)
└── React + TypeScript UI (`web/`)
    └── FastAPI local boundary (`src/litrev/api.py`)
        ├── domain rules (`src/litrev/domain/`)
        ├── application services (`src/litrev/services/`)
        └── SQLite/SQLAlchemy adapters (`src/litrev/infrastructure/`)
```

React owns the interface, FastAPI owns the local HTTP contract, and the Python domain and service
layers own research behavior. Tauri currently starts the development processes but does not yet
package the Python service as a production sidecar.

### Document conversion

[Anydoc](https://github.com/firecrawl/anydoc) extracts GitHub-Flavored Markdown from PDF, Word,
PowerPoint, Excel, OpenDocument, RTF, EPUB, and CSV files. It does not render PDF pages or provide
annotation geometry; page-accurate reading will require a separate renderer.

Scanned or image-only PDF pages are reported as needing OCR. Litrev does not silently enable
Anydoc's hosted OCR option.

### PDF Reader

The dedicated Reader uses locally bundled [PDF.js](https://mozilla.github.io/pdf.js/) under the
Apache 2.0 license. FastAPI resolves a PDF by its attachment identifier, verifies the managed file,
and serves it with byte-range support; the React interface renders one page at a time. PDF.js is
separate from Anydoc, and opening a PDF neither runs extraction nor changes the managed original.

The Reader provides previous and next page navigation, direct page entry, zoom, fit-to-width, and a
PDF.js text layer when the page contains selectable text. An explicit **Highlight** action stores
the exact selected text and normalized page rectangles in the local database; saved highlights
remain aligned across zoom changes and reopen as non-destructive overlays. Image-only pages remain
readable but explain that highlighting is unavailable, and Litrev does not start OCR automatically.
The compact note panel creates and edits shared Litrev notes for a page or saved highlight. A new
selection and its first note are saved atomically. Saved note locators reopen the owning PDF and
page; if the managed file is missing or changed, Litrev keeps the note and locator visible instead
of discarding the research record.

## Run locally

Requirements:

- Python 3.14 or newer and [uv](https://docs.astral.sh/uv/)
- Node.js and npm
- [mise](https://mise.jdx.dev/) or an equivalent Rust installation
- Tauri's operating-system prerequisites

Install the declared toolchains and dependencies:

```bash
mise install
uv sync
npm install
```

Run the browser workflow at `http://127.0.0.1:1420`:

```bash
npm run dev
```

Run the same interface inside Tauri:

```bash
npm run tauri dev
```

### Docker browser workflow

Docker Compose runs Vite and FastAPI, not the native Tauri window:

```bash
docker volume create litrev-data
docker compose up --build -d
```

Source edits hot reload. Dependency, lockfile, Dockerfile, and Compose changes require a rebuild.
Use `docker compose logs -f litrev` to follow logs and `docker compose down` to stop the container.

The external `litrev-data` volume preserves the library across rebuilds and `docker compose down`.
Remove it only after backing up the library and intentionally choosing to delete it.

## Library data and safety

The service binds to `127.0.0.1:8765`. By default, Litrev stores its library in the operating
system's per-user data directory; on Linux this is normally `~/.local/share/litrev/`.

```text
litrev/
├── litrev.sqlite3
├── attachments/
├── extracted/
├── thumbnails/
└── temporary-imports/
```

Set `LITREV_DATA_DIR` to use an isolated library root. Tests inject temporary roots and must never
touch the real library. Application startup applies forward-only Alembic migrations.

Do not commit copyrighted papers or real research databases. Development files that must live in
the repository belong under ignored `local-data/`; PDF, SQLite, and SQLite journal files are also
ignored as a second safeguard. Ignore rules are not encryption and do not untrack existing files.

Selecting a document does not copy it. Confirming an import saves the original under
`attachments/`; successful extraction saves Markdown under `extracted/`.

Deleting a source requires explicit confirmation and removes its owned database records and
managed originals and extracted text. Litrev stages managed files before committing the database
deletion so a database failure can restore them; an incomplete post-commit cleanup is reported
instead of presenting the source as recoverable.

### Explicit metadata networking

DOI metadata lookup is opt-in. Clicking **Look up DOI metadata** sends the source DOI to Crossref;
opening or editing a source does not make that network request. Litrev shows provider values beside
saved values, leaves conflicts for the user to select, applies only selected fields, and records
the provider link and applied fields as local provenance.

ISBN catalog lookup is also opt-in. **Look up ISBN** validates the ISBN locally and checks for
canonical ISBN-10/ISBN-13 matches already in the library before contacting Open Library. A user can
open those local matches or explicitly continue to the catalog. Litrev describes Open Library data
as a catalog match—not proof of official ISBN assignment—and creates nothing until the user reviews
the proposal and chooses **Add book**. Confirmation re-fetches the record, saves the required ISBN
and title plus selected fields, and records Open Library provenance in the same transaction.

For a saved book, **Look up ISBN metadata** sends only the explicitly selected saved ISBN to Open
Library. Litrev shows saved and proposed values together, leaves conflicts unselected, and changes
nothing until **Apply selected fields** is chosen. Applying re-fetches the catalog record; changed
provider data returns a fresh review, while a successful apply records the provider link and exact
fields as local provenance.

When developing migrations, use an isolated library and never run Alembic commands against the
real one.

### Backup and recovery

Litrev does not yet provide in-app backup or restore. Until it does:

1. Close Litrev so no SQLite transaction is active.
2. Copy the entire library directory, not only `litrev.sqlite3`, to a separate location.
3. Keep dated backups before upgrades or consequential imports.
4. To restore, close Litrev, move the current library aside, copy the backup into place, and verify
   it before deleting the moved copy.

Never overwrite the only copy of a library. Migrations can upgrade an older backup, but they cannot
recover files or records deleted before that backup was made.

## Local API

### Sources and library interchange

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Report local stack health and versions |
| `GET` | `/api/sources` | List sources |
| `GET` | `/api/sources/{source_id}` | Read a source and its attachment states |
| `POST` | `/api/sources` | Create a manual book or paper |
| `PUT` | `/api/sources/{source_id}` | Replace a source's validated metadata and organization |
| `DELETE` | `/api/sources/{source_id}` | Remove a source, its relationships, and managed files |
| `POST` | `/api/bibliography-imports` | Import source metadata from BibTeX, RIS, or CSL JSON |
| `GET` | `/api/bibliography-exports/{format}` | Download the full library as `bibtex`, `ris`, or `csl-json` |

### External metadata

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/sources/from-doi` | Create a source from reviewed Crossref metadata |
| `POST` | `/api/sources/from-isbn` | Create a book from reviewed Open Library catalog metadata |
| `POST` | `/api/doi-metadata-previews` | Preview Crossref metadata for a DOI without saving it |
| `POST` | `/api/isbn-metadata-previews` | Validate an ISBN, check local matches, and explicitly preview Open Library metadata without saving it |
| `POST` | `/api/sources/{source_id}/doi-metadata-lookups` | Retrieve a reviewable Crossref proposal for the saved DOI |
| `POST` | `/api/sources/{source_id}/doi-metadata-lookups/{lookup_id}/apply` | Apply explicitly selected proposal fields and save provenance |
| `POST` | `/api/sources/{source_id}/isbn-metadata-lookups` | Retrieve a reviewable Open Library proposal for one saved ISBN |
| `POST` | `/api/sources/{source_id}/isbn-metadata-lookups/{lookup_id}/apply` | Revalidate and apply selected catalog fields with provenance |

### Documents and Reader

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/imports` | Save a source and original document |
| `POST` | `/api/attachments/{attachment_id}/convert` | Extract or retry Markdown |
| `GET` | `/api/attachments/{attachment_id}/extracted-text` | Read persisted Markdown |
| `GET` | `/api/reader/documents` | List managed PDFs available to the local Reader |
| `GET` | `/api/attachments/{attachment_id}/content` | Stream a verified managed PDF to the Reader |
| `GET` | `/api/attachments/{attachment_id}/highlights` | List persistent page-aware highlights for a PDF |
| `POST` | `/api/attachments/{attachment_id}/highlights` | Save reviewed text and normalized page rectangles as a highlight |
| `DELETE` | `/api/highlights/{highlight_id}` | Explicitly remove one saved highlight |
| `GET` | `/api/attachments/{attachment_id}/notes` | List page-aware shared notes for a PDF |
| `POST` | `/api/attachments/{attachment_id}/notes` | Save a manual page note, optionally with an existing or new highlight |
| `PUT` | `/api/notes/{note_id}` | Edit the body of a shared Reader note |
| `DELETE` | `/api/attachments/{attachment_id}` | Remove a failed attachment and its files |

## Verification

```bash
uv run litrev --check
uv run pytest
uv run ruff check .
uv run ruff format --check .
npm run test:web
npm run lint:web
npm run build:web
mise exec -- cargo check --manifest-path src-tauri/Cargo.toml
```

These automated checks are the default verification path for agent-driven changes. Interactive
browser QA is opt-in: agents should not launch or control a browser for visual inspection unless
the user specifically requests it.

## Project guidance

- [Roadmap](docs/ROADMAP.md) defines the milestone order and acceptance criteria.
- [Network page roadmap](docs/roadmap_network.md) expands the citation and relationship work in
  milestone 5.
- [Agent instructions](AGENTS.md) define architecture, safety rules, and the definition of done.
- [Opt-in audit backlog](TODO.md) is consulted only when the user explicitly asks to review or work
  from `TODO.md`. Agents must otherwise ignore it when choosing or scoping work.

Start future work with the first unchecked item in the current roadmap milestone unless the user
explicitly changes the priority.
