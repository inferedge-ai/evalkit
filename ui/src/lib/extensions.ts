import type { LucideIcon } from "lucide-react";
import type { ReactElement } from "react";

export type NavItem = { to: string; label: string; icon?: LucideIcon };

export type ExtensionRoute = { path: string; element: ReactElement };

export type UiExtensions = {
  routes: ExtensionRoute[];
  nav: NavItem[];
  moreNav: NavItem[];
  adminNav: NavItem[];
};
