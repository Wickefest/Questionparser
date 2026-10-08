export function LoadingOverlay({
  show,
  title = "Working…",
  message = "Please wait while this finishes.",
}) {
  if (!show) return null;
  return (
    <div className="loading-overlay" role="status" aria-live="polite">
      <div className="loading-card">
        <div className="spinner" aria-hidden="true" />
        <div>
          <strong>{title}</strong>
          <p className="muted">{message}</p>
        </div>
      </div>
    </div>
  );
}

export function BusyBanner({ show, title, message }) {
  if (!show) return null;
  return (
    <div className="busy-banner" role="status" aria-live="polite">
      <div className="spinner sm" aria-hidden="true" />
      <div>
        <strong>{title}</strong>
        {message ? <span className="muted"> — {message}</span> : null}
      </div>
    </div>
  );
}
