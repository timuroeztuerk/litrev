# Litrev implementation roadmap

This roadmap turns the product direction into sequential, testable milestones. Future work should
normally start with the first unchecked milestone unless a user request explicitly changes the
priority.

## North star

Litrev should help one researcher move safely from a collected document to source-linked notes,
evidence, relationships, and a literature-review outline. Every derived idea should remain
traceable to its origin.

## Status

- `[x]` complete and verified
- `[ ]` planned
- `[-]` intentionally deferred

## 0. Technical foundation

Outcome: the repository has a working local desktop/web development environment and one small
end-to-end workflow.

- [x] React, TypeScript, Vite, and Tauri desktop shell
- [x] FastAPI local service
- [x] SQLite and SQLAlchemy persistence foundation
- [x] Source and source-linked note prototype models
- [x] Anydoc document-to-Markdown conversion with stable application errors
- [x] Local document selection and Markdown preview in React
- [x] Docker browser workflow with source hot reload and persistent library data
- [x] Python, frontend, and Rust checks
- [x] Repository guidance and phased roadmap

## 1. Durable document ingestion — next

Outcome: importing a paper creates a durable, deduplicated library record rather than a temporary
preview.

### 1.1 Safe storage and migrations

- [x] Add Alembic or an equivalent explicit SQLite migration mechanism.
- [x] Define a managed application-data layout for the database, attachments, extracted content,
  thumbnails, and temporary imports.
- [x] Add a configurable test data directory so tests never touch the real library.
- [x] Document backup and recovery expectations before storing irreplaceable annotations.

Acceptance criteria:

- A database created by the previous application version upgrades without data loss.
- Tests can create and destroy isolated libraries without using the user's data directory.
- Partial imports do not leave records pointing to missing files.

All three criteria are covered now. Attachment storage installs the managed file before committing
its database record, and failure-path tests prove that storage errors cannot leave a visible record
pointing to a missing file.

### 1.2 Document and attachment records

- [x] Add a document/attachment model with source, original filename, managed path, media type,
  byte size, checksum, detected format, conversion status, and timestamps.
- [x] Persist Anydoc Markdown and conversion diagnostics.
- [x] Use content hashes to detect duplicate files.
- [x] Decide whether one source may own multiple document versions or supplements; encode the
  decision in the domain model and tests.
- [ ] Add safeguarded removal for failed attachments and their managed artifacts.

A source may own multiple distinct attachments so versions and supplements are not forced into
separate source records. A SHA-256 checksum uniquely identifies attachment bytes across the local
library; repeated bytes report the existing attachment instead of creating another record.
Successful conversions store Markdown under the managed extracted-content directory. Expected
Anydoc failures retain a stable status, message, and format-specific diagnostics on the attachment;
rerunning conversion retries the unchanged managed original.

Acceptance criteria:

- Importing a supported document persists the file and extracted Markdown across restarts.
- Importing the same bytes twice does not silently create duplicate attachments.
- Unsupported, encrypted, malformed, oversized, and OCR-needed documents remain visible with a
  useful status and can be retried or removed.

### 1.3 Import workflow

- [x] Replace the temporary conversion preview endpoint with an ingestion service and API.
- [x] Let the user confirm or edit the source title before saving.
- [x] Show import progress and specific conversion failures in React.
- [x] Add a source detail screen with attachment and extracted-text status.

Saving the confirmed source and managed original is one database transaction with the file install.
Extraction is a separate retryable API step, so the interface reports real save and extraction
stages and an interrupted conversion leaves a recoverable pending attachment. Duplicate bytes open
the existing source. Expected Anydoc failures remain visible on source detail, while an upload over
50 MB is rejected before creating a source or retaining partial bytes.

Acceptance criteria:

- The workflow is: select document → inspect detected result → confirm source → reopen it later.
- API and UI tests cover success, duplicate import, and major failure classes.

## 2. Useful library and metadata

Outcome: a growing research library can be found, corrected, and organized.

- [x] Add manual quick capture for books and papers with a durable type and title.
- [ ] Expand source metadata: authors, year, venue, DOI, URL, abstract, language, and reading
  status.
- [ ] Add library search, sorting, filtering, tags, and collections.
- [ ] Add source editing and explicit deletion with attachment cleanup safeguards.
- [ ] Import BibTeX, RIS, and CSL JSON.
- [ ] Export standard bibliographic formats without losing identifiers.
- [ ] Add opt-in DOI metadata lookup with provenance and conflict review.

