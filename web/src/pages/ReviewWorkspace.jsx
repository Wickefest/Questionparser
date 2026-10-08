import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { useRouter } from "../lib/router";
import { QuestionRenderer } from "../components/QuestionRenderer";
import { RichHtml } from "../components/Rich";
import { SourcePreview } from "../components/SourcePreview";
import { StatusBadge } from "../components/ui";
import { LoadingOverlay } from "../components/LoadingOverlay";
import { upgradeNumberedLists } from "../lib/normalize";

const REJECT_REASONS = [
  "Source parsed incorrectly",
  "Visual incomplete",
  "Question boundary incorrect",
  "Answer mismatch",
  "Duplicate",
  "Other",
];

/** Match mark-scheme rows to nav labels: a(i), (a)(i), ai → a(i). */
function normalizePartLabel(label) {
  const raw = String(label || "")
    .toLowerCase()
    .replace(/\s+/g, "");
  const m = raw.match(/^\(?([a-z])\)?\(?([ivxlc]+)\)?$/);
  if (m && m[2]) return `${m[1]}(${m[2]})`;
  const letter = raw.match(/^\(?([a-z])\)?$/);
  if (letter) return letter[1];
  const compact = raw.replace(/[^a-z]/g, "");
  const c = compact.match(/^([a-z])([ivxlc]+)$/);
  if (c) return `${c[1]}(${c[2]})`;
  return compact || raw;
}

