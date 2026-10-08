import { useEffect, useState } from "react";
import { api } from "../api/client";
import { Link, useRouter } from "../lib/router";
import { EmptyState, ProgressBar, StatusBadge } from "../components/ui";
import { cleanTopicTitle } from "./QuestionBank";

const FILTERS = ["ALL", "NEEDS_REVIEW", "READY", "APPROVED", "REJECTED"];
const EMPTY_OPTIONS = { A: "", B: "", C: "", D: "" };

export function ReviewQueue({ batchId }) {
  const { navigate } = useRouter();
  const [filter, setFilter] = useState("ALL");
  const [q, setQ] = useState("");
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [addOpen, setAddOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({
    questionRef: "",
    questionType: "MCQ",
    stem: "",
    options: { ...EMPTY_OPTIONS },
    answerValue: "A",
    explanation: "",
  });

  useEffect(() => {
    let alive = true;
    api
      .drafts(batchId, { status: filter, q: q || undefined })
      .then((d) => {
        if (!alive) return;
        setData(d);
        setForm((prev) => ({
          ...prev,
          questionRef: addOpen ? prev.questionRef : d.suggestedQuestionRef || prev.questionRef || "",
        }));
      })
      .catch((e) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [batchId, filter, q]);

  const openAdd = () => {
    setError("");
    setForm({
      questionRef: data?.suggestedQuestionRef || "",
      questionType: "MCQ",
      stem: "",
      options: { ...EMPTY_OPTIONS },
      answerValue: "A",
      explanation: "",
    });
    setAddOpen(true);
  };

  const isStructuredForm = form.questionType === "STRUCTURED";

  const onCreate = async (e) => {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      const result = await api.createDraft(batchId, {
        questionRef: form.questionRef || undefined,
        questionType: form.questionType,
        stem: form.stem,
        options: isStructuredForm ? undefined : form.options,
        answerValue: isStructuredForm ? undefined : form.answerValue,
        explanation: form.explanation || undefined,
      });
      setAddOpen(false);
      navigate(`/admin/question-bank/imports/${batchId}/review/${result.draftId}`);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  if (error && !data) return <div className="page error-banner">{error}</div>;
  if (!data) return <div className="page muted">Loading review queue…</div>;

  const batch = data.batch || {};
  const done = data.approved || 0;
  const total = data.total || 0;

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <button type="button" className="btn linkish" onClick={() => navigate(`/admin/question-bank/imports/${batchId}`)}>
            ← Back to Import
          </button>
          <h1>
            {batch.subject} {batch.examYear} · {batch.paperCode}
          </h1>
          <p className="subtitle">Review Progress · {done} / {total} approved</p>
          <ProgressBar value={done} max={total || 1} />
        </div>
        <button type="button" className="btn primary" onClick={openAdd}>
          + Add question
        </button>
      </header>

      {error && <div className="error-banner">{error}</div>}

      {addOpen && (
        <form className="panel" style={{ marginBottom: 20 }} onSubmit={onCreate}>
          <h2 style={{ marginTop: 0 }}>Add manual question</h2>
          <p className="muted" style={{ marginTop: 0 }}>
            Use when parse missed a question (e.g. gap in numbering). Suggested next ref:{" "}
            <strong>Q{data.suggestedQuestionRef || "—"}</strong>
          </p>
          <div className="filter-pills" style={{ marginBottom: 12 }}>
            <button
              type="button"
              className={`pill ${!isStructuredForm ? "active" : ""}`}
              onClick={() => setForm({ ...form, questionType: "MCQ" })}
            >
              MCQ (A/B/C/D)
            </button>
            <button
              type="button"
              className={`pill ${isStructuredForm ? "active" : ""}`}
              onClick={() => setForm({ ...form, questionType: "STRUCTURED" })}
            >
              Structured (free-response)
            </button>
          </div>
          <div className="field-row" style={{ display: "grid", gridTemplateColumns: isStructuredForm ? "1fr" : "120px 1fr", gap: 12 }}>
            <label className="field">
              <span>Question #</span>
              <input
                value={form.questionRef}
                onChange={(e) => setForm({ ...form, questionRef: e.target.value })}
                placeholder={data.suggestedQuestionRef || "3"}
                required
              />
            </label>
            {!isStructuredForm && (
              <label className="field">
                <span>Correct answer</span>
                <select
                  value={form.answerValue}
                  onChange={(e) => setForm({ ...form, answerValue: e.target.value })}
                >
                  {["A", "B", "C", "D"].map((l) => (
                    <option key={l} value={l}>
                      {l}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
          <label className="field" style={{ display: "block", marginTop: 12 }}>
            <span>Stem</span>
            <textarea
              rows={4}
              value={form.stem}
              onChange={(e) => setForm({ ...form, stem: e.target.value })}
              placeholder="Question text (LaTeX/chem ok, e.g. Fe^2+)"
              required
              style={{ width: "100%" }}
            />
          </label>
          {isStructuredForm ? (
            <p className="muted tiny" style={{ marginTop: 12 }}>
              This creates a structured question with one empty part ("a") — after
              creating, use the review editor's part/answer builder to add the
              rest of the parts, marks, images and mark-scheme answers.
            </p>
          ) : (
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 12 }}>
              {["A", "B", "C", "D"].map((label) => (
                <label key={label} className="field">
                  <span>Option {label}</span>
                  <input
                    value={form.options[label]}
                    onChange={(e) =>
                      setForm({
                        ...form,
                        options: { ...form.options, [label]: e.target.value },
                      })
                    }
                    placeholder={`Option ${label}`}
                  />
                </label>
              ))}
            </div>
          )}
          <label className="field" style={{ display: "block", marginTop: 12 }}>
            <span>{isStructuredForm ? "Notes for part (a) (optional)" : "Explanation (optional)"}</span>
            <textarea
              rows={2}
              value={form.explanation}
              onChange={(e) => setForm({ ...form, explanation: e.target.value })}
              style={{ width: "100%" }}
            />
          </label>
          <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
            <button type="submit" className="btn primary" disabled={saving}>
              {saving ? "Creating…" : "Create & open review"}
            </button>
            <button type="button" className="btn" disabled={saving} onClick={() => setAddOpen(false)}>
              Cancel
            </button>
          </div>
        </form>
      )}

      <div className="toolbar">
        <div className="filter-pills">
          {FILTERS.map((f) => (
            <button
              key={f}
              type="button"
              className={`pill ${filter === f ? "active" : ""}`}
              onClick={() => setFilter(f)}
            >
              {f.replace(/_/g, " ")}
            </button>
          ))}
        </div>
        <input
          className="search"
          placeholder="Search question reference or text"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
      </div>

      {!data.drafts?.length ? (
        <EmptyState title={done === total && total > 0 ? "All questions reviewed." : "No drafts match."}>
          {done === total && total > 0
            ? `${done} / ${total} completed.`
            : total > 0
              ? "Try the ALL tab — new parses often land as Draft, which still need review."
              : "No questions yet — use Add question, or re-run import."}
        </EmptyState>
      ) : (
        <div className="draft-list">
          {data.drafts.map((d) => (
            <article key={d.id} className={`draft-row ${d.status === "APPROVED" ? "approved" : ""}`}>
              <div>
                <strong>Q{d.questionRef}</strong>
                <div className="preview">{d.preview || "—"}</div>
                <div className="muted">
                  Official answer: {d.officialAnswer || "—"}
                  {d.hasVisual ? " · visual" : ""}
                </div>
                <div className="badge-row">
                  {d.classification ? (
                    <>
                      <span className="topic-badge">🏷️ {cleanTopicTitle(d.classification.title)}</span>
                      {d.classification.subtopicTitle && (
                        <span className="topic-badge subtopic-badge">
                          ↳ {cleanTopicTitle(d.classification.subtopicTitle)}
                        </span>
                      )}
                    </>
                  ) : d.status === "APPROVED" ? (
                    <span className="topic-badge">🏷️ Original classification kept</span>
                  ) : (
                    <span className="pending-badge">⏳ Pending classification</span>
                  )}
                  {d.bloomLevel && <span className="bloom-badge">🧠 {d.bloomLevel}</span>}
                </div>
              </div>
              <div className="draft-side">
                <StatusBadge status={d.status} />
                <Link className="btn" to={`/admin/question-bank/imports/${batchId}/review/${d.id}`}>
                  Review →
                </Link>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
