import { Navigate, Route, Routes } from "react-router-dom";
import { LoginCallbackPage } from "./auth/LoginCallbackPage";
import { LoginPage } from "./auth/LoginPage";
import { Home } from "./pages/Home";
import { DocsPage } from "./pages/DocsPage";
import { AgentPage } from "./agents/AgentPage";
import { AgentsPage } from "./agents/AgentsPage";
import { ReviewsPage } from "./pages/ReviewsPage";
import { AuthGate, RequireMe } from "./shell/capabilities";
import { ShellOrBare, WorkspaceOrPublic } from "./shell/layout";
import { CasePage } from "./evals/CasePage";
import { NotFound } from "./shell/NotFound";
import { ComparePage } from "./evals/ComparePage";
import { DatasetsPage } from "./evals/DatasetsPage";
import { DatasetPage } from "./evals/DatasetPage";
import { ExperimentPage } from "./evals/ExperimentPage";
import { ExperimentsPage } from "./evals/ExperimentsPage";
import { RunPage } from "./evals/RunPage";
import { RunsBrowser } from "./runs/RunsBrowser";
import { AgentRunPage } from "./runs/AgentRunPage";
import { extensions } from "./extensions";
import {
  renderMovedSettingsRoutes,
  renderSettingsRoutes,
} from "./settingsRoutes";

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/login/callback" element={<LoginCallbackPage />} />
      {renderMovedSettingsRoutes()}
      <Route element={<AuthGate />}>
        <Route element={<WorkspaceOrPublic />}>
          <Route element={<ShellOrBare />}>
            <Route
              path="/docs"
              element={<Navigate to="/docs/quickstart" replace />}
            />
            <Route path="/docs/:tab" element={<DocsPage />} />
            <Route
              path="/cli"
              element={<Navigate to="/docs/install" replace />}
            />
            <Route element={<RequireMe />}>
              <Route path="/" element={<Home />} />
              <Route path="/datasets" element={<DatasetsPage />} />
              <Route
                path="/datasets/:ref"
                element={<Navigate to="experiments" replace />}
              />
              <Route path="/datasets/:ref/:tab" element={<DatasetPage />} />
              <Route
                path="/datasets/:ref/cases/:caseId"
                element={<CasePage />}
              />
              <Route path="/experiments" element={<ExperimentsPage />} />
              <Route path="/experiments/compare" element={<ComparePage />} />
              <Route
                path="/experiments/:experimentId"
                element={<ExperimentPage />}
              />
              <Route
                path="/experiments/:experimentId/:exampleId"
                element={<RunPage />}
              />
              <Route path="/reviews" element={<ReviewsPage />} />
              <Route path="/runs" element={<RunsBrowser />} />
              <Route path="/runs/:agent/:runId" element={<AgentRunPage />} />
              <Route path="/agents" element={<AgentsPage />} />
              <Route
                path="/agents/:agent"
                element={<Navigate to="overview" replace />}
              />
              <Route path="/agents/:agent/:tab" element={<AgentPage />} />
              <Route
                path="/harness"
                element={<Navigate to="/agents" replace />}
              />
              {renderSettingsRoutes()}
              {extensions.routes.map((r) => (
                <Route key={r.path} path={r.path} element={r.element} />
              ))}
              <Route
                path="*"
                element={
                  <NotFound what="page" up={{ label: "Home", to: "/" }} />
                }
              />
            </Route>
          </Route>
        </Route>
      </Route>
    </Routes>
  );
}
