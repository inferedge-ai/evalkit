import { useEffect, useState } from "react";
import { Mark } from "@/components/widgets/Logo";

export const LOADER_LINES = [
  "Every failed case is a lesson for your agent.",
  "Good agents are measured, not guessed.",
  "Turning traces into better agents.",
];

const LINE_MS = 2400;

function pickOther(current: number): number {
  const next = Math.floor(Math.random() * (LOADER_LINES.length - 1));
  return next >= current ? next + 1 : next;
}

export function SessionLoader() {
  const [line, setLine] = useState(() =>
    Math.floor(Math.random() * LOADER_LINES.length),
  );

  useEffect(() => {
    const timer = setInterval(() => setLine(pickOther), LINE_MS);
    return () => clearInterval(timer);
  }, []);

  return (
    <div
      role="status"
      aria-label="Checking session…"
      className="flex h-dvh flex-col items-center justify-center gap-6 px-6"
    >
      <div className="relative size-14">
        <div className="absolute inset-0 rounded-[22%] bg-[#6B9AE8] blur-xl motion-safe:animate-glow" />
        <Mark className="relative size-14 motion-safe:animate-breathe" />
      </div>
      <p
        key={line}
        aria-hidden="true"
        className="text-center text-sm text-muted-foreground motion-safe:animate-in motion-safe:fade-in motion-safe:duration-500"
      >
        {LOADER_LINES[line]}
      </p>
    </div>
  );
}
