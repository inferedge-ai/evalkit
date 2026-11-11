import * as React from "react";

// Tailwind's md breakpoint, where the desktop sidebar and the phone bar swap.
// In rem, so it moves with the browser's font size as the CSS does.
export const MOBILE_QUERY = "(width < 48rem)";

export function useIsMobile() {
  const [isMobile, setIsMobile] = React.useState<boolean | undefined>(
    undefined,
  );

  React.useEffect(() => {
    const mql = window.matchMedia(MOBILE_QUERY);
    const onChange = () => setIsMobile(mql.matches);
    mql.addEventListener("change", onChange);
    setIsMobile(mql.matches);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  return !!isMobile;
}
