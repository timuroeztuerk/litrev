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

## Current state

The working vertical slice supports:

- manual capture of books and papers;
- creating a source from a DOI after reviewing selected Crossref metadata;
- viewing and editing bibliographic metadata, standard identifiers, and reading status;
- finding saved sources by metadata, source type, reading status, year, or date added;
- organizing sources with reusable tags and collections;
- importing source metadata, identifiers, and record keys from BibTeX, RIS, and CSL JSON
  bibliographies;
- exporting the full library as UTF-8 BibTeX, RIS, or CSL JSON with preserved identifiers and
  stable record keys;
- explicitly looking up DOI metadata from Crossref, reviewing conflicts, applying selected fields,
  and retaining provenance;
- durable, deduplicated local document imports;
- structured Markdown extraction with explicit failure states and retries;
- reopening a source to inspect its attachments and extracted text;
- opening stored PDFs in a dedicated, single-page Reader with page and zoom controls;
- confirmed removal of failed attachments with safeguarded file cleanup; and
- explicit source deletion with relationship and managed-file cleanup safeguards.

Persistent PDF highlights and notes, research maps, distributable packaging, and AI assistance are
not implemented yet. The useful-library milestone is complete, DOI-first capture remains in
progress, and the read-only foundation of the [Reader milestone](docs/ROADMAP.md#3-reader-locators-and-annotations)
is now available. [ISBN validation and metadata lookup](docs/ROADMAP.md#22-isbn-validation-and-metadata-lookup)
also remains planned.

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

This first slice provides previous and next page navigation, direct page entry, zoom, and
fit-to-width. It does not yet add a selectable text layer, persistent highlights, or reader notes.

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

DOI metadata lookup is opt-in. Clicking **Look up DOI metadata** sends the source DOI to Crossref;
opening or editing a source does not make that network request. Litrev shows provider values beside
saved values, leaves conflicts for the user to select, applies only selected fields, and records
the provider link and applied fields as local provenance.

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

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Report local stack health and versions |
| `GET` | `/api/sources` | List sources |
| `GET` | `/api/sources/{source_id}` | Read a source and its attachment states |
| `POST` | `/api/sources` | Create a manual book or paper |
| `POST` | `/api/sources/from-doi` | Create a source from reviewed Crossref metadata |
| `PUT` | `/api/sources/{source_id}` | Replace a source's validated metadata and organization |
| `DELETE` | `/api/sources/{source_id}` | Remove a source, its relationships, and managed files |
| `POST` | `/api/doi-metadata-previews` | Preview Crossref metadata for a DOI without saving it |
| `POST` | `/api/sources/{source_id}/doi-metadata-lookups` | Retrieve a reviewable Crossref proposal for the saved DOI |
| `POST` | `/api/sources/{source_id}/doi-metadata-lookups/{lookup_id}/apply` | Apply explicitly selected proposal fields and save provenance |
| `POST` | `/api/bibliography-imports` | Import source metadata from BibTeX, RIS, or CSL JSON |
| `GET` | `/api/bibliography-exports/{format}` | Download the full library as `bibtex`, `ris`, or `csl-json` |
| `POST` | `/api/imports` | Save a source and original document |
| `POST` | `/api/attachments/{attachment_id}/convert` | Extract or retry Markdown |
| `GET` | `/api/attachments/{attachment_id}/extracted-text` | Read persisted Markdown |
| `GET` | `/api/reader/documents` | List managed PDFs available to the local Reader |
| `GET` | `/api/attachments/{attachment_id}/content` | Stream a verified managed PDF to the Reader |
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
- [Agent instructions](AGENTS.md) define architecture, safety rules, and the definition of done.
- [Opt-in audit backlog](TODO.md) is consulted only when the user explicitly asks to review or work
  from `TODO.md`. Agents must otherwise ignore it when choosing or scoping work.

Start future work with the first unchecked item in the current roadmap milestone unless the user
explicitly changes the priority.
