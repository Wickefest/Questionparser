import { BrowserRouter, matchPath, useRouter } from "./lib/router";
import { AdminShell } from "./layout/AdminShell";
import { FactoryDashboard } from "./pages/FactoryDashboard";
import { ImportWizard } from "./pages/ImportWizard";
import { BatchProcessing } from "./pages/BatchProcessing";
import { ReviewQueue } from "./pages/ReviewQueue";
import { ReviewWorkspace } from "./pages/ReviewWorkspace";
import { QuestionBankPage, QuestionDetailPage } from "./pages/QuestionBank";
import { ClassificationPage, SyllabiPage } from "./pages/Classification";
import { SimulatedTest } from "./pages/SimulatedTest";

function Routes() {
  const { path, navigate } = useRouter();

  if (path === "/" || path === "") {
    // Defer redirect to avoid setState during render
    queueMicrotask(() => navigate("/admin/question-bank", { replace: true }));
    return null;
  }

  let page = null;
  if (path === "/admin/question-bank") page = <FactoryDashboard />;
  else if (path === "/admin/question-bank/import") page = <ImportWizard />;
  else if (path === "/admin/question-bank/classification") page = <ClassificationPage />;
  else if (path === "/admin/questions") page = <QuestionBankPage />;
  else if (path === "/admin/syllabi") page = <SyllabiPage />;
  else if (path === "/simulated-test") page = <SimulatedTest />;
  else {
    let m;
    if ((m = matchPath("/admin/question-bank/imports/:batchId/review/:draftId", path))) {
      page = <ReviewWorkspace batchId={m.batchId} draftId={m.draftId} />;
    } else if ((m = matchPath("/admin/question-bank/imports/:batchId/review", path))) {
      page = <ReviewQueue batchId={m.batchId} />;
    } else if ((m = matchPath("/admin/question-bank/imports/:batchId", path))) {
      page = <BatchProcessing batchId={m.batchId} />;
    } else if ((m = matchPath("/admin/questions/:questionId", path))) {
      page = <QuestionDetailPage questionId={m.questionId} />;
    } else {
      page = (
        <div className="page">
          <h1>Not found</h1>
          <p className="muted">{path}</p>
        </div>
      );
    }
  }

  const bare = path.includes("/review/") && matchPath(
    "/admin/question-bank/imports/:batchId/review/:draftId",
    path
  );
  if (bare) return page;
  return <AdminShell>{page}</AdminShell>;
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes />
    </BrowserRouter>
  );
}
