import { formatBytes } from "../api/client";

/**
 * Distinct upload box with hover state, padding, and clear role labeling.
 */
export function UploadZone({
  title,
  hint,
  badge,
  file,
  accept = "application/pdf,.pdf",
  disabled = false,
  onFile,
  onClear,
}) {
  return (
    <label className={`upload-zone ${file ? "has-file" : ""} ${disabled ? "disabled" : ""}`}>
      <div className="upload-zone-top">
        {badge ? <span className="upload-badge">{badge}</span> : null}
        <div className="upload-zone-title">{title}</div>
      </div>
      {!file ? (
        <div className="upload-zone-body">
          <div className="upload-icon" aria-hidden="true">
            PDF
          </div>
          <div className="upload-zone-hint">{hint || "Drop a PDF here or click to browse"}</div>
          <div className="muted tiny">PDF only</div>
        </div>
      ) : (
        <div className="upload-zone-file">
          <div>
            <strong className="upload-file-name">{file.name}</strong>
            <div className="muted">{formatBytes(file.size)}</div>
          </div>
          <button
            type="button"
            className="btn"
            disabled={disabled}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onClear?.();
            }}
          >
            Remove
          </button>
        </div>
      )}
      <input
        type="file"
        accept={accept}
        hidden
        disabled={disabled}
        onChange={(e) => {
          onFile?.(e.target.files?.[0] || null);
          e.target.value = "";
        }}
      />
    </label>
  );
}

export function HistoryPanel({
  title = "Recent activity",
  items = [],
  empty = "No history yet.",
  headerExtra = null,
  bare = false,
}) {
  const body = !items.length ? (
    <p className="muted">{empty}</p>
  ) : (
    <ul className="history-list">
      {items.map((item) => (
        <li key={item.id} className={`history-item ${item.tone ? `history-item-${item.tone}` : ""}`}>
          <div className="history-main">
            <strong>{item.title}</strong>
            {item.meta ? <div className="muted tiny">{item.meta}</div> : null}
          </div>
          <div className="history-side">
            {item.status ? (
              <span className={`status-badge status-${String(item.status).toLowerCase()}`}>
                {item.status}
              </span>
            ) : null}
            {item.when ? <span className="muted tiny">{item.when}</span> : null}
            {item.action || null}
          </div>
        </li>
      ))}
    </ul>
  );

  if (bare) return body;

  return (
    <section className="panel history-panel">
      {(title || headerExtra) && (
        <div
          className="panel-header"
          style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}
        >
          {title ? <h2 style={{ margin: 0 }}>{title}</h2> : <span />}
          {headerExtra}
        </div>
      )}
      {body}
    </section>
  );
}
