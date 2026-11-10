import { Navigate, Route, useParams } from "react-router-dom";
import { LlmProvidersPage } from "./pages/LlmProvidersPage";
import { TraceSourcesPage } from "./pages/TraceSourcesPage";
import { UsersAdminPage } from "./pages/UsersAdmin";
import { WorkspacePage } from "./pages/WorkspacePage";
import { useMe } from "./shell/capabilities";
import { getSettingsSections, getWrapperSettings } from "./shell/nav";
import { useWorkspace } from "./shell/workspace";

function FirstSettingsPage() {
  const me = useMe();
  const { workspace } = useWorkspace();
  const [first] = getSettingsSections(me.is_admin, workspace.name);
  return <Navigate to={first.items[0].to} replace />;
}

function MovedWorkspaceTab() {
  const { tab } = useParams();
  return <Navigate to={`/settings/workspace/${tab}`} replace />;
}

export function renderSettingsRoutes() {
  const { account, organization } = getWrapperSettings();
  const wrapperPages = [...account, ...organization];
  return (
    <>
      <Route path="/settings" element={<FirstSettingsPage />} />
      <Route
        path="/settings/workspace"
        element={<Navigate to="evaluators" replace />}
      />
      <Route path="/settings/workspace/:tab" element={<WorkspacePage />} />
      <Route path="/settings/users" element={<UsersAdminPage />} />
      <Route path="/settings/trace-sources" element={<TraceSourcesPage />} />
      <Route path="/settings/llm-providers" element={<LlmProvidersPage />} />
      {wrapperPages.map((page) => (
        <Route
          key={page.name}
          path={`/settings/${page.name}`}
          element={page.element}
        />
      ))}
    </>
  );
}

export function renderMovedSettingsRoutes() {
  return (
    <>
      <Route
        path="/workspace"
        element={<Navigate to="/settings/workspace" replace />}
      />
      <Route path="/workspace/:tab" element={<MovedWorkspaceTab />} />
      <Route
        path="/users"
        element={<Navigate to="/settings/users" replace />}
      />
      <Route
        path="/admin/trace-sources"
        element={<Navigate to="/settings/trace-sources" replace />}
      />
      <Route
        path="/admin/llm-providers"
        element={<Navigate to="/settings/llm-providers" replace />}
      />
    </>
  );
}
