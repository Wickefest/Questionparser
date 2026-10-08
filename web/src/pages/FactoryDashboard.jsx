import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { Link, useRouter } from "../lib/router";
import { EmptyState, MetricCard, ProgressBar, StatusBadge } from "../components/ui";

function isParsingBatch(batch) {
  const status = String(batch?.status || "").toUpperCase();
  const run = String(batch?.latestRunStatus || "").toUpperCase();
  return (
    ["UPLOADED", "PROCESSING"].includes(status) ||
    ["PENDING", "RUNNING", "PROCESSING", "QUEUED"].includes(run)
  );
}

function isSucceededBatch(batch) {
  if (isParsingBatch(batch)) return false;
  const status = String(batch?.status || "").toUpperCase();
  const run = String(batch?.latestRunStatus || "").toUpperCase();
  const approved = batch?.approvedCount || 0;
  const questions = batch?.questionCount || 0;
  return (
    approved > 0 ||
    ["READY_FOR_REVIEW", "COMPLETED"].includes(status) ||
    (run === "COMPLETED" && questions > 0)
  );
}

export function FactoryDashboard() {
  const { navigate } = useRouter();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [importView, setImportView] = useState("parsing"); // parsing | history | all

  const load = () => {
    setLoading(true);
    api
      .dashboard({ limit: 0 })
      .then((d) => {
        setData(d);
        setError("");
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, []);

  const allImports = data?.imports || data?.recentImports || [];
  const parsingImports = useMemo(() => allImports.filter(isParsingBatch), [allImports]);
  const historyImports = useMemo(() => allImports.filter(isSucceededBatch), [allImports]);
  const imports =
    importView === "parsing" ? parsingImports : importView === "history" ? historyImports : allImports;

  if (loading && !data) return <div className="page muted">Loading dashboard…</div>;
  if (error && !data) {
    return (
      <div className="page">
        <div className="error-banner">{error}</div>
        <p className="muted">
          The admin UI proxies `/api` to `http://127.0.0.1:8001`. Start the API with
          `python -m questbank serve` (do not use `--reload` while ingesting).
        </p>
        <button type="button" className="btn primary" onClick={load}>
          Retry
        </button>
      </div>
    );
  }

  const m = data?.metrics || {};

  return (
    <div className="page">
      {error ? <div className="error-banner">{error}</div> : null}
      <header className="page-header">
        <div>
          <h1>Question Factory</h1>
          <p className="subtitle">Turn examination papers into verified EduNets questions.</p>
        </div>
        <button type="button" className="btn primary" onClick={() => navigate("/admin/question-bank/import")}>
          + Import New Paper
        </button>
      </header>

      <div className="metrics-row">
        <MetricCard label="Total Questions" value={m.totalQuestions ?? 0} />
        <MetricCard label="Needs Review" value={m.needsReview ?? 0} />
        <MetricCard label="Processing Imports" value={m.processingImports ?? parsingImports.length} />
        <MetricCard label="Published Questions" value={m.publishedQuestions ?? 0} />
      </div>

      <section className="panel">
        <div
          className="panel-header"
          style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}
        >
          <h2 style={{ margin: 0 }}>Imports</h2>
          <div className="filter-pills" style={{ display: "flex", gap: 8 }}>
            <button
              type="button"
              className={`pill ${importView === "parsing" ? "active" : ""}`}
              onClick={() => setImportView("parsing")}
            >
              Parsing now ({parsingImports.length})
            </button>
            <button
              type="button"
              className={`pill ${importView === "history" ? "active" : ""}`}
              onClick={() => setImportView("history")}
            >
              History ({historyImports.length})
            </button>
            <button
              type="button"
              className={`pill ${importView === "all" ? "active" : ""}`}
              onClick={() => setImportView("all")}
            >
              All ({allImports.length})
            </button>
          </div>
        </div>
        {!imports.length ? (
          <EmptyState
            title={
              importView === "parsing"
                ? "Nothing is parsing right now."
                : importView === "history"
                  ? "No previous successful imports yet."
                  : "No examination papers imported yet."
            }
            action={
              importView === "all" ? (
                <button
                  type="button"
                  className="btn primary"
                  onClick={() => navigate("/admin/question-bank/import")}
                >
                  Import Your First Paper
                </button>
              ) : (
                <button type="button" className="btn" onClick={() => setImportView("all")}>
                  Show all imports
                </button>
              )
            }
          />
        ) : (
          <div className="import-list">
            {imports.map((batch) => {
              const total = batch.questionCount || 0;
              const approved = batch.approvedCount || 0;
              const needs = batch.needsReviewCount || 0;
              const succeeded = isSucceededBatch(batch);
              return (
                <article key={batch.id} className={`import-row ${succeeded ? "succeeded" : ""}`}>
                  <div className="import-main">
                    <div className="import-title">
                      {batch.subject} {batch.examYear} · {batch.paperCode || "Paper"}
                    </div>
                    <div className="muted">{batch.title}</div>
                    <div className="import-stats">
                      {total} questions parsed · {approved} approved · {needs} need review
                      {batch.latestRunStatus ? ` · run: ${batch.latestRunStatus}` : ""}
                      {succeeded ? " · original approved kept" : ""}
                    </div>
                    <ProgressBar value={approved} max={total || 1} />
                  </div>
                  <div className="import-side">
                    <StatusBadge status={batch.status} />
                    {succeeded && approved > 0 ? (
                      <span className="status-badge status-approved">{approved} approved</span>
                    ) : null}
                    <div className="muted tiny">
                      {batch.createdAt ? new Date(batch.createdAt).toLocaleString() : ""}
                    </div>
                    <div className="actions-row">
                      <Link className="btn" to={`/admin/question-bank/imports/${batch.id}`}>
                        Open Import
                      </Link>
                      <button
                        type="button"
                        className="btn danger"
                        style={{ padding: "4px 8px", fontSize: "12px" }}
                        onClick={async (e) => {
                          e.preventDefault();
                          if (window.confirm(`Are you sure you want to delete this import batch?`)) {
                            try {
                              setError("");
                              await api.deleteBatch(batch.id);
                              load();
                            } catch (err) {
                              setError(err.message);
                            }
                          }
                        }}
                      >
                        Delete
                      </button>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
