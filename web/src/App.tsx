import { FormEvent, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  convertDocument,
  createSource,
  getHealth,
  getSources,
  type ConvertedDocument,
  type Source,
} from "./api";
import "./styles.css";

const themeStorageKey = "litrev-theme";

function loadDarkModePreference(): boolean {
  try {
    return window.localStorage.getItem(themeStorageKey) !== "light";
  } catch {
    return true;
  }
}

export default function App() {
  const [sources, setSources] = useState<Source[]>([]);
  const [serviceReady, setServiceReady] = useState(false);
  const [title, setTitle] = useState("");
  const [document, setDocument] = useState<File | null>(null);
  const [convertedDocument, setConvertedDocument] = useState<ConvertedDocument | null>(null);
  const [isConverting, setIsConverting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isDarkMode, setIsDarkMode] = useState(loadDarkModePreference);

  useEffect(() => {
    try {
      window.localStorage.setItem(themeStorageKey, isDarkMode ? "dark" : "light");
    } catch {
      // Storage can be unavailable in private or embedded browser contexts.
    }
  }, [isDarkMode]);

  useEffect(() => {
    Promise.all([getHealth(), getSources()])
      .then(([, library]) => {
        setServiceReady(true);
        setSources(library);
      })
      .catch(() => {
        setError("The local Litrev service is not available.");
      });
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const cleanTitle = title.trim();
    if (!cleanTitle) return;

    try {
      const source = await createSource(cleanTitle);
      setSources((current) => [...current, source].sort((a, b) => a.title.localeCompare(b.title)));
      setTitle("");
      setError(null);
    } catch {
      setError("The source could not be saved.");
    }
  }

  async function handleDocumentImport(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!document) return;

    setIsConverting(true);
    try {
      setConvertedDocument(await convertDocument(document));
      setError(null);
    } catch {
      setError("Anydoc could not convert this document.");
    } finally {
      setIsConverting(false);
    }
  }

  return (
    <div className="app-shell" data-theme={isDarkMode ? "dark" : "light"}>
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">L</span>
          <span>Litrev</span>
        </div>
        <nav aria-label="Workspace">
          <button className="nav-item active" type="button">
            <span>Library</span>
            <span className="count">{sources.length}</span>
          </button>
          <button className="nav-item" type="button" disabled>
            Workbench
          </button>
          <button className="nav-item" type="button" disabled>
            Research map
          </button>
        </nav>
        <div className={`service-status ${serviceReady ? "ready" : ""}`}>
          <span className="status-dot" />
          {serviceReady ? "Local service ready" : "Connecting locally"}
        </div>
      </aside>

      <div className="workspace">
        <header className="page-header">
          <div>
            <p className="eyebrow">Research workspace</p>
            <h1>Your library</h1>
            <p>Collect papers and keep every idea connected to its source.</p>
          </div>
          <button
            aria-label={isDarkMode ? "Switch to light mode" : "Switch to dark mode"}
            aria-pressed={isDarkMode}
            className="theme-toggle"
            onClick={() => setIsDarkMode((current) => !current)}
            title={isDarkMode ? "Switch to light mode" : "Switch to dark mode"}
            type="button"
          >
            <svg aria-hidden="true" viewBox="0 0 24 24">
              <circle cx="12" cy="12" r="4" />
              <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
            </svg>
          </button>
        </header>

        <main>
          <section className="capture-panel" aria-labelledby="capture-heading">
            <div>
              <p className="eyebrow">Quick capture</p>
              <h2 id="capture-heading">Add a source</h2>
            </div>
            <form onSubmit={handleSubmit}>
              <label htmlFor="source-title">Title</label>
              <div className="input-row">
                <input
                  id="source-title"
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  placeholder="Paper, book, article, or dataset"
                />
                <button type="submit">Add to library</button>
              </div>
            </form>
          </section>

          <section className="document-panel" aria-labelledby="document-heading">
            <div className="document-copy">
              <p className="eyebrow">Anydoc import</p>
              <h2 id="document-heading">Read a document locally</h2>
              <p>Convert a PDF or research document to structured Markdown without uploading it.</p>
            </div>
            <form onSubmit={handleDocumentImport}>
              <label htmlFor="document-file">Choose a document</label>
              <div className="input-row">
                <input
                  id="document-file"
                  type="file"
                  accept=".pdf,.doc,.docx,.odt,.rtf,.epub,.ppt,.pptx,.xls,.xlsx,.ods,.odp,.csv"
                  onChange={(event) => setDocument(event.target.files?.[0] ?? null)}
                />
                <button type="submit" disabled={!document || isConverting}>
                  {isConverting ? "Reading…" : "Read document"}
                </button>
              </div>
            </form>
          </section>

          {error && <p className="error-message">{error}</p>}

          {convertedDocument && (
            <section className="document-preview" aria-labelledby="preview-heading">
              <div className="section-heading">
                <div>
                  <p className="eyebrow">{convertedDocument.format}</p>
                  <h2 id="preview-heading">{convertedDocument.filename}</h2>
                </div>
                <span>Converted locally</span>
              </div>
              <article>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {convertedDocument.markdown}
                </ReactMarkdown>
              </article>
            </section>
          )}

          <section className="library" aria-labelledby="library-heading">
            <div className="section-heading">
              <h2 id="library-heading">Sources</h2>
              <span>{sources.length} total</span>
            </div>
            {sources.length === 0 ? (
              <div className="empty-state">
                <span className="empty-glyph">↗</span>
                <h3>Start with one useful source</h3>
                <p>Add its title or import a document with Anydoc.</p>
              </div>
            ) : (
              <ul className="source-list">
                {sources.map((source) => (
                  <li key={source.id}>
                    <div>
                      <strong>{source.title}</strong>
                      <span>{source.doi ?? "Metadata not added yet"}</span>
                    </div>
                    <span className="source-kind">Source</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </main>
      </div>
    </div>
  );
}
