import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import { MOBILE_QUERY } from "./hooks/use-mobile";

afterEach(cleanup);

// jsdom has no matchMedia; the sidebar asks it whether the screen is a phone's,
// at jsdom's 16px rem.
if (typeof window !== "undefined")
  window.matchMedia ??= (query: string) =>
    ({
      matches: query === MOBILE_QUERY && window.innerWidth < 768,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }) as unknown as MediaQueryList;
