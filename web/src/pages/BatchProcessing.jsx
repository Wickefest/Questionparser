import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useRouter } from "../lib/router";
import { BusyBanner, LoadingOverlay } from "../components/LoadingOverlay";
import { StatusBadge } from "../components/ui";

export function BatchProcessing({ batchId }) {
  const { navigate } = useRouter();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    let alive = true;
    let timer = null;
    setNotFound(false);
    setData(null);
    setError("");

    const load = () =>
      api
        .batch(batchId)
        .then((d) => {
          if (!alive) return;
          setData(d);
          setError("");
          setNotFound(false);
        })
        .catch((e) => {
          if (!alive) return;
          const msg = e.message || String(e);
          // Deleted / missing batches must not poll forever.
          if (/not found/i.test(msg) || /\b404\b/.test(msg)) {
            setNotFound(true);
            setError("This import batch no longer exists (it was deleted or never saved).");
            if (timer) {
              clearInterval(timer);
              timer = null;
            }
            return;
          }
          setError(msg);
        });

    load();
    timer = setInterval(load, 2500);
    return () => {
      alive = false;
      if (timer) clearInterval(timer);
    };
  }, [batchId]);

  const reprocess = async () => {
    setBusy(true);
    setError("");
    try {
      await api.reprocess(batchId);
      const refreshed = await api.batch(batchId);
      setData(refreshed);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  if (notFound) {
    return (
      <div className="page">
        <div className="error-banner">Import batch not found</div>
        <p className="muted">
          Batch <code>{batchId}</code> is gone from the database. Open Import History and pick a live batch.
        </p>
        <button type="button" className="btn primary" onClick={() => navigate("/admin/question-bank/imports")}>
          Back to Imports
        </button>
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="page">
        <div className="error-banner">{error}</div>
        <p className="muted">Start the API with `python -m questbank serve` (no --reload), then refresh.</p>
      </div>
    );
  }
  if (!data) return <div className="page muted">Loading import…</div>;

  const stages = data.stages || {};
  const complete = stages.complete;
  const failed = stages.failed && !(data.questionCount > 0);
  const inProgress = !complete && !failed;
  const canReview = (data.questionCount || 0) > 0;

  return (
    <div className="page relative-page">
      <LoadingOverlay
        show={busy}
        title="Restarting processing"
        message="Queuing a fresh ingest run for this batch…"
      />

      <header className="page-header">
        <div>
          <h1>
            {data.subject} {data.examYear}
          </h1>
          <p className="subtitle">
            {data.paperCode} · {data.title}
          </p>
        </div>
        <StatusBadge status={data.status} />
      </header>

      {error ? <div className="error-banner">{error}</div> : null}
      <BusyBanner
        show={busy || inProgress}
        title={busy ? "Restarting…" : "Processing in progress"}
        message={
          busy
            ? "Please wait."
            : data.job?.message || "Stages update automatically. Drafts appear below as each page finishes."
        }
      />

      <div className="stage-grid">
        <StageColumn title="QUESTION PAPER" items={stages.questionPaper || []} />
        <StageColumn title="ANSWER SCHEME" items={stages.answerScheme || []} />
        <StageColumn title="VALIDATION" items={stages.validation || []} />
      </div>

      <section className="panel counts-row">
        <Count label="Questions detected" value={data.questionCount} />
        <Count label="Questions persisted" value={data.questionCount} />
        <Count label="Needs review" value={data.needsReviewCount} />
        <Count label="Approved" value={data.approvedCount} />
      </section>

      {failed ? (
        <section className="panel error-panel">
          <h2>Processing failed</h2>
          <p>{stages.errorMessage || data.job?.message || "An error occurred during processing."}</p>
          <p className="muted">Run status: {data.latestRunStatus}</p>
          <button type="button" className="btn" disabled={busy} onClick={reprocess}>
            {busy ? "Restarting…" : "Retry / Reprocess"}
          </button>
        </section>
      ) : null}

      {complete ? (
        <section className="panel success-panel">
          <h2>✓ Processing Complete</h2>
          <p>
            {data.questionCount} questions detected · {data.approvedCount} approved ·{" "}
            {data.needsReviewCount} need review
          </p>
          {data.needsReviewCount ? (
            <p className="muted">
              Incomplete matches or visual crops are flagged for review — they are not a failed import.
            </p>
          ) : null}
          <div className="actions-row">
            <button
              type="button"
              className="btn primary"
              onClick={() => navigate(`/admin/question-bank/imports/${batchId}/review`)}
            >
              Review Questions
            </button>
            <button type="button" className="btn" disabled={busy} onClick={reprocess}>
              {busy ? "Restarting…" : "Reprocess"}
            </button>
          </div>
        </section>
      ) : !failed ? (
        <section className="panel">
          <p>
            <strong>{data.job?.message || "Processing in progress…"}</strong>
          </p>
          <p className="muted">
            Drafts are saved after each page. Gemini can take several minutes per page — this screen
            updates on its own.
          </p>
          <div className="actions-row">
            {canReview ? (
              <button
                type="button"
                className="btn primary"
                onClick={() => navigate(`/admin/question-bank/imports/${batchId}/review`)}
              >
                Review {data.questionCount} drafts so far
              </button>
            ) : null}
            <button
              type="button"
              className="btn danger"
              disabled={busy}
              onClick={async () => {
                if (window.confirm("Are you sure you want to cancel the ingestion process?")) {
                  try {
                    setBusy(true);
                    await api.cancelIngest(batchId);
                    const refreshed = await api.batch(batchId);
                    setData(refreshed);
                  } catch (e) {
                    setError(e.message || String(e));
                  } finally {
                    setBusy(false);
                  }
                }
              }}
            >
              Cancel Ingestion
            </button>
          </div>
        </section>
      ) : null}
    </div>
  );
}

function StageColumn({ title, items }) {
  return (
    <section className="panel stage-col">
      <h3>{title}</h3>
      <ul className="stage-list">
        {items.map((item) => (
          <li key={item.label} className={`stage-${item.state}`}>
            <span className="stage-mark">
              {item.state === "done" ? "✓" : item.state === "active" ? "●" : item.state === "error" ? "!" : "○"}
            </span>
            {item.label}
          </li>
        ))}
      </ul>
    </section>
  );
}

function Count({ label, value }) {
  return (
    <div>
      <div className="metric-value">{value ?? "—"}</div>
      <div className="metric-label">{label}</div>
    </div>
  );
}
