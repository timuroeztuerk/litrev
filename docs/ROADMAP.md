# Litrev roadmap

Litrev should help one researcher move safely from a collected document to source-linked notes,
evidence, relationships, and a literature-review outline. Every derived idea should remain
traceable to its origin.

## How to use this roadmap

- Milestones are ordered by product dependency, not by estimated duration.
- Work should normally start with the first unchecked item in the current milestone.
- Each change should deliver one observable workflow and its important failure states.
- Automated tests, static checks, and builds are the default acceptance evidence; interactive
  browser QA is performed only when the user specifically requests it.
- `[ ]` means planned; `[-]` means intentionally deferred.

The current implementation slice is the read-only foundation of **Milestone 3: Reader, locators,
and annotations**, brought forward by explicit product priority. The unfinished DOI-first capture
work and Milestone 2.2 remain planned; highlights and notes do not begin until the Reader foundation
is dependable.

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
- [x] Add explicit source deletion with attachment cleanup safeguards.
- [x] Import BibTeX, RIS, and CSL JSON.
- [x] Export standard bibliographic formats without losing identifiers.
- [x] Add opt-in DOI metadata lookup with provenance and conflict review.

Metadata is stored locally and can be edited from the source detail screen. Empty optional values
are normalized consistently, URLs must use HTTP or HTTPS, and duplicate DOI changes are rejected
without partially changing the source.

Library discovery now provides case-insensitive search across title, authors, venue, and DOI;
sorting by title, publication year, or date added; and combinable source-type and reading-status
filters. It covers saved source metadata only. Search within extracted text belongs to milestone 6.
Tags and collections are reusable, case-insensitive names assigned from the source editor and can
be combined with the other library filters. Source deletion now requires confirmation, removes
source-owned database relationships, stages every managed original and extraction before the
database commit, and restores staged files when the commit fails. Shared tag and collection
definitions remain available for other sources. Bibliography import now accepts UTF-8 BibTeX, RIS,
and CSL JSON files, validates the complete import before saving, maps supported metadata into the
source model, preserves supported non-DOI identifiers and format-scoped record keys, and reports
DOI duplicates without overwriting saved sources. Preserved identifiers can be corrected from the
source detail screen. Bibliography export downloads the full library as deterministic UTF-8
BibTeX, RIS, or CSL JSON, reuses safe format-scoped record keys, generates stable keys for manual
sources, and preserves standard and explicitly named identifiers across Litrev round trips. The
DOI workflow contacts Crossref only after an explicit lookup action, presents current and proposed
values side by side, leaves conflicting fields unselected, and applies only the fields the user
chooses. Saved identifiers are merged rather than replaced. Applied fields retain the provider,
provider record link, DOI, and retrieval and application times on the source detail screen.

Milestone 2 is complete. Bounded DOI-first capture and ISBN lookup follow-ups come next, before
milestone 3.

Acceptance criteria:

- A user can import an existing bibliography, find a source, correct its metadata, and export it
  without losing identifiers.
- External metadata never overwrites user edits without confirmation.

## 2.1. DOI-first source capture

Outcome: a user can enter one DOI, review the matching provider metadata, and create a source
without inventing a temporary title or saving a placeholder record.

This slice reuses the completed Crossref client, canonical metadata proposal, identifier handling,
and provenance model. It must not introduce a second DOI parser, a parallel metadata model, or an
automatic lookup on page load.

- [x] Add a source-independent DOI preview endpoint.
  - Accept one DOI, normalize it through the authoritative DOI boundary, and reject empty or
    unusable input before networking.
  - Check the library for an existing canonical DOI before contacting Crossref. Return the existing
    source identity so the UI can open it instead of creating or overwriting anything.
  - Return the same canonical provider proposal used by source enrichment, plus provider identity,
    record URL, retrieval time, and a deterministic proposal fingerprint. Do not create a source or
    provenance row during preview.
