# Litrev

Litrev is an experimental, local-first desktop workspace for organizing the papers, notes,
summaries, and relationships that make up a literature review.

The project currently targets one person's research workflow. Its first product goal is a
traceable path from a source to a precise note, and from that note to the ideas and relationships
used in a review.

## Architecture

Litrev combines a web-based interface with a local Python research service inside a desktop shell:

```text
Tauri desktop window
└── React + TypeScript interface
    └── FastAPI local service
        ├── SQLite + SQLAlchemy library
        ├── PyMuPDF document processing
        └── NetworkX research relationships
```

The interface uses React, TypeScript, and Vite because the planned workspace needs rich tables,
note editing, PDF interaction, and visual research maps. Tauri turns that interface into a small
desktop application. Python remains responsible for local data, document processing, research
logic, and eventual AI-assisted features.

The Python code is separated into `domain`, `services`, and `infrastructure` packages so the
research model does not depend on React, FastAPI, or a particular storage implementation.

## Repository layout

```text
web/                 React and TypeScript interface
src-tauri/           Tauri 2 desktop shell
src/litrev/          Python local service and research logic
tests/               Python tests
package.json         Frontend and desktop commands
pyproject.toml       Python package and tooling
```

## Requirements

- Python 3.14 or newer
- [uv](https://docs.astral.sh/uv/)
- Node.js and npm
- Rust and the platform prerequisites required by Tauri for desktop development

Rust is only needed to run or compile the Tauri window. React and the Python service can be
developed in a normal browser without it.

## Setup

```bash
uv sync
npm install
```

Run the React interface and local Python service together:

```bash
npm run dev
```

Then open `http://127.0.0.1:1420`. To run the same interface in the Tauri desktop window:

```bash
npm run tauri dev
```

During desktop development, Tauri's `beforeDevCommand` starts both Vite and the local Python
service. Producing a distributable application will additionally require packaging the Python
service as a Tauri sidecar; that packaging step is intentionally deferred while the workflow is
still evolving.

## Checks

```bash
uv run litrev --check
uv run pytest
uv run ruff check .
npm run test:web
npm run lint:web
npm run build:web
```

## Current vertical slice

The repository already supports a small end-to-end workflow:

1. React checks the local service status.
2. The library reads sources through FastAPI.
3. A source can be created from the interface.
4. SQLAlchemy saves it to the local SQLite library.

PDF import, metadata lookup, note capture, research maps, synchronization, and AI assistance remain
future milestones.
