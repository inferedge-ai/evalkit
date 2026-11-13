import { startTransition, useEffect, useState } from "react";
import { Lockup } from "@/components/widgets/Logo";
import {
  NavLink,
  Outlet,
  useLocation,
  useMatch,
  useNavigate,
} from "react-router-dom";
import {
  ArrowLeft,
  ChevronRight,
  LogOut,
  Moon,
  Puzzle,
  Search,
  Settings,
  Sun,
} from "lucide-react";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
  SIDEBAR_COOKIE_NAME,
  useSidebar,
} from "@/components/ui/sidebar";
import { cn } from "@/lib/utils";
import { extensions } from "../extensions";
import { logout } from "../lib/api";
import type { NavItem } from "../lib/extensions";
import { Select } from "../lib/Select";
import {
  describeOtherWorkspaces,
  othersPersonal,
  sectionRoot,
  workspaceHint,
} from "../lib/workspace";
import type { Me, WorkspaceInfo } from "../lib/types";
import { useClearMe, useMe } from "./capabilities";
import { JumpPalette } from "./JumpPalette";
import {
  DOCS_NAV,
  getNavSections,
  type NavSection,
  useSettingsSections,
} from "./nav";
import { useTheme } from "./theme";
import { SWITCH_FLASH_MS, useSwitchWorkspace, useWorkspace } from "./workspace";

export function BrandLink({
  to = "/",
  onClick,
}: {
  to?: string;
  onClick?: () => void;
}) {
  return (
    <NavLink to={to} className="flex items-center" onClick={onClick}>
      <Lockup />
    </NavLink>
  );
}

// SidebarProvider writes this cookie whenever the sidebar is toggled.
function readSidebarCookie(): boolean {
  return !document.cookie.split("; ").includes(`${SIDEBAR_COOKIE_NAME}=false`);
}

export function Shell() {
  const { pathname, search } = useLocation();
  const inSettings = useMatch({ path: "/settings", end: false }) !== null;
  const { workspace } = useWorkspace();
  const [appPage, setAppPage] = useState({ path: "/", workspace: "" });
  useEffect(() => {
    if (!inSettings)
      setAppPage({ path: pathname + search, workspace: workspace.name });
  }, [inSettings, pathname, search, workspace.name]);
  const backTo =
    appPage.workspace === workspace.name
      ? appPage.path
      : (sectionRoot(appPage.path) ?? appPage.path);
  return (
    <SidebarProvider
      defaultOpen={readSidebarCookie()}
      className="h-dvh overflow-hidden"
    >
      {inSettings ? <SettingsNav backTo={backTo} /> : <SideNav />}
      <SidebarInset>
        <header className="flex h-12 shrink-0 items-center gap-2 border-b bg-card px-2 md:hidden">
          <SidebarTrigger aria-label="Open navigation" />
          <BrandLink />
        </header>
        <div className="min-h-0 flex-1 overflow-auto">
          <Outlet />
        </div>
      </SidebarInset>
      <JumpPalette />
    </SidebarProvider>
  );
}

function SideNav() {
  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="gap-0 p-0">
        <SideNavHeader />
      </SidebarHeader>
      <SidebarContent>
        <NavSections sections={getNavSections()} />
      </SidebarContent>
      <NavFooter />
    </Sidebar>
  );
}

function SettingsNav({ backTo }: { backTo: string }) {
  const sections = useSettingsSections();
  const { isMobile, setOpenMobile } = useSidebar();
  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="gap-0 p-0">
        <SideNavHeader />
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton
                render={<NavLink to={backTo} />}
                tooltip="Back to evalkit"
                onClick={() => {
                  if (isMobile) setOpenMobile(false);
                }}
              >
                <ArrowLeft />
                <span>Back to evalkit</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarGroup>
        <NavSections sections={sections} />
      </SidebarContent>
      <NavFooter />
    </Sidebar>
  );
}

function NavSections({ sections }: { sections: NavSection[] }) {
  return sections.map((section, i) => (
    <SidebarGroup
      key={section.label ?? i}
      role="group"
      aria-label={section.label}
    >
      {section.label && <SidebarGroupLabel>{section.label}</SidebarGroupLabel>}
      <SidebarMenu>
        {section.items.map((item) => (
          <NavRow key={item.to} item={item} />
        ))}
      </SidebarMenu>
    </SidebarGroup>
  ));
}

function NavFooter() {
  const me = useMe();
  return (
    <SidebarFooter>
      <SidebarMenu>
        <NavRow item={DOCS_NAV} />
        <ProfileMenu me={me} />
      </SidebarMenu>
    </SidebarFooter>
  );
}

function NavRow({ item }: { item: NavItem }) {
  const active = useMatch({ path: item.to, end: false }) !== null;
  const { isMobile, setOpenMobile } = useSidebar();
  const Icon = item.icon ?? Puzzle;
  return (
    <SidebarMenuItem>
      <SidebarMenuButton
        render={<NavLink to={item.to} />}
        isActive={active}
        tooltip={item.label}
        onClick={() => {
          if (isMobile) setOpenMobile(false);
        }}
      >
        <Icon />
        <span>{item.label}</span>
      </SidebarMenuButton>
    </SidebarMenuItem>
  );
}