- [x] Add a transactional create-from-DOI endpoint.
  - Require the normalized DOI, reviewed proposal fingerprint, and the fields explicitly selected
    by the user. DOI and a non-empty provider title are mandatory. Use the existing provider type
    mapping when available and fall back to `other`; other available fields are optional.
  - Re-fetch and validate the provider proposal before creation so an untrusted or stale client
    cannot manufacture Crossref provenance. If the fingerprint changed, return the new review
    instead of applying unseen metadata.
  - Create the source and its applied provenance in one database transaction. A provider,
    validation, uniqueness, or commit failure must leave no partial source or lookup record.
  - Recheck DOI uniqueness at creation time and return a specific existing-source conflict for the
    race between preview and confirmation.
- [x] Add the DOI-first capture workflow to the existing capture area.
  - Let the user choose title-based capture or DOI-based capture without adding a separate generic
    dashboard or duplicating the existing source editor.
  - Make both outbound actions visible: **Look up DOI** retrieves the preview and **Add source**
    revalidates it before saving.
  - Show the normalized DOI, provider link, mapped source type, title, authors, year, venue, URL,
    abstract, language, and identifiers that are available. Keep the required title selected and
    allow the user to exclude optional fields.
  - On success, open the newly created source. If the DOI already exists, offer to open that source.
  - Cover initial, invalid-input, loading, review, changed-provider-data, duplicate, service-error,
    cancellation, and success states with labels, keyboard access, and deliberate focus movement.
- [ ] Prove the workflow through its real boundaries.
  - API tests cover DOI normalization, duplicate detection without a provider call, missing titles,
    provider 404/rate-limit/timeout/malformed responses, changed proposal fingerprints, selected
    fields, identifier deduplication, transaction rollback, and the preview-to-create race.
  - React tests prove there is no request before an explicit action, cancellation saves nothing,
    the reviewed selection is sent, duplicates open the existing source, failures remain
    actionable, and successful creation opens the persisted source.
  - The acceptance path is: DOI → preview → review → create → reopen → export → import into an empty
    library → compare canonical metadata and supported identifiers. Provenance is verified on the
    created source because bibliography formats do not carry Litrev's provider audit record.

Acceptance criteria:

- Given only a DOI whose provider record has a usable title, a user can create and reopen a source
  without entering placeholder metadata.
- No source is stored before confirmation, and every failure or cancellation leaves the library
  unchanged.
- The created source contains exactly the required DOI and title plus the optional fields the user
  selected, with inspectable Crossref provenance.
- A DOI already in the library is opened, never duplicated or overwritten.

Out of scope for this slice: batch DOI capture, automatic enrichment, alternate metadata providers,
attachment discovery or download, and citation/reference ingestion.

## 2.2. ISBN validation and metadata lookup

Outcome: a user can enter one ISBN-10 or ISBN-13, distinguish a structurally valid number from a
catalog match, review edition metadata, and apply it to a saved source or create a book without a
placeholder title.