export function ReviewWorkspace({ batchId, draftId }) {
  const { navigate } = useRouter();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editContent, setEditContent] = useState([]);
  const [editOptions, setEditOptions] = useState(null);
  const [editAnswerValue, setEditAnswerValue] = useState("A");
  const [editExplanation, setEditExplanation] = useState("");
  const [editPartsAnswer, setEditPartsAnswer] = useState([]);
  const [editQuestionRef, setEditQuestionRef] = useState("");
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState(REJECT_REASONS[0]);
  const [partIndex, setPartIndex] = useState(0);

  const load = () =>
    api
      .draft(batchId, draftId)
      .then((d) => {
        setData(d);
        setPartIndex(0);
        setEditContent(upgradeNumberedLists(structuredClone(d.draft.content || [])));
        setEditOptions(structuredClone(d.draft.options || null));
        setEditAnswerValue(d.draft.answer?.value || "");
        setEditExplanation(d.draft.answer?.explanation || "");
        setEditPartsAnswer(
          structuredClone(d.draft.answer?.partsAnswer || d.draft.answer?.parts_answer || [])
        );
        setEditQuestionRef(d.draft.questionRef || "");
      })
      .catch((e) => setError(e.message));

  useEffect(() => {
    setError("");
    load();
  }, [batchId, draftId]);

  const draft = data?.draft;
  const pos = data?.position;

  const structuredNav = useMemo(() => {
    const opts = draft?.options;
    if (!opts || opts.mode !== "structured") return [];
    const skipShell = (p) => {
      const prompt = String(p?.prompt || "").trim();
      const hasKids = Boolean(p?.hasChildren || p?.children?.length);
      const hasVisual = Boolean((p?.visuals || []).length);
      return !((hasKids && !prompt && !hasVisual) || p?.sectionOnly);
    };
    if (Array.isArray(opts.navigationOrder) && opts.navigationOrder.length) {
      return opts.navigationOrder.filter(skipShell);
    }
    const flatten = (parts, parent = "") => {
      const out = [];
      for (const part of parts || []) {
        const label = parent ? `${parent}(${part.label})` : part.label;
        const prompt = part.prompt || "";
        const hasKids = Boolean(part.children?.length);
        // Skip empty letter shells — start at (a)(i).
        if (!(hasKids && !String(prompt).trim() && !(part.visuals || []).length)) {
          out.push({
            label,
            prompt,
            marks: part.marks,
            responseKind: part.responseKind || "text",
            visuals: part.visuals || [],
          });
        }
        if (hasKids) out.push(...flatten(part.children, label));
      }
      return out;
    };
    return flatten(opts.parts || []);
  }, [draft]);

  const isStructured = structuredNav.length > 0;
  const activePart = isStructured ? structuredNav[Math.min(partIndex, structuredNav.length - 1)] : null;
  const isStructuredEdit = editOptions?.mode === "structured";

  /** Flat navigable labels from the LIVE edit state (not the saved draft),
   * so the answer editor's rows stay in sync as parts are added/renamed. */
  const editPartLabels = useMemo(() => {
    if (!isStructuredEdit) return [];
    const out = [];
    // Only leaf parts (no children) are answerable — a part like "c" that
    // just leads into "c(i)"/"c(ii)" is a statement, not a graded part, so
    // it gets no accepted-answers row of its own.
    const walk = (parts, parent = "") => {
      for (const part of parts || []) {
        const label = parent ? `${parent}(${part.label})` : part.label;
        const hasKids = Boolean(part.children?.length);
        if (hasKids) {
          walk(part.children, label);
        } else {
          out.push(label);
        }
      }
    };
    walk(editOptions.parts || []);
    return out;
  }, [isStructuredEdit, editOptions]);

  const go = (id) => {
    if (!id) navigate(`/admin/question-bank/imports/${batchId}/review`);
    else navigate(`/admin/question-bank/imports/${batchId}/review/${id}`);
  };

  const goPrevPartOrDraft = () => {
    if (isStructured && partIndex > 0) {
      setPartIndex((i) => i - 1);
      return;
    }
    go(pos?.prevId);
  };

  const goNextPartOrDraft = () => {
    if (isStructured && partIndex < structuredNav.length - 1) {
      setPartIndex((i) => i + 1);
      return;
    }
    go(pos?.nextId);
  };

  const onApprove = async () => {
    setBusy(true);
    setError("");
    try {
      const result = await api.approveDraft(batchId, draftId);
      if (result.nextDraftId) go(result.nextDraftId);
      else navigate(`/admin/question-bank/imports/${batchId}/review`);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const onApproveAll = async () => {
    const count = pos?.total ?? null;
    const confirmed = window.confirm(
      count
        ? `Approve all remaining unresolved questions in this batch (up to ${count} total)? ` +
          `Questions flagged "requires review" (missing answer, needs a diagram, etc.) will be skipped automatically.`
        : `Approve every remaining unresolved question in this batch? ` +
          `Questions flagged "requires review" will be skipped automatically.`
    );
    if (!confirmed) return;
    setBusy(true);
    setError("");
    try {
      const result = await api.approveAllInBatch(batchId);
      const parts = [`Approved ${result.approvedCount} question(s).`];
      if (result.skippedFlaggedCount) {
        parts.push(`Skipped ${result.skippedFlaggedCount} flagged for review (open them individually).`);
      }
      if (result.failedCount) {
        parts.push(`${result.failedCount} failed — see console for details.`);
        console.error("approve-all failures:", result.failed);
      }
      window.alert(parts.join(" "));
      navigate(`/admin/question-bank/imports/${batchId}/review`);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const onReject = async () => {
    setBusy(true);
    try {
      await api.rejectDraft(batchId, draftId, rejectReason);
      setRejectOpen(false);
      if (pos?.nextId) go(pos.nextId);
      else navigate(`/admin/question-bank/imports/${batchId}/review`);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const onSaveEdit = async ({ approve = false } = {}) => {
    setBusy(true);
    setError("");
    try {
      const body = {
        content: editContent,
        options: editOptions,
        comment: "Edited in review workspace",
      };
      if (isStructuredEdit) {
        body.partsAnswer = editPartsAnswer;
      } else {
        body.answerValue = editAnswerValue || undefined;
        body.explanation = editExplanation;
      }
      if (editQuestionRef && editQuestionRef !== draft.questionRef) {
        body.questionRef = editQuestionRef;
      }
      await api.editDraft(batchId, draftId, body);
      setEditing(false);
      if (approve) {
        const result = await api.approveDraft(batchId, draftId);
        if (result.nextDraftId) go(result.nextDraftId);
        else navigate(`/admin/question-bank/imports/${batchId}/review`);
        return;
      }
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const onReplaceAsset = async (
    assetId,
    file,
    { contentIndex = null, optionsVisual = false, addBlock = false, insertAt = null } = {}
  ) => {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      let result;
      let nextAssetId = assetId;
      let relinked = false;
      if (assetId) {
        try {
          result = await api.replaceDraftAsset(batchId, draftId, assetId, file);
        } catch {
          result = await api.uploadGeneralAsset(file);
          nextAssetId = result.assetId;
          relinked = true;
        }
      } else {
        result = await api.uploadGeneralAsset(file);
        nextAssetId = result.assetId;
        relinked = true;
      }

      const bustUrl = `${result.url}?t=${Date.now()}`;
      let nextContent = editContent;
      let nextOptions = editOptions;

      if (insertAt != null) {
        // Slot a new image block in between existing blocks, matching where
        // the diagram actually sits in the source PDF (e.g. right after the
        // stem sentence that refers to "Fig 7.1 shown below").
        nextContent = [
          ...editContent.slice(0, insertAt),
          { type: "image", asset_id: nextAssetId },
          ...editContent.slice(insertAt),
        ];
        setEditContent(nextContent);
      } else if (contentIndex != null) {
        if (addBlock) {
          nextContent = [
            ...editContent,
            { type: "image", asset_id: nextAssetId },
          ];
        } else {
          nextContent = editContent.map((b, idx) =>
            idx === contentIndex
              ? { ...b, type: "image", asset_id: nextAssetId, asset: undefined }
              : b
          );
        }
        setEditContent(nextContent);
      }
      if (optionsVisual) {
        nextOptions = editOptions
          ? { ...editOptions, asset_id: nextAssetId, asset: undefined }
          : { mode: "composite_visual", labels: ["A", "B", "C", "D"], asset_id: nextAssetId };
        setEditOptions(nextOptions);
      }

      setData((prev) => {
        if (!prev?.draft) return prev;
        const urls = { ...(prev.draft.assetUrls || {}) };
        if (assetId && assetId !== nextAssetId) {
          delete urls[assetId];
        }
        urls[nextAssetId] = bustUrl;
        return { ...prev, draft: { ...prev.draft, assetUrls: urls } };
      });

      if (relinked) {
        await api.editDraft(batchId, draftId, {
          content: nextContent,
          options: nextOptions,
          comment: "Re-linked replaced image asset",
        });
        await load();
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  /** Generic upload for a part-level or answer-level visual: upload the file,
   * register the returned asset in draft.assetUrls for immediate preview,
   * then hand the assetId to the caller to store wherever it belongs
   * (a question part's `visuals`, or an answer part's `visuals`). */
  const uploadVisualAsset = async (file, onDone) => {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const result = await api.uploadGeneralAsset(file);
      if (!result.success) throw new Error("Upload failed");
      setData((prev) => {
        if (!prev?.draft) return prev;
        const urls = { ...(prev.draft.assetUrls || {}), [result.assetId]: `${result.url}?t=${Date.now()}` };
        return { ...prev, draft: { ...prev.draft, assetUrls: urls } };
      });
      onDone(result.assetId);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };
  const onUploadPartVisual = (file, onDone) => uploadVisualAsset(file, onDone);
  const onUploadAnswerVisual = (file, onDone) => uploadVisualAsset(file, onDone);

  const issues = useMemo(() => {
    const raw = draft?.validationIssues || [];
    if (!Array.isArray(raw)) return [];
    return raw;
  }, [draft]);

  if (error && !data) return <div className="page error-banner">{error}</div>;
  if (!data || !draft) return <div className="page muted">Loading review workspace…</div>;

  const canApprove =
    !busy && draft.status !== "APPROVED" && draft.status !== "REJECTED" && !!draft.answer;

  return (
    <div className="review-workspace">
      <LoadingOverlay show={busy} title="Updating draft..." message="Please wait while we process this change." />
      <header className="review-topbar">
        <button
          type="button"
          className="btn linkish"
          onClick={() => navigate(`/admin/question-bank/imports/${batchId}/review`)}
        >
          ← Back to Review Queue
        </button>
        <div className="review-pos">
          {isStructured ? (
            <>
              Q{draft.questionRef} · part {partIndex + 1} of {structuredNav.length}
              {activePart ? ` · (${activePart.label})` : ""}
            </>
          ) : (
            <>
              Question {pos?.index} of {pos?.total}
            </>
          )}
        </div>
        <StatusBadge status={draft.status} />
        <div className="review-nav">
          <button
            type="button"
            className="btn"
            disabled={isStructured ? partIndex <= 0 && !pos?.prevId : !pos?.prevId}
            onClick={goPrevPartOrDraft}
          >
            Previous
          </button>
          <button
            type="button"
            className="btn"
            disabled={
              isStructured
                ? partIndex >= structuredNav.length - 1 && !pos?.nextId
                : !pos?.nextId
            }
            onClick={goNextPartOrDraft}
          >
            Next
          </button>
        </div>
      </header>

      {error ? <div className="error-banner">{error}</div> : null}

      <div className="review-columns">
        <SourcePreview
          url={data.sourceDocument?.url}
          page={draft.sourcePage || 1}
          title="ORIGINAL SOURCE"
          missing={!data.sourceDocument}
        />
        <section className="parsed-pane panel">
          <div className="label">{editing ? "LIVE EDIT PREVIEW" : "PARSED QUESTION"}</div>
          <div className="q-ref">Q{draft.questionRef}</div>
          {draft.classification ? (
            <div className="badge-row" style={{ marginBottom: 12 }}>
              <span className="topic-badge">
                🏷️ Topic {draft.classification.topicCode}: {draft.classification.title}
              </span>
              {draft.classification.subtopicTitle ? (
                <span className="topic-badge subtopic-badge">
                  ↳ {draft.classification.subtopicCode}: {draft.classification.subtopicTitle}
                </span>
              ) : null}
            </div>
          ) : null}
          <QuestionRenderer
            content={editing ? editContent : draft.content}
            options={editing ? editOptions : draft.options}
            assetUrls={draft.assetUrls || {}}
            correct={editing ? editAnswerValue : draft.answer?.value}
            partsAnswer={draft.answer?.partsAnswer || draft.answer?.parts_answer}
            activePartLabel={activePart?.label || null}
            revealAnswer
          />
          {isStructured ? (
            <p className="tiny muted" style={{ marginTop: 8 }}>
              Stem and diagrams stay fixed. Next/Previous moves through parts in order:
              (a)(i) → (a)(ii) → (b) → …
            </p>
          ) : null}
          {editing ? (
            <p className="tiny muted" style={{ marginTop: 8 }}>
              Type in the right panel — this preview updates as you edit. Scroll to the bottom of
              that panel for <strong>Save Changes</strong>.
            </p>
          ) : null}
        </section>
      </div>

      <section className="review-footer panel">
        <div className="footer-grid">
          <div>
            <div className="label">Official Answer</div>
            <div className="answer-value">
              {isStructured
                ? activePart
                  ? `(${activePart.label})`
                  : "Parts"
                : draft.answer?.value || "—"}
            </div>
            {isStructured ? (
              <div className="structured-answer-part" style={{ marginTop: 8 }}>
                {(draft.answer?.partsAnswer || draft.answer?.parts_answer || [])
                  .filter((p) =>
                    activePart
                      ? normalizePartLabel(p.label) === normalizePartLabel(activePart.label)
                      : true
                  )
                  .slice(0, activePart ? 3 : 0)
                  .map((p, i) => (
                    <div key={i}>
                      {((p.accepted_answers || p.acceptedAnswers || [])[0] && (
                        <RichHtml text={String((p.accepted_answers || p.acceptedAnswers)[0])} />
                      )) || <span className="muted">No mark-scheme line for this part</span>}
                    </div>
                  ))}
              </div>
            ) : (
              <>
                <div className="label">Official explanation</div>
                <p className="muted">
                  {draft.answer?.explanation || "No official explanation provided."}
                </p>
              </>
            )}
            {!isStructured ? (
              <p className="tiny muted">Mark-scheme explanation only — not AI-generated. Use Edit Question to change it.</p>
            ) : (
              <p className="tiny muted">Per-part mark scheme is shown under the question as you step through parts.</p>
            )}
          </div>
          <div>
            <div className="label">Validation</div>
            <ul className="validation-list">
              {issues.length === 0 ? (
                <li className="ok">✓ No validation issues recorded</li>
              ) : (
                issues.map((issue, i) => (
                  <li key={i} className="warn">
                    ⚠ {issue.message || issue.code || JSON.stringify(issue)}
                  </li>
                ))
              )}
            </ul>
          </div>
        </div>
        <div className="actions-row review-actions">
          <button type="button" className="btn danger" disabled={busy} onClick={() => setRejectOpen(true)}>
            Reject
          </button>
          <button type="button" className="btn" disabled={busy} onClick={() => setEditing(true)}>
            Edit Question
          </button>
          <button type="button" className="btn primary" disabled={!canApprove} onClick={onApprove}>
            Approve & Next
          </button>
          <button type="button" className="btn" disabled={busy} onClick={onApproveAll}>
            Approve All in Batch
          </button>
        </div>
      </section>

      {editing ? (
        <div className="drawer-backdrop" onClick={() => setEditing(false)}>
          <aside className="drawer" onClick={(e) => e.stopPropagation()}>
            <div className="drawer-body">
            <h3>Edit Question</h3>
            <p className="muted">
              Rewrite the stem and options to match the PDF on the left. For flowchart / diagram
              questions, use <strong>+ Add picture</strong> (screenshot/crop the figure), then
              keep only a short prompt in the text block. Save is pinned at the bottom.
            </p>
            <label className="edit-field" style={{ maxWidth: 220, marginBottom: 12 }}>
              Question number
              <input
                value={editQuestionRef}
                onChange={(e) => setEditQuestionRef(e.target.value)}
                disabled={busy}
                placeholder="e.g. 7 or 7(a)"
              />
            </label>
            <p className="tiny muted" style={{ marginTop: -8, marginBottom: 12 }}>
              Renumbering also moves the paired mark-scheme answer, so they stay linked.
            </p>
            {(editContent || []).map((block, i) => {
              const assetId = block.asset_id || (isUuid(block.asset) ? block.asset : null);
              return (
              <BlockEditor
                key={i}
                index={i}
                block={block}
                assetUrl={
                  assetId && draft.assetUrls ? draft.assetUrls[assetId] : null
                }
                onChange={(next) =>
                  setEditContent((prev) => prev.map((b, idx) => (idx === i ? next : b)))
                }
                onRemove={() =>
                  setEditContent((prev) => prev.filter((_, idx) => idx !== i))
                }
                onReplaceImage={(file) => onReplaceAsset(assetId, file, { contentIndex: i })}
                onInsertImageAfter={(file) => onReplaceAsset(null, file, { insertAt: i + 1 })}
                busy={busy}
              />
              );
            })}
            <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginBottom: "12px" }}>
              <label className="btn secondary" style={{ cursor: "pointer" }}>
                + Add picture
                <input
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  hidden
                  disabled={busy}
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) onReplaceAsset(null, file, { contentIndex: editContent.length, addBlock: true });
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
            {isStructuredEdit ? (
              <StructuredPartsEditor
                parts={editOptions.parts || []}
                onChange={(parts) => setEditOptions((opts) => ({ ...opts, parts }))}
                assetUrls={draft.assetUrls || {}}
                onUploadVisual={(file, onDone) => onUploadPartVisual(file, onDone)}
                busy={busy}
              />
            ) : null}
            {isStructuredEdit ? (
              <StructuredAnswerEditor
                labels={editPartLabels}
                parts={editPartsAnswer}
                onChange={setEditPartsAnswer}
                assetUrls={draft.assetUrls || {}}
                onUploadVisual={(file, onDone) => onUploadAnswerVisual(file, onDone)}
                busy={busy}
              />
            ) : null}
            {!isStructuredEdit && (editOptions?.asset_id || editOptions?.mode === "composite_visual" || editOptions?.mode === "mixed") ? (
              <div className="edit-block">
                <div className="label">OPTIONS VISUAL</div>
                {(editOptions?.asset_id && draft.assetUrls?.[editOptions.asset_id]) ? (
                  <img
                    src={draft.assetUrls[editOptions.asset_id]}
                    alt="Options"
                    className="asset-img edit-preview"
                  />
                ) : (
                  <div className="muted">No options image yet — upload one.</div>
                )}
                <label className="btn">
                  {editOptions?.asset_id ? "Replace options image" : "Upload options image"}
                  <input
                    type="file"
                    accept="image/png,image/jpeg,image/webp"
                    hidden
                    disabled={busy}
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) onReplaceAsset(editOptions?.asset_id, file, { optionsVisual: true });
                      e.target.value = "";
                    }}
                  />
                </label>
                {editOptions?.asset_id ? (
                  <button
                    type="button"
                    className="btn"
                    disabled={busy}
                    onClick={() =>
                      setEditOptions((opts) => {
                        if (!opts) return opts;
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
                      })
                    }
                  >
                    Remove options image
                  </button>
                ) : null}
              </div>
            ) : null}
            {!isStructuredEdit && (editOptions?.mode === "text" || editOptions?.mode === "math" || editOptions?.mode === "mixed") ? (
              <div className="edit-block">
                <div className="label">OPTIONS ({editOptions.mode})</div>
                {(editOptions.items || []).map((item, i) => (
                  <label key={item.label} className="edit-field">
                    {item.label}
                    <textarea
                      rows={2}
                      value={item.content || item.latex || ""}
                      onChange={(e) => {
                        const value = e.target.value;
                        setEditOptions((opts) => ({
                          ...opts,
                          items: opts.items.map((it, idx) =>
                            idx === i
                              ? item.latex != null
                                ? { ...it, latex: value }
                                : { ...it, content: value }
                              : it
                          ),
                        }));
                      }}
                    />
                  </label>
                ))}
              </div>
            ) : null}
            {!isStructuredEdit ? (
              <div className="edit-block" style={{ marginTop: 8 }}>
                <div className="label">OFFICIAL ANSWER</div>
                <label className="edit-field">
                  Correct option
                  <select
                    value={editAnswerValue}
                    onChange={(e) => setEditAnswerValue(e.target.value)}
                    disabled={busy}
                    style={{ maxWidth: 100 }}
                  >
                    {["A", "B", "C", "D"].map((l) => (
                      <option key={l} value={l}>
                        {l}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="edit-field">
                  Explanation (optional)
                  <textarea
                    rows={3}
                    value={editExplanation}
                    onChange={(e) => setEditExplanation(e.target.value)}
                    placeholder="Official mark-scheme explanation"
                    disabled={busy}
                  />
                </label>
              </div>
            ) : null}
            </div>
            <div className="drawer-footer">
              <div className="actions-row">
                <button type="button" className="btn" onClick={() => setEditing(false)}>
                  Cancel
                </button>
                <button type="button" className="btn" disabled={busy} onClick={() => onSaveEdit()}>
                  Save Changes
                </button>
                <button
                  type="button"
                  className="btn primary"
                  disabled={!canApprove}
                  onClick={() => onSaveEdit({ approve: true })}
                >
                  Save &amp; Approve
                </button>
              </div>
            </div>
          </aside>
        </div>
      ) : null}

      {rejectOpen ? (
        <div className="drawer-backdrop" onClick={() => setRejectOpen(false)}>
          <aside className="drawer narrow" onClick={(e) => e.stopPropagation()}>
            <h3>Reject draft</h3>
            <p className="muted">Rejection is not deletion. The draft remains in the database.</p>
            <label className="edit-field">
              Reason
              <select value={rejectReason} onChange={(e) => setRejectReason(e.target.value)}>
                {REJECT_REASONS.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </label>
            <div className="actions-row">
              <button type="button" className="btn" onClick={() => setRejectOpen(false)}>
                Cancel
              </button>
              <button type="button" className="btn danger" disabled={busy} onClick={onReject}>
                Confirm Reject
              </button>
            </div>
          </aside>
        </div>
      ) : null}
    </div>
  );
}

const RESPONSE_KINDS = ["text", "calculation", "table", "diagram", "structure", "image"];

/** Recursive editor for a structured question's parts/sub-parts tree.
 * Each part chooses a responseKind — this is "what kind of answer box" the
 * part expects (free text, a calculation, a table, or a drawing/diagram the
 * student/marker attaches as an image) — and can carry its own visuals
 * (e.g. a table or figure printed as part of that sub-question). */
function StructuredPartsEditor({ parts, onChange, assetUrls, onUploadVisual, busy }) {
  const updateAt = (path, updater) => {
    const next = structuredClone(parts);
    let list = next;
    for (let i = 0; i < path.length - 1; i++) {
      list = list[path[i]].children;
    }
    list[path[path.length - 1]] = updater(list[path[path.length - 1]]);
    onChange(next);
  };
  const removeAt = (path) => {
    const next = structuredClone(parts);
    let list = next;
    for (let i = 0; i < path.length - 1; i++) {
      list = list[path[i]].children;
    }
    list.splice(path[path.length - 1], 1);
    onChange(next);
  };
  const addChildAt = (path) => {
    const next = structuredClone(parts);
    let list = next;
    for (const idx of path) {
      list = list[idx].children || (list[idx].children = []);
    }
    list.push({ label: "i", prompt: "", marks: null, responseKind: "text", visuals: [], children: [] });
    onChange(next);
  };

  const renderPart = (part, path) => (
    <div
      key={path.join(".")}
      className="edit-block"
      style={{ marginLeft: path.length > 1 ? 20 : 0, borderLeft: path.length > 1 ? "2px solid var(--line)" : undefined }}
    >
      <div className="label" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <span>PART</span>
        <button
          type="button"
          className="btn"
          style={{ padding: "2px 8px", fontSize: "0.75rem" }}
          disabled={busy}
          onClick={() => removeAt(path)}
        >
          Remove part
        </button>
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <label className="edit-field" style={{ maxWidth: 90 }}>
          Label
          <input
            value={part.label || ""}
            disabled={busy}
            onChange={(e) => updateAt(path, (p) => ({ ...p, label: e.target.value }))}
          />
        </label>
        <label className="edit-field" style={{ maxWidth: 110 }}>
          Marks
          <input
            type="number"
            min={0}
            value={part.marks ?? ""}
            disabled={busy}
            onChange={(e) =>
              updateAt(path, (p) => ({ ...p, marks: e.target.value === "" ? null : Number(e.target.value) }))
            }
          />
        </label>
        <label className="edit-field" style={{ maxWidth: 160 }}>
          Answer type
          <select
            value={part.responseKind || "text"}
            disabled={busy}
            onChange={(e) => updateAt(path, (p) => ({ ...p, responseKind: e.target.value }))}
          >
            {RESPONSE_KINDS.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className="edit-field">
        Prompt (what the student is asked)
        <textarea
          rows={2}
          value={part.prompt || ""}
          disabled={busy}
          onChange={(e) => updateAt(path, (p) => ({ ...p, prompt: e.target.value }))}
          placeholder="e.g. Explain why substance W has high melting and boiling points."
        />
      </label>
      {part.prompt ? (
        <div className="latex-preview">
          <RichHtml text={part.prompt} />
        </div>
      ) : null}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 8 }}>
        {(part.visuals || []).map((v, vi) => {
          const assetId = v.asset_id || v.assetId || v.asset;
          const url = assetId ? assetUrls[assetId] : null;
          return (
            <div key={vi} className="edit-image-block" style={{ maxWidth: 160 }}>
              {url ? <img src={url} alt="Part visual" className="asset-img edit-preview" /> : <div className="muted tiny">no preview</div>}
              <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                <label className="btn" style={{ padding: "2px 8px", fontSize: "0.7rem", cursor: "pointer" }}>
                  Replace image
                  <input
                    type="file"
                    accept="image/png,image/jpeg,image/webp"
                    hidden
                    disabled={busy}
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) {
                        onUploadVisual(file, (newAssetId) =>
                          updateAt(path, (p) => ({
                            ...p,
                            visuals: (p.visuals || []).map((existing, i) =>
                              i === vi ? { ...existing, asset_id: newAssetId } : existing
                            ),
                          }))
                        );
                      }
                      e.target.value = "";
                    }}
                  />
                </label>
                <button
                  type="button"
                  className="btn"
                  style={{ padding: "2px 8px", fontSize: "0.7rem" }}
                  disabled={busy}
                  onClick={() =>
                    updateAt(path, (p) => ({ ...p, visuals: (p.visuals || []).filter((_, i) => i !== vi) }))
                  }
                >
                  Remove image
                </button>
              </div>
            </div>
          );
        })}
        <label className="btn" style={{ cursor: "pointer" }}>
          + Attach diagram/table image
          <input
            type="file"
            accept="image/png,image/jpeg,image/webp"
            hidden
            disabled={busy}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) {
                onUploadVisual(file, (assetId) =>
                  updateAt(path, (p) => ({ ...p, visuals: [...(p.visuals || []), { role: "diagram", asset_id: assetId }] }))
                );
              }
              e.target.value = "";
            }}
          />
        </label>
      </div>
      {(part.children || []).map((child, ci) => renderPart(child, [...path, ci]))}
      <button
        type="button"
        className="btn secondary"
        style={{ marginTop: 8 }}
        disabled={busy}
        onClick={() => addChildAt(path)}
      >
        + Add sub-part (i)/(ii)…
      </button>
    </div>
  );

  return (
    <div className="edit-block" style={{ background: "#fbfcf9" }}>
      <div className="label">QUESTION PARTS</div>
      <p className="tiny muted">
        Each part's "Answer type" decides what the student/marker fills in later: text = free-text box,
        calculation/table = structured working, diagram/structure/image = a drawing the student attaches
        as a photo.
      </p>
      {(parts || []).map((part, i) => renderPart(part, [i]))}
      <button
        type="button"
        className="btn"
        style={{ marginTop: 8 }}
        disabled={busy}
        onClick={() =>
          onChange([...(parts || []), { label: String.fromCharCode(97 + (parts || []).length), prompt: "", marks: null, responseKind: "text", visuals: [], children: [] }])
        }
      >
        + Add part (a)/(b)…
      </button>
    </div>
  );
}

