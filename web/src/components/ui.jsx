export function StatusBadge({ status }) {
  if (!status) return null;
  const key = String(status).toUpperCase().replace(/\s+/g, "_");
  return <span className={`status-badge status-${key.toLowerCase()}`}>{formatStatus(key)}</span>;
}

function formatStatus(key) {
  return key.replace(/_/g, " ");
}

export function ProgressBar({ value, max = 100 }) {
  const pct = max ? Math.round((value / max) * 100) : 0;
  return (
    <div className="progress-bar" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
      <div className="progress-fill" style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
    </div>
  );
}

export function MetricCard({ label, value }) {
  return (
    <div className="metric">
      <div className="metric-value">{value}</div>
      <div className="metric-label">{label}</div>
    </div>
  );
}

export function EmptyState({ title, children, action }) {
  return (
    <div className="empty-state panel">
      <h3>{title}</h3>
      <div className="muted">{children}</div>
      {action}
    </div>
  );
}
