import type { LucideIcon } from "lucide-react";
import type { ReactElement } from "react";

export type NavSectionName = "Evaluate" | "Observe";

export type NavItem = {
  to: string;
  label: string;
  icon?: LucideIcon;
  section?: NavSectionName;
};

export type ExtensionRoute = { path: string; element: ReactElement };

export type SettingsPage = {
  name: string;
  label: string;
  icon?: LucideIcon;
  element: ReactElement;
};

// An entry in the trajectory viewer's sidebar. One with a stepId jumps to that
// step when clicked, in the trajectory `tab` names ("main", or a subagent's
// trajectory id); one without is plain text.
export type RunSidebarItem = {
  key: string;
  label: string;
  tab?: string;
  stepId?: number;
};

export type RunSidebarSection = { title: string; items: RunSidebarItem[] };

export type RunRef = { agent: string; runId: string };

export type UiExtensions = {
  routes: ExtensionRoute[];
  publicRoutes: ExtensionRoute[];
  landingPage: ReactElement | null;
  nav: NavItem[];
  accountSettings: SettingsPage[];
  organizationSettings: SettingsPage[];
  // A React hook the agent run page calls: the sections it returns are listed
  // in the trajectory viewer's sidebar, above Prompts.
  useRunSidebar?: (run: RunRef) => RunSidebarSection[];
};
