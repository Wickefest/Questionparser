import { useState } from "react";
import { api } from "../api/client";
import { QuestionRenderer } from "../components/QuestionRenderer";

export function SimulatedTest() {
  const [phase, setPhase] = useState("setup"); // setup | running | done
  const [source, setSource] = useState("draft"); // draft | structured | bank
  const [count, setCount] = useState(10);
  const [assetFormat, setAssetFormat] = useState("svg"); // svg | png — Simulated Test only
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const [questions, setQuestions] = useState([]);
  const [index, setIndex] = useState(0);
  const [selected, setSelected] = useState(null);
  const [partAnswers, setPartAnswers] = useState({}); // structured: {label: text}
  const [result, setResult] = useState(null); // {correct, correctAnswer, partsAnswer, explanation, explanationSource}
  const [results, setResults] = useState([]); // per-question outcome, for the summary screen

  async function startTest() {
    setError("");
    setLoading(true);
    try {
      const data = await api.simulatedTestQuestions(source, count, assetFormat);
      if (!data.questions?.length) {
        setError("No questions available for this source yet.");
        setLoading(false);
        return;
      }
      setQuestions(data.questions);
      setIndex(0);
      setSelected(null);
      setPartAnswers({});
      setResult(null);
      setResults([]);
      setPhase("running");
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function submitAnswer() {
    const q = questions[index];
    const isStructured = q.source === "structured";
    if (!isStructured && !selected) return;
    setLoading(true);
    setError("");
    try {
      const res = await api.simulatedTestAnswer({
        id: q.id,
        source: q.source,
        submittedAnswer: isStructured ? undefined : selected,
      });
      setResult(res);
      setResults((prev) => [...prev, { question: q, selected, ...res }]);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  function nextQuestion() {
    if (index + 1 >= questions.length) {
      setPhase("done");
      return;
    }
    setIndex(index + 1);
    setSelected(null);
    setPartAnswers({});
    setResult(null);
  }

  function restart() {
    setPhase("setup");
    setQuestions([]);
    setIndex(0);
    setSelected(null);
    setPartAnswers({});
    setResult(null);
    setResults([]);
    setError("");
  }

  if (phase === "setup") {
    return (
      <div className="page">
        <header className="page-header">
          <div>
            <h1>Simulated Test</h1>
            <p className="subtitle">
              Practice with questions pulled live from the database — also useful as a quick
              QA pass on freshly parsed content. Images default to SVG (PyMuPDF wrap) for
              this screen only; bank storage stays PNG.
            </p>
          </div>
        </header>

        {error && <div className="error-banner">{error}</div>}

        <div className="panel" style={{ maxWidth: 420 }}>
          <label className="field">
            <span>Question source</span>
            <div className="filter-pills" style={{ display: "flex", gap: 8, marginTop: 6, flexWrap: "wrap" }}>
              <button
                type="button"
                className={`pill ${source === "draft" ? "active" : ""}`}
                onClick={() => setSource("draft")}
              >
                MCQ draft (unreviewed)
              </button>
              <button
                type="button"
                className={`pill ${source === "structured" ? "active" : ""}`}
                onClick={() => setSource("structured")}
              >
                Structured draft (unreviewed)
              </button>
              <button
                type="button"
                className={`pill ${source === "structuredApproved" ? "active" : ""}`}
                onClick={() => setSource("structuredApproved")}
              >
                Structured (approved)
              </button>
              <button
                type="button"
                className={`pill ${source === "bank" ? "active" : ""}`}
                onClick={() => setSource("bank")}
              >
                Approved bank (MCQ)
              </button>
            </div>
          </label>

          {source === "structured" || source === "structuredApproved" ? (
            <p className="tiny muted" style={{ marginTop: 8 }}>
              Structured questions are free-response — there's no auto-grading. You'll get a
              "Reveal Mark Scheme" button instead of instant right/wrong.
            </p>
          ) : null}

          <label className="field" style={{ marginTop: 16 }}>
            <span>Number of questions</span>
            <input
              type="number"
              min={1}
              max={50}
              value={count}
              onChange={(e) => setCount(Number(e.target.value) || 1)}
            />
          </label>

          <label className="field" style={{ marginTop: 16 }}>
            <span>Image format (this test only)</span>
            <div className="filter-pills" style={{ display: "flex", gap: 8, marginTop: 6 }}>
              <button
                type="button"
                className={`pill ${assetFormat === "svg" ? "active" : ""}`}
                onClick={() => setAssetFormat("svg")}
              >
                SVG (PyMuPDF)
              </button>
              <button
                type="button"
                className={`pill ${assetFormat === "png" ? "active" : ""}`}
                onClick={() => setAssetFormat("png")}
              >
                Original PNG
              </button>
            </div>
          </label>

          <button
            type="button"
            className="primary"
            style={{ marginTop: 20 }}
            disabled={loading}
            onClick={startTest}
          >
            {loading ? "Starting…" : "Start Test"}
          </button>
        </div>
      </div>
    );
  }

  if (phase === "done") {
    const graded = results.filter((r) => r.correct !== null && r.correct !== undefined);
    const score = graded.filter((r) => r.correct).length;
    return (
      <div className="page">
        <header className="page-header">
          <h1>Test Complete</h1>
        </header>
        <div className="panel">
          <p style={{ fontSize: 20 }}>
            {graded.length ? (
              <>
                Score: <strong>{score}</strong> / {graded.length}
              </>
            ) : (
              <>Reviewed {results.length} structured question{results.length === 1 ? "" : "s"}.</>
            )}
          </p>
          <div style={{ marginTop: 16, display: "flex", flexDirection: "column", gap: 8 }}>
            {results.map((r, i) => {
              const isStructured = r.question.source === "structured";
              return (
                <div
                  key={r.question.id}
                  className={`option static ${isStructured ? "" : r.correct ? "correct" : "wrong"}`}
                  style={{ display: "flex", justifyContent: "space-between" }}
                >
                  <span>
                    Q{i + 1} {r.question.questionRef ? `(#${r.question.questionRef})` : ""}
                    {isStructured
                      ? " — mark scheme reviewed"
                      : ` — you answered ${r.selected}, correct is ${r.correctAnswer}`}
                  </span>
                  <span>{isStructured ? "📖" : r.correct ? "✓" : "✗"}</span>
                </div>
              );
            })}
          </div>
          <button type="button" className="primary" style={{ marginTop: 20 }} onClick={restart}>
            Start Another Test
          </button>
        </div>
      </div>
    );
  }

  const q = questions[index];
  const isStructured = q.source === "structured";
  const revealAnswer = Boolean(result);

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>Simulated Test</h1>
          <p className="subtitle">
            Question {index + 1} of {questions.length}
            {q.paperTitle ? ` — ${q.paperTitle}` : ""}
          </p>
        </div>
      </header>

      {error && <div className="error-banner">{error}</div>}

      <div className="panel">
        <QuestionRenderer
          content={q.content}
          options={q.options}
          assetUrls={q.assetUrls}
          selected={selected}
          onSelect={!isStructured && !revealAnswer ? setSelected : undefined}
          correct={!isStructured && revealAnswer ? result.correctAnswer : null}
          partsAnswer={isStructured && revealAnswer ? result.partsAnswer : null}
          revealAnswer={revealAnswer}
          interactive={!revealAnswer}
          partAnswers={partAnswers}
          onPartAnswerChange={(label, value) => setPartAnswers((prev) => ({ ...prev, [label]: value }))}
        />

        {revealAnswer && !isStructured && (
          <div className={`error-banner ${result.correct ? "" : "warn"}`} style={{ marginTop: 16 }}>
            <strong>{result.correct ? "Correct!" : "Incorrect."}</strong>{" "}
            {result.explanation ? <span>{result.explanation}</span> : null}
          </div>
        )}

        <div style={{ marginTop: 20, display: "flex", gap: 12 }}>
          {!revealAnswer ? (
            <button
              type="button"
              className="primary"
              disabled={(!isStructured && !selected) || loading}
              onClick={submitAnswer}
            >
              {loading ? "Loading…" : isStructured ? "Reveal Mark Scheme" : "Submit Answer"}
            </button>
          ) : (
            <button type="button" className="primary" onClick={nextQuestion}>
              {index + 1 >= questions.length ? "Finish" : "Next Question"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
