import type { ReactElement } from "react";

export type NavItem = { to: string; label: string };

export type ExtensionRoute = { path: string; element: ReactElement };

export type UiExtensions = {
  routes: ExtensionRoute[];
  nav: NavItem[];
  moreNav: NavItem[];
  adminNav: NavItem[];
};
