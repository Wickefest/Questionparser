import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { Link } from "../lib/router";
import { EmptyState, StatusBadge } from "../components/ui";
import { QuestionRenderer } from "../components/QuestionRenderer";
import { RichHtml } from "../components/Rich";
import { upgradeNumberedLists } from "../lib/normalize";

const MCQ_ANSWER_RE = /^[A-Da-d]$/;

/** Reveal MCQ keys (A–D). Hide short-answer / free-text answers as "—". */
const formatBankAnswer = (val) => {
  if (val == null) return "—";
  const text = String(val).trim();
  if (!text) return "—";
  if (MCQ_ANSWER_RE.test(text)) return text.toUpperCase();
  // Short answers / multi-word keys stay hidden in the bank list
  return "—";
};

const classificationParts = (classification) => {
  if (!classification) return { topicCode: null, topicTitle: "", subCode: null, subTitle: "" };
  const topicCode =
    classification.topicCode ||
    (classification.nodeType === "SUBTOPIC"
      ? String(classification.code || "").split(".")[0]
      : classification.code);
  const topicTitle = cleanTopicTitle(
    classification.topicTitle ||
      (classification.nodeType === "SUBTOPIC" ? "" : classification.title || ""),
  );
  const subCode =
    classification.subtopicCode ||
    (classification.nodeType === "SUBTOPIC" ? classification.code : null);
  const subTitle = cleanTopicTitle(
    classification.subtopicTitle ||
      (classification.nodeType === "SUBTOPIC" ? classification.title || "" : ""),
  );
  return { topicCode, topicTitle, subCode, subTitle };
};

const formatClassificationLabel = (classification) => {
  const { topicCode, topicTitle, subCode, subTitle } = classificationParts(classification);
  if (!topicCode && !subCode) {
    return cleanTopicTitle(classification?.title) || classification?.code || "Classified";
  }
  if (topicCode && subCode) {
    return `Topic ${topicCode}${topicTitle ? `: ${topicTitle}` : ""} · Subtopic ${subCode}${subTitle ? `: ${subTitle}` : ""}`;
  }
  if (topicCode) return `Topic ${topicCode}${topicTitle ? `: ${topicTitle}` : ""}`;
  return `Subtopic ${subCode}${subTitle ? `: ${subTitle}` : ""}`;
};

function ClassificationBadges({ classification, bloomLevel, compact = false }) {
  if (!classification) {
    return (
      <div className="badge-row" style={{ flexDirection: compact ? "column" : "row", alignItems: "flex-start", flexWrap: "wrap", gap: "6px" }}>
        <span className="pending-badge">⏳ Pending Classification</span>
        {bloomLevel ? <span className="bloom-badge">🧠 {bloomLevel}</span> : null}
      </div>
    );
  }
  const { topicCode, topicTitle, subCode, subTitle } = classificationParts(classification);
  return (
    <div className="badge-row" style={{ flexDirection: compact ? "column" : "row", alignItems: "flex-start", flexWrap: "wrap", gap: "6px" }}>
      {topicCode ? (
        <span
          style={{
            background: "#f0fdf4",
            border: "1px solid #bbf7d0",
            color: "#166534",
            padding: "4px 10px",
            borderRadius: "14px",
            fontSize: "0.78rem",
            fontWeight: 600,
          }}
        >
          Topic {topicCode}{topicTitle ? `: ${topicTitle}` : ""}
        </span>
      ) : null}
      {subCode ? (
        <span
          style={{
            background: "#eff6ff",
            border: "1px solid #bfdbfe",
            color: "#1e40af",
            padding: "4px 10px",
            borderRadius: "14px",
            fontSize: "0.78rem",
            fontWeight: 600,
          }}
        >
          Subtopic {subCode}{subTitle ? `: ${subTitle}` : ""}
        </span>
      ) : topicCode ? (
        <span
          style={{
            background: "#f8fafc",
            border: "1px solid #e2e8f0",
            color: "#64748b",
            padding: "4px 10px",
            borderRadius: "14px",
            fontSize: "0.78rem",
            fontWeight: 600,
          }}
        >
          Subtopic: N/A
        </span>
      ) : null}
      {bloomLevel ? <span className="bloom-badge">🧠 {bloomLevel}</span> : null}
    </div>
  );
}

