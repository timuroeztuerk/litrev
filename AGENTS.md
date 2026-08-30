# Working on Litrev

These instructions apply to the entire repository.

## Start here

Before changing code:

1. Read `README.md` and `docs/ROADMAP.md`.
2. Run `git status --short` and preserve changes you did not create.
3. Confirm which roadmap milestone the requested work belongs to.
4. Inspect the relevant Python, React, and API tests before changing a shared contract.

Litrev is an early, personal, local-first literature-review application. Optimize for a coherent
single-user workflow and trustworthy research data, not for collaboration or premature scale.

## Architecture and ownership

```text
Tauri desktop shell (`src-tauri/`)
└── React + TypeScript UI (`web/`)
    └── FastAPI local boundary (`src/litrev/api.py`)
        ├── domain rules (`src/litrev/domain/`)
        ├── application services (`src/litrev/services/`)
        └── SQLite/SQLAlchemy adapters (`src/litrev/infrastructure/`)
```

- Keep research concepts independent of FastAPI, React, and SQLAlchemy where practical.
- Keep HTTP request/response details in `api.py` and frontend transport details in `web/src/api.ts`.
- Put document conversion behind `services/documents.py`; do not call Anydoc throughout the codebase.
- Treat Tauri as the desktop lifecycle and packaging layer. Do not move research logic into Rust
  without a documented reason.
- Anydoc extracts structured Markdown. It does not render PDF pages or provide annotation geometry.

## Product invariants

- Local-first: papers, extracted content, notes, and relationships remain local by default.
- Traceable: summaries and claims must be able to point back to a source and, eventually, a page or
  exact passage.
- Interoperable: prefer standard identifiers and formats such as DOI, CSL JSON, BibTeX, and RIS.
- Recoverable: schema and file operations must not silently destroy a research library.
- Explicit networking: metadata services, OCR, sync, and AI providers require visible user intent.
- AI is downstream of a dependable non-AI workflow; do not make core library functions require AI.

## AI-assisted development quality bar

Treat model output as an untrusted draft, including code, tests, documentation, dependency choices,
and claims about external APIs. Productivity means a smaller verified path to user value, not more
generated code.

In this repository, **AI slop** means plausible-looking output that increases review or maintenance
cost without verified product value. Typical examples are broad rewrites, guessed APIs, duplicate
models, speculative abstractions, superficial tests, hidden fallbacks, generic UI, boilerplate
comments, dead code, and documentation that claims more than the application does.

Use this loop for every change:

1. **Ground:** Trace the current behavior through the real code path, tests, and data model. Search
   the repository before inventing a helper or type. Verify unstable external facts against primary
   documentation; do not code from package-name memory. Run the narrow existing checks first when a
   baseline result would distinguish a pre-existing failure from a regression.
2. **Bound:** Define one observable outcome, its failure behavior, and its acceptance check. Make the
   smallest coherent change that delivers that outcome. Keep unrelated cleanup and formatting out.
3. **Implement:** Reuse existing boundaries and vocabulary. Maintain one source of truth. Do not add
   a dependency, abstraction, configuration option, compatibility layer, or extension point for a
   hypothetical future need.
4. **Prove:** Test externally visible behavior at the lowest useful real boundary and include
   important failure paths. A test should fail for the regression it claims to prevent. Do not
   weaken an assertion, catch an error broadly, or update a snapshot merely to make checks pass.
5. **Inspect:** Review the complete diff as a skeptical maintainer. Remove duplication, stale names,
   unused code, generated artifacts, narration comments, and accidental scope. Run the affected
   static checks, tests, and build commands.
6. **Report:** State exactly what was verified and what was not. Separate observed facts from
   inference and future work. Never describe a stub, mock-only path, or unrun build as complete.

Reject or simplify a change when any of these are true:

- The agent cannot explain why each new abstraction exists in terms of a current requirement.
- The diff implements multiple roadmap milestones or performs a functional change plus a broad
  refactor.
- New types, validation, or constants independently reinterpret an existing contract instead of
  updating its authoritative boundary and intentional mirrors together.