/** Flat mark-scheme editor, one row per navigable question part label.
 * Deliberately mirrors StructuredPartsEditor's label set 1:1 so the human
 * reviewer edits the official answer right next to the question it grades —
 * the same shape the AI grader will read later, so nothing is left
 * ambiguous between "what was asked" and "what counts as correct". */
function StructuredAnswerEditor({ labels, parts, onChange, assetUrls, onUploadVisual, busy }) {
  const byLabel = new Map((parts || []).map((p) => [normalizePartLabel(p.label), p]));
  const orphans = (parts || []).filter((p) => !labels.some((l) => normalizePartLabel(l) === normalizePartLabel(p.label)));

  const updatePart = (label, updater) => {
    const key = normalizePartLabel(label);
    const existing = byLabel.get(key);
    const base = existing || { label, marks_max: null, accepted_answers: [], notes: null, ecf: false, visuals: [] };
    const updated = updater(base);
    const next = (parts || []).filter((p) => normalizePartLabel(p.label) !== key);
    next.push(updated);
    onChange(next);
  };

  const renderRow = (label) => {
    const part = byLabel.get(normalizePartLabel(label)) || {
      label,
      marks_max: null,
      accepted_answers: [],
      notes: null,
      ecf: false,
      visuals: [],
    };
    return (
      <div key={label} className="edit-block">
        <div className="label">({label})</div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
          <label className="edit-field" style={{ maxWidth: 110 }}>
            Marks
            <input
              type="number"
              min={0}
              value={part.marks_max ?? ""}
              disabled={busy}
              onChange={(e) =>
                updatePart(label, (p) => ({ ...p, marks_max: e.target.value === "" ? null : Number(e.target.value) }))
              }
            />
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "0.85rem" }}>
            <input
              type="checkbox"
              checked={Boolean(part.ecf)}
              disabled={busy}
              onChange={(e) => updatePart(label, (p) => ({ ...p, ecf: e.target.checked }))}
            />
            Error-carried-forward allowed
          </label>
        </div>
        <div className="label" style={{ marginTop: 8 }}>Accepted answers</div>
        {(part.accepted_answers || part.acceptedAnswers || []).map((ans, ai) => (
          <div key={ai} style={{ display: "flex", gap: 8, alignItems: "flex-start", marginBottom: 6 }}>
            <textarea
              rows={2}
              style={{ flex: 1 }}
              value={ans}
              disabled={busy}
              onChange={(e) =>
                updatePart(label, (p) => {
                  const list = [...(p.accepted_answers || p.acceptedAnswers || [])];
                  list[ai] = e.target.value;
                  return { ...p, accepted_answers: list };
                })
              }
            />
            <button
              type="button"
              className="btn"
              style={{ padding: "4px 8px", fontSize: "0.75rem" }}
              disabled={busy}
              onClick={() =>
                updatePart(label, (p) => ({
                  ...p,
                  accepted_answers: (p.accepted_answers || p.acceptedAnswers || []).filter((_, i) => i !== ai),
                }))
              }
            >
              Remove
            </button>
          </div>
        ))}
        <button
          type="button"
          className="btn secondary"
          style={{ marginBottom: 8 }}
          disabled={busy}
          onClick={() =>
            updatePart(label, (p) => ({ ...p, accepted_answers: [...(p.accepted_answers || p.acceptedAnswers || []), ""] }))
          }
        >
          + Add acceptable wording
        </button>
        <label className="edit-field">
          Notes (ECF rules, alternative units, examiner notes)
          <textarea
            rows={2}
            value={part.notes || ""}
            disabled={busy}
            onChange={(e) => updatePart(label, (p) => ({ ...p, notes: e.target.value }))}
          />
        </label>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 8 }}>
          {(part.visuals || []).map((v, vi) => {
            const assetId = v.asset_id || v.assetId || v.asset;
            const url = assetId ? assetUrls[assetId] : null;
            return (
              <div key={vi} className="edit-image-block" style={{ maxWidth: 160 }}>
                {url ? <img src={url} alt="Answer visual" className="asset-img edit-preview" /> : <div className="muted tiny">no preview</div>}
                <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                  <label className="btn" style={{ padding: "2px 8px", fontSize: "0.7rem", cursor: "pointer" }}>
                    Replace image
                    <input
                      type="file"
                      accept="image/png,image/jpeg,image/webp"
                      hidden
                      disabled={busy}
                      onChange={(e) => {
                        const file = e.target.files?.[0];
                        if (file) {
                          onUploadVisual(file, (newAssetId) =>
                            updatePart(label, (p) => ({
                              ...p,
                              visuals: (p.visuals || []).map((existing, i) =>
                                i === vi ? { ...existing, asset_id: newAssetId } : existing
                              ),
                            }))
                          );
                        }
                        e.target.value = "";
                      }}
                    />
                  </label>
                  <button
                    type="button"
                    className="btn"
                    style={{ padding: "2px 8px", fontSize: "0.7rem" }}
                    disabled={busy}
                    onClick={() =>
                      updatePart(label, (p) => ({ ...p, visuals: (p.visuals || []).filter((_, i) => i !== vi) }))
                    }
                  >
                    Remove image
                  </button>
                </div>
              </div>
            );
          })}
          <label className="btn" style={{ cursor: "pointer" }}>
            + Attach official diagram answer
            <input
              type="file"
              accept="image/png,image/jpeg,image/webp"
              hidden
              disabled={busy}
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) {
                  onUploadVisual(file, (assetId) =>
                    updatePart(label, (p) => ({ ...p, visuals: [...(p.visuals || []), { role: "structure", asset_id: assetId }] }))
                  );
                }
                e.target.value = "";
              }}
            />
          </label>
        </div>
      </div>
    );
  };

  return (
    <div className="edit-block" style={{ background: "#fbfcf9" }}>
      <div className="label">OFFICIAL ANSWER (mark scheme, one row per part)</div>
      <p className="tiny muted">
        This mirrors the question parts above by label, so the AI grader always has an unambiguous
        answer to check against. Attach a diagram here if the official answer is itself a drawing.
      </p>
      {labels.length === 0 ? (
        <p className="muted">Add question parts above first — the answer rows will appear here.</p>
      ) : (
        labels.map((label) => renderRow(label))
      )}
      {orphans.length ? (
        <div style={{ marginTop: 12, padding: 10, border: "1px dashed var(--review)", borderRadius: 8 }}>
          <p className="tiny" style={{ color: "var(--review)", margin: 0 }}>
            ⚠ These mark-scheme rows don't match any current question part label — likely the question
            parser missed that part's prompt. Add a matching part above, or fix the label here.
          </p>
          {orphans.map((p) => (
            <div key={p.label} style={{ marginTop: 6 }}>
              <label className="edit-field" style={{ maxWidth: 160 }}>
                Label
                <input
                  value={p.label}
                  disabled={busy}
                  onChange={(e) => {
                    const next = (parts || []).map((row) =>
                      row === p ? { ...row, label: e.target.value } : row
                    );
                    onChange(next);
                  }}
                />
              </label>
              <p className="tiny muted">{(p.accepted_answers || p.acceptedAnswers || []).join("; ") || "(no wording)"}</p>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function isUuid(value) {
  return typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

function BlockEditor({ index, block, onChange, onRemove, assetUrl, onReplaceImage, onInsertImageAfter, busy }) {
  const type = block.type;
  return (
    <div className="edit-block">
      <div className="label" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px" }}>
        <span>
          {index + 1}. {String(type).toUpperCase()}
        </span>
        {onRemove ? (
          <button type="button" className="btn" style={{ padding: "2px 8px", fontSize: "0.75rem" }} disabled={busy} onClick={onRemove}>
            Remove
          </button>
        ) : null}
      </div>
      {type === "text" || type === "chemistry" ? (
        <>
          <textarea
            rows={6}
            value={block.value || ""}
            onChange={(e) => onChange({ ...block, value: e.target.value })}
            placeholder="Type or paste the question stem here…"
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
          rows={2}
          value={block.latex || ""}
          onChange={(e) => onChange({ ...block, latex: e.target.value })}
        />
      ) : null}
      {type === "image" ? (
        <div className="edit-image-block">
          {assetUrl ? (
            <img src={assetUrl} alt="Stem visual" className="asset-img edit-preview" />
          ) : (
            <div className="muted">No preview — upload an image.</div>
          )}
          <div className="muted tiny">Asset ID: {block.asset_id || block.asset || "—"}</div>
          <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
            <label className="btn">
              {block.asset_id || isUuid(block.asset) ? "Replace image" : "Upload image"}
              <input
                type="file"
                accept="image/png,image/jpeg,image/webp"
                hidden
                disabled={busy}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file && onReplaceImage) onReplaceImage(file);
                  e.target.value = "";
                }}
              />
            </label>
            {onRemove ? (
              <button type="button" className="btn" disabled={busy} onClick={onRemove}>
                Remove picture
              </button>
            ) : null}
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
              onChange={(e) => onChange({ ...block, ordered: e.target.checked })}
            />
            Numbered list (1, 2, 3…) — uncheck for bullet list (•)
          </label>
          {(block.items || []).map((item, itemIdx) => (
            <div key={itemIdx} style={{ display: "flex", gap: "8px", alignItems: "flex-start" }}>
              <span className="muted" style={{ minWidth: "24px", paddingTop: "6px", fontWeight: 600 }}>
                {block.ordered !== false ? `${itemIdx + 1}.` : "•"}
              </span>
              <textarea
                rows={2}
                style={{ flex: 1 }}
                value={item}
                disabled={busy}
                onChange={(e) => {
                  const items = [...(block.items || [])];
                  items[itemIdx] = e.target.value;
                  onChange({ ...block, items });
                }}
              />
              <button
                type="button"
                className="btn"
                style={{ padding: "4px 8px", fontSize: "0.75rem" }}
                disabled={busy || (block.items || []).length <= 1}
                onClick={() =>
                  onChange({
                    ...block,
                    items: (block.items || []).filter((_, i) => i !== itemIdx),
                  })
                }
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
            onClick={() => onChange({ ...block, items: [...(block.items || []), ""] })}
          >
            + Add statement
          </button>
        </div>
      ) : null}
      {type === "table" ? (
        <textarea
          rows={4}
          value={JSON.stringify({ columns: block.columns, rows: block.rows }, null, 2)}
          onChange={(e) => {
            try {
              const parsed = JSON.parse(e.target.value);
              onChange({ ...block, ...parsed });
            } catch {
              /* ignore while typing */
            }
          }}
        />
      ) : null}
      {onInsertImageAfter ? (
        <label className="btn secondary" style={{ cursor: "pointer", marginTop: 8, display: "inline-block" }}>
          + Insert picture below this block
          <input
            type="file"
            accept="image/png,image/jpeg,image/webp"
            hidden
            disabled={busy}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) onInsertImageAfter(file);
              e.target.value = "";
            }}
          />
        </label>
      ) : null}
    </div>
  );
}
