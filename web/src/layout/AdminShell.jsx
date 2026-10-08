import { Link } from "../lib/router";

export function AdminShell({ children }) {
  return (
    <div className="admin-shell">
      <aside className="admin-nav">
        <div className="nav-brand">
          <strong>EduNets</strong>
          <span>Question Ops</span>
        </div>

        <div className="nav-section">
          <div className="nav-section-title">Question Factory</div>
          <Link to="/admin/question-bank" end>
            Overview
          </Link>
          <Link to="/admin/question-bank/import">Import Paper</Link>
          <Link to="/admin/question-bank/classification">Classification</Link>
        </div>

        <div className="nav-section">
          <div className="nav-section-title">Trusted Bank</div>
          <Link to="/admin/questions">Question Bank</Link>
          <Link to="/admin/syllabi">Syllabus</Link>
        </div>

        <div className="nav-section">
          <div className="nav-section-title">Practice</div>
          <Link to="/simulated-test">Simulated Test</Link>
        </div>
      </aside>
      <div className="admin-main">{children}</div>
    </div>
  );
}
