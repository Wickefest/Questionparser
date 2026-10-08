import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { EmptyState } from "../components/ui";
import { BusyBanner, LoadingOverlay } from "../components/LoadingOverlay";
import { HistoryPanel, UploadZone } from "../components/UploadZone";
import { QuestionRenderer } from "../components/QuestionRenderer";

const DEFAULT_SUBJECT_PRESETS = [
  {
    subject: "Chemistry",
    code: "6092",
    defaultTitle: "Singapore-Cambridge GCE O-Level Chemistry (6092)",
  },
  {
    subject: "Biology",
    code: "6093",
    defaultTitle: "Singapore-Cambridge GCE O-Level Biology (6093)",
  },
  {
    subject: "Mathematics",
    code: "4048",
    defaultTitle: "Singapore-Cambridge GCE O-Level Mathematics (4048)",
  },
  {
    subject: "Physics",
    code: "6091",
    defaultTitle: "Singapore-Cambridge GCE O-Level Physics (6091)",
  },
  {
    subject: "Additional Mathematics",
    code: "4049",
    defaultTitle: "Singapore-Cambridge GCE O-Level Additional Mathematics (4049)",
  },
];

export function ClassificationPage() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [successMsg, setSuccessMsg] = useState("");
  const [busyMessage, setBusyMessage] = useState("");
  const [expandedId, setExpandedId] = useState(null);
  const [expandedBatchId, setExpandedBatchId] = useState(null);
  const [expandedHistoryId, setExpandedHistoryId] = useState(null);
  const [manualVersionId, setManualVersionId] = useState(null);
  const [manualTopicCode, setManualTopicCode] = useState("");
  const [manualSubtopicCode, setManualSubtopicCode] = useState("");
  const [manualBloom, setManualBloom] = useState("");
  const [manualReasoning, setManualReasoning] = useState("");
  const [queueView, setQueueView] = useState("pending"); // pending | history | all

  const loadQueue = () => {
    api
      .classification()
      .then(setData)
      .catch((e) => setError(e.message));
  };

  useEffect(() => {
    loadQueue();
  }, []);

  const cleanTopicTitle = (title) => {
    if (!title) return "";
    return title.replace(/^\s*(?:\d+[\d\.]*|[A-Za-z])\s*[\.\)-:]+\s*/, "").trim();
  };

  const handleClassify = async (versionId) => {
    const item = (data?.items || []).find((i) => i.versionId === versionId);
    if (item?.classificationStatus === "CLASSIFIED") {
      const ok = window.confirm(
        "This question is already classified. Re-run AI and overwrite the existing topic/subtopic?",
      );
      if (!ok) return;
    }
    setBusy(true);
    setBusyMessage("Asking Kimi Moonshot AI to analyze topic outcomes...");
    setError("");
    setSuccessMsg("");
    try {
      const res = await api.runClassification(versionId);
      if (res.success) {
        const cleanTitle = cleanTopicTitle(res.title || "");
        setSuccessMsg(`Successfully classified question under "${cleanTitle}" [${res.code || "No Code"}] using ${res.method}. Reasoning: ${res.reasoning}`);
        loadQueue();
      } else {
        setError(res.message || "Failed to classify question.");
      }
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setBusy(false);
      setBusyMessage("");
    }
  };

  const handleClassifyBatch = async (batchId, versionIds) => {
    if (!versionIds || versionIds.length === 0) {
      setSuccessMsg("All questions in this batch are already classified — nothing to run.");
      return;
    }
    setBusy(true);
    setBusyMessage(`Classifying ${versionIds.length} pending question(s) in this batch...`);
    setError("");
    setSuccessMsg("");
    try {
      const res = await api.runBatchClassification(versionIds, { skipClassified: true });
      if (res.success) {
        const skipped = res.skippedCount || 0;
        setSuccessMsg(
          skipped
            ? `Classified ${res.classifiedCount} pending question(s). Kept ${skipped} already classified.`
            : `Successfully classified ${res.classifiedCount} question(s) in this batch.`,
        );
        loadQueue();
      } else {
        setError(res.message || "Failed to run batch classification.");
      }
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setBusy(false);
      setBusyMessage("");
    }
  };

  const handleClassifyAll = async () => {
    if (!data?.items?.length) return;
    const pendingVersionIds = data.items
      .filter((item) => item.classificationStatus === "PENDING")
      .map((item) => item.versionId);

    if (pendingVersionIds.length === 0) {
      setSuccessMsg("All questions are already classified — existing tags retained.");
      return;
    }

    setBusy(true);
    setBusyMessage(`Classifying ${pendingVersionIds.length} pending question(s)...`);
    setError("");
    setSuccessMsg("");

    try {
      const res = await api.runBatchClassification(pendingVersionIds, { skipClassified: true });
      if (res.success) {
        const skipped = res.skippedCount || 0;
        setSuccessMsg(
          skipped
            ? `Classified ${res.classifiedCount} pending. Kept ${skipped} already classified.`
            : `Classification completed! Classified ${res.classifiedCount} of ${pendingVersionIds.length} pending question(s).`,
        );
        setExpandedId(null);
        loadQueue();
      } else {
        setError(res.message || "Failed to run batch classification.");
      }
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setBusy(false);
      setBusyMessage("");
    }
  };

  const groupedBatches = useMemo(() => {
    if (!data?.items) return [];
    
    const groups = {};
    for (const item of data.items) {
      const bId = item.batchId || "legacy_or_manual";
      if (!groups[bId]) {
        groups[bId] = {
          id: bId,
          title: item.batchTitle || "Unlinked Questions (Legacy/Manual)",
          items: [],
          classifiedCount: 0,
          pendingCount: 0,
        };
      }
      groups[bId].items.push(item);
      if (item.classificationStatus === "CLASSIFIED") {
        groups[bId].classifiedCount++;
      } else {
        groups[bId].pendingCount++;
      }
    }
    return Object.values(groups);
  }, [data?.items]);

  const pendingBatches = useMemo(
    () => groupedBatches.filter((b) => b.pendingCount > 0),
    [groupedBatches],
  );
  const historyBatches = useMemo(
    () => groupedBatches.filter((b) => b.pendingCount === 0),
    [groupedBatches],
  );
  const visibleBatches =
    queueView === "pending" ? pendingBatches : queueView === "history" ? historyBatches : groupedBatches;

  const itemsByVersionId = useMemo(() => {
    const map = {};
    for (const item of data?.items || []) {
      map[item.versionId] = item;
    }
    return map;
  }, [data?.items]);

  const itemsBySourceKey = useMemo(() => {
    const map = {};
    for (const item of data?.items || []) {
      if (item.sourceKey) map[item.sourceKey] = item;
    }
    return map;
  }, [data?.items]);

  const resolveHistoryItem = (h) => {
    const byVersion = itemsByVersionId[h.questionVersionId];
    if (byVersion) return { item: byVersion, staleVersion: false };
    const byKey = h.sourceKey ? itemsBySourceKey[h.sourceKey] : null;
    if (byKey) return { item: byKey, staleVersion: true };
    return { item: null, staleVersion: false };
  };

  const resolveTaxonomy = (item) => {
    const tax = data?.taxonomy || {};
    if (item?.syllabusId && tax[item.syllabusId]) return tax[item.syllabusId];
    const values = Object.values(tax);
    if (!values.length) return null;
    const bySubject = values.find((t) => t.subject === item?.subject);
    return bySubject || values[0];
  };

  const openManualTag = (item) => {
    setManualVersionId(item.versionId);
    setManualTopicCode(item.classification?.topic?.code || "");
    setManualSubtopicCode(item.classification?.subtopic?.code || "");
    setManualBloom(item.bloomLevel || "");
    setManualReasoning("");
    setExpandedId(item.versionId);
  };

  const handleManualSave = async () => {
    if (!manualVersionId || !manualTopicCode) {
      setError("Pick a numbered Topic code before saving.");
      return;
    }
    setBusy(true);
    setBusyMessage("Saving manual topic / subtopic tag...");
    setError("");
    setSuccessMsg("");
    try {
      const res = await api.manualClassification({
        versionId: manualVersionId,
        topicCode: manualTopicCode,
        subtopicCode: manualSubtopicCode || null,
        bloomLevel: manualBloom || null,
        reasoning: manualReasoning || null,
      });
      if (res.success) {
        setSuccessMsg(
          `Manual tag saved: Topic ${res.topicCode}` +
            (res.subtopicCode ? ` / Subtopic ${res.subtopicCode}` : "") +
            (res.bloomLevel ? ` · Bloom ${res.bloomLevel}` : ""),
        );
        setManualVersionId(null);
        loadQueue();
      } else {
        setError(res.message || "Failed to save manual tag.");
      }
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setBusy(false);
      setBusyMessage("");
    }
  };


  const historyForBatch = (batch) => {
    const versionIds = new Set(batch.items.map((i) => i.versionId));
    const sourceKeys = new Set(batch.items.map((i) => i.sourceKey).filter(Boolean));
    return (data?.history || []).filter(
      (h) => versionIds.has(h.questionVersionId) || (h.sourceKey && sourceKeys.has(h.sourceKey)),
    );
  };

  const orphanHistory = useMemo(() => {
    const versionIds = new Set((data?.items || []).map((i) => i.versionId));
    const sourceKeys = new Set((data?.items || []).map((i) => i.sourceKey).filter(Boolean));
    return (data?.history || []).filter(
      (h) => !versionIds.has(h.questionVersionId) && !(h.sourceKey && sourceKeys.has(h.sourceKey)),
    );
  }, [data?.history, data?.items]);

  const renderHistoryCard = (h) => {
    const isOpen = expandedHistoryId === h.id;
    const { item: linkedItem, staleVersion } = resolveHistoryItem(h);
return (
                <div
                  key={h.id}
                  style={{
                    padding: "10px 12px",
                    background: "#f8fafc",
                    border: "1px solid #e2e8f0",
                    borderRadius: "8px",
                    fontSize: "0.82rem",
                  }}
                >
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "140px 1fr auto",
                      gap: "12px",
                      alignItems: "start",
                    }}
                  >
                    <div>
                      <strong>{h.sourceKey || "—"}</strong>
                      <div className="muted" style={{ fontSize: "0.72rem", marginTop: "2px" }}>
                        {h.createdAt ? new Date(h.createdAt).toLocaleString() : ""}
                      </div>
                    </div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", alignItems: "center" }}>
                      {h.topicCode ? (
                        <span style={{ background: "#f0fdf4", border: "1px solid #bbf7d0", color: "#166534", padding: "2px 8px", borderRadius: "10px", fontWeight: 600 }}>
                          Topic {h.topicCode}{h.topicTitle ? `: ${cleanTopicTitle(h.topicTitle)}` : ""}
                        </span>
                      ) : null}
                      {h.subtopicCode ? (
                        <span style={{ background: "#eff6ff", border: "1px solid #bfdbfe", color: "#1e40af", padding: "2px 8px", borderRadius: "10px", fontWeight: 600 }}>
                          Subtopic {h.subtopicCode}{h.subtopicTitle ? `: ${cleanTopicTitle(h.subtopicTitle)}` : ""}
                        </span>
                      ) : (
                        <span className="muted">No numbered subtopic</span>
                      )}
                      {h.bloomLevel ? (
                        <span style={{ background: "#faf5ff", border: "1px solid #e9d5ff", color: "#6b21a8", padding: "2px 8px", borderRadius: "10px", fontWeight: 600 }}>
                          Bloom: {h.bloomLevel}
                        </span>
                      ) : null}
                      {h.reasoning ? (
                        <div className="muted" style={{ width: "100%", marginTop: "4px" }}>
                          {h.reasoning.length > 160 ? `${h.reasoning.slice(0, 160)}…` : h.reasoning}
                        </div>
                      ) : null}
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "6px" }}>
                      <div className="muted" style={{ fontSize: "0.72rem", textAlign: "right", maxWidth: "140px" }}>
                        {h.method || h.classifiedBy || ""}
                        {h.fromLive ? " · current" : ""}
                      </div>
                      <button
                        type="button"
                        className="btn secondary"
                        style={{ padding: "4px 9px", fontSize: "0.75rem" }}
                        onClick={() => setExpandedHistoryId(isOpen ? null : h.id)}
                      >
                        {isOpen ? "Collapse Review" : "Expand & Review"}
                      </button>
                    </div>
                  </div>

                  {isOpen ? (
                    <div style={{ marginTop: "10px", paddingTop: "10px", borderTop: "1px solid #e2e8f0" }}>
                      {linkedItem ? (
                        <>
                          <div style={{ display: "flex", flexWrap: "wrap", gap: "8px", marginBottom: "8px", alignItems: "center" }}>
                            <span className="muted" style={{ fontSize: "0.75rem" }}>
                              {linkedItem.subject} · Version {linkedItem.version}
                              {staleVersion ? " · showing current bank copy (history was from an older ingest)" : ""}
                            </span>
                            {linkedItem.bloomLevel ? (
                              <span style={{ background: "#eff6ff", border: "1px solid #bfdbfe", color: "#1e40af", padding: "2px 8px", borderRadius: "10px", fontWeight: 600 }}>
                                Bloom: {linkedItem.bloomLevel}
                              </span>
                            ) : null}
                            <button
                              type="button"
                              className="btn secondary"
                              style={{ padding: "4px 9px", fontSize: "0.75rem", marginLeft: "auto" }}
                              disabled={busy}
                              onClick={() =>
                                openManualTag({
                                  ...linkedItem,
                                  classification: {
                                    topic: h.topicCode
                                      ? { code: h.topicCode, title: h.topicTitle }
                                      : linkedItem.classification?.topic,
                                    subtopic: h.subtopicCode
                                      ? { code: h.subtopicCode, title: h.subtopicTitle }
                                      : linkedItem.classification?.subtopic,
                                  },
                                  bloomLevel: h.bloomLevel || linkedItem.bloomLevel,
                                })
                              }
                            >
                              Fix with Manual Tag
                            </button>
                          </div>
                          {manualVersionId === linkedItem.versionId && (() => {
                            const tax = resolveTaxonomy(linkedItem);
                            const topics = tax?.topics || [];
                            const subtopics = (tax?.subtopics || []).filter(
                              (s) => s.parentCode === manualTopicCode || String(s.code).startsWith(`${manualTopicCode}.`),
                            );
                            const topicHasSubs = subtopics.length > 0;
                            return (
                              <div
                                style={{
                                  marginBottom: "10px",
                                  padding: "12px",
                                  border: "1px solid #cbd5e1",
                                  borderRadius: "8px",
                                  background: "#fffbeb",
                                }}
                              >
                                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                                  <strong>Manual Tag (numbered codes)</strong>
                                  <button
                                    type="button"
                                    className="btn"
                                    style={{ padding: "4px 8px", fontSize: "0.75rem" }}
                                    onClick={() => setManualVersionId(null)}
                                  >
                                    Cancel
                                  </button>
                                </div>
                                {!tax ? (
                                  <p className="muted">No syllabus taxonomy loaded. Import a syllabus PDF first.</p>
                                ) : (
                                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "10px" }}>
                                    <label style={{ display: "flex", flexDirection: "column", gap: "4px", fontSize: "0.8rem" }}>
                                      Topic *
                                      <select
                                        value={manualTopicCode}
                                        onChange={(e) => {
                                          setManualTopicCode(e.target.value);
                                          setManualSubtopicCode("");
                                        }}
                                        style={{ padding: "6px 8px", borderRadius: "6px", border: "1px solid #cbd5e1" }}
                                      >
                                        <option value="">Select topic…</option>
                                        {topics.map((t) => (
                                          <option key={t.id} value={t.code}>
                                            {t.code} — {cleanTopicTitle(t.title)}
                                          </option>
                                        ))}
                                      </select>
                                    </label>
                                    <label style={{ display: "flex", flexDirection: "column", gap: "4px", fontSize: "0.8rem" }}>
                                      Subtopic {topicHasSubs ? "*" : "(none for this topic)"}
                                      <select
                                        value={manualSubtopicCode}
                                        disabled={!manualTopicCode || !topicHasSubs}
                                        onChange={(e) => setManualSubtopicCode(e.target.value)}
                                        style={{ padding: "6px 8px", borderRadius: "6px", border: "1px solid #cbd5e1" }}
                                      >
                                        <option value="">{topicHasSubs ? "Select subtopic…" : "N/A — topic only"}</option>
                                        {subtopics.map((s) => (
                                          <option key={s.id} value={s.code}>
                                            {s.code} — {cleanTopicTitle(s.title)}
                                          </option>
                                        ))}
                                      </select>
                                    </label>
                                    <label style={{ display: "flex", flexDirection: "column", gap: "4px", fontSize: "0.8rem" }}>
                                      Bloom's Level (optional)
                                      <select
                                        value={manualBloom}
                                        onChange={(e) => setManualBloom(e.target.value)}
                                        style={{ padding: "6px 8px", borderRadius: "6px", border: "1px solid #cbd5e1" }}
                                      >
                                        <option value="">Keep / leave unset</option>
                                        {["Remembering", "Understanding", "Applying", "Analyzing", "Evaluating", "Creating"].map((b) => (
                                          <option key={b} value={b}>{b}</option>
                                        ))}
                                      </select>
                                    </label>
                                    <label style={{ gridColumn: "1 / -1", display: "flex", flexDirection: "column", gap: "4px", fontSize: "0.8rem" }}>
                                      Note / reasoning (optional)
                                      <input
                                        value={manualReasoning}
                                        onChange={(e) => setManualReasoning(e.target.value)}
                                        placeholder="Why this correction is right"
                                        style={{ padding: "6px 8px", borderRadius: "6px", border: "1px solid #cbd5e1" }}
                                      />
                                    </label>
                                    <div style={{ gridColumn: "1 / -1", display: "flex", justifyContent: "flex-end" }}>
                                      <button
                                        type="button"
                                        className="btn primary"
                                        disabled={busy || !manualTopicCode || (topicHasSubs && !manualSubtopicCode)}
                                        onClick={handleManualSave}
                                      >
                                        Save Manual Tag
                                      </button>
                                    </div>
                                  </div>
                                )}
                              </div>
                            );
                          })()}
                          <div className="panel" style={{ margin: 0, padding: "12px", background: "#fff" }}>
                            <QuestionRenderer
                              content={linkedItem.content}
                              options={linkedItem.options}
                              assetUrls={linkedItem.assetUrls || {}}
                            />
                          </div>
                          {h.reasoning ? (
                            <div style={{ marginTop: "8px", padding: "8px 10px", background: "#fff", border: "1px solid #e2e8f0", borderRadius: "6px" }}>
                              <strong>Classification Reasoning:</strong> {h.reasoning}
                            </div>
                          ) : null}
                        </>
                      ) : (
                        <div className="muted">
                          No live bank question matches this history row (version was removed or not approved).
                        </div>
                      )}
                    </div>
                  ) : null}
                </div>
              );

  };

  if (error && !data) return <div className="page error-banner">{error}</div>;
  if (!data) return <div className="page muted">Loading…</div>;

  return (
    <div className="page relative-page">
      <LoadingOverlay show={busy} title="AI Classifier Engine" message={busyMessage || "Analyzing syllabus outcomes..."} />

      <header className="page-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h1>Classification & Taxonomy Queue</h1>
          <p className="subtitle">AI-powered syllabus topic and Bloom's Taxonomy assignment using Moonshot.</p>
        </div>
        <div style={{ display: "flex", gap: "10px", flexWrap: "wrap", justifyContent: "flex-end" }}>
          <div className="filter-pills" style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <button
              type="button"
              className={`pill ${queueView === "pending" ? "active" : ""}`}
              onClick={() => setQueueView("pending")}
            >
              Pending ({pendingBatches.length})
            </button>
            <button
              type="button"
              className={`pill ${queueView === "history" ? "active" : ""}`}
              onClick={() => setQueueView("history")}
            >
              History ({historyBatches.length})
            </button>
            <button
              type="button"
              className={`pill ${queueView === "all" ? "active" : ""}`}
              onClick={() => setQueueView("all")}
            >
              All ({groupedBatches.length})
            </button>
          </div>
          {queueView !== "history" && data.items?.some((i) => i.classificationStatus === "PENDING") ? (
            <button
              type="button"
              className="btn primary"
              style={{ padding: "10px 20px", fontWeight: "600", borderRadius: "8px" }}
              disabled={busy}
              onClick={handleClassifyAll}
            >
              Classify Pending ({data.items.filter((i) => i.classificationStatus === "PENDING").length})
            </button>
          ) : queueView === "history" ? (
            <span className="muted" style={{ fontSize: "0.9rem", alignSelf: "center" }}>
              Original classifications kept — no re-classify
            </span>
          ) : data.items?.length > 0 ? (
            <span className="muted" style={{ fontSize: "0.9rem", alignSelf: "center" }}>
              All questions classified — existing tags retained
            </span>
          ) : null}
        </div>
      </header>

      {error ? <div className="error-banner">{error}</div> : null}
      {successMsg ? <div className="panel success-panel" style={{ marginBottom: "16px" }}>{successMsg}</div> : null}

      {orphanHistory.length > 0 ? (
        <section className="panel" style={{ marginBottom: "20px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
            <h2 style={{ margin: 0, fontSize: "1.05rem" }}>Other Classification History</h2>
            <span className="muted" style={{ fontSize: "0.8rem" }}>
              From removed or re-ingested versions (not in current batches)
            </span>
          </div>
          <div style={{ maxHeight: "220px", overflowY: "auto", display: "flex", flexDirection: "column", gap: "8px" }}>
            {orphanHistory.slice(0, 20).map((h) => (
              <div key={h.id}>{renderHistoryCard(h)}</div>
            ))}
          </div>
        </section>
      ) : null}

      {visibleBatches.length === 0 ? (
        <EmptyState
          title={
            queueView === "history"
              ? "No previously classified batches yet."
              : queueView === "pending"
                ? "Nothing pending — all current batches are classified."
                : "Classification Queue is empty!"
          }
        >
          {queueView === "history"
            ? "Successful batches keep their original topic tags. They appear here in green, with no re-classify."
            : "All approved question versions have been successfully mapped to syllabus nodes."}
        </EmptyState>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "20px" }}>
          {visibleBatches.map((batch) => {
            const isBatchExpanded = expandedBatchId === batch.id;
            const pendingIds = batch.items
              .filter((i) => i.classificationStatus === "PENDING")
              .map((i) => i.versionId);
            const fullyClassified = batch.pendingCount === 0;
            
            return (
              <div
                key={batch.id}
                className={`panel ${fullyClassified ? "classification-batch-succeeded" : ""}`}
                style={{
                  border: fullyClassified ? "1px solid #bbf7d0" : "1px solid var(--line)",
                  borderRadius: "12px",
                  background: fullyClassified ? "#f0fdf4" : "#fff",
                  overflow: "hidden",
                  padding: 0,
                }}
              >
                {/* Batch Header Row */}
                <div 
                  style={{ 
                    display: "flex", 
                    justifyContent: "space-between", 
                    alignItems: "center", 
                    padding: "16px 20px", 
                    background: fullyClassified ? "#ecfdf3" : "#f8fafc", 
                    borderBottom: isBatchExpanded ? "1px solid var(--line)" : "none",
                    cursor: "pointer"
                  }}
                  onClick={() => setExpandedBatchId(isBatchExpanded ? null : batch.id)}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                    <span style={{ fontSize: "1.5rem" }}>📦</span>
                    <div>
                      <h3 style={{ margin: 0, fontSize: "1.1rem" }}>{batch.title}</h3>
                      <div style={{ display: "flex", gap: "12px", fontSize: "0.85rem", marginTop: "4px" }} className="muted">
                        <span>📋 Total: <strong>{batch.items.length}</strong></span>
                        <span style={{ color: "#166534" }}>✅ Classified: <strong>{batch.classifiedCount}</strong></span>
                        <span style={{ color: "#b45309" }}>⏳ Pending: <strong>{batch.pendingCount}</strong></span>
                        <span>🕘 History: <strong>{historyForBatch(batch).length}</strong></span>
                      </div>
                    </div>
                  </div>
                  
                  <div style={{ display: "flex", alignItems: "center", gap: "12px" }} onClick={(e) => e.stopPropagation()}>
                    {batch.pendingCount > 0 ? (
                      <button
                        type="button"
                        className="btn primary"
                        style={{ padding: "6px 12px", fontSize: "0.85rem" }}
                        disabled={busy}
                        onClick={() => handleClassifyBatch(batch.id, pendingIds)}
                      >
                        Classify Batch ({batch.pendingCount} pending)
                      </button>
                    ) : (
                      <span className="status-badge status-approved">Already classified</span>
                    )}
                    <button
                      type="button"
                      className="btn secondary"
                      style={{ padding: "6px 12px", fontSize: "0.85rem" }}
                      onClick={() => setExpandedBatchId(isBatchExpanded ? null : batch.id)}
                    >
                      {isBatchExpanded ? "Collapse Batch" : "Expand & Review"}
                    </button>
                  </div>
                </div>

                {(() => {
                  const batchHistory = historyForBatch(batch);
                  if (!batchHistory.length) return null;
                  return (
                    <div
                      style={{
                        padding: "14px 16px",
                        borderTop: "1px solid #e2e8f0",
                        background: "#fff",
                      }}
                      onClick={(e) => e.stopPropagation()}
                    >
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
                        <strong style={{ fontSize: "0.95rem" }}>Previous classification history</strong>
                        <span className="muted" style={{ fontSize: "0.78rem" }}>
                          {batchHistory.length} run{batchHistory.length === 1 ? "" : "s"} · newest first
                        </span>
                      </div>
                      <div style={{ maxHeight: isBatchExpanded ? "320px" : "220px", overflowY: "auto", display: "flex", flexDirection: "column", gap: "8px" }}>
                        {batchHistory.slice(0, 40).map((h) => (
                          <div key={h.id}>{renderHistoryCard(h)}</div>
                        ))}
                      </div>
                    </div>
                  );
                })()}

                {/* Batch Questions List */}
                {isBatchExpanded && (
                  <div style={{ padding: "20px", display: "flex", flexDirection: "column", gap: "16px", background: "#fcfdfe" }}>
                    {batch.items.map((item) => {
                      const isQuestionExpanded = expandedId === item.versionId;
                      const classified = item.classificationStatus === "CLASSIFIED";
                      return (
                        <div 
                          key={item.versionId} 
                          style={{ 
                            display: "flex", 
                            flexDirection: "column", 
                            padding: "16px", 
                            border: classified ? "1px solid #bbf7d0" : "1px solid #e2e8f0", 
                            borderRadius: "8px", 
                            background: classified ? "#f0fdf4" : "#fff" 
                          }}
                        >
                          {/* Question Row Header */}
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", width: "100%" }}>
                            <div>
                              <strong style={{ fontSize: "1.1rem" }}>{item.sourceKey}</strong>
                              <div className="muted" style={{ fontSize: "0.8rem", marginTop: "2px" }}>
                                {item.subject} · Version {item.version}
                              </div>
                            </div>
                            <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", justifyContent: "flex-end" }}>
                              <button
                                type="button"
                                className="btn secondary"
                                style={{ padding: "5px 10px", fontSize: "0.8rem" }}
                                onClick={() => setExpandedId(isQuestionExpanded ? null : item.versionId)}
                              >
                                {isQuestionExpanded ? "Hide Preview" : "View Question"}
                              </button>
                              
                              {item.classificationStatus === "CLASSIFIED" ? (
                                <span className="status-badge status-approved" style={{ background: "#dcfce7", color: "#15803d" }}>CLASSIFIED</span>
                              ) : (
                                <span className="status-badge status-needs_review" style={{ background: "#fef3c7", color: "#d97706" }}>PENDING</span>
                              )}

                              <button
                                type="button"
                                className="btn secondary"
                                style={{ padding: "5px 10px", fontSize: "0.8rem" }}
                                disabled={busy}
                                onClick={() => openManualTag(item)}
                              >
                                Manual Tag
                              </button>

                              {item.classificationStatus === "PENDING" ? (
                                <button
                                  type="button"
                                  className="btn primary"
                                  style={{ padding: "5px 10px", fontSize: "0.8rem" }}
                                  disabled={busy}
                                  onClick={() => handleClassify(item.versionId)}
                                >
                                  Classify with AI
                                </button>
                              ) : null}
                            </div>
                          </div>

                          {/* Classification outcome badges inside each question */}
                          <div style={{ display: "flex", flexWrap: "wrap", gap: "10px", marginTop: "12px", borderTop: "1px solid #f1f5f9", paddingTop: "12px" }}>
                            {item.classification?.topic ? (
                              <div style={{ background: "#f0fdf4", border: "1px solid #bbf7d0", color: "#166534", padding: "6px 12px", borderRadius: "16px", fontSize: "0.8rem", fontWeight: "600" }}>
                                Topic {item.classification.topic.code}: {cleanTopicTitle(item.classification.topic.title)}
                              </div>
                            ) : (
                              <div style={{ background: "#fffbeb", border: "1px solid #fde68a", color: "#92400e", padding: "6px 12px", borderRadius: "16px", fontSize: "0.8rem", fontWeight: "600" }}>
                                Topic: Pending Assignment
                              </div>
                            )}

                            {item.classification?.subtopic ? (
                              <div style={{ background: "#f0fdf4", border: "1px solid #bbf7d0", color: "#166534", padding: "6px 12px", borderRadius: "16px", fontSize: "0.8rem", fontWeight: "600" }}>
                                Subtopic {item.classification.subtopic.code}: {cleanTopicTitle(item.classification.subtopic.title)}
                              </div>
                            ) : item.classification?.topic ? (
                              <div style={{ background: "#f8fafc", border: "1px solid #e2e8f0", color: "#64748b", padding: "6px 12px", borderRadius: "16px", fontSize: "0.8rem", fontWeight: "600" }}>
                                Subtopic: N/A (no numbered children under Topic {item.classification.topic.code})
                              </div>
                            ) : (
                              <div style={{ background: "#fffbeb", border: "1px solid #fde68a", color: "#92400e", padding: "6px 12px", borderRadius: "16px", fontSize: "0.8rem", fontWeight: "600" }}>
                                ⏳ Subtopic: Pending Assignment
                              </div>
                            )}

                            {item.bloomLevel ? (
                              <div style={{ background: "#eff6ff", border: "1px solid #bfdbfe", color: "#1e40af", padding: "6px 12px", borderRadius: "16px", fontSize: "0.8rem", fontWeight: "600" }}>
                                🧠 Bloom's Level: {item.bloomLevel}
                              </div>
                            ) : (
                              <div style={{ background: "#fffbeb", border: "1px solid #fde68a", color: "#92400e", padding: "6px 12px", borderRadius: "16px", fontSize: "0.8rem", fontWeight: "600" }}>
                                ⏳ Bloom's: Pending Assignment
                              </div>
                            )}
                          </div>

                          {/* Bloom's Reasoning Explanation Block */}
                          {item.bloomReasoning && (
                            <div style={{ marginTop: "10px", padding: "10px 12px", background: "#f8fafc", borderLeft: "4px solid #3b82f6", borderRadius: "0 6px 6px 0", fontSize: "0.8rem", color: "#334155" }}>
                              <strong>Bloom's Taxonomy Reasoning:</strong> {item.bloomReasoning}
                            </div>
                          )}

                          {manualVersionId === item.versionId && (() => {
                            const tax = resolveTaxonomy(item);
                            const topics = tax?.topics || [];
                            const subtopics = (tax?.subtopics || []).filter(
                              (s) => s.parentCode === manualTopicCode || String(s.code).startsWith(`${manualTopicCode}.`),
                            );
                            const topicHasSubs = subtopics.length > 0;
                            return (
                              <div
                                style={{
                                  marginTop: "12px",
                                  padding: "14px",
                                  border: "1px solid #cbd5e1",
                                  borderRadius: "8px",
                                  background: "#fffbeb",
                                }}
                              >
                                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
                                  <strong>Manual Tag (numbered codes)</strong>
                                  <button
                                    type="button"
                                    className="btn"
                                    style={{ padding: "4px 8px", fontSize: "0.75rem" }}
                                    onClick={() => setManualVersionId(null)}
                                  >
                                    Cancel
                                  </button>
                                </div>
                                {!tax ? (
                                  <p className="muted">No syllabus taxonomy loaded. Import a syllabus PDF first.</p>
                                ) : (
                                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "10px" }}>
                                    <label style={{ display: "flex", flexDirection: "column", gap: "4px", fontSize: "0.8rem" }}>
                                      Topic *
                                      <select
                                        value={manualTopicCode}
                                        onChange={(e) => {
                                          setManualTopicCode(e.target.value);
                                          setManualSubtopicCode("");
                                        }}
                                        style={{ padding: "6px 8px", borderRadius: "6px", border: "1px solid #cbd5e1" }}
                                      >
                                        <option value="">Select topic…</option>
                                        {topics.map((t) => (
                                          <option key={t.id} value={t.code}>
                                            {t.code} — {cleanTopicTitle(t.title)}
                                          </option>
                                        ))}
                                      </select>
                                    </label>
                                    <label style={{ display: "flex", flexDirection: "column", gap: "4px", fontSize: "0.8rem" }}>
                                      Subtopic {topicHasSubs ? "*" : "(none for this topic)"}
                                      <select
                                        value={manualSubtopicCode}
                                        disabled={!manualTopicCode || !topicHasSubs}
                                        onChange={(e) => setManualSubtopicCode(e.target.value)}
                                        style={{ padding: "6px 8px", borderRadius: "6px", border: "1px solid #cbd5e1" }}
                                      >
                                        <option value="">{topicHasSubs ? "Select subtopic…" : "N/A — topic only"}</option>
                                        {subtopics.map((s) => (
                                          <option key={s.id} value={s.code}>
                                            {s.code} — {cleanTopicTitle(s.title)}
                                          </option>
                                        ))}
                                      </select>
                                    </label>
                                    <label style={{ display: "flex", flexDirection: "column", gap: "4px", fontSize: "0.8rem" }}>
                                      Bloom's Level (optional)
                                      <select
                                        value={manualBloom}
                                        onChange={(e) => setManualBloom(e.target.value)}
                                        style={{ padding: "6px 8px", borderRadius: "6px", border: "1px solid #cbd5e1" }}
                                      >
                                        <option value="">Keep / leave unset</option>
                                        {["Remembering", "Understanding", "Applying", "Analyzing", "Evaluating", "Creating"].map((b) => (
                                          <option key={b} value={b}>{b}</option>
                                        ))}
                                      </select>
                                    </label>
                                    <label className="span-2" style={{ gridColumn: "1 / -1", display: "flex", flexDirection: "column", gap: "4px", fontSize: "0.8rem" }}>
                                      Note / reasoning (optional)
                                      <input
                                        value={manualReasoning}
                                        onChange={(e) => setManualReasoning(e.target.value)}
                                        placeholder="Why this topic/subtopic is correct"
                                        style={{ padding: "6px 8px", borderRadius: "6px", border: "1px solid #cbd5e1" }}
                                      />
                                    </label>
                                    <div style={{ gridColumn: "1 / -1", display: "flex", justifyContent: "flex-end", gap: "8px" }}>
                                      <button
                                        type="button"
                                        className="btn primary"
                                        disabled={busy || !manualTopicCode || (topicHasSubs && !manualSubtopicCode)}
                                        onClick={handleManualSave}
                                      >
                                        Save Manual Tag
                                      </button>
                                    </div>
                                  </div>
                                )}
                              </div>
                            );
                          })()}

                          {/* Full Question Live Renderer Panel */}
                          {isQuestionExpanded && (
                            <div className="panel" style={{ marginTop: "16px", padding: "16px", border: "1px solid #e2e8f0", borderRadius: "8px", background: "#f8fafc" }}>
                              <QuestionRenderer
                                content={item.content}
                                options={item.options}
                                assetUrls={item.assetUrls || {}}
                              />
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function previewText(content) {
  if (!content) return "";
  if (isString(content)) return content;
  if (Array.isArray(content)) {
    const txt = content.find((b) => b?.type === "text")?.value;
    if (txt) return txt;
  }
  return JSON.stringify(content);
}

function isString(val) {
  return typeof val === "string";
}

export function SyllabiPage() {
  const [list, setList] = useState([]);
  const [presets, setPresets] = useState(DEFAULT_SUBJECT_PRESETS);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [busyTitle, setBusyTitle] = useState("");
  const [busyMessage, setBusyMessage] = useState("");
  const [showAdd, setShowAdd] = useState(false);
  const [subjectFilter, setSubjectFilter] = useState("All");
  const [form, setForm] = useState({
    subject: "Chemistry",
    code: "6092",
    year: String(new Date().getFullYear()),
    title: DEFAULT_SUBJECT_PRESETS[0].defaultTitle,
  });
  const [pdfFile, setPdfFile] = useState(null);

  const subjectOptions = useMemo(() => {
    const merged = [...DEFAULT_SUBJECT_PRESETS];
    for (const p of presets) {
      if (!merged.some((m) => m.subject === p.subject)) merged.push(p);
    }
    return merged.sort((a, b) => a.subject.localeCompare(b.subject));
  }, [presets]);

  const filteredList = useMemo(() => {
    if (subjectFilter === "All") return list;
    return list.filter((s) => s.subject === subjectFilter);
  }, [list, subjectFilter]);

  const loadList = async () => {
    const data = await api.syllabi();
    setList(data.syllabi || []);
    if (data.subjectPresets?.length) setPresets(data.subjectPresets);
    return data.syllabi || [];
  };

  useEffect(() => {
    let alive = true;
    loadList()
      .then((syllabi) => {
        if (!alive) return;
        if (syllabi.length && !selectedId) setSelectedId(syllabi[0].id);
      })
      .catch((e) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let alive = true;
    api
      .syllabus(selectedId)
      .then((d) => alive && setDetail(d))
      .catch((e) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [selectedId]);

  useEffect(() => {
    if (!filteredList.length) return;
    if (!filteredList.some((s) => s.id === selectedId)) {
      setSelectedId(filteredList[0].id);
    }
  }, [filteredList, selectedId]);

  const applySubjectPreset = (subject) => {
    const preset = subjectOptions.find((p) => p.subject === subject);
    setForm((f) => ({
      ...f,
      subject,
      code: preset?.code || f.code,
      title: preset?.defaultTitle || f.title,
    }));
  };

  const onAdd = async () => {
    setBusy(true);
    setError("");
    setBusyTitle("Creating syllabus");
    setBusyMessage(`Saving ${form.subject} ${form.code} · ${form.year}…`);
    try {
      const created = await api.createSyllabus({
        subject: form.subject,
        code: form.code,
        year: Number(form.year),
        title: form.title || undefined,
      });
      if (pdfFile) {
        setBusyTitle("Parsing syllabus PDF");
        setBusyMessage("Extracting sections, topics, subtopics, and (a)/(i) objectives…");
        await api.importSyllabusPdf(created.id, pdfFile);
      }
      await loadList();
      setSelectedId(created.id);
      setSubjectFilter(created.subject || form.subject);
      setShowAdd(false);
      setPdfFile(null);
      const refreshed = await api.syllabus(created.id);
      setDetail(refreshed);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
      setBusyTitle("");
      setBusyMessage("");
    }
  };

  const onImportPdf = async (file) => {
    if (!selectedId || !file) return;
    setBusy(true);
    setError("");
    setBusyTitle("Refreshing syllabus from PDF");
    setBusyMessage("Overwriting existing topics with the newly parsed structure…");
    try {
      const result = await api.importSyllabusPdf(selectedId, file);
      setDetail(result.syllabus);
      await loadList();
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
      setBusyTitle("");
      setBusyMessage("");
    }
  };

  const tree = detail?.tree || [];

  const historyItems = list.map((s) => ({
    id: s.id,
    title: `${s.subject} ${s.code} · ${s.year}`,
    meta: `${s.title || ""} · ${s.topicCount || 0} topics · ${s.subtopicCount || 0} subtopics · ${s.outcomeCount || 0} objectives`,
    status: (s.topicCount || 0) > 0 ? "LOADED" : "EMPTY",
    when: s.updatedAt ? new Date(s.updatedAt).toLocaleString() : "",
    action: (
      <button type="button" className="btn" onClick={() => { setSelectedId(s.id); setSubjectFilter(s.subject); }}>
        View
      </button>
    ),
  }));

  return (
    <div className="page relative-page">
      <LoadingOverlay show={busy} title={busyTitle || "Working…"} message={busyMessage} />

      <header className="page-header">
        <div>
          <h1>Syllabus</h1>
          <p className="subtitle">
            Browse topics, subtopics, and learning objectives by subject.
          </p>
        </div>
        <div className="syllabus-header-actions">
          <label className="subject-dropdown">
            <span className="muted">Subject</span>
            <select
              value={subjectFilter}
              onChange={(e) => {
                const value = e.target.value;
                setSubjectFilter(value);
                if (value !== "All") applySubjectPreset(value);
              }}
              aria-label="Filter by subject"
            >
              <option value="All">All subjects</option>
              {subjectOptions.map((p) => (
                <option key={p.subject} value={p.subject}>
                  {p.subject}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="btn primary"
            disabled={busy}
            onClick={() => {
              if (subjectFilter !== "All") applySubjectPreset(subjectFilter);
              setShowAdd(true);
            }}
          >
            + Add syllabus
          </button>
        </div>
      </header>

      <BusyBanner show={busy} title={busyTitle || "Working…"} message={busyMessage} />
      {error ? <div className="error-banner">{error}</div> : null}

      {showAdd ? (
        <section className="panel form-grid">
          <label>
            Subject
            <select value={form.subject} onChange={(e) => applySubjectPreset(e.target.value)} disabled={busy}>
              {subjectOptions.map((p) => (
                <option key={p.subject} value={p.subject}>
                  {p.subject}
                </option>
              ))}
            </select>
          </label>
          <label>
            Syllabus code
            <input value={form.code} disabled={busy} onChange={(e) => setForm((f) => ({ ...f, code: e.target.value }))} />
          </label>
          <label>
            Year
            <input
              value={form.year}
              disabled={busy}
              onChange={(e) => setForm((f) => ({ ...f, year: e.target.value }))}
              inputMode="numeric"
            />
          </label>
          <label className="span-2">
            Title
            <input
              value={form.title}
              disabled={busy}
              onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
              placeholder="Display title"
            />
          </label>
          <div className="span-2">
            <UploadZone
              badge="Optional"
              title="Syllabus PDF"
              hint="Drop the official syllabus PDF to populate Section → Topic → Subtopic → (a) → (i)"
              file={pdfFile}
              disabled={busy}
              onFile={setPdfFile}
              onClear={() => setPdfFile(null)}
            />
          </div>
          <div className="actions-row span-2">
            <button type="button" className="btn" disabled={busy} onClick={() => setShowAdd(false)}>
              Cancel
            </button>
            <button type="button" className="btn primary" disabled={busy} onClick={onAdd}>
              {busy ? "Working…" : `Add ${form.subject} syllabus`}
            </button>
          </div>
        </section>
      ) : null}

      {!filteredList.length && !showAdd ? (
        <EmptyState title={list.length ? `No ${subjectFilter} syllabi yet.` : "No syllabi in the database yet."}>
          Use the <strong>Subject</strong> dropdown, then <strong>+ Add syllabus</strong>.
        </EmptyState>
      ) : (
        <div className="syllabus-layout">
          <aside className="panel syllabus-list">
            <div className="label">
              {subjectFilter === "All" ? "Subjects" : subjectFilter}
            </div>
            {filteredList.map((s) => (
              <button
                key={s.id}
                type="button"
                className={`syllabus-item ${selectedId === s.id ? "active" : ""}`}
                onClick={() => setSelectedId(s.id)}
              >
                <strong>
                  {s.subject} {s.code}
                </strong>
                <span className="muted">
                  {s.year} · {s.topicCount || 0} topics · {s.subtopicCount || 0} subtopics
                </span>
              </button>
            ))}
          </aside>

          <section className="panel syllabus-detail">
            {!detail ? (
              <p className="muted">Select a syllabus to view its structure.</p>
            ) : (
              <>
                <div className="syllabus-detail-header">
                  <div>
                    <h2>
                      {detail.subject} {detail.code} · {detail.year}
                    </h2>
                    <p className="muted">{detail.title}</p>
                    <p className="muted tiny">
                      {detail.topicCount || 0} topics · {detail.subtopicCount || 0} subtopics ·{" "}
                      {detail.outcomeCount || 0} learning objectives
                    </p>
                  </div>
                </div>

                <div className="syllabus-refresh-box">
                  <div>
                    <strong>Import / refresh PDF</strong>
                    <p className="muted tiny">
                      Re-uploading overwrites the existing topic tree for this syllabus.
                    </p>
                  </div>
                  <UploadZone
                    badge="Overwrite"
                    title="Syllabus PDF"
                    hint="Drop a PDF to refresh topics and objectives"
                    file={null}
                    disabled={busy}
                    onFile={(file) => file && onImportPdf(file)}
                    onClear={() => {}}
                  />
                </div>

                {!tree.length ? (
                  <EmptyState title="No topics loaded yet.">
                    Import a syllabus PDF to populate topics, subtopics, and learning objectives.
                  </EmptyState>
                ) : (
                  <div className="syllabus-tree">
                    {tree.map((node) => (
                      <SyllabusNodeView key={node.id} node={node} depth={0} />
                    ))}
                  </div>
                )}
              </>
            )}
          </section>
        </div>
      )}

      <HistoryPanel
        title="Syllabus history"
        empty="No syllabus imports yet."
        items={historyItems}
      />
    </div>
  );
}

function SyllabusNodeView({ node, depth }) {
  const [open, setOpen] = useState(depth < 2);
  const type = node.nodeType || "";
  const hasChildren = (node.children || []).length > 0;
  const isOutcome = type === "OUTCOME";
  const isSection = type === "SECTION";
  const letterLabel = outcomeLetter(node.code);
  const description = node.outcomesText || node.outcomes_text || "";

  return (
    <div className={`syl-node syl-${String(type).toLowerCase()}`} style={{ marginLeft: depth ? 0.85 : 0 }}>
      <div className="syl-node-row">
        {hasChildren || (isSection && description) ? (
          <button type="button" className="syl-toggle" onClick={() => setOpen((v) => !v)} aria-label="Toggle">
            {open ? "▾" : "▸"}
          </button>
        ) : (
          <span className="syl-toggle spacer" />
        )}
        <span className={`syl-type type-${String(type).toLowerCase()}`}>{labelForType(type, node.code)}</span>
        {!isOutcome && !isSection && node.code ? <span className="syl-code">{node.code}</span> : null}
        {isOutcome && letterLabel ? <span className="syl-code">({letterLabel})</span> : null}
        <span className="syl-title">{node.title}</span>
      </div>
      {open && isSection && description ? (
        <p className="syl-section-desc muted">{description}</p>
      ) : null}
      {open && hasChildren
        ? node.children.map((child) => <SyllabusNodeView key={child.id} node={child} depth={depth + 1} />)
        : null}
    </div>
  );
}

function outcomeLetter(code) {
  if (!code) return null;
  const parts = String(code).split(".");
  return parts[parts.length - 1] || null;
}

function labelForType(type, code) {
  if (type === "SECTION") return "Section";
  if (type === "TOPIC") return "Topic";
  if (type === "SUBTOPIC") return "Subtopic";
  if (type === "OUTCOME") {
    const letter = outcomeLetter(code);
    if (letter && /^[a-z]$/i.test(letter)) return "Letter";
    if (letter && /^(i|ii|iii|iv|v|vi|vii|viii|ix|x)$/i.test(letter)) return "Sub";
    return "Objective";
  }
  return type || "Node";
}
