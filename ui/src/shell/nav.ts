import {
  Activity,
  BookOpenText,
  Bot,
  Boxes,
  BrainCircuit,
  ClipboardCheck,
  Database,
  FlaskConical,
  Users,
  Waypoints,
} from "lucide-react";
import { extensions } from "../extensions";
import type { NavItem, SettingsPage } from "../lib/extensions";

export interface NavSection {
  label?: string;
  items: NavItem[];
}

export const DOCS_NAV: NavItem = {
  to: "/docs",
  label: "Docs",
  icon: BookOpenText,
};

export function getNavSections(): NavSection[] {
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
  ];
  return sections.filter((section) => section.items.length > 0);
}

export const RESERVED_SETTINGS = [
  "workspace",
  "users",
  "trace-sources",
  "llm-providers",
];

export function wrapperSettingsPages(pages: SettingsPage[]): SettingsPage[] {
  return pages.filter((page) => !RESERVED_SETTINGS.includes(page.name));
}

function settingsItem(page: SettingsPage): NavItem {
  return { to: `/settings/${page.name}`, label: page.label, icon: page.icon };
}

export function getSettingsSections(
  isAdmin: boolean,
  workspace: string,
): NavSection[] {
  const sections: NavSection[] = [
    {
      label: "Account",
      items: wrapperSettingsPages(extensions.accountSettings).map(settingsItem),
    },
    {
      label: "Workspace",
      items: [
        { to: "/settings/workspace", label: `@${workspace}`, icon: Boxes },
      ],
    },
  ];
  if (isAdmin)
    sections.push({
      label: "Organization",
      items: [
        { to: "/settings/users", label: "Users", icon: Users },
        {
          to: "/settings/trace-sources",
          label: "Trace sources",
          icon: Waypoints,
        },
        {
          to: "/settings/llm-providers",
          label: "LLM providers",
          icon: BrainCircuit,
        },
        ...wrapperSettingsPages(extensions.organizationSettings).map(
          settingsItem,
        ),
      ],
    });
  return sections.filter((section) => section.items.length > 0);
}
