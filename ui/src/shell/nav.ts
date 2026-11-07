import { extensions } from "../extensions";
import type { NavItem } from "../lib/extensions";

export const NAV: NavItem[] = [
  { to: "/datasets", label: "Datasets" },
  { to: "/experiments", label: "Experiments" },
  { to: "/reviews", label: "Reviews" },
  { to: "/runs", label: "Runs" },
  ...extensions.nav,
];

export const MORE_NAV: NavItem[] = [
  { to: "/agents", label: "Agents" },
  { to: "/workspace", label: "Settings" },
  { to: "/docs", label: "Docs" },
  ...extensions.moreNav,
];

export const ADMIN_NAV: NavItem[] = [
  { to: "/users", label: "Users" },
  { to: "/admin/trace-sources", label: "Trace sources" },
  { to: "/admin/llm-providers", label: "LLM providers" },
  ...extensions.adminNav,
];
