import { useMemo, useState } from "react";

/**
 * Source PDF preview with zoom. Uses browser PDF viewer via iframe.
 */
export function SourcePreview({
  url,
  page = 1,
  title = "Original source",
  missing = false,
  error = null,
}) {
  const [zoom, setZoom] = useState(100);

  const src = useMemo(() => {
    if (!url) return null;
    const base = url.split("#")[0];
    return `${base}#page=${page || 1}`;
  }, [url, page]);

  if (missing || !url) {
    return (
      <div className="source-preview empty-state">
        <div className="label">{title}</div>
        <p className="warn">Original source unavailable.</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="source-preview empty-state">
        <div className="label">{title}</div>
        <p className="error-text">{error}</p>
      </div>
    );
  }

  return (
    <div className="source-preview">
      <div className="source-toolbar">
        <div className="label">{title}</div>
        <div className="muted">Page {page || 1}</div>
        <div className="zoom-controls">
          <button type="button" onClick={() => setZoom((z) => Math.max(50, z - 10))} aria-label="Zoom out">
            −
          </button>
          <span>{zoom}%</span>
          <button type="button" onClick={() => setZoom((z) => Math.min(200, z + 10))} aria-label="Zoom in">
            +
          </button>
          <button type="button" onClick={() => setZoom(100)}>
            Fit
          </button>
        </div>
      </div>
      <div className="source-frame-wrap">
        <iframe
          title={title}
          src={src}
          className="source-frame"
          style={{ width: `${zoom}%`, height: `${Math.max(100, zoom)}%` }}
        />
      </div>
    </div>
  );
}
