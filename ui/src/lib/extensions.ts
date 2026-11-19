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

export type UiExtensions = {
  routes: ExtensionRoute[];
  publicRoutes: ExtensionRoute[];
  landingPage: ReactElement | null;
  nav: NavItem[];
  accountSettings: SettingsPage[];
  organizationSettings: SettingsPage[];
};
