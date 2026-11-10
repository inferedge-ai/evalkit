import type { UiExtensions } from "../lib/extensions";

// A package extending evalkit replaces this folder before building; see "Extending evalkit" in AGENTS.md.
export const extensions: UiExtensions = {
  routes: [],
  nav: [],
  accountSettings: [],
  organizationSettings: [],
};