- A TODO, placeholder control, fake data set, or disabled feature is added without an explicit
  request and roadmap entry.
- Comments restate the code instead of explaining a non-obvious constraint or decision.
- Tests mirror implementation details so closely that a broken user workflow could still pass.
- Error handling converts a specific failure into silent success or an unactionable generic state.
- The implementation is longer or more configurable only because generation is cheap.

For migrations, file deletion, authentication, cryptography, external uploads, or other
security/data-loss-sensitive changes, perform a separate adversarial review of the final diff.
Tests produced in the same pass as the implementation are useful but are not independent assurance.

For UI work, extend the existing visual language and build complete states: loading, empty, error,
success, keyboard use, labels, and focus behavior. Do not add non-functional controls or a generic
"AI dashboard" aesthetic—gratuitous gradients, glass panels, card grids, excessive badges, or
decorative copy—unless the product requirement specifically calls for it.

## Current decisions

- React and TypeScript own the interface; do not reintroduce PySide.
- SQLite and SQLAlchemy own local structured storage.
- The official `firecrawl-anydoc` binding is the document parser; do not reintroduce PyMuPDF merely
  for text extraction. A future visual PDF renderer is a separate decision.
- NetworkX is currently an in-memory domain prototype. Persisted relationship design belongs to the
  research-map milestone.
- The Python service is started beside Vite during development. Production packaging as a Tauri
  sidecar is not implemented yet.
- `Base.metadata.create_all()` is only sufficient for the current prototype. Add a migration system
  before making consequential schema changes to user data.

## Commands

Install dependencies:

```bash
uv sync
npm install
mise install
```

Run in a browser:

```bash
npm run dev
```

Run in Tauri:

```bash
npm run tauri dev
```

Run all relevant checks before handing off a change:

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

Run the checks affected by the change while iterating, then run the full set before completion.

## Change rules

- API changes require Python API tests and matching TypeScript types in `web/src/api.ts`.
- UI behavior changes require a Vitest/Testing Library test.
- Domain and persistence behavior changes require focused pytest coverage.
- Database changes must include a migration strategy once persistent user libraries are in use.
- Document-import changes must cover empty, unsupported, encrypted, malformed, scanned/OCR-needed,
  and oversized inputs where relevant.
- Do not silently enable Anydoc's hosted OCR option.
- Keep generated directories (`web/dist`, `src-tauri/target`, caches, Tauri schemas) out of commits.
- Commit lockfile changes when dependencies change: `uv.lock`, `package-lock.json`, or
  `src-tauri/Cargo.lock` as applicable.
- Update `README.md` when setup, architecture, or user-visible capabilities change.
- Update `docs/ROADMAP.md` when a milestone is completed, split, reordered, or intentionally deferred.

## Definition of done

A change is complete when:

1. The requested workflow works through the real boundary, not only as an isolated helper.
2. Failure states are safe and understandable.
3. Relevant tests cover the behavior and all affected checks pass.
4. No obsolete implementation, dependency, or generated artifact is left behind.
5. Documentation and the roadmap reflect the new state.

When handing off unfinished work, state the exact failing command, blocker, and next concrete step.

## Basis for the AI quality policy

- [OpenAI model guidance](https://developers.openai.com/api/docs/guides/latest-model): lean prompts,
  explicit autonomy boundaries, success criteria, and evaluation against representative work.
- [Google Engineering Practices](https://google.github.io/eng-practices/review/developer/small-cls.html):
  small self-contained changes with related tests are easier to reason about and review thoroughly.
- [OWASP Secure Coding with AI](https://cheatsheetseries.owasp.org/cheatsheets/Secure_Coding_with_AI_Cheat_Sheet.html):
  generated code needs normal security controls, and same-agent tests are not independent assurance.
- [2025 DORA report](https://cloud.google.com/blog/products/ai-machine-learning/announcing-the-2025-dora-report):
  AI amplifies the quality of the surrounding platform, workflows, and engineering system.
- [METR's 2026 productivity update](https://metr.org/blog/2026-02-24-uplift-update/): measured AI
  uplift is task- and workflow-dependent, and self-reported speed is an unreliable quality signal.
