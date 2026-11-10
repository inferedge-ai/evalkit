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

const WORKSPACE_SETTINGS: NavItem = {
  to: "/settings/workspace",
  label: "Workspace",
  icon: Boxes,
};

const ORGANIZATION_SETTINGS: NavItem[] = [
  { to: "/settings/users", label: "Users", icon: Users },
  { to: "/settings/trace-sources", label: "Trace sources", icon: Waypoints },
  { to: "/settings/llm-providers", label: "LLM providers", icon: BrainCircuit },
];

const EVALKIT_SETTINGS = [WORKSPACE_SETTINGS, ...ORGANIZATION_SETTINGS].map(
  (item) => item.to,
);

export function getWrapperSettings(): {
  account: SettingsPage[];
  organization: SettingsPage[];
} {
  const taken = new Set(EVALKIT_SETTINGS);
  const claim = (page: SettingsPage) => {
    const to = `/settings/${page.name}`;
    if (!/^[a-z0-9-]+$/.test(page.name) || taken.has(to)) return false;
    taken.add(to);
    return true;
  };
  return {
    account: extensions.accountSettings.filter(claim),
    organization: extensions.organizationSettings.filter(claim),
  };
}

function toSettingsItem(page: SettingsPage): NavItem {
  return { to: `/settings/${page.name}`, label: page.label, icon: page.icon };
}

export function getSettingsSections(
  isAdmin: boolean,
  workspace: string,
): NavSection[] {
  const wrapperSettings = getWrapperSettings();
  const sections: NavSection[] = [
    {
      label: "Account",
      items: wrapperSettings.account.map(toSettingsItem),
    },
    {
      label: "Workspace",
      items: [{ ...WORKSPACE_SETTINGS, label: `@${workspace}` }],
    },
  ];
  if (isAdmin)
    sections.push({
      label: "Organization",
      items: [
        ...ORGANIZATION_SETTINGS,
        ...wrapperSettings.organization.map(toSettingsItem),
      ],
    });
  return sections.filter((section) => section.items.length > 0);
}