function SideNavHeader() {
  const { state, isMobile, setOpenMobile } = useSidebar();
  const collapsed = state === "collapsed" && !isMobile;
  const toggleLabel = isMobile
    ? "Close navigation"
    : collapsed
      ? "Expand sidebar"
      : "Collapse sidebar";
  return (
    <>
      <div className="flex min-h-12 items-center justify-between overflow-hidden p-2 group-data-[collapsible=icon]:justify-center">
        {!collapsed && (
          <span className="px-2">
            <BrandLink onClick={() => setOpenMobile(false)} />
          </span>
        )}
        <SidebarTrigger
          aria-label={toggleLabel}
          title={isMobile ? toggleLabel : `${toggleLabel} (⌘B)`}
          className="shrink-0"
        />
      </div>
      <SidebarMenu className="gap-2 px-2 pb-2">
        <WorkspaceSwitcher />
        <WorkspaceBadge />
        <SidebarMenuItem>
          <SidebarMenuButton
            variant="outline"
            tooltip="Search (⌘K)"
            onClick={() => {
              setOpenMobile(false);
              window.dispatchEvent(new Event("jump-palette:open"));
            }}
          >
            <Search />
            <span>Search</span>
            <kbd className="ml-auto font-mono text-xs text-muted-foreground group-data-[collapsible=icon]:hidden">
              ⌘K
            </kbd>
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
    </>
  );
}

function WorkspaceBadge() {
  const { workspace } = useWorkspace();
  const { setOpen } = useSidebar();
  return (
    <SidebarMenuItem className="hidden group-data-[collapsible=icon]:block">
      <SidebarMenuButton
        tooltip={`@${workspace.name}`}
        aria-label={`Workspace @${workspace.name}`}
        onClick={() => setOpen(true)}
      >
        <span className="text-xs font-semibold">
          {workspace.name.slice(0, 2).toUpperCase()}
        </span>
      </SidebarMenuButton>
    </SidebarMenuItem>
  );
}

function WorkspaceSwitcher() {
  const me = useMe();
  const { workspace, workspaces, switchedAt } = useWorkspace();
  const switchTo = useSwitchWorkspace();
  const [flash, setFlash] = useState(false);
  useEffect(() => {
    if (switchedAt === null) return;
    setFlash(true);
    const timer = setTimeout(() => setFlash(false), SWITCH_FLASH_MS);
    return () => clearTimeout(timer);
  }, [switchedAt]);
  const option = (ws: WorkspaceInfo) => ({
    value: ws.name,
    label: `@${ws.name}`,
    hint: workspaceHint(ws, me.personal_workspace),
  });
  const others = workspaces.filter((ws) =>
    othersPersonal(ws, me.personal_workspace),
  );
  return (
    <SidebarMenuItem
      className={cn(
        "flex flex-col gap-1 rounded-md group-data-[collapsible=icon]:hidden",
        flash && "ws-switched",
      )}
      aria-live="polite"
    >
      <span className="px-2 text-xs text-sidebar-foreground/70">
        {flash ? `Switched to @${workspace.name}` : "Workspace"}
        {othersPersonal(workspace, me.personal_workspace) && " · read-only"}
      </span>
      <Select
        className="w-full"
        ariaLabel="workspace"
        title="workspace (⌘K, w)"
        value={workspace.name}
        onChange={switchTo}
        options={workspaces
          .filter((ws) => !othersPersonal(ws, me.personal_workspace))
          .map(option)}
        more={{
          label: describeOtherWorkspaces(others.length),
          options: others.map(option),
        }}
      />
    </SidebarMenuItem>
  );
}

function ProfileMenu({ me }: { me: Me }) {
  const navigate = useNavigate();
  const clearMe = useClearMe();
  const { theme, toggle } = useTheme();
  const initials = me.username.slice(0, 2).toUpperCase();
  return (
    <SidebarMenuItem>
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <SidebarMenuButton
              tooltip={me.username}
              size="lg"
              aria-label={me.username}
            />
          }
        >
          <Avatar className="size-8 shrink-0">
            <AvatarFallback>{initials}</AvatarFallback>
          </Avatar>
          <span className="flex min-w-0 flex-1 flex-col group-data-[collapsible=icon]:hidden">
            <span className="truncate font-semibold">{me.username}</span>
            {me.email !== null && (
              <span className="truncate text-xs text-muted-foreground">
                {me.email}
              </span>
            )}
          </span>
          <ChevronRight className="text-muted-foreground group-data-[collapsible=icon]:hidden" />
        </DropdownMenuTrigger>
        <DropdownMenuContent side="right" align="end" className="min-w-56">
          <div className="flex items-center gap-2 p-2">
            <Avatar className="size-10 shrink-0">
              <AvatarFallback>{initials}</AvatarFallback>
            </Avatar>
            <div className="flex min-w-0 flex-col">
              <span className="truncate text-sm font-semibold">
                {me.username}
              </span>
              {me.email !== null && (
                <span className="truncate text-xs text-muted-foreground">
                  {me.email}
                </span>
              )}
            </div>
          </div>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => navigate("/settings")}>
            <Settings />
            Settings
          </DropdownMenuItem>
          <DropdownMenuItem onClick={toggle}>
            {theme === "dark" ? <Sun /> : <Moon />}
            {theme === "dark" ? "Light mode" : "Dark mode"}
          </DropdownMenuItem>
          <DropdownMenuItem
            onClick={() =>
              logout().finally(() =>
                startTransition(() => {
                  clearMe();
                  navigate(extensions.landingPage ? "/" : "/login");
                }),
              )
            }
          >
            <LogOut />
            Sign out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </SidebarMenuItem>
  );
}