export function QuestionBankPage() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [q, setQ] = useState("");
  const [viewMode, setViewMode] = useState("full"); // "full" | "table"
  const [browseMode, setBrowseMode] = useState("batch"); // "batch" | "topic"
  const [answerFilter, setAnswerFilter] = useState("ALL");
  const [topicFilter, setTopicFilter] = useState("ALL");
  const [subtopicFilter, setSubtopicFilter] = useState("ALL");
  const [selectedBatchId, setSelectedBatchId] = useState(null);
  const [selectedTopicCode, setSelectedTopicCode] = useState(null); // topic folder open
  const [syllabusTopics, setSyllabusTopics] = useState([]);
  const [syllabusSubtopics, setSyllabusSubtopics] = useState([]);

  useEffect(() => {
    api
      .questions()
      .then(setData)
      .catch((e) => setError(e.message));
    api
      .syllabi()
      .then(async (res) => {
        const list = res.syllabi || res.items || res || [];
        const arr = Array.isArray(list) ? list : [];
        if (!arr.length) return;
        const chem = arr.find((s) => s.subject === "Chemistry") || arr[0];
        if (!chem?.id) return;
        const detail = await api.syllabus(chem.id);
        const nodes = detail.nodes || [];
        setSyllabusTopics(
          nodes
            .filter((n) => n.node_type === "TOPIC" && /^\d+$/.test(String(n.code || "").trim()))
            .map((n) => ({
              id: n.id,
              code: String(n.code).trim(),
              title: n.title || "",
            }))
            .sort((a, b) => Number(a.code) - Number(b.code)),
        );
        const topicById = Object.fromEntries(
          nodes.filter((n) => n.node_type === "TOPIC").map((n) => [n.id, String(n.code || "").trim()]),
        );
        setSyllabusSubtopics(
          nodes
            .filter((n) => n.node_type === "SUBTOPIC")
            .map((n) => {
              const parentId = n.parent_id || n.parentId || null;
              const code = String(n.code || "").trim();
              return {
                id: n.id,
                code,
                title: n.title || "",
                parentId,
                topicCode: topicById[parentId] || (code.includes(".") ? code.split(".")[0] : null),
              };
            })
            .sort((a, b) => String(a.code).localeCompare(String(b.code), undefined, { numeric: true })),
        );
      })
      .catch(() => {
        /* filters still work from question data */
      });
  }, []);

  useEffect(() => {
    setSubtopicFilter("ALL");
  }, [topicFilter, selectedTopicCode]);

  const resetDetailFilters = () => {
    setQ("");
    setTopicFilter("ALL");
    setSubtopicFilter("ALL");
    setAnswerFilter("ALL");
  };

  const batches = useMemo(() => {
    const map = new Map();
    for (const item of data?.questions || []) {
      const batchId = item.batchId || "unlinked";
      if (!map.has(batchId)) {
        map.set(batchId, {
          id: batchId,
          title:
            item.batchTitle ||
            (batchId === "unlinked" ? "Unlinked / Legacy questions" : `Batch ${String(batchId).slice(0, 8)}`),
          subject: item.subject || "—",
          examYear: item.examYear || null,
          paperCode: item.paperCode || null,
          count: 0,
          classifiedCount: 0,
          topics: new Set(),
        });
      }
      const b = map.get(batchId);
      b.count += 1;
      const c = item.currentVersion?.classification;
      if (c) {
        b.classifiedCount += 1;
        const topicCode =
          c.topicCode ||
          (c.nodeType === "SUBTOPIC" ? String(c.code || "").split(".")[0] : c.code);
        if (topicCode) b.topics.add(String(topicCode));
      }
      if (!b.examYear && item.examYear) b.examYear = item.examYear;
      if (!b.paperCode && item.paperCode) b.paperCode = item.paperCode;
      if ((!b.subject || b.subject === "—") && item.subject) b.subject = item.subject;
      if (item.batchTitle) b.title = item.batchTitle;
    }
    return [...map.values()].sort((a, b) => a.title.localeCompare(b.title));
  }, [data?.questions]);

  const topicFolders = useMemo(() => {
    const map = new Map();
    for (const t of syllabusTopics) {
      map.set(t.code, {
        code: t.code,
        title: cleanTopicTitle(t.title) || "",
        count: 0,
        subtopics: new Set(),
        batches: new Set(),
      });
    }
    let unclassified = 0;
    for (const item of data?.questions || []) {
      const c = item.currentVersion?.classification;
      const topicCode =
        c?.topicCode ||
        (c?.nodeType === "SUBTOPIC" ? String(c?.code || "").split(".")[0] : c?.code);
      const subCode = c?.subtopicCode || (c?.nodeType === "SUBTOPIC" ? c?.code : null);
      if (!topicCode || !/^\d+$/.test(String(topicCode))) {
        unclassified += 1;
        continue;
      }
      const code = String(topicCode);
      if (!map.has(code)) {
        map.set(code, {
          code,
          title: cleanTopicTitle(c.topicTitle || c.title || "") || "",
          count: 0,
          subtopics: new Set(),
          batches: new Set(),
        });
      }
      const folder = map.get(code);
      folder.count += 1;
      if (subCode) folder.subtopics.add(String(subCode));
      if (item.batchId) folder.batches.add(item.batchId);
      if (!folder.title) {
        folder.title = cleanTopicTitle(c.topicTitle || c.title || "") || "";
      }
    }
    const folders = [...map.values()]
      .filter((f) => f.count > 0)
      .sort((a, b) => Number(a.code) - Number(b.code));
    if (unclassified > 0) {
      folders.push({
        code: "UNCLASSIFIED",
        title: "Unclassified",
        count: unclassified,
        subtopics: new Set(),
        batches: new Set(),
      });
    }
    return folders;
  }, [data?.questions, syllabusTopics]);

  const selectedBatch = useMemo(
    () => batches.find((b) => b.id === selectedBatchId) || null,
    [batches, selectedBatchId],
  );

  const selectedTopic = useMemo(
    () => topicFolders.find((t) => t.code === selectedTopicCode) || null,
    [topicFolders, selectedTopicCode],
  );

  const inFolder = browseMode === "batch" ? Boolean(selectedBatchId) : Boolean(selectedTopicCode);

  const topicOptions = useMemo(() => {
    const scope =
      browseMode === "batch" && selectedBatchId
        ? (data?.questions || []).filter((item) => (item.batchId || "unlinked") === selectedBatchId)
        : data?.questions || [];
    const map = new Map();
    for (const t of syllabusTopics) {
      map.set(t.code, t.title);
    }
    for (const item of scope) {
      const c = item.currentVersion?.classification;
      const code =
        c?.topicCode ||
        (c?.nodeType === "SUBTOPIC" ? String(c?.code || "").split(".")[0] : c?.code);
      if (code && /^\d+$/.test(String(code))) {
        if (!map.has(String(code))) {
          map.set(String(code), c.topicTitle || c.title || "");
        }
      }
    }
    return [...map.entries()]
      .map(([code, title]) => ({ code, title }))
      .sort((a, b) => Number(a.code) - Number(b.code));
  }, [data?.questions, syllabusTopics, selectedBatchId, browseMode]);

  const subtopicOptions = useMemo(() => {
    const scope =
      browseMode === "batch" && selectedBatchId
        ? (data?.questions || []).filter((item) => (item.batchId || "unlinked") === selectedBatchId)
        : data?.questions || [];
    const selectedTopicKey =
      browseMode === "topic" && selectedTopicCode && selectedTopicCode !== "UNCLASSIFIED"
        ? selectedTopicCode
        : topicFilter !== "ALL" && topicFilter !== "UNCLASSIFIED"
          ? topicFilter
          : null;
    const map = new Map();

    for (const s of syllabusSubtopics) {
      if (selectedTopicKey && s.topicCode !== selectedTopicKey) continue;
      if (!selectedTopicKey && (topicFilter === "UNCLASSIFIED" || selectedTopicCode === "UNCLASSIFIED")) {
        continue;
      }
      map.set(s.code, s.title);
    }
    for (const item of scope) {
      const c = item.currentVersion?.classification;
      const topicCode =
        c?.topicCode ||
        (c?.nodeType === "SUBTOPIC" ? String(c?.code || "").split(".")[0] : c?.code);
      const subCode = c?.subtopicCode || (c?.nodeType === "SUBTOPIC" ? c?.code : null);
      if (!subCode) continue;
      if (selectedTopicKey && String(topicCode || "") !== selectedTopicKey) continue;
      if (!map.has(String(subCode))) {
        map.set(String(subCode), c.subtopicTitle || c.title || "");
      }
    }
    return [...map.entries()]
      .map(([code, title]) => ({ code, title }))
      .sort((a, b) => String(a.code).localeCompare(String(b.code), undefined, { numeric: true }));
  }, [
    data?.questions,
    syllabusSubtopics,
    selectedBatchId,
    topicFilter,
    browseMode,
    selectedTopicCode,
  ]);

  if (error) return <div className="page error-banner">{error}</div>;
  if (!data) return <div className="page muted">Loading question bank…</div>;

  const items = (data.questions || []).filter((item) => {
    if (browseMode === "batch" && selectedBatchId && (item.batchId || "unlinked") !== selectedBatchId) {
      return false;
    }

    const current = item.currentVersion;
    const answerVal = current?.answer?.value || "";
    const c = current?.classification;
    const topicCode =
      c?.topicCode ||
      (c?.nodeType === "SUBTOPIC" ? String(c?.code || "").split(".")[0] : c?.code);
    const subCode = c?.subtopicCode || (c?.nodeType === "SUBTOPIC" ? c?.code : null);

    if (browseMode === "topic" && selectedTopicCode) {
      if (selectedTopicCode === "UNCLASSIFIED") {
        if (c) return false;
      } else if (String(topicCode || "") !== selectedTopicCode) {
        return false;
      }
    }

    if (answerFilter !== "ALL" && String(answerVal).toUpperCase() !== answerFilter) return false;
    if (browseMode === "batch" && topicFilter !== "ALL") {
      if (topicFilter === "UNCLASSIFIED") {
        if (c) return false;
      } else if (String(topicCode || "") !== topicFilter) {
        return false;
      }
    }
    if (subtopicFilter !== "ALL") {
      if (subtopicFilter === "NONE") {
        if (subCode) return false;
        if (!c || selectedTopicCode === "UNCLASSIFIED" || topicFilter === "UNCLASSIFIED") return false;
      } else if (String(subCode || "") !== subtopicFilter) {
        return false;
      }
    }

    if (!q) return true;
    const needle = q.toLowerCase();
    const preview = previewText(current?.content);
    return (
      item.sourceKey?.toLowerCase().includes(needle) ||
      preview.toLowerCase().includes(needle) ||
      item.subject?.toLowerCase().includes(needle) ||
      item.batchTitle?.toLowerCase().includes(needle)
    );
  });

  if (!inFolder) {
    const searchNeedle = q.trim().toLowerCase();
    return (
      <div className="page">
        <header className="page-header">
          <div>
            <h1>Question Bank</h1>
            <p className="subtitle">
              Browse by import batch or by syllabus topic — pick whichever is faster for the task.
            </p>
          </div>
          <div className="filter-pills" style={{ display: "flex", gap: "8px" }}>
            <button
              type="button"
              className={`pill ${browseMode === "batch" ? "active" : ""}`}
              onClick={() => {
                setBrowseMode("batch");
                setSelectedTopicCode(null);
                resetDetailFilters();
              }}
            >
              By batch
            </button>
            <button
              type="button"
              className={`pill ${browseMode === "topic" ? "active" : ""}`}
              onClick={() => {
                setBrowseMode("topic");
                setSelectedBatchId(null);
                resetDetailFilters();
              }}
            >
              By topic
            </button>
          </div>
        </header>

        <div className="toolbar" style={{ display: "flex", gap: "12px", alignItems: "center", marginBottom: "16px" }}>
          <input
            className="search"
            style={{ flex: 1, margin: 0 }}
            placeholder={browseMode === "batch" ? "Search batch titles..." : "Search topic titles..."}
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>

        {browseMode === "batch" ? (
          !batches.length ? (
            <EmptyState title="No approved questions in the bank yet.">
              Approve reviewed drafts to publish them here.
            </EmptyState>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
              {batches
                .filter((b) => {
                  if (!searchNeedle) return true;
                  return (
                    b.title.toLowerCase().includes(searchNeedle) ||
                    b.subject.toLowerCase().includes(searchNeedle)
                  );
                })
                .map((batch) => (
                  <button
                    key={batch.id}
                    type="button"
                    className="panel"
                    onClick={() => {
                      resetDetailFilters();
                      setSelectedBatchId(batch.id);
                    }}
                    style={{
                      textAlign: "left",
                      padding: "18px 20px",
                      border: "1px solid var(--line)",
                      borderRadius: "12px",
                      background: "#fff",
                      cursor: "pointer",
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      gap: "16px",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: "14px", minWidth: 0 }}>
                      <span style={{ fontSize: "1.6rem", lineHeight: 1 }} aria-hidden>
                        📁
                      </span>
                      <div style={{ minWidth: 0 }}>
                        <div style={{ fontWeight: 700, fontSize: "1.05rem" }}>{batch.title}</div>
                        <div className="muted" style={{ fontSize: "0.85rem", marginTop: "4px" }}>
                          {batch.subject}
                          {batch.examYear ? ` · ${batch.examYear}` : ""}
                          {batch.paperCode ? ` · ${batch.paperCode}` : ""}
                          {batch.topics.size
                            ? ` · Topics ${[...batch.topics].sort((a, b) => Number(a) - Number(b)).join(", ")}`
                            : ""}
                        </div>
                      </div>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: "12px", flexShrink: 0 }}>
                      <span className="pill active" style={{ whiteSpace: "nowrap" }}>
                        {batch.count} question{batch.count === 1 ? "" : "s"}
                      </span>
                      <span className="muted" style={{ fontSize: "0.85rem", whiteSpace: "nowrap" }}>
                        {batch.classifiedCount}/{batch.count} classified
                      </span>
                      <span className="btn secondary" style={{ pointerEvents: "none" }}>
                        Open
                      </span>
                    </div>
                  </button>
                ))}
            </div>
          )
        ) : !topicFolders.length ? (
          <EmptyState title="No topic folders yet.">
            Classify approved questions to group them by syllabus topic.
          </EmptyState>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
            {topicFolders
              .filter((t) => {
                if (!searchNeedle) return true;
                return (
                  t.code.toLowerCase().includes(searchNeedle) ||
                  (t.title || "").toLowerCase().includes(searchNeedle)
                );
              })
              .map((topic) => (
                <button
                  key={topic.code}
                  type="button"
                  className="panel"
                  onClick={() => {
                    resetDetailFilters();
                    setSelectedTopicCode(topic.code);
                  }}
                  style={{
                    textAlign: "left",
                    padding: "18px 20px",
                    border: "1px solid var(--line)",
                    borderRadius: "12px",
                    background: "#fff",
                    cursor: "pointer",
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    gap: "16px",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: "14px", minWidth: 0 }}>
                    <span style={{ fontSize: "1.6rem", lineHeight: 1 }} aria-hidden>
                      {topic.code === "UNCLASSIFIED" ? "📭" : "📂"}
                    </span>
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontWeight: 700, fontSize: "1.05rem" }}>
                        {topic.code === "UNCLASSIFIED"
                          ? "Unclassified"
                          : `Topic ${topic.code}${topic.title ? `: ${topic.title}` : ""}`}
                      </div>
                      <div className="muted" style={{ fontSize: "0.85rem", marginTop: "4px" }}>
                        {topic.code === "UNCLASSIFIED"
                          ? "Questions with no topic tag yet"
                          : [
                              topic.subtopics.size
                                ? `${topic.subtopics.size} subtopic${topic.subtopics.size === 1 ? "" : "s"}`
                                : "No subtopics tagged",
                              topic.batches.size
                                ? `${topic.batches.size} batch${topic.batches.size === 1 ? "" : "es"}`
                                : null,
                            ]
                              .filter(Boolean)
                              .join(" · ")}
                      </div>
                    </div>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: "12px", flexShrink: 0 }}>
                    <span className="pill active" style={{ whiteSpace: "nowrap" }}>
                      {topic.count} question{topic.count === 1 ? "" : "s"}
                    </span>
                    <span className="btn secondary" style={{ pointerEvents: "none" }}>
                      Open
                    </span>
                  </div>
                </button>
              ))}
          </div>
        )}
      </div>
    );
  }

  const folderTitle =
    browseMode === "batch"
      ? selectedBatch?.title || "Batch"
      : selectedTopicCode === "UNCLASSIFIED"
        ? "Unclassified"
        : `Topic ${selectedTopic?.code || selectedTopicCode}${
            selectedTopic?.title ? `: ${selectedTopic.title}` : ""
          }`;

  const folderSubtitle =
    browseMode === "batch"
      ? [
          selectedBatch?.subject || "—",
          selectedBatch?.examYear || null,
          selectedBatch?.paperCode || null,
          `${items.length} shown / ${selectedBatch?.count || 0} in batch`,
        ]
          .filter(Boolean)
          .join(" · ")
      : `${items.length} shown / ${selectedTopic?.count || 0} in topic · across all batches`;

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <button
            type="button"
            className="btn secondary"
            style={{ marginBottom: "10px", padding: "6px 12px", fontSize: "0.85rem" }}
            onClick={() => {
              setSelectedBatchId(null);
              setSelectedTopicCode(null);
              resetDetailFilters();
            }}
          >
            {browseMode === "batch" ? "← All batches" : "← All topics"}
          </button>
          <h1>{folderTitle}</h1>
          <p className="subtitle">{folderSubtitle}</p>
        </div>
        <div className="filter-pills" style={{ display: "flex", gap: "8px" }}>
          <button
            type="button"
            className={`pill ${viewMode === "full" ? "active" : ""}`}
            onClick={() => setViewMode("full")}
          >
            Full Questions
          </button>
          <button
            type="button"
            className={`pill ${viewMode === "table" ? "active" : ""}`}
            onClick={() => setViewMode("table")}
          >
            Compact Table
          </button>
        </div>
      </header>

      <div
        className="toolbar"
        style={{ display: "flex", gap: "12px", alignItems: "center", marginBottom: "16px", flexWrap: "wrap" }}
      >
        <input
          className="search"
          style={{ flex: "2 1 180px", margin: 0, minWidth: "160px" }}
          placeholder="Search question text or source key..."
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        {browseMode === "batch" ? (
          <select
            className="search"
            style={{ flex: "1.2 1 140px", margin: 0, padding: "8px", borderRadius: "8px", border: "1px solid #ced4da" }}
            value={topicFilter}
            onChange={(e) => setTopicFilter(e.target.value)}
          >
            <option value="ALL">All Topics</option>
            <option value="UNCLASSIFIED">Unclassified</option>
            {topicOptions.map((t) => (
              <option key={t.code} value={t.code}>
                Topic {t.code}
                {t.title ? ` — ${cleanTopicTitle(t.title)}` : ""}
              </option>
            ))}
          </select>
        ) : null}
        <select
          className="search"
          style={{ flex: "1.4 1 160px", margin: 0, padding: "8px", borderRadius: "8px", border: "1px solid #ced4da" }}
          value={subtopicFilter}
          onChange={(e) => setSubtopicFilter(e.target.value)}
          disabled={
            browseMode === "topic"
              ? selectedTopicCode === "UNCLASSIFIED"
              : topicFilter === "UNCLASSIFIED"
          }
        >
          <option value="ALL">All Subtopics</option>
          <option value="NONE">No subtopic</option>
          {subtopicOptions.map((s) => (
            <option key={s.code} value={s.code}>
              Subtopic {s.code}
              {s.title ? ` — ${cleanTopicTitle(s.title)}` : ""}
            </option>
          ))}
        </select>
        <select
          className="search"
          style={{ flex: "0.8 1 110px", margin: 0, padding: "8px", borderRadius: "8px", border: "1px solid #ced4da" }}
          value={answerFilter}
          onChange={(e) => setAnswerFilter(e.target.value)}
        >
          <option value="ALL">All Answers</option>
          <option value="A">Answer A</option>
          <option value="B">Answer B</option>
          <option value="C">Answer C</option>
          <option value="D">Answer D</option>
        </select>
      </div>

      {!items.length ? (
        <EmptyState
          title={
            browseMode === "batch"
              ? "No questions match your filters in this batch."
              : "No questions match your filters in this topic."
          }
        >
          Try clearing subtopic filters or the search query.
        </EmptyState>
      ) : viewMode === "table" ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr style={{ background: "#f8f9fa", borderBottom: "2px solid #dee2e6" }}>
                <th style={{ width: "16%", padding: "12px 16px" }}>Source Key</th>
                <th style={{ width: "34%", padding: "12px 16px" }}>Question Preview</th>
                <th style={{ width: "28%", padding: "12px 16px" }}>Topic / Subtopic</th>
                <th style={{ width: "10%", padding: "12px 16px", textAlign: "center" }}>Answer</th>
                <th style={{ width: "12%", padding: "12px 16px", textAlign: "center" }}>Action</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const current = item.currentVersion;
                return (
                  <tr key={item.id} style={{ borderBottom: "1px solid #e9ecef" }}>
                    <td style={{ padding: "12px 16px", fontWeight: "600" }}>{item.sourceKey}</td>
                    <td style={{ padding: "12px 16px", color: "#495057", fontSize: "0.9rem" }}>
                      {previewText(current?.content) || <em className="muted">[Diagram only]</em>}
                    </td>
                    <td style={{ padding: "12px 16px" }}>
                      <ClassificationBadges
                        classification={current?.classification}
                        bloomLevel={current?.bloomLevel}
                        compact
                      />
                    </td>
                    <td style={{ padding: "12px 16px", textAlign: "center" }}>
                      <span className="pill active" style={{ display: "inline-block", minWidth: "24px", padding: "4px 8px" }}>
                        {formatBankAnswer(current?.answer?.value)}
                      </span>
                    </td>
                    <td style={{ padding: "12px 16px", textAlign: "center" }}>
                      <Link className="btn" to={`/admin/questions/${item.id}`} style={{ padding: "4px 12px", fontSize: "0.85rem" }}>
                        Open
                      </Link>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "20px" }}>
          {items.map((item) => {
            const current = item.currentVersion;
            const answerVal = formatBankAnswer(current?.answer?.value);
            return (
              <article
                key={item.id}
                className="panel"
                style={{
                  padding: "20px",
                  border: "1px solid var(--line)",
                  borderRadius: "12px",
                  background: "#fff",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "flex-start",
                    gap: "12px",
                    flexWrap: "wrap",
                    marginBottom: "14px",
                    paddingBottom: "12px",
                    borderBottom: "1px solid #eef2f7",
                  }}
                >
                  <div>
                    <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
                      <strong style={{ fontSize: "1.05rem" }}>{item.sourceKey}</strong>
                      <StatusBadge status={current?.status || "APPROVED"} />
                      <span className="muted" style={{ fontSize: "0.85rem" }}>
                        {item.subject} · v{current?.version || "—"}
                      </span>
                    </div>
                    <div style={{ marginTop: "10px" }}>
                      <ClassificationBadges
                        classification={current?.classification}
                        bloomLevel={current?.bloomLevel}
                      />
                    </div>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                    {answerVal !== "—" ? (
                      <span className="pill active" style={{ minWidth: "28px", textAlign: "center" }}>
                        {answerVal}
                      </span>
                    ) : null}
                    <Link className="btn secondary" to={`/admin/questions/${item.id}`}>
                      Edit / Open
                    </Link>
                  </div>
                </div>

                {current ? (
                  <QuestionRenderer
                    content={current.content}
                    options={current.options}
                    assetUrls={current.assetUrls || {}}
                  />
                ) : (
                  <p className="muted">No approved version.</p>
                )}

                {current?.answer?.explanation &&
                MCQ_ANSWER_RE.test(String(current.answer?.value || "").trim()) ? (
                  <div
                    style={{
                      marginTop: "14px",
                      padding: "12px",
                      background: "#f8fafc",
                      border: "1px solid #e2e8f0",
                      borderRadius: "8px",
                      fontSize: "0.9rem",
                    }}
                  >
                    <strong>Explanation:</strong> <RichHtml text={current.answer.explanation} />
                  </div>
                ) : null}
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function QuestionDetailPage({ questionId }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [successMsg, setSuccessMsg] = useState("");
  const [editing, setEditing] = useState(false);
  
  // Full block-level editor states
  const [editContent, setEditContent] = useState([]);
  const [editOptions, setEditOptions] = useState(null);
  const [editAssetUrls, setEditAssetUrls] = useState({});

  const [ansValue, setAnsValue] = useState("");
  const [explanation, setExplanation] = useState("");
  const [busy, setBusy] = useState(false);

  const [taxonomyTopics, setTaxonomyTopics] = useState([]);
  const [taxonomySubtopics, setTaxonomySubtopics] = useState([]);
  const [manualTopicCode, setManualTopicCode] = useState("");
  const [manualSubtopicCode, setManualSubtopicCode] = useState("");
  const [showManualTag, setShowManualTag] = useState(false);

  const loadQuestion = () => {
    api
      .question(questionId)
      .then((d) => {
        setData(d);
        const current = (d.versions || []).find((v) => v.status !== "RETIRED") || d.versions?.[0];
        if (current) {
          setEditContent(upgradeNumberedLists(JSON.parse(JSON.stringify(current.content || []))));
          setEditOptions(JSON.parse(JSON.stringify(current.options || null)));
          setEditAssetUrls(JSON.parse(JSON.stringify(current.assetUrls || {})));
          setAnsValue(current.answer?.value || "A");
          setExplanation(current.answer?.explanation || "");
          const c = current.classification || {};
          setManualTopicCode(c.topicCode || (c.nodeType !== "SUBTOPIC" ? c.code : String(c.code || "").split(".")[0]) || "");
          setManualSubtopicCode(c.subtopicCode || (c.nodeType === "SUBTOPIC" ? c.code : "") || "");
        }
      })
      .catch((e) => setError(e.message));
  };

  useEffect(() => {
    loadQuestion();
  }, [questionId]);

  useEffect(() => {
    api
      .syllabi()
      .then(async (res) => {
        const list = res.syllabi || [];
        if (!list.length) return;
        const match =
          list.find((s) => s.subject === data?.subject) ||
          list.find((s) => s.subject === "Chemistry") ||
          list[0];
        if (!match?.id) return;
        const detail = await api.syllabus(match.id);
        const nodes = detail.nodes || [];
        setTaxonomyTopics(
          nodes
            .filter((n) => n.node_type === "TOPIC" && /^\d+$/.test(String(n.code || "").trim()))
            .map((n) => ({
              id: n.id,
              code: String(n.code).trim(),
              title: n.title || "",
            }))
            .sort((a, b) => Number(a.code) - Number(b.code)),
        );
        setTaxonomySubtopics(
          nodes
            .filter((n) => n.node_type === "SUBTOPIC" && /^\d+\.\d+$/.test(String(n.code || "").trim()))
            .map((n) => ({
              id: n.id,
              code: String(n.code).trim(),
              title: n.title || "",
              parentId: n.parent_id,
              parentCode: String(n.code).split(".", 1)[0],
            }))
            .sort((a, b) => {
              const [a1, a2] = a.code.split(".").map(Number);
              const [b1, b2] = b.code.split(".").map(Number);
              return a1 - b1 || a2 - b2;
            }),
        );
      })
      .catch(() => {});
  }, [data?.subject]);

  if (!data && error) return <div className="page error-banner">{error}</div>;
  if (!data) return <div className="page muted">Loading…</div>;

  const current = (data.versions || []).find((v) => v.status !== "RETIRED") || data.versions?.[0];
  const manualSubs = taxonomySubtopics.filter(
    (s) => s.parentCode === manualTopicCode || String(s.code).startsWith(`${manualTopicCode}.`),
  );
  const topicHasSubs = manualSubs.length > 0;

  const handleManualTagSave = async () => {
    if (!current?.id) {
      setError("No current version to tag.");
      return;
    }
    if (!manualTopicCode) {
      setError("Pick a Topic code.");
      return;
    }
    if (topicHasSubs && !manualSubtopicCode) {
      setError(`Topic ${manualTopicCode} requires a subtopic.`);
      return;
    }
    setBusy(true);
    setError("");
    setSuccessMsg("");
    try {
      const res = await api.manualClassification({
        versionId: current.id,
        topicCode: manualTopicCode,
        subtopicCode: topicHasSubs ? manualSubtopicCode : null,
        reasoning: "Tagged from Question Bank",
      });
      if (res.success) {
        setSuccessMsg(
          `Saved: Topic ${res.topicCode}` +
            (res.subtopicCode ? ` / Subtopic ${res.subtopicCode}` : " (topic only)"),
        );
        setShowManualTag(false);
        loadQuestion();
      } else {
        setError(res.message || "Failed to save topic tag.");
      }
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setBusy(false);
    }
  };

  const handleBlockChange = (index, updatedBlock) => {
    setEditContent((prev) => {
      const next = [...prev];
      next[index] = updatedBlock;
      return next;
    });
  };

  const handleRemoveBlock = (index) => {
    setEditContent((prev) => prev.filter((_, i) => i !== index));
  };

  const handleAddImageBlock = async (file) => {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const res = await api.uploadGeneralAsset(file);
      if (!res.success) {
        setError("Failed to upload image.");
        return;
      }
      setEditContent((prev) => [
        ...prev,
        { type: "image", asset_id: res.assetId },
      ]);
      setEditAssetUrls((prev) => ({
        ...prev,
        [res.assetId]: `${res.url}?t=${Date.now()}`,
      }));
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setBusy(false);
    }
  };

  const handleRemoveOptionsImage = () => {
    setEditOptions((opts) => {
      if (!opts || typeof opts !== "object") return opts;
      const next = { ...opts };
      delete next.asset_id;
      delete next.asset;
      if (next.mode === "composite_visual") {
        next.mode = "text";
        if (!next.items) {
          next.items = (next.labels || ["A", "B", "C", "D"]).map((label) => ({
            label,
            content: "",
          }));
        }
      }
      return next;
    });
  };

  const handleReplaceImage = async (index, file) => {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const res = await api.uploadGeneralAsset(file);
      if (res.success) {
        setEditContent((prev) => {
          const next = [...prev];
          next[index] = {
            ...next[index],
            type: "image",
            asset_id: res.assetId,
            asset: undefined,
            url: undefined,
          };
          return next;
        });
        setEditAssetUrls((prev) => ({
          ...prev,
          [res.assetId]: `${res.url}?t=${Date.now()}`,
        }));
      } else {
        setError("Failed to upload replacement image.");
      }
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setBusy(false);
    }
  };

  const handleReplaceOptionsImage = async (file) => {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const res = await api.uploadGeneralAsset(file);
      if (!res.success) {
        setError("Failed to upload options image.");
        return;
      }
      setEditOptions((opts) => {
        const base = opts && typeof opts === "object" ? { ...opts } : { mode: "composite_visual", labels: ["A", "B", "C", "D"] };
        if (!base.mode || base.mode === "text") {
          base.mode = "composite_visual";
          base.labels = base.labels || ["A", "B", "C", "D"];
        }
        base.asset_id = res.assetId;
        delete base.asset;
        return base;
      });
      setEditAssetUrls((prev) => ({
        ...prev,
        [res.assetId]: `${res.url}?t=${Date.now()}`,
      }));
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setBusy(false);
    }
  };

  const updateOptionItemText = (label, value) => {
    setEditOptions((opts) => {
      if (!opts) {
        return {
          mode: "text",
          items: ["A", "B", "C", "D"].map((l) => ({
            label: l,
            content: l === label ? value : "",
          })),
        };
      }
      if (opts.mode === "text" || opts.mode === "mixed") {
        const items = [...(opts.items || [])];
        const idx = items.findIndex((it) => it.label === label);
        if (idx >= 0) {
          items[idx] = { ...items[idx], content: value, latex: undefined };
        } else {
          items.push({ label, content: value });
        }
        return { ...opts, items };
      }
      if (opts.mode === "math") {
        const items = [...(opts.items || [])];
        const idx = items.findIndex((it) => it.label === label);
        if (idx >= 0) {
          items[idx] = { ...items[idx], latex: value };
        } else {
          items.push({ label, latex: value });
        }
        return { ...opts, items };
      }
      return opts;
    });
  };

  const optionItemValue = (label) => {
    const items = editOptions?.items || [];
    const item = items.find((it) => it.label === label);
    if (!item) return "";
    return item.content ?? item.latex ?? "";
  };

  const handleSave = async () => {
    setBusy(true);
    setError("");
    try {
      await api.createCorrectionVersion(questionId, {
        content: editContent,
        options: editOptions,
        value: ansValue,
        explanation,
      });

      setEditing(false);
      loadQuestion();
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page">
      {error ? <div className="error-banner">{error}</div> : null}
      {successMsg ? <div className="panel success-panel" style={{ marginBottom: "16px" }}>{successMsg}</div> : null}
      <header className="page-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h1>{data.sourceKey}</h1>
          <p className="subtitle">
            {data.subject} · Current version {current ? `v${current.version}` : "—"}
          </p>
        </div>
        <div style={{ display: "flex", gap: "12px", alignItems: "center" }}>
          {/* Navigation Controls */}
          {data.prevId && (
            <Link className="btn" to={`/admin/questions/${data.prevId}`} style={{ padding: "8px 16px" }}>
              ← Previous
            </Link>
          )}
          {data.nextId && (
            <Link className="btn" to={`/admin/questions/${data.nextId}`} style={{ padding: "8px 16px" }}>
              Next →
            </Link>
          )}
          
          {!editing ? (
            <button type="button" className="btn primary" onClick={() => setEditing(true)}>
              Create correction version
            </button>
          ) : (
            <div className="actions-row">
              <button type="button" className="btn primary" disabled={busy} onClick={handleSave}>
                {busy ? "Saving…" : "Save Correction"}
              </button>
              <button type="button" className="btn" disabled={busy} onClick={() => setEditing(false)}>
                Cancel
              </button>
            </div>
          )}
        </div>
      </header>

      {editing ? (
        <section className="panel form-grid">
          <h2>Edit Corrected Version</h2>
          
          <div className="span-2" style={{ borderBottom: "1px solid var(--line)", paddingBottom: "16px", marginBottom: "16px" }}>
            <h3 style={{ marginBottom: "12px" }}>Content Blocks</h3>
            {editContent.map((block, index) => {
              const type = block.type;
              const assetId = block.asset_id || (typeof block.asset === "string" && /^[0-9a-f-]{36}$/i.test(block.asset) ? block.asset : null);
              const assetUrl = assetId ? editAssetUrls[assetId] : block.url;
              return (
                <div key={index} className="edit-block" style={{ marginBottom: "16px", padding: "12px", background: "#f8f9fa", borderRadius: "6px", border: "1px solid var(--line)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px", gap: "8px" }}>
                    <div className="label" style={{ fontWeight: "600", fontSize: "0.85rem", textTransform: "uppercase", color: "#6c757d", margin: 0 }}>
                      Block {index + 1}: {type}
                    </div>
                    <button
                      type="button"
                      className="btn"
                      style={{ padding: "4px 10px", fontSize: "0.75rem" }}
                      disabled={busy}
                      onClick={() => handleRemoveBlock(index)}
                    >
                      Remove block
                    </button>
                  </div>
                  
                  {type === "text" || type === "chemistry" ? (
                    <>
                      <textarea
                        style={{ width: "100%", padding: "8px", borderRadius: "6px", border: "1px solid #ced4da", minHeight: "80px" }}
                        value={block.value || ""}
                        onChange={(e) => handleBlockChange(index, { ...block, value: e.target.value })}
                      />
                      <p className="tiny muted latex-hint">
                        Tip: wrap math/chemistry in <code>$...$</code> for LaTeX, e.g. <code>$x^2 + 1$</code> or{" "}
                        <code>$H_2SO_4$</code>.
                      </p>
                      {block.value ? (
                        <div className="latex-preview">
                          <RichHtml text={block.value} />
                        </div>
                      ) : null}
                    </>
                  ) : null}

                  {type === "math" ? (
                    <textarea
                      style={{ width: "100%", padding: "8px", borderRadius: "6px", border: "1px solid #ced4da", minHeight: "50px" }}
                      value={block.latex || ""}
                      onChange={(e) => handleBlockChange(index, { ...block, latex: e.target.value })}
                    />
                  ) : null}

                  {type === "image" ? (
                    <div className="edit-image-block" style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                      {assetUrl ? (
                        <img src={assetUrl} alt="Visual preview" style={{ maxWidth: "100%", maxHeight: "150px", alignSelf: "flex-start", borderRadius: "4px" }} />
                      ) : (
                        <div className="muted" style={{ fontSize: "0.85rem", color: "#6c757d" }}>No preview — choose an image.</div>
                      )}
                      <div className="muted tiny" style={{ fontSize: "0.75rem", color: "#6c757d" }}>Asset ID: {assetId || "—"}</div>
                      <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
                        <label className="btn" style={{ cursor: "pointer", display: "inline-block", width: "fit-content" }}>
                          {assetId ? "Replace Image" : "Upload Image"}
                          <input
                            type="file"
                            accept="image/png,image/jpeg,image/webp"
                            hidden
                            disabled={busy}
                            onChange={(e) => {
                              const file = e.target.files?.[0];
                              if (file) handleReplaceImage(index, file);
                              e.target.value = "";
                            }}
                          />
                        </label>
                        <button
                          type="button"
                          className="btn"
                          style={{ padding: "6px 12px", fontSize: "0.85rem" }}
                          disabled={busy}
                          onClick={() => handleRemoveBlock(index)}
                        >
                          Remove picture
                        </button>
                      </div>
                    </div>
                  ) : null}

                  {type === "list" ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                      <label style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "0.85rem" }}>
                        <input
                          type="checkbox"
                          checked={block.ordered !== false}
                          disabled={busy}
                          onChange={(e) =>
                            handleBlockChange(index, { ...block, ordered: e.target.checked })
                          }
                        />
                        Numbered list (1, 2, 3…) — uncheck for bullet list (•)
                      </label>
                      {(block.items || []).map((item, itemIdx) => (
                        <div key={itemIdx} style={{ display: "flex", gap: "8px", alignItems: "flex-start" }}>
                          <span className="muted" style={{ minWidth: "24px", paddingTop: "8px", fontWeight: 600 }}>
                            {block.ordered !== false ? `${itemIdx + 1}.` : "•"}
                          </span>
                          <textarea
                            style={{ flex: 1, padding: "8px", borderRadius: "6px", border: "1px solid #ced4da", minHeight: "56px" }}
                            value={item}
                            disabled={busy}
                            onChange={(e) => {
                              const items = [...(block.items || [])];
                              items[itemIdx] = e.target.value;
                              handleBlockChange(index, { ...block, items });
                            }}
                          />
                          <button
                            type="button"
                            className="btn"
                            style={{ padding: "6px 10px", fontSize: "0.75rem" }}
                            disabled={busy || (block.items || []).length <= 1}
                            onClick={() => {
                              const items = (block.items || []).filter((_, i) => i !== itemIdx);
                              handleBlockChange(index, { ...block, items });
                            }}
                          >
                            Remove
                          </button>
                        </div>
                      ))}
                      <button
                        type="button"
                        className="btn secondary"
                        style={{ width: "fit-content" }}
                        disabled={busy}
                        onClick={() =>
                          handleBlockChange(index, {
                            ...block,
                            items: [...(block.items || []), ""],
                          })
                        }
                      >
                        + Add statement
                      </button>
                    </div>
                  ) : null}

                  {type === "table" ? (
                    <textarea
                      style={{ width: "100%", padding: "8px", borderRadius: "6px", border: "1px solid #ced4da", fontFamily: "monospace" }}
                      rows={4}
                      value={JSON.stringify({ columns: block.columns, rows: block.rows }, null, 2)}
                      onChange={(e) => {
                        try {
                          const parsed = JSON.parse(e.target.value);
                          handleBlockChange(index, { ...block, ...parsed });
                        } catch {
                          /* ignore while typing */
                        }
                      }}
                    />
                  ) : null}
                </div>
              );
            })}
            <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginTop: "8px" }}>
              <label className="btn secondary" style={{ cursor: "pointer", display: "inline-block" }}>
                + Add picture
                <input
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  hidden
                  disabled={busy}
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) handleAddImageBlock(file);
                    e.target.value = "";
                  }}
                />
              </label>
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => setEditContent((prev) => [...prev, { type: "text", value: "" }])}
              >
                + Add text block
              </button>
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() =>
                  setEditContent((prev) => [
                    ...prev,
                    { type: "list", ordered: true, items: ["", "", ""] },
                  ])
                }
              >
                + Add numbered list
              </button>
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() =>
                  setEditContent((prev) => [
                    ...prev,
                    { type: "list", ordered: false, items: ["", "", ""] },
                  ])
                }
              >
                + Add bullet list
              </button>
            </div>
          </div>

          <div className="span-2" style={{ borderBottom: "1px solid var(--line)", paddingBottom: "16px", marginBottom: "16px" }}>
            <h3 style={{ marginBottom: "12px" }}>Options ({editOptions?.mode || "text"})</h3>
            {(editOptions?.mode === "composite_visual" || editOptions?.mode === "mixed" || editOptions?.asset_id) ? (
              <div className="edit-image-block" style={{ display: "flex", flexDirection: "column", gap: "8px", marginBottom: "16px" }}>
                {editOptions?.asset_id && editAssetUrls[editOptions.asset_id] ? (
                  <img
                    src={editAssetUrls[editOptions.asset_id]}
                    alt="Options visual"
                    style={{ maxWidth: "100%", maxHeight: "180px", alignSelf: "flex-start", borderRadius: "4px" }}
                  />
                ) : (
                  <div className="muted">No options image linked.</div>
                )}
                <label className="btn" style={{ cursor: "pointer", display: "inline-block", width: "fit-content" }}>
                  {editOptions?.asset_id ? "Replace options image" : "Upload options image"}
                  <input
                    type="file"
                    accept="image/png,image/jpeg,image/webp"
                    hidden
                    disabled={busy}
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) handleReplaceOptionsImage(file);
                      e.target.value = "";
                    }}
                  />
                </label>
                {editOptions?.asset_id ? (
                  <button
                    type="button"
                    className="btn"
                    style={{ width: "fit-content" }}
                    disabled={busy}
                    onClick={handleRemoveOptionsImage}
                  >
                    Remove options image
                  </button>
                ) : null}
              </div>
            ) : null}

            {editOptions?.mode === "table" ? (
              <textarea
                style={{ width: "100%", padding: "8px", borderRadius: "6px", border: "1px solid #ced4da", fontFamily: "monospace" }}
                rows={6}
                value={JSON.stringify({ columns: editOptions.columns, rows: editOptions.rows }, null, 2)}
                onChange={(e) => {
                  try {
                    const parsed = JSON.parse(e.target.value);
                    setEditOptions((opts) => ({ ...opts, ...parsed, mode: "table" }));
                  } catch {
                    /* ignore while typing */
                  }
                }}
              />
            ) : (editOptions?.mode === "text" || editOptions?.mode === "math" || editOptions?.mode === "mixed" || !editOptions?.mode) ? (
              <div style={{ display: "grid", gap: "10px" }}>
                {["A", "B", "C", "D"].map((label) => (
                  <label key={label}>
                    Option {label}
                    <input
                      value={optionItemValue(label)}
                      onChange={(e) => updateOptionItemText(label, e.target.value)}
                    />
                  </label>
                ))}
              </div>
            ) : (
              <p className="muted">Visual options mode — use Replace options image above.</p>
            )}
          </div>

          <label>
            Correct Answer
            <select
              style={{ padding: "8px", borderRadius: "6px", border: "1px solid #ced4da" }}
              value={ansValue}
              onChange={(e) => setAnsValue(e.target.value)}
            >
              <option value="A">A</option>
              <option value="B">B</option>
              <option value="C">C</option>
              <option value="D">D</option>
            </select>
          </label>
          <label className="span-2">
            Explanation
            <textarea
              style={{ width: "100%", minHeight: "80px", padding: "8px", borderRadius: "6px", border: "1px solid #ced4da" }}
              value={explanation}
              onChange={(e) => setExplanation(e.target.value)}
            />
          </label>
        </section>
      ) : (
        <section className="panel">
          <div className="label">Content (immutable snapshot)</div>
          {current ? (
            <>
              <QuestionRenderer
                content={current.content}
                options={current.options}
                assetUrls={current.assetUrls || {}}
                correct={MCQ_ANSWER_RE.test(String(current.answer?.value || "").trim()) ? String(current.answer.value).trim().toUpperCase() : null}
                revealAnswer={MCQ_ANSWER_RE.test(String(current.answer?.value || "").trim())}
              />
              <div className="label">Answer</div>
              <div style={{ fontWeight: "600", fontSize: "1.1rem" }}>
                {MCQ_ANSWER_RE.test(String(current.answer?.value || "").trim()) ? (
                  <span className="pill active">{String(current.answer.value).trim().toUpperCase()}</span>
                ) : (
                  <span className="muted">—</span>
                )}
              </div>
              {MCQ_ANSWER_RE.test(String(current.answer?.value || "").trim()) ? (
                <p className="muted" style={{ background: "#f8f9fa", padding: "12px", borderRadius: "6px", border: "1px solid var(--line)", marginTop: "8px" }}>
                  <RichHtml text={current.answer?.explanation || "No official explanation provided."} />
                </p>
              ) : null}
            </>
          ) : (
            <p className="muted">No versions.</p>
          )}
        </section>
      )}

      <section className="panel">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
          <h2 style={{ margin: 0 }}>Classification & Taxonomy</h2>
          <button
            type="button"
            className="btn secondary"
            disabled={busy || !current?.id}
            onClick={() => setShowManualTag((v) => !v)}
          >
            {showManualTag ? "Cancel Topic Tag" : "Choose Topic"}
          </button>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: "16px", marginTop: "16px" }}>
          <div className="badge-row lg">
            <ClassificationBadges
              classification={current?.classification}
              bloomLevel={current?.bloomLevel}
            />
            {!current?.bloomLevel ? (
              <span className="pending-badge">⏳ Bloom's Level: Pending AI Classification</span>
            ) : null}
          </div>

          {showManualTag ? (
            <div
              style={{
                padding: "14px",
                border: "1px solid #cbd5e1",
                borderRadius: "8px",
                background: "#fffbeb",
                display: "grid",
                gridTemplateColumns: "1fr 1fr auto",
                gap: "10px",
                alignItems: "end",
              }}
            >
              <label style={{ display: "flex", flexDirection: "column", gap: "4px", fontSize: "0.85rem" }}>
                Topic *
                <select
                  value={manualTopicCode}
                  onChange={(e) => {
                    setManualTopicCode(e.target.value);
                    setManualSubtopicCode("");
                  }}
                  style={{ padding: "8px", borderRadius: "6px", border: "1px solid #cbd5e1" }}
                >
                  <option value="">Select topic…</option>
                  {taxonomyTopics.map((t) => (
                    <option key={t.id} value={t.code}>
                      {t.code} — {cleanTopicTitle(t.title)}
                    </option>
                  ))}
                </select>
              </label>
              <label style={{ display: "flex", flexDirection: "column", gap: "4px", fontSize: "0.85rem" }}>
                Subtopic {topicHasSubs ? "*" : "(none for this topic)"}
                <select
                  value={manualSubtopicCode}
                  disabled={!manualTopicCode || !topicHasSubs}
                  onChange={(e) => setManualSubtopicCode(e.target.value)}
                  style={{ padding: "8px", borderRadius: "6px", border: "1px solid #cbd5e1" }}
                >
                  <option value="">{topicHasSubs ? "Select subtopic…" : "N/A — topic only"}</option>
                  {manualSubs.map((s) => (
                    <option key={s.id} value={s.code}>
                      {s.code} — {cleanTopicTitle(s.title)}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                className="btn primary"
                disabled={busy || !manualTopicCode || (topicHasSubs && !manualSubtopicCode)}
                onClick={handleManualTagSave}
              >
                Save Topic Tag
              </button>
              {!taxonomyTopics.length ? (
                <p className="muted" style={{ gridColumn: "1 / -1", margin: 0 }}>
                  No syllabus topics loaded. Import a syllabus first.
                </p>
              ) : null}
            </div>
          ) : null}

          {(current?.classification || current?.bloomLevel) && (
            <div style={{ padding: "12px", background: "#f8fafc", border: "1px solid #e2e8f0", borderRadius: "8px", fontSize: "0.85rem" }}>
              {current?.bloomReasoning && (
                <div style={{ marginBottom: "8px" }}>
                  <strong>Bloom's Taxonomy Reasoning:</strong> {current.bloomReasoning}
                </div>
              )}
            </div>
          )}
        </div>
      </section>

      <section className="panel">
        <h2>Version history</h2>
        <div className="version-list">
          {(data.versions || []).map((v) => (
            <div key={v.id} className="version-row">
              <strong>Version {v.version}</strong>
              <StatusBadge status={v.status} />
              <span className="muted">
                {v.approvedAt ? `Approved ${new Date(v.approvedAt).toLocaleDateString()}` : ""}
              </span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

export function cleanTopicTitle(title) {
  if (!title) return "";
  // Strip leading numbering like "1.1 ", "1. ", "1.1.2 ", "A. ", "a) "
  return title.replace(/^\s*(?:\d+[\d\.]*|[A-Za-z])\s*[\.\)-:]+\s*/, "").trim();
}

function previewText(content) {
  if (!Array.isArray(content)) return "";
  for (const block of content) {
    if (block?.type === "text" && block.value) return String(block.value).slice(0, 140);
  }
  return "";
}
