import { FormEvent, useEffect, useState } from "react";

import { createSource, getHealth, getSources, type Source } from "./api";
import "./styles.css";

export default function App() {
  const [sources, setSources] = useState<Source[]>([]);
  const [serviceReady, setServiceReady] = useState(false);
  const [title, setTitle] = useState("");
  const [error, setError] = useState<string | null>(null);

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

  return (
    <div className="app-shell">
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

      <main>
        <header className="page-header">
          <div>
            <p className="eyebrow">Research workspace</p>
            <h1>Your library</h1>
            <p>Collect papers and keep every idea connected to its source.</p>
          </div>
        </header>

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

        {error && <p className="error-message">{error}</p>}

        <section className="library" aria-labelledby="library-heading">
          <div className="section-heading">
            <h2 id="library-heading">Sources</h2>
            <span>{sources.length} total</span>
          </div>
          {sources.length === 0 ? (
            <div className="empty-state">
              <span className="empty-glyph">↗</span>
              <h3>Start with one useful source</h3>
              <p>Add its title above. PDF import and metadata lookup come next.</p>
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
  );
}
