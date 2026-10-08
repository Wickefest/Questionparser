const API_BASE = import.meta.env.VITE_API_BASE || "";

async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, options);
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!res.ok) {
    const detail = data?.detail || data?.message || res.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

export const api = {
  dashboard: (params = {}) => {
    const qs = new URLSearchParams();
    if (params.limit != null) qs.set("limit", String(params.limit));
    const query = qs.toString();
    return request(`/api/factory/dashboard${query ? `?${query}` : ""}`);
  },
  batches: (params = {}) => {
    const qs = new URLSearchParams();
    if (params.limit != null) qs.set("limit", String(params.limit));
    const query = qs.toString();
    return request(`/api/factory/batches${query ? `?${query}` : ""}`);
  },
  syllabi: () => request("/api/factory/syllabi"),
  syllabus: (id) => request(`/api/factory/syllabi/${id}`),
  createSyllabus: (body) =>
    request("/api/factory/syllabi", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  importSyllabusPdf: (id, file) => {
    const fd = new FormData();
    fd.append("syllabus_pdf", file);
    return request(`/api/factory/syllabi/${id}/import-pdf`, { method: "POST", body: fd });
  },
  checkDuplicates: (body) =>
    request("/api/factory/imports/check-duplicates", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  createImport: (formData) =>
    request("/api/factory/imports", { method: "POST", body: formData }),
  batch: (batchId) => request(`/api/factory/batches/${batchId}`),
  deleteBatch: (batchId) =>
    request(`/api/factory/batches/${batchId}`, { method: "DELETE" }),
  cancelIngest: (batchId) =>
    request(`/api/factory/batches/${batchId}/cancel`, { method: "POST" }),
  reprocess: (batchId) =>
    request(`/api/factory/batches/${batchId}/reprocess`, { method: "POST" }),
  drafts: (batchId, params = {}) => {
    const qs = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value == null || value === "") continue;
      qs.set(key, String(value));
    }
    const query = qs.toString();
    return request(`/api/factory/batches/${batchId}/drafts${query ? `?${query}` : ""}`);
  },
  createDraft: (batchId, body) =>
    request(`/api/factory/batches/${batchId}/drafts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  draft: (batchId, draftId) =>
    request(`/api/factory/batches/${batchId}/drafts/${draftId}`),
  editDraft: (batchId, draftId, body) =>
    request(`/api/factory/batches/${batchId}/drafts/${draftId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  replaceDraftAsset: (batchId, draftId, assetId, file) => {
    const fd = new FormData();
    fd.append("file", file);
    return request(
      `/api/factory/batches/${batchId}/drafts/${draftId}/assets/${assetId}/replace`,
      { method: "POST", body: fd }
    );
  },
  rejectDraft: (batchId, draftId, reason) =>
    request(`/api/factory/batches/${batchId}/drafts/${draftId}/reject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason }),
    }),
  approveDraft: (batchId, draftId) =>
    request(`/api/factory/batches/${batchId}/drafts/${draftId}/approve`, {
      method: "POST",
    }),
  approveAllInBatch: (batchId, { includeFlagged = false } = {}) =>
    request(
      `/api/factory/batches/${batchId}/approve-all?include_flagged=${includeFlagged}`,
      { method: "POST" }
    ),
  questions: () => request("/api/factory/questions"),
  question: (id) => request(`/api/factory/questions/${id}`),
  uploadGeneralAsset: (file) => {
    const fd = new FormData();
    fd.append("file", file);
    return request("/api/factory/assets/upload", {
      method: "POST",
      body: fd,
    });
  },
  createCorrectionVersion: (id, body) =>
    request(`/api/factory/questions/${id}/versions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  classification: () => request("/api/factory/classification"),
  classificationHistory: () => request("/api/factory/classification/history"),
  runClassification: (versionId) =>
    request("/api/factory/classification/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ versionId }),
    }),
  runBatchClassification: (versionIds, { skipClassified = true } = {}) =>
    request("/api/factory/classification/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ versionIds, skipClassified }),
    }),
  manualClassification: (body) =>
    request("/api/factory/classification/manual", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  simulatedTestQuestions: (source, count, assets = "svg") =>
    request(
      `/api/simulated-test/questions?source=${encodeURIComponent(source)}&count=${count}&assets=${encodeURIComponent(assets)}`,
    ),
  simulatedTestAnswer: (body) =>
    request("/api/simulated-test/answer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
};

export async function sha256File(file) {
  const buf = await file.arrayBuffer();
  const hash = await crypto.subtle.digest("SHA-256", buf);
  return [...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}