Acceptance criteria:

- A user can import an existing bibliography, find a source quickly, correct its metadata, and
  export it again.
- External metadata never overwrites user edits without confirmation.

## 3. Reader, locators, and annotations

Outcome: the user can read a PDF and create notes that return to an exact location.

- [ ] Evaluate visual PDF renderers separately from Anydoc; record the decision and license.
- [ ] Add page navigation, zoom, text selection, and search.
- [ ] Store page-aware highlights and annotations with stable locators and selected text.
- [ ] Link annotations to notes and reopen the document at the relevant location.
- [ ] Define fallback locators for non-PDF document formats.

Acceptance criteria:

- Clicking a note locator reopens the correct source and location.
- Annotation data survives application upgrades and does not depend only on transient DOM geometry.

## 4. Notes and synthesis workbench

Outcome: reading artifacts become a structured, reviewable body of evidence.

- [ ] Add free-form and structured paper summaries.
- [ ] Add atomic notes linked to one or more sources or annotations.
- [ ] Add claims with supporting, contradicting, and contextual evidence.
- [ ] Add research questions, review projects, and an evidence matrix.
- [ ] Add backlinks and orphan-note detection.
- [ ] Add Markdown export for a project outline with source references.

Acceptance criteria:

- A claim can show every supporting or contradicting source passage.
- A review project can be exported without losing source identity or locators.

## 5. Relationships and research map

Outcome: the user can inspect meaningful relationships among sources, authors, concepts, and
claims.

- [ ] Replace the in-memory graph prototype with persisted typed relationships.
- [ ] Separate explicit user-created links from imported citation links and inferred links.
- [ ] Add a focused graph UI with filtering and useful default layouts.
- [ ] Add citation and reference ingestion where reliable metadata is available.
- [ ] Add author, topic, and timeline views only after the underlying data is inspectable.

Acceptance criteria:

- Every graph edge exposes its type and provenance.
- The graph remains usable on a realistically sized personal library.

## 6. Search and retrieval

Outcome: the user can retrieve passages and ideas across the entire library.

- [ ] Index source metadata, Anydoc Markdown, notes, and annotations with SQLite FTS5.
- [ ] Return contextual snippets with links back to source locations.
- [ ] Add saved searches and project-scoped search.
- [ ] Evaluate optional local embeddings only after lexical search is dependable.

Acceptance criteria:

- Search results identify why they matched and open the underlying source or note.
- Rebuilding the index is safe and does not alter canonical research data.

## 7. Packaging, portability, and resilience

Outcome: Litrev behaves like a dependable personal desktop application.

- [ ] Package the Python service as a managed Tauri sidecar.
- [ ] Add clean startup, shutdown, health recovery, and port-conflict handling.
- [ ] Produce installable Linux builds, then evaluate macOS and Windows.
- [ ] Add library backup, restore, integrity checks, and a human-readable export.
- [ ] Add crash-safe import and database transaction boundaries.
- [ ] Add an upgrade test using a library from the previous release.

Acceptance criteria:

- The packaged application starts without separately installed Python or Node.js.
- A user can back up, restore, and inspect their library outside Litrev.

## 8. Optional AI assistance

Outcome: AI accelerates synthesis without weakening traceability or making the library dependent on
a provider.

- [ ] Define provider-neutral interfaces and a completely functional no-AI mode.
- [ ] Add library-grounded question answering with source and locator citations.
- [ ] Add suggested structured summaries that require user review.
- [ ] Add evidence extraction with visible provenance and confidence boundaries.
- [ ] Make local versus remote processing and transmitted data explicit before every provider is
  enabled.

Acceptance criteria:

- Generated statements link to inspectable evidence.
- Disabling or changing the AI provider does not make existing research inaccessible.

## Deferred until justified

- [-] Multi-user collaboration and shared libraries
- [-] Cloud synchronization
- [-] Mobile applications
- [-] Plugin marketplace
- [-] Automated writing without source review

## Recommended next task

Finish milestone 1.2 with a safeguarded attachment-removal service. Coordinate database deletion
with the managed original and extracted Markdown so a database failure cannot leave a surviving
record pointing to a removed file; tolerate already-missing artifacts and test recovery from file
and database failures. Keep that deletion work separate from metadata expansion.
