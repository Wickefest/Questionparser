import { useEffect, useMemo, useState } from "react";
import { api, formatBytes, sha256File } from "../api/client";
import { Link, useRouter } from "../lib/router";
import { BusyBanner, LoadingOverlay } from "../components/LoadingOverlay";
import { HistoryPanel, UploadZone } from "../components/UploadZone";

const INITIAL = {
  subject: "Chemistry",
  syllabusId: "",
  examYear: "2023",
  paperCode: "Paper 1",
  school: "",
  title: "",
};

export function ImportWizard() {
  const { navigate } = useRouter();
  const [step, setStep] = useState(1);
  const [form, setForm] = useState(INITIAL);
  const [syllabi, setSyllabi] = useState([]);
  const [questionFile, setQuestionFile] = useState(null);
  const [answerFile, setAnswerFile] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [busyTitle, setBusyTitle] = useState("");
  const [busyMessage, setBusyMessage] = useState("");
  const [duplicate, setDuplicate] = useState(null);
  const [history, setHistory] = useState([]);
  const [historyView, setHistoryView] = useState("all"); // parsing | history | all

  const loadHistory = () => {
    api
      .dashboard({ limit: 0 })
      .then((d) => setHistory(d.imports || d.recentImports || []))
      .catch(() => {});
  };

  useEffect(() => {
    api.syllabi().then((d) => setSyllabi(d.syllabi || [])).catch(() => {});
    loadHistory();
    const id = setInterval(loadHistory, 15000);
    return () => clearInterval(id);
  }, []);

  const isParsingBatch = (batch) => {
    const status = String(batch?.status || "").toUpperCase();
    const run = String(batch?.latestRunStatus || "").toUpperCase();
    return (
      ["UPLOADED", "PROCESSING"].includes(status) ||
      ["PENDING", "RUNNING", "PROCESSING", "QUEUED"].includes(run)
    );
  };

  const isSucceededBatch = (batch) => {
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
  };

  const parsingHistory = useMemo(() => history.filter(isParsingBatch), [history]);
  const finishedHistory = useMemo(() => history.filter(isSucceededBatch), [history]);
  const visibleHistory =
    historyView === "parsing"
      ? parsingHistory
      : historyView === "history"
        ? finishedHistory
        : history;

  const selectedSyllabus = useMemo(
    () => syllabi.find((s) => s.id === form.syllabusId),
    [syllabi, form.syllabusId]
  );

  const setField = (key, value) => setForm((f) => ({ ...f, [key]: value }));

  const onFile = (kind, file) => {
    setError("");
    setDuplicate(null);
    if (!file) return;
    if (file.type && file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) {
      setError("Only PDF files are accepted.");
      return;
    }
    if (kind === "question") setQuestionFile(file);
    else setAnswerFile(file);
  };

  const start = async ({ force = false } = {}) => {
    setBusy(true);
    setError("");
    setBusyTitle("Starting import");
    setBusyMessage("Validating files and checking for duplicates…");
    try {
      if (!questionFile || !answerFile) throw new Error("Both PDFs are required.");
      if (!form.syllabusId) throw new Error("Syllabus is required.");
      const resolvedTitle = questionFile.name.replace(/\.pdf$/i, "");

      if (!force) {
        const qHash = await sha256File(questionFile);
        const aHash = await sha256File(answerFile);
        const dup = await api.checkDuplicates({
          questionSha256: qHash,
          answerSha256: aHash,
        });
        if (dup.status !== "new") {
          setDuplicate(dup);
          setBusy(false);
          return;
        }
      }

      setBusyTitle("Uploading papers");
      setBusyMessage("Sending question paper and mark scheme to the server…");

      const fd = new FormData();
      fd.append("question_pdf", questionFile);
      fd.append("answer_pdf", answerFile);
      fd.append("title", resolvedTitle);
      fd.append("subject", form.subject);
      
      const yearStr = selectedSyllabus ? String(selectedSyllabus.year) : "2023";
      fd.append("exam_year", yearStr);
      fd.append("paper_code", "Paper 1");
      if (form.syllabusId) fd.append("syllabus_id", form.syllabusId);
      if (force) fd.append("force", "true");

      setBusyTitle("Creating ingestion batch");
      setBusyMessage("Saving source documents and starting the parser…");

      const result = await api.createImport(fd);
      if (!result.created) {
        setDuplicate(result);
        return;
      }
      setBusyTitle("Redirecting");
      setBusyMessage("Opening the processing page…");
      navigate(`/admin/question-bank/imports/${result.batchId}`);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
      setBusyTitle("");
      setBusyMessage("");
    }
  };

  const historyItems = visibleHistory.map((batch) => ({
    id: batch.id,
    title: `${batch.subject || "Paper"} · ${batch.examYear || "—"} · ${batch.paperCode || ""}`.trim(),
    meta: `${batch.title || ""} · ${batch.questionCount || 0} questions · ${batch.approvedCount || 0} approved${
      batch.latestRunStatus ? ` · run: ${batch.latestRunStatus}` : ""
    }${isSucceededBatch(batch) ? " · original approved kept" : ""}`,
    status: batch.status,
    tone: isSucceededBatch(batch) ? "succeeded" : "",
    when: batch.createdAt ? new Date(batch.createdAt).toLocaleString() : "",
    action: (
      <div className="actions-row">
        <Link className="btn" to={`/admin/question-bank/imports/${batch.id}`}>
          Open
        </Link>
        <button
          type="button"
          className="btn danger"
          style={{ padding: "4px 8px", fontSize: "12px" }}
          onClick={async (e) => {
            e.preventDefault();
            if (window.confirm("Are you sure you want to delete this import batch?")) {
              try {
                setBusy(true);
                setBusyTitle("Deleting paper");
                setBusyMessage("Cleaning up assets and database tables...");
                await api.deleteBatch(batch.id);
                loadHistory();
              } catch (err) {
                setError(err.message);
              } finally {
                setBusy(false);
                setBusyTitle("");
                setBusyMessage("");
              }
            }
          }}
        >
          Delete
        </button>
      </div>
    ),
  }));

  return (
    <div className="page relative-page">
      <LoadingOverlay show={busy} title={busyTitle || "Working…"} message={busyMessage} />

      <header className="page-header">
        <div>
          <h1>Import Paper</h1>
          <p className="subtitle">Upload a question paper and mark scheme to begin ingestion.</p>
        </div>
      </header>

      <BusyBanner show={busy} title={busyTitle || "Working…"} message={busyMessage} />

      <div className="wizard-steps">
        <span className={step === 1 ? "active" : ""}>1. Paper Details</span>
        <span className={step === 2 ? "active" : ""}>2. Source Files</span>
        <span className={step === 3 ? "active" : ""}>3. Confirm</span>
      </div>

      {error ? <div className="error-banner">{error}</div> : null}

      {duplicate ? (
        <div className="panel warn-panel">
          <h3>{duplicate.message}</h3>
          {duplicate.existingBatch ? (
            <div className="muted">
              {duplicate.existingBatch.subject} · {duplicate.existingBatch.examYear} ·{" "}
              {duplicate.existingBatch.paperCode}
              <div>
                {duplicate.existingBatch.questionCount} questions ·{" "}
                {duplicate.existingBatch.approvedCount} approved ·{" "}
                {duplicate.existingBatch.needsReviewCount} need review
              </div>
            </div>
          ) : null}
          <div className="actions-row">
            {duplicate.status === "exact_duplicate" ? (
              <>
                <button
                  type="button"
                  className="btn primary"
                  onClick={() =>
                    navigate(`/admin/question-bank/imports/${duplicate.existingBatch.id}`)
                  }
                >
                  Open Existing Import
                </button>
                <button type="button" className="btn" disabled={busy} onClick={() => start({ force: true })}>
                  Import Again
                </button>
              </>
            ) : (
              <>
                <button type="button" className="btn primary" disabled={busy} onClick={() => start({ force: true })}>
                  Continue as New Import
                </button>
                <button type="button" className="btn" onClick={() => setDuplicate(null)}>
                  Cancel
                </button>
              </>
            )}
          </div>
        </div>
      ) : null}

      {step === 1 ? (
        <section className="panel form-grid">
          <label>
            Subject
            <input value={form.subject} onChange={(e) => setField("subject", e.target.value)} />
          </label>
          <label>
            Syllabus <span style={{ color: "red" }}>*</span>
            <select value={form.syllabusId} onChange={(e) => setField("syllabusId", e.target.value)}>
              <option value="">Select syllabus…</option>
              {syllabi.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.subject} {s.code} · {s.year}
                </option>
              ))}
            </select>
          </label>
          <div className="actions-row span-2">
            <button
              type="button"
              className="btn primary"
              disabled={!form.syllabusId}
              onClick={() => setStep(2)}
            >
              Continue
            </button>
          </div>
        </section>
      ) : null}

      {step === 2 ? (
        <section className="panel">
          <div className="upload-legend">
            <div>
              <strong>Source files</strong>
              <p className="muted">Upload both PDFs. Each box is labelled so you can tell question paper from mark scheme.</p>
            </div>
          </div>
          <div className="upload-grid">
            <UploadZone
              badge="Required"
              title="Question Paper"
              hint="Drop the examination question PDF here"
              file={questionFile}
              disabled={busy}
              onFile={(f) => onFile("question", f)}
              onClear={() => setQuestionFile(null)}
            />
            <UploadZone
              badge="Required"
              title="Answer / Mark Scheme"
              hint="Drop the corresponding mark scheme PDF here"
              file={answerFile}
              disabled={busy}
              onFile={(f) => onFile("answer", f)}
              onClear={() => setAnswerFile(null)}
            />
          </div>
          <div className="actions-row">
            <button type="button" className="btn" onClick={() => setStep(1)} disabled={busy}>
              Back
            </button>
            <button
              type="button"
              className="btn primary"
              disabled={!questionFile || !answerFile || busy}
              onClick={() => setStep(3)}
            >
              Continue
            </button>
          </div>
        </section>
      ) : null}

      {step === 3 ? (
        <section className="panel">
          <h2>Confirm</h2>
          <dl className="summary-list">
            <div>
              <dt>Subject</dt>
              <dd>{form.subject}</dd>
            </div>
            <div>
              <dt>Syllabus</dt>
              <dd>
                {selectedSyllabus
                  ? `${selectedSyllabus.code} · ${selectedSyllabus.year}`
                  : "Not selected"}
              </dd>
            </div>
            <div>
              <dt>Paper</dt>
              <dd>
                {form.examYear} · {form.title || "Untitled"} · {form.paperCode}
              </dd>
            </div>
            <div>
              <dt>Question Paper</dt>
              <dd>
                ✓ {questionFile?.name}
                {questionFile ? ` · ${formatBytes(questionFile.size)}` : ""}
              </dd>
            </div>
            <div>
              <dt>Answer Scheme</dt>
              <dd>
                ✓ {answerFile?.name}
                {answerFile ? ` · ${formatBytes(answerFile.size)}` : ""}
              </dd>
            </div>
          </dl>
          <div className="actions-row">
            <button type="button" className="btn" onClick={() => setStep(2)} disabled={busy}>
              Back
            </button>
            <button type="button" className="btn primary" onClick={() => start()} disabled={busy}>
              {busy ? "Working…" : "Start Processing"}
            </button>
          </div>
        </section>
      ) : null}

      <HistoryPanel
        title="Imports"
        empty={
          historyView === "parsing"
            ? "Nothing is parsing right now."
            : historyView === "history"
              ? "No previous successful imports yet."
              : "No papers imported yet. Completed and in-progress imports will appear here."
        }
        headerExtra={
          <div className="filter-pills" style={{ display: "flex", gap: 8 }}>
            <button
              type="button"
              className={`pill ${historyView === "parsing" ? "active" : ""}`}
              onClick={() => setHistoryView("parsing")}
            >
              Parsing now ({parsingHistory.length})
            </button>
            <button
              type="button"
              className={`pill ${historyView === "history" ? "active" : ""}`}
              onClick={() => setHistoryView("history")}
            >
              History ({finishedHistory.length})
            </button>
            <button
              type="button"
              className={`pill ${historyView === "all" ? "active" : ""}`}
              onClick={() => setHistoryView("all")}
            >
              All ({history.length})
            </button>
          </div>
        }
        items={historyItems}
      />
    </div>
  );
}