ISBN validation and ISBN metadata lookup are separate guarantees. A checksum can detect malformed
or mistyped input but does not prove that an ISBN was assigned to a publication. There is no public
global publication-metadata registry equivalent to Crossref: the International ISBN Agency's
[Global Register](https://grp.isbn-international.org/content/using-register/358) identifies
publishers, not specific publications. Use [Open Library](https://openlibrary.org/developers/api)
as the initial low-volume, user-triggered catalog provider. Describe its result as a catalog match,
not authoritative verification; missing or conflicting catalog data must remain reviewable.

- [ ] Establish one authoritative ISBN identity boundary.
  - Accept one ISBN-10 or ISBN-13 with ordinary display separators, normalize it to digits plus a
    possible terminal `X`, and validate the appropriate check digit before networking.
  - Derive the equivalent ISBN-13 key for an ISBN-10 so both forms match as one identity while
    preserving the user's or importer's original display value.
  - Return distinct empty, malformed, unsupported-prefix, and checksum errors. Do not describe a
    checksum-valid number as assigned, registered, or found.
  - Apply strict validation to lookup inputs first. Do not make an existing imported ISBN prevent an
    unrelated source edit, and do not rewrite or discard stored identifiers during migration.
- [ ] Add an opt-in Open Library metadata provider behind the service boundary.
  - Contact Open Library only after an explicit lookup action. Follow its identification, caching,
    and rate-limit guidance; bound response size and duration as for the Crossref client.
  - Require the returned edition to contain the requested canonical ISBN. Treat no match, timeout,
    rate limit, malformed data, identifier mismatch, and multiple exact candidates as distinct
    actionable outcomes; never silently choose an ambiguous edition.
  - Map only supported source fields into the canonical proposal: book type, title, authors,
    publication year, publisher as venue, record URL, description as abstract, language, and known
    identifiers. A usable provider title is mandatory for creation.
  - Keep provider-specific response parsing in `services/`; do not make Open Library response types
    part of the FastAPI or React contract.
- [ ] Generalize metadata review and provenance without creating an ISBN-only parallel model.
  - Migrate the DOI-specific requested and retrieved identifier columns to an identifier type and
    value representation while preserving every existing Crossref provenance record.
  - Reuse one proposal, field-selection, conflict, fingerprint, application, and provenance shape
    across DOI and ISBN workflows. Provider-specific clients may produce that shape but must not
    reinterpret it independently in the API and UI.
  - Preview without creating a source or provenance row. Before applying or creating, re-fetch the
    provider record and return a new review if its fingerprint changed.
  - Save selected fields and applied provenance in one transaction. Provider, validation, or commit
    failures and cancellation must leave the source and lookup history unchanged.
- [ ] Add ISBN lookup to saved books and ISBN-first capture to the existing capture area.
  - Reuse the DOI review interface and existing source editor rather than adding another dashboard.
    If a saved source has multiple ISBNs, require the user to choose the one to look up.
  - Make both outbound actions visible: **Look up ISBN** retrieves a proposal and **Apply metadata**
    or **Add source** revalidates it before saving. Opening, importing, or editing a source must not
    trigger a lookup.
  - Show the entered and canonical ISBN, provider link, current and proposed values, unavailable
    fields, and any ambiguity. Leave conflicting fields unselected and let the user exclude optional
    provider values.
  - Search the local library by canonical ISBN before networking and offer to open matching sources.
    Do not impose global ISBN uniqueness in this slice: imported citations may share a set or
    publication ISBN, and historical catalog data can contain reused ISBNs.
  - Cover initial, invalid-input, loading, review, changed-provider-data, local-match, ambiguous,
    not-found, service-error, cancellation, and success states with labels, keyboard access, and
    deliberate focus movement.
- [ ] Prove the workflow through its real boundaries.
  - Domain tests cover ISBN-10 and ISBN-13 normalization, check digits, terminal `X`, equivalent
    canonical keys, separators, invalid prefixes, and failure messages.
  - Provider and API tests cover no request before validation, exact identifier matching, missing
    titles, zero or multiple records, rate limits, timeout, oversized and malformed responses,
    changed fingerprints, selected fields, transaction rollback, and preservation of DOI
    provenance through the migration.
  - React tests prove no implicit request, ISBN choice when several are saved, conflict selection,
    ambiguity handling, cancellation without writes, and reopening after successful apply or create.
  - The acceptance path is: ISBN → validate → preview → review → create → reopen →
    export → import into an empty library → compare supported metadata and canonical ISBN.
    Verify provider provenance separately because bibliography formats do not carry Litrev's audit
    record.

Acceptance criteria:

- Mistyped or malformed ISBNs are rejected locally without a network request or data change.
- A catalog result is shown as provider-supplied edition metadata, never as proof of official ISBN
  assignment, and is not applied without explicit review.
- Creation or enrichment saves exactly the required ISBN and title plus the optional fields selected
  by the user, with inspectable Open Library provenance.
- Not-found, ambiguous, changed, failed, and cancelled lookups leave the library unchanged and give
  the user a specific next action.
- Existing ISBN identifiers and Crossref provenance survive the migration and bibliography
  round-trip without silent loss.

Out of scope for this slice: batch ISBN lookup, automatic enrichment, Google Books or Crossref
fallbacks, automatic updates of ISBN allocation-range data, cover or attachment download, retailer
availability, and new edition-, format-, or page-count fields.

## 3. Reader, locators, and annotations

Outcome: a user can open a saved PDF in a dedicated Reader, move through it one page at a time,
create persistent text highlights and manual notes, and reopen a note on the correct page.

This milestone is intentionally a small local reading workflow, not a general PDF editor or the
full notes workbench. PDF is the only visual format in scope. The Reader must remain useful without
AI or network access, must not modify the original PDF, and must not depend on Anydoc conversion
succeeding. Anydoc continues to own structured Markdown extraction; the visual renderer owns page
display, selectable text, and highlight geometry.

### 3.1. Establish the local PDF rendering boundary

- [x] Adopt PDF.js as the visual PDF renderer and record the integration and license decision.
  - Pin and bundle the renderer and its worker with the application; do not load renderer code,
    fonts, or other runtime assets from a CDN.
  - Keep the integration behind a focused React reader component so PDF.js-specific rendering and
    viewport details do not become part of the general API or domain model.
  - Treat PDF page numbers as one-based at the application boundary.
- [x] Add a read-only attachment-content endpoint for managed PDFs.
  - Accept an attachment identifier, never a client-supplied filesystem path, and resolve the file
    through the existing managed attachment store and path-containment checks.
  - Verify that the attachment exists, belongs to a source, is a PDF, and still matches the managed
    file assumptions before returning it with the correct media type and byte-range support.
  - Distinguish missing records, missing or changed managed files, non-PDF attachments, and read
    failures with actionable responses. Do not expose an arbitrary local-file server.
  - Allow a valid stored PDF to be read even when Markdown extraction failed or reported that OCR
    is needed; visual reading and text extraction are separate capabilities.

### 3.2. Add the dedicated single-page Reader

- [x] Add **Reader** as a first-class section in the existing application navigation.
  - Provide an empty state when no PDFs are stored and a focused list of locally available PDFs
    when the Reader has no open document.
  - Add **Open in Reader** to PDF attachments on the source detail screen. Non-PDF attachments must
    not present a control that cannot work.
  - Keep the source identity and document title visible without turning the Reader into a second
    source editor.
- [x] Render one page at a time with a compact reading toolbar.
  - Provide previous and next page actions, a labeled page-number input with total page count,
    zoom in and out, and fit-to-width. Disable or constrain controls at their valid boundaries.
  - Preserve the open attachment and current one-based page in structured application state so a
    note locator can target the Reader directly. Exact scroll offset, zoom restoration, and passage
    centering are not required in this milestone.
  - Support keyboard operation, visible focus, deliberate focus movement after document load or
    failure, and labels that do not rely on icons alone.
  - Cover initial, loading, ready, empty, malformed, encrypted, missing-file, changed-file, and page
    render-error states without presenting a partially loaded document as usable.

### 3.3. Persist page-aware text highlights

- [ ] Add text selection and one persistent highlight style.
  - Build selection on the PDF text layer rather than inferred OCR or canvas pixels. Selecting text
    offers an explicit **Highlight** action; selection alone must not write data.
  - Define one coordinate conversion path between rendered viewport rectangles and persisted page
    coordinates.
  - Store the attachment, one-based page number, exact selected text, and one or more rectangles in
    stable page coordinates. Do not persist transient browser or zoom-scaled DOM coordinates.
  - Render saved highlights as a non-destructive overlay and leave the managed PDF bytes unchanged.
    A highlight must remain aligned after zooming, closing the Reader, and reopening the document.
  - Support deleting a highlight with an explicit action. Deleting a highlight must not silently
    delete a linked note.
- [ ] Handle pages without selectable text honestly.
  - Display scanned or image-only pages for reading when PDF.js can render them, but disable text
    highlighting on pages with no usable text layer and explain that selectable text is unavailable.
  - Do not invoke local or hosted OCR automatically. Area highlights, OCR-backed text selection,
    and reconstruction of reading order are deferred.

### 3.4. Create manual Litrev notes from the Reader

- [ ] Let the user create and edit a manual note for the current page, optionally anchored to a
  selected highlight.
  - Persist the note through the existing note model rather than introducing a reader-only comment
    record. Reader-created notes must be available to the future notes workbench without copying or
    converting them.
  - Add a structured reader anchor containing the source-owned attachment and one-based page, plus
    the highlight reference when present. Do not rely on parsing a display-only locator string.
  - Present the source, page locator, selected quote when available, note body, and save state in a
    compact Reader side panel. Empty notes are not saved.
  - Keep note creation and editing entirely manual. No prompt, summary, suggestion, or other AI
    action belongs in the Reader milestone.
- [ ] Reopen Reader notes on the correct page.
  - A note locator opens the owning PDF in Reader and navigates to its saved page. Returning to the
    exact scroll position, selection range, or zoom level is not required.
  - If the attachment or managed file is unavailable, preserve the note and its locator and show a
    specific unresolved-attachment state rather than dropping research data.

### 3.5. Make annotation persistence recoverable

- [ ] Add forward-only migrations for annotations and structured reader note anchors.
  - Preserve all existing sources, attachments, and notes. New locator fields must be nullable for
    notes created outside the Reader or before this milestone.
  - Define ownership and deletion behavior explicitly: source deletion removes source-owned reader
    records through the existing confirmed workflow; removing an attachment with highlights or
    anchored notes must not silently destroy them; deleting a highlight preserves its note.
  - Validate page numbers, finite coordinate values, rectangle bounds, selected-text limits, and
    attachment relationships at the authoritative service boundary.
  - Save a highlight and its initial note atomically when the user creates both in one action. A
    validation, storage, or commit failure must leave neither a partial highlight nor a false saved
    state in the UI.
- [ ] Prove the Reader through its real boundaries.
  - API and persistence tests cover full and ranged PDF responses, wrong attachment types, missing
    or changed managed files, invalid pages and geometry, migration of existing libraries,
    highlight and note transactions, deletion behavior, and reopening a persisted locator.
  - React tests cover Reader navigation, control boundaries, loading and failure states, explicit
    highlight creation, persistence after reopen, manual note save and edit, locator navigation,
    keyboard use, and scanned pages without selectable text.
  - Use a small generated, non-copyrighted PDF fixture with selectable text. Mock only rendering
    behavior that the component test environment cannot execute; verify the attachment-content and
    persistence boundaries with real files and the real database.
  - Renderer-specific interactive browser QA remains opt-in and, when requested, uses an isolated
    test library and is reported separately from automated checks.

Acceptance criteria:

- A user can open a locally stored PDF from either its source or the dedicated Reader and navigate
  it one page at a time without an external network request.
- Previous, next, page-number, zoom, and fit-to-width controls are bounded, labeled, keyboard
  accessible, and have understandable loading and failure states.
- A selectable text passage can be highlighted, survives an application restart and zoom changes,
  and remains an overlay without changing the original PDF checksum.
- A user can create and edit a manual note for a page or highlight, and that note uses Litrev's
  shared note record rather than a Reader-only data model.
- Opening a saved note returns to the correct source attachment and page. Missing attachments or
  files leave the note recoverable and explain why its page cannot currently be opened.
- Scanned or image-only PDFs remain readable but clearly report that text highlighting is
  unavailable; the Reader neither silently enables OCR nor claims text selection succeeded.
- Existing libraries migrate without losing attachments or notes, and failed writes never leave a
  partial annotation or misleading saved state.

Out of scope for this milestone: continuous scrolling, in-document search, page thumbnails,
bookmarks, multiple highlight colors, freehand or area annotations, exact scroll or zoom
restoration, OCR, non-PDF readers and fallback locators, embedding annotations into or exporting an
annotated PDF, a standalone notes workbench, and all AI-generated notes or summaries.

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
