import {
  Activity,
  BookOpenText,
  Bot,
  BrainCircuit,
  ClipboardCheck,
  Database,
  FlaskConical,
  Settings,
  Users,
  Waypoints,
} from "lucide-react";
import { extensions } from "../extensions";
import type { NavItem } from "../lib/extensions";

export interface NavSection {
  label?: string;
  items: NavItem[];
}

export const DOCS_NAV: NavItem = {
  to: "/docs",
  label: "Docs",
  icon: BookOpenText,
};

export function getNavSections(isAdmin: boolean): NavSection[] {
  const sections: NavSection[] = [
    {
      label: "Evaluate",
      items: [
        { to: "/datasets", label: "Datasets", icon: Database },
        { to: "/experiments", label: "Experiments", icon: FlaskConical },
        { to: "/reviews", label: "Reviews", icon: ClipboardCheck },
      ],
    },
    {
      label: "Observe",
      items: [
        { to: "/runs", label: "Runs", icon: Activity },
        { to: "/agents", label: "Agents", icon: Bot },
      ],
    },
    { items: extensions.nav },
    {
      label: "Workspace",
      items: [
        { to: "/workspace", label: "Settings", icon: Settings },
        ...extensions.moreNav,
      ],
    },
  ];
  if (isAdmin)
    sections.push({
      label: "Admin",
      items: [
        { to: "/users", label: "Users", icon: Users },
        { to: "/admin/trace-sources", label: "Trace sources", icon: Waypoints },
        {
          to: "/admin/llm-providers",
          label: "LLM providers",
          icon: BrainCircuit,
        },
        ...extensions.adminNav,
      ],
    });
  return sections.filter((section) => section.items.length > 0);
}
