# Litrev roadmap

Litrev should help one researcher move safely from a collected document to source-linked notes,
evidence, relationships, and a literature-review outline. Every derived idea should remain
traceable to its origin.

## How to use this roadmap

- Milestones are ordered by product dependency, not by estimated duration.
- Work should normally start with the first unchecked item in the current milestone.
- Each change should deliver one observable workflow and its important failure states.
- `[ ]` means planned; `[-]` means intentionally deferred.

The current milestone is **2. Useful library and metadata**.

## Completed baseline: foundation and durable ingestion

Milestones 0 and 1 established the development stack and the first dependable vertical slice:

- React, TypeScript, Vite, and Tauri interface with a local FastAPI service;
- SQLite/SQLAlchemy storage with forward-only Alembic migrations and isolated test libraries;
- manual capture of books and papers;
- confirmed document import into a managed local library;
- attachment records with checksums, duplicate detection, conversion state, and diagnostics;
- persisted Anydoc Markdown with visible, retryable failure states;
- multiple distinct attachments per source; and
- confirmed removal of failed attachments with database and filesystem safeguards.

The completed workflow is:

```text
select document → inspect → confirm source → save original → extract → reopen later
```

Verified guarantees include migration from the pre-Alembic schema, persistence across restarts,
duplicate-byte detection, a 50 MB import limit, explicit unsupported/encrypted/malformed/OCR-needed
states, recovery from storage or database failures, and protection of pending or successful
attachments from deletion.

## 2. Useful library and metadata

Outcome: a growing research library can be found, corrected, organized, imported, and exported.

Manual title-and-type capture already exists.

- [x] Add authors, year, venue, DOI, URL, abstract, language, and reading status.
- [x] Add source editing with explicit validation and conflict behavior.
- [x] Add source-list search, sorting, and filtering across saved metadata.
- [x] Add tags and collections for persistent organization.
- [ ] Add explicit source deletion with attachment cleanup safeguards.
- [ ] Import BibTeX, RIS, and CSL JSON.
- [ ] Export standard bibliographic formats without losing identifiers.
- [ ] Add opt-in DOI metadata lookup with provenance and conflict review.

Metadata is stored locally and can be edited from the source detail screen. Empty optional values
are normalized consistently, URLs must use HTTP or HTTPS, and duplicate DOI changes are rejected
without partially changing the source.

Library discovery now provides case-insensitive search across title, authors, venue, and DOI;
sorting by title, publication year, or date added; and combinable source-type and reading-status
filters. It covers saved source metadata only. Search within extracted text belongs to milestone 6.
Tags and collections are reusable, case-insensitive names assigned from the source editor and can
be combined with the other library filters. The next slice is explicit source deletion with
attachment cleanup safeguards.

Acceptance criteria:

- A user can import an existing bibliography, find a source, correct its metadata, and export it
  without losing identifiers.
- External metadata never overwrites user edits without confirmation.

## 3. Reader, locators, and annotations

Outcome: a user can read a document and create notes that return to an exact location.

- [ ] Evaluate visual PDF renderers separately from Anydoc; record the decision and license.
- [ ] Add page navigation, zoom, text selection, and in-document search.
- [ ] Store page-aware highlights and annotations with stable locators and selected text.
- [ ] Link annotations to notes and reopen the relevant location.
- [ ] Define fallback locators for non-PDF formats.

Acceptance criteria:

- Clicking a note locator reopens the correct source and location.
- Annotations survive upgrades and do not rely only on transient DOM geometry.

## 4. Notes and synthesis workbench

Outcome: reading artifacts become a structured, reviewable body of evidence.

- [ ] Add free-form and structured paper summaries.
- [ ] Add atomic notes linked to one or more sources or annotations.
- [ ] Add claims with supporting, contradicting, and contextual evidence.
- [ ] Add research questions, review projects, and an evidence matrix.
- [ ] Add backlinks and orphan-note detection.
- [ ] Export a project outline as Markdown with source references.

Acceptance criteria:

- A claim exposes every supporting or contradicting source passage.
- A project export preserves source identity and locators.

## 5. Relationships and research map

Outcome: meaningful relationships among sources, authors, concepts, and claims are inspectable.

- [ ] Replace the in-memory graph prototype with persisted typed relationships.
- [ ] Distinguish user-created, imported citation, and inferred links.
- [ ] Add a focused graph UI with filtering and useful default layouts.
- [ ] Ingest citations and references where reliable metadata is available.
- [ ] Add author, topic, and timeline views only when their underlying data is inspectable.

Acceptance criteria:

- Every graph edge exposes its type and provenance.
- The graph remains usable for a realistically sized personal library.

## 6. Full-text search and retrieval

Outcome: a user can retrieve passages and ideas across the library, not only source metadata.

- [ ] Index source metadata, Anydoc Markdown, notes, and annotations with SQLite FTS5.
- [ ] Return contextual snippets linked to their source locations.
- [ ] Add saved searches and project-scoped search.
- [ ] Evaluate optional local embeddings only after lexical search is dependable.

Acceptance criteria:

- Results explain why they matched and open the underlying source or note.
- Rebuilding the index never changes canonical research data.

## 7. Packaging, portability, and resilience

Outcome: Litrev behaves like a dependable personal desktop application.

- [ ] Package the Python service as a managed Tauri sidecar.
- [ ] Add clean startup, shutdown, health recovery, and port-conflict handling.
- [ ] Produce installable Linux builds, then evaluate macOS and Windows.
- [ ] Add library backup, restore, integrity checks, and a human-readable export.
- [ ] Harden crash recovery across imports and database/file transactions.
- [ ] Test upgrades using a library from the previous release.

Acceptance criteria:

- The packaged application starts without separately installed Python or Node.js.
- A user can back up, restore, and inspect the library outside Litrev.

## 8. Optional AI assistance

Outcome: AI accelerates synthesis without weakening traceability or creating provider dependence.

- [ ] Define provider-neutral interfaces and a fully functional no-AI mode.
- [ ] Add library-grounded question answering with source and locator citations.
- [ ] Suggest structured summaries that require user review.
- [ ] Extract evidence with visible provenance and confidence boundaries.
- [ ] Make local versus remote processing and transmitted data explicit before enabling a provider.

Acceptance criteria:

- Generated statements link to inspectable evidence.
- Disabling or changing the provider does not make existing research inaccessible.

## Deferred until justified

- [-] Multi-user collaboration and shared libraries
- [-] Cloud synchronization
- [-] Mobile applications
- [-] Plugin marketplace
- [-] Automated writing without source review
